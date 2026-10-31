"""Bounded image-led extraction/review batches with persistent card coverage.

Source acquisition is separate. Native model calls require a Slurm allocation.
The result is an evidence-qualified schematic atlas, not automatically a history
of invention or an executable laboratory protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import shutil
import shlex
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .codex_worker import CodexWorker, assess_archived_attempt
from .illumina_diagrams import (DiagramExtraction, DiagramReview, digest,
    export_graph, review_outcome, review_targets, validate_extraction, validate_review)
from .illumina_prompts import build_prompt


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def card_title(card):
    """A recovered caption is explicit; an empty CMS title is never rewritten."""
    return card.get("source_title") or card.get("recovered_title") or card["record_id"]


def card_references(card):
    return [*card.get("references", []), *card.get("recovered_references", [])]


def requires_paper_matches(card):
    return bool(card.get("require_paper_matches")) or any(r.get("role", "recovered") in {"references", "recovered"}
               for r in card_references(card))


def source_file_pins(record, base):
    """Resolve current source custody explicitly; historical cache paths stay historical."""
    pins = {}
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for path_key, hash_key in (("path", "sha256"), ("text_path", "text_sha256"),
                    ("blocks_path", "blocks_sha256"), ("metadata_source", "metadata_source_sha256")):
                if isinstance(value.get(path_key), str) and value.get(hash_key):
                    path = str((base / value[path_key]).resolve())
                    expected = value[hash_key]
                    if path in pins and pins[path] != expected:
                        raise ValueError("Contradictory source-file pins: " + path)
                    pins[path] = expected
            for key, item in value.items():
                if key not in {"cache_provenance", "prior_fulltext_attempt"}:
                    visit(item)
    visit(record)
    return [{"path": path, "sha256": expected} for path, expected in sorted(pins.items())]


def verify_registry_files(registry):
    pins = {}
    for source in registry.values():
        rows = source.get("custody_files", [])
        if source.get("path") and source.get("sha256"):
            rows = [*rows, {"path": source["path"], "sha256": source["sha256"]}]
        for row in rows:
            path, expected = row["path"], row["sha256"]
            if path in pins and pins[path] != expected:
                raise ValueError("Contradictory registered file hashes: " + path)
            pins[path] = expected
    if any(sha(path) != expected for path, expected in pins.items()):
        raise ValueError("Registered source original/rendition changed")


def card_sources(card, acquired, base):
    """Build exact, separate diagram/catalogue/metadata/abstract/fulltext sources."""
    registry, images = {}, []
    text = "\n\n".join(filter(None, (card.get("source_title"),
        card.get("description_verbatim"), *[s.get("text", "")
        for s in card.get("source_sections") or []])))
    registry[card["source_id"]] = {"kind": "text", "text": text,
        "content_level": "catalogue", "url": card.get("source_page_url"),
        "title": card.get("source_title")}
    for asset in acquired["assets"]:
        if asset["asset_id"] not in card["diagram_asset_ids"] or asset["status"] != "downloaded":
            continue
        path = (base / asset["path"]).resolve()
        if sha(path) != asset["sha256"]:
            raise ValueError("Source image changed: " + str(path))
        registry[asset["source_id"]] = {"kind": "image", "path": str(path),
            "sha256": asset["sha256"], "url": asset["url"], "content_level": "diagram",
            "original": asset, "custody_files": source_file_pins(asset, base)}
        images.append((asset["source_id"], path))
    references = card_references(card)
    candidates = card.get("candidate_publications", [])
    if any(not isinstance(row, dict) or not isinstance(row.get("pmid"), str)
           or not row["pmid"].isdecimal() for row in candidates):
        raise ValueError("Candidate publications require explicit PMID records")
    listed_pmids = {r["pmid"] for r in references if r.get("pmid")}
    pmids = list(dict.fromkeys([*(r["pmid"] for r in references if r.get("pmid")),
                               *(row["pmid"] for row in candidates)]))
    for pmid in pmids:
        publication = acquired.get("publications", {}).get(pmid)
        if not publication or publication.get("identity_status") != "pubmed_record_retrieved":
            listed = [r.get("source_reference", r) for r in references if r.get("pmid") == pmid]
            registry["metadata:" + pmid] = {"kind": "text", "content_level": "metadata",
                "identity_status": "unverified_catalogue_reference",
                "text": json.dumps(listed, ensure_ascii=False, indent=2), "pmid": pmid,
                "url": "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/"}
            continue
        metadata = {k: v for k, v in publication.items() if k != "abstract"}
        registry["metadata:" + pmid] = {"kind": "text", "content_level": "metadata",
            "identity_status": publication["identity_status"],
            "text": json.dumps(metadata, ensure_ascii=False, indent=2),
            "pmid": pmid, "title": publication["title"],
            "url": "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/",
            "custody_files": source_file_pins(publication, base)}
        full = acquired.get("fulltexts", {}).get(pmid, {})
        if full.get("status") == "fulltext_acquired" and full.get("text_path"):
            path = (base / full["text_path"]).resolve()
            if sha(path) != full["text_sha256"]:
                raise ValueError("Fulltext rendition changed: " + str(path))
            registry["paper:" + pmid] = {"kind": "text", "content_level": "fulltext",
                "text": path.read_text(), "path": str(path), "sha256": sha(path),
                "pmid": pmid, "title": publication["title"],
                "url": (full.get("url") or full.get("public_url") or
                        ("https://pmc.ncbi.nlm.nih.gov/articles/" + full["pmcid"] + "/"
                         if full.get("pmcid") else "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/")),
                "original": full, "custody_files": source_file_pins(full, base)}
        elif publication.get("abstract"):
            abstract = "\n\n".join((p.get("label") or "Abstract") + ": " + p["text"]
                                    for p in publication["abstract"])
            registry["paper:" + pmid] = {"kind": "text", "content_level": "abstract",
                "text": publication["title"] + "\n\n" + abstract,
                "pmid": pmid, "title": publication["title"],
                "url": "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/",
                "fulltext_status": full.get("status", "not_acquired")}
    for reference in references:
        external = acquired.get("external_publications", {}).get(reference["reference_id"])
        if external and external.get("content_level") in {"metadata", "abstract", "fulltext"}:
            path = (base / external["text_path"]).resolve()
            if sha(path) != external["text_sha256"]:
                raise ValueError("Recovered publication rendition changed: " + str(path))
            registry["reference:" + reference["reference_id"]] = {"kind": "text",
                "content_level": external["content_level"],
                "identity_status": external.get("identity_status", "unverified_catalogue_reference"),
                "text": path.read_text(), "path": str(path), "sha256": external["text_sha256"],
                "title": external.get("title"), "pmid": external.get("pmid"),
                "url": external.get("public_url") or external.get("url") or
                    ("https://doi.org/" + external["doi"] if external.get("doi") else None),
                "original": external, "custody_files": source_file_pins(external, base)}
        elif not reference.get("pmid"):
            registry["reference:" + reference["reference_id"]] = {"kind": "text",
                "content_level": "metadata", "identity_status": "unverified_catalogue_reference",
                "text": json.dumps(reference.get("source_reference", reference), ensure_ascii=False, indent=2)}
    candidate_only_pmids = set(pmids) - listed_pmids
    for source in registry.values():
        if source.get("pmid") in candidate_only_pmids:
            source["association_status"] = "candidate_for_this_source_row; not_a_verified_paper_association"
    verify_registry_files(registry)
    return registry, images


def paper_coverage(candidate, card):
    errors = []
    matched = {p.source_id for p in candidate.paper_matches}
    for reference in card_references(card):
        if reference.get("role", "recovered") not in {"references", "recovered"}:
            continue
        pmid = reference.get("pmid")
        choices = {"reference:" + reference["reference_id"]}
        if pmid:
            choices.update({"paper:" + pmid, "metadata:" + pmid})
        assessed_with_literal_reference = any(
            p.assessment in {"identity_verified", "content_checked"} and any(
                e.source_id == "reference:" + reference["reference_id"] for e in p.evidence)
            for p in candidate.paper_matches)
        if not matched.intersection(choices) and not assessed_with_literal_reference:
            errors.append("Listed method reference has no paper-match assessment: " + reference["reference_id"])
    return errors


def checkpoint(root, state, cards):
    state["updated_at"] = now()
    state["counts"] = dict(Counter(r["status"] for r in state["cards"].values()))
    write(root / "state.json", state)
    path = root / "CHECKLIST.csv"
    temporary = path.with_suffix(".csv.tmp")
    fields = ["record_id", "title", "status", "source_status", "paper_support",
              "operations", "states", "flows", "revision", "attempt_directory", "reason"]
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for card in cards:
            row = state["cards"][card["record_id"]]
            writer.writerow({key: row.get(key, "") for key in fields})
    temporary.replace(path)


def stage_images(images, directory):
    """Attach deterministic display renditions, preserving original source pins."""
    attached, receipts = [], []
    for index, (source_id, original) in enumerate(images):
        destination = directory / f"diagram_{index:02d}.png"
        derivation = "byte-preserving copy"
        try:
            from PIL import Image
            with Image.open(original) as image:
                rgba = image.convert("RGBA")
                display = Image.new("RGBA", rgba.size, "white")
                display.alpha_composite(rgba)
                display = display.convert("RGB")
                # Display-only enlargement makes tiny schematic captions readable.
                if display.width < 1600:
                    display = display.resize((display.width * 2, display.height * 2))
                display.save(destination)
            derivation = "RGBA composited on white; 2x display enlargement if original width <1600"
        except ImportError:
            shutil.copyfile(original, destination)
        receipts.append({"source_id": source_id, "original": str(original),
            "original_sha256": sha(original), "attachment": str(destination),
            "attachment_sha256": sha(destination), "derivation": derivation})
        attached.append(destination)
    write(directory / "image_delivery.json", receipts)
    return attached, receipts


def verify_stage_inputs(attempt, delivery, pins):
    """Validate what was delivered, independently of a successful model exit."""
    if any(sha(Path(path)) != expected for path, expected in pins.items()):
        raise ValueError("Read-only stage input changed during model execution")
    requested = [{"path": row["attachment"], "sha256": row["attachment_sha256"]}
                 for row in delivery]
    if any(sha(Path(row["path"])) != row["sha256"] for row in requested):
        raise ValueError("Delivered image changed during model execution")
    command = read(attempt / "command.json")
    if command.get("image_inputs") != requested:
        raise ValueError("Archived native image inputs differ from requested attachments")
    argv = command.get("argv", [])
    attached = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--image"]
    if attached != [row["path"] for row in requested]:
        raise ValueError("Native command did not attach the requested images")


async def extract_card(card, acquired, source_base, directory, worker, max_revisions=1):
    registry, images = card_sources(card, acquired, source_base)
    if not images:
        return {"status": "source_recovery_needed", "reason": "No acquired source diagram; retain card for recovery."}
    directory.mkdir(parents=True, exist_ok=False)
    write(directory / "card.json", card)
    card_pin = sha(directory / "card.json")
    source_path = directory / "source_registry.json"
    write(source_path, registry)
    source_pin = sha(source_path)
    stages, candidate, review = [], None, None

    async def stage(role, revision):
        attempt = directory / "attempts" / f"{len(stages) + 1:03d}_{role}"
        attempt.mkdir(parents=True, exist_ok=False)
        paths, delivery = stage_images(images, attempt)
        if any(row["original_sha256"] != registry[row["source_id"]]["sha256"] for row in delivery):
            raise ValueError("Original diagram changed before image delivery")
        model = DiagramExtraction if role == "author" else DiagramReview
        schema = attempt / "schema.json"
        write(schema, model.model_json_schema())
        context = {"record_id": card["record_id"], "title": card_title(card),
            "scope": "The assigned Illumina schematic: its title/caption targets and depicted alternatives. Unshown adaptations or background references do not automatically add workflow targets.",
            "source_registry": str(source_path), "card": str(directory / "card.json"),
            "sources": [{"source_id": key, **{k: v for k, v in value.items()
                if k in {"kind", "content_level", "title", "pmid", "url", "association_status"}},
                "characters": len(value.get("text", ""))} for key, value in registry.items()],
            "attached_images": delivery, "output": str(attempt / "artifact.json"),
            "source_helper": shlex.join([sys.executable, "-m", "genomics_discovery.curation.illumina_batch", "source", "--registry", str(source_path)]),
            "self_check": shlex.join([sys.executable, "-m", "genomics_discovery.curation.illumina_batch", "check", str(attempt / 'artifact.json'), "--registry", str(source_path), "--record-id", card['record_id'], "--card", str(directory / "card.json")]),
            "source_requirement": "Assess every listed method reference and every declared method in paper_matches. Candidate publications are unverified association leads, not assumed matches. A resolved bibliographic association can cite the literal reference source alongside the identified paper's own identity/content evidence. Use metadata-only status honestly. If no paper evidence exists, record a blocking Unknown on the exact method scope rather than invent a citation. Related publications are background unless content shows otherwise."}
        if role == "reviewer":
            candidate_path = attempt / "candidate.json"
            write(candidate_path, candidate.model_dump(mode="json"))
            context.update(candidate=str(candidate_path), candidate_sha256=digest(candidate),
                           review_targets=sorted(review_targets(candidate)))
            context["self_check"] += f" --candidate {candidate_path}"
        prompt = build_prompt(role, context, schema,
            previous_candidate=candidate.model_dump(mode="json") if role == "author" and candidate else None,
            previous_review=review.model_dump(mode="json") if role == "author" and review else None)
        (attempt / "stage_prompt.txt").write_text(prompt)
        fixed_inputs = [attempt / "schema.json", attempt / "stage_prompt.txt", attempt / "image_delivery.json"]
        if role == "reviewer":
            fixed_inputs.append(candidate_path)
        input_pins = {str(path): sha(path) for path in fixed_inputs}
        entry = {"role": role, "revision": revision, "path": str(attempt),
                 "started_at": now(), "status": "running", "prompt_sha256": sha(attempt / "stage_prompt.txt"),
                 "input_pins": input_pins}
        stages.append(entry)
        write(directory / "stages.json", stages)
        started = time.monotonic()
        try:
            result = await worker.run(prompt, attempt, image_paths=paths)
            assessment = assess_archived_attempt(attempt)
            if not assessment.complete or result.exit_code != 0 or result.error:
                raise ValueError("Native stage failed: " + str(assessment.error or result.error))
            if result.thread_id != assessment.thread_id or result.usage != assessment.usage:
                raise ValueError("Worker result disagrees with archived native transport")
            if (attempt / "prompt.txt").read_text() != prompt:
                raise ValueError("Archived prompt differs from the requested stage prompt")
            verify_stage_inputs(attempt, delivery, input_pins)
            if any(s.get("thread_id") == assessment.thread_id for s in stages[:-1]):
                raise ValueError("Author/reviewer stages must have fresh independent native threads")
            if sha(source_path) != source_pin:
                raise ValueError("Frozen source registry changed during model execution")
            verify_registry_files(registry)
            if sha(directory / "card.json") != card_pin:
                raise ValueError("Assigned source card changed during model execution")
            if any(sha(Path(row["original"])) != row["original_sha256"] for row in delivery):
                raise ValueError("Original diagram changed during image-led extraction")
            document = model.model_validate_json((attempt / "artifact.json").read_bytes())
            errors = (validate_extraction(document, registry, expected_record_id=card["record_id"],
                        expected_title=context["title"], require_paper_matches=requires_paper_matches(card)) + paper_coverage(document, card)
                      if role == "author" else validate_review(document, candidate, registry))
            if errors:
                raise ValueError("; ".join(errors))
            entry.update(status="validated", thread_id=assessment.thread_id,
                usage=assessment.usage, artifact_sha256=sha(attempt / "artifact.json"))
            return document
        except BaseException as exc:
            entry.update(status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                         error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            entry.update(finished_at=now(), elapsed_seconds=time.monotonic() - started)
            write(directory / "stages.json", stages)

    for revision in range(max_revisions + 1):
        candidate = await stage("author", revision)
        review = await stage("reviewer", revision)
        outcome = review_outcome(review, candidate, registry)
        write(directory / "extraction.json", candidate.model_dump(mode="json"))
        write(directory / "review.json", review.model_dump(mode="json"))
        if outcome != "needs_revision" or revision == max_revisions:
            break
    write(directory / "graph.json", export_graph(candidate, review, registry))
    questions = ([{"target": c.target, "basis": "independent_review", "question": c.rationale}
                  for c in review.checks if c.action != "accept"] +
                 [{"target": "scope:" + s.method, "basis": "unresolved_method_applicability", "question": s.rationale}
                  for s in candidate.method_scopes if s.status == "unresolved"] +
                 [{"target": "unknown:" + u.id, "basis": "blocking_unknown", "question": u.question}
                  for u in candidate.unknowns if u.blocking] +
                 [{"target": "correction:" + c.id, "basis": "unresolved_source_discrepancy", "question": c.primary_statement}
                  for c in candidate.corrections if c.disposition == "keep_unresolved"])
    return {"status": outcome, "source_status": "diagram_and_registered_literature",
        "operations": len(candidate.operations), "states": len(candidate.states),
        "flows": len(candidate.flows), "revision": revision,
        "paper_support": dict(Counter(x.assessment for x in candidate.paper_matches)),
        "candidate_sha256": digest(candidate), "review_sha256": digest(review),
        "artifact_pins": {name: sha(directory / name) for name in
            ("card.json", "source_registry.json", "extraction.json", "review.json", "graph.json", "stages.json")},
        "research_questions": questions,
        "reason": "; ".join(q["target"] + ": " + q["question"] for q in questions)[:3000]}


def revalidate_completed(row):
    directory = Path(row["attempt_directory"])
    if not row.get("artifact_pins"):
        raise ValueError("Completed card lacks artifact pins; cannot silently skip it")
    if any(sha(directory / name) != expected for name, expected in row["artifact_pins"].items()):
        raise ValueError("Completed extraction artifact changed")
    registry = read(directory / "source_registry.json")
    verify_registry_files(registry)
    candidate = DiagramExtraction.model_validate_json((directory / "extraction.json").read_bytes())
    review = DiagramReview.model_validate_json((directory / "review.json").read_bytes())
    if validate_extraction(candidate, registry) or review_outcome(review, candidate, registry) != "supported":
        raise ValueError("Completed extraction no longer passes recorded review")
    for stage in read(directory / "stages.json"):
        if not assess_archived_attempt(Path(stage["path"])).complete:
            raise ValueError("Completed card lacks valid native transport")
        for image in read(Path(stage["path"]) / "image_delivery.json"):
            if sha(image["original"]) != image["original_sha256"]:
                raise ValueError("Completed card's original image changed")
        if stage.get("input_pins"):
            verify_stage_inputs(Path(stage["path"]),
                read(Path(stage["path"]) / "image_delivery.json"), stage["input_pins"])


async def run(sources, output, *, workers=8, records=(), max_revisions=1, resume=False):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Native extraction/review must run in a Sherlock Slurm allocation")
    sources, output = Path(sources).resolve(), Path(output).resolve()
    acquired, inventory = read(sources), read(sources.parent / "inventory.json")
    cards = [c for c in inventory["cards"] if not records or c["record_id"] in records]
    if records and set(records) - {c["record_id"] for c in cards}:
        raise ValueError("Requested card ID absent from inventory")
    policy = {"model": "gpt-6.1-sol", "effort": "xhigh", "max_revisions": max_revisions,
              "pinned_shell_environment": True,
              "workers": workers, "source_sha256": sha(sources),
              "inventory_sha256": sha(sources.parent / "inventory.json"),
              "record_ids": [c["record_id"] for c in cards],
              "implementation": {p.name: sha(p) for p in (Path(__file__),
                  Path(__file__).with_name("illumina_diagrams.py"),
                  Path(__file__).with_name("illumina_prompts.py"))}}
    if resume:
        state = read(output / "state.json")
        if state["policy"] != policy:
            raise ValueError("Resume requires unchanged sources and policy; use a new run for changed evidence")
        for row in state["cards"].values():
            if row["status"] == "supported":
                revalidate_completed(row)
    else:
        output.mkdir(parents=True, exist_ok=False)
        state = {"format_version": "illumina_batch/1.0", "started_at": now(),
            "slurm_job_id": os.environ["SLURM_JOB_ID"], "policy": policy,
            "cards": {c["record_id"]: {"record_id": c["record_id"],
                "title": card_title(c), "status": "pending"} for c in cards}}
        checkpoint(output, state, cards)
    semaphore = asyncio.Semaphore(workers)
    worker = CodexWorker(model=policy["model"], effort=policy["effort"],
                         timeout_seconds=None, web_search=False, network_access=False,
                         pin_shell_environment=True)

    async def one(card):
        ident = card["record_id"]
        row = state["cards"][ident]
        if resume and row["status"] == "supported":
            return
        async with semaphore:
            existing = list((output / "cards" / ident).glob("attempt_*"))
            directory = output / "cards" / ident / f"attempt_{len(existing) + 1:03d}"
            row.update(status="running", attempt_directory=str(directory), started_at=now())
            checkpoint(output, state, cards)
            try:
                row.update(await extract_card(card, acquired, sources.parent, directory, worker,
                                              max_revisions=max_revisions))
            except Exception as exc:
                row.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
            row["finished_at"] = now()
            checkpoint(output, state, cards)
    await asyncio.gather(*(one(card) for card in cards))
    state["finished_at"] = now()
    checkpoint(output, state, cards)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    launch = sub.add_parser("run")
    launch.add_argument("sources", type=Path)
    launch.add_argument("--output", type=Path, required=True)
    launch.add_argument("--workers", type=int, default=8)
    launch.add_argument("--record", action="append", default=[])
    launch.add_argument("--max-revisions", type=int, default=1)
    launch.add_argument("--resume", action="store_true")
    source = sub.add_parser("source")
    source.add_argument("--registry", type=Path, required=True)
    source.add_argument("--id")
    source.add_argument("--query")
    source.add_argument("--start", type=int, default=0)
    source.add_argument("--limit", type=int, default=8000)
    check = sub.add_parser("check")
    check.add_argument("artifact", type=Path)
    check.add_argument("--registry", type=Path, required=True)
    check.add_argument("--record-id", required=True)
    check.add_argument("--candidate", type=Path)
    check.add_argument("--card", type=Path, help="Read-only assigned card for the same scope/reference checks as admission")
    args = parser.parse_args(argv)
    if args.command == "run":
        if not 1 <= args.workers <= 32 or not 0 <= args.max_revisions <= 2:
            parser.error("Use 1-32 workers and 0-2 scientific corrections")
        state = asyncio.run(run(args.sources, args.output, workers=args.workers,
            records=args.record, max_revisions=args.max_revisions, resume=args.resume))
        print(json.dumps(state["counts"]))
        return 0 if set(state["counts"]) <= {"supported"} else 1
    registry = read(args.registry)
    if args.command == "source":
        if not args.id:
            value = [{"source_id": key, **{k: v for k, v in row.items()
                if k in {"kind", "content_level", "title", "pmid", "url", "association_status"}},
                "characters": len(row.get("text", ""))} for key, row in registry.items()]
        else:
            row = registry[args.id]
            text = row.get("text", "")
            start = args.start
            if args.query:
                match = text.casefold().find(args.query.casefold())
                start = max(0, match - 700) if match >= 0 else 0
            value = {"source_id": args.id, "start": start,
                "text": text[start:start + min(max(args.limit, 1), 16000)],
                "total_characters": len(text), "path": row.get("path")}
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return 0
    try:
        if args.candidate:
            candidate = DiagramExtraction.model_validate_json(args.candidate.read_bytes())
            document = DiagramReview.model_validate_json(args.artifact.read_bytes())
            errors = validate_review(document, candidate, registry)
        else:
            document = DiagramExtraction.model_validate_json(args.artifact.read_bytes())
            card = read(args.card) if args.card else None
            errors = validate_extraction(document, registry, expected_record_id=args.record_id,
                expected_title=card_title(card) if card else None,
                require_paper_matches=requires_paper_matches(card) if card else False)
            if card:
                errors += paper_coverage(document, card)
    except Exception as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors}, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())

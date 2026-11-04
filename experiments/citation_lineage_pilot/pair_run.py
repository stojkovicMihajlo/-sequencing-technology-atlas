"""Compare two existing Illumina graphs on Sherlock; never re-extract them.

prepare pinned inputs -> author -> fresh review -> at most one revision -> export.
Uses the existing image transport, graph group query and source/review gates.
"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import time
import xml.etree.ElementTree as ET

from genomics_discovery.execution.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.illumina.extraction import read, sha, source_excerpt, stage_images, verify_stage_inputs, write
from genomics_discovery.illumina import relationships as relationship_policy
from genomics_discovery.illumina.graph import _load_card, operation_group
from models import Review, validate_review
from pair_models import PairAnalysis, export_pair, validate_pair
from pair_prompts import COMMON, AUTHOR, REVIEWER
from run import bibliography


def prepare_primary(source, source_id, side, inputs):
    """Copy the registered original; do not mistake unparsed PDF references for none."""
    original = source["original"]
    article = Path(original["path"])
    raw = article.read_bytes()
    if hashlib.sha256(raw).hexdigest() != original.get("sha256"):
        raise ValueError("Primary paper original differs from its registered hash")
    if raw.startswith(b"%PDF-"):
        source_format, status, references = "PDF", "not_parsed", None
        limitation = (
            "The registered full-text PDF is available, but this paired runner does not "
            "extract its bibliography or citation contexts. This does not mean the paper "
            "has no references. Inspect the registered text for claim-specific support; "
            "if a required citation context is absent, keep the historical claim unresolved."
        )
    else:
        root = ET.fromstring(raw)
        if root.tag.split("}")[-1] != "article":
            raise ValueError("Primary paper must be a registered full-text PDF or JATS article")
        source_format, status, references = "JATS", "parsed", bibliography(raw)
        limitation = None
    destination = inputs / (side + "_primary" + (article.suffix or (".pdf" if source_format == "PDF" else ".xml")))
    destination.write_bytes(raw)
    return {"source_id": source_id, "source_format": source_format, "status": status,
            "original_path": str(article), "original_sha256": original["sha256"],
            "local_path": str(destination), "references": references, "limitation": limitation}


def prepare(plan_path, output):
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "inputs"
    inputs.mkdir()
    plan = read(plan_path)
    graph = {"cards": {}, "nodes": [], "edges": []}
    sides, sources, images, references = {}, {}, [], {}
    for side in ("left", "right"):
        spec = plan[side]
        row = read(Path(spec["batch"]) / "state.json")["cards"][spec["record_id"]]
        if row["status"] != "supported":
            raise ValueError("Comparison requires two existing supported extractions")
        parent = Path(row["attempt_directory"])
        card = read(parent / "card.json")
        original_graph, registry = _load_card(row, card)
        ident = spec["record_id"]
        if ident in graph["cards"]:
            raise ValueError("Choose two distinct method records")
        graph["cards"][ident] = {"record_id": ident, "title": original_graph["workflow"]["title"],
            "status": "supported", "admission": "supported", "publication_ids": [],
            "scope": original_graph["workflow"]["scope"], "source_scope": {},
            "unknowns": original_graph["workflow"]["unknowns"],
            "candidate_sha256": original_graph["candidate_sha256"],
            "review_sha256": original_graph["review_sha256"],
            "source_registry": original_graph["source_registry"],
            "artifact_pins": row["artifact_pins"], "attempt_directory": str(parent)}
        graph["nodes"].extend({**n, "record_id": ident, "admission": "supported"} for n in original_graph["nodes"])
        graph["edges"].extend({**e, "record_id": ident, "admission": "supported"} for e in original_graph["edges"])
        # Copies retain exact bytes; originals and prior reviews remain untouched.
        for name in ("extraction.json", "graph.json", "review.json", "card.json"):
            (inputs / (side + "_" + name)).write_bytes((parent / name).read_bytes())
        paper_ids = []
        for sid, source in registry.items():
            scoped = side + ":" + sid
            if source.get("kind") == "image":
                src = Path(source["path"])
                dest = inputs / (side + "_diagram_" + str(len(images)) + src.suffix)
                dest.write_bytes(src.read_bytes())
                images.append({"source_id": scoped, "path": str(dest), "sha256": sha(dest)})
            elif source.get("text"):
                sources[scoped] = {"text": source["text"], "title": source.get("title"),
                    "content_level": source.get("content_level"), "pmid": source.get("pmid"),
                    "url": source.get("url"), "original": source.get("original"),
                    "source_registry": str(parent / "source_registry.json"),
                    "source_registry_sha256": row["artifact_pins"]["source_registry.json"]}
                if sid == spec["primary_paper"]:
                    if source.get("content_level") != "fulltext":
                        raise ValueError("This paired experiment requires both original full texts")
                    paper_ids.append(scoped)
                    references[side] = prepare_primary(source, scoped, side, inputs)
        if len(paper_ids) != 1:
            raise ValueError("Primary paper must be present exactly once")
        sides[side] = {"record_id": ident, "title": graph["cards"][ident]["title"],
            "paper_source_ids": paper_ids, "parent_attempt": str(parent),
            "extraction_sha256": sha(parent / "extraction.json"),
            "graph_sha256": sha(parent / "graph.json"), "artifact_pins": row["artifact_pins"]}
    write(inputs / "graph.json", graph)
    write(inputs / "sources.json", sources)
    write(inputs / "sides.json", sides)
    write(inputs / "images.json", images)
    write(inputs / "bibliography.json", references)
    write(inputs / "plan.json", plan)
    return inputs


def load_inputs(path):
    return {k: read(path / (k + ".json")) for k in ("graph", "sources", "sides")}


def validate(role, artifact, inputs, analysis_path=None):
    context = load_inputs(inputs)
    if role == "author":
        result = PairAnalysis.model_validate_json(artifact.read_bytes())
        validate_pair(result, context)
    else:
        result = Review.model_validate_json(artifact.read_bytes())
        analysis = PairAnalysis.model_validate_json(analysis_path.read_bytes())
        validate_pair(analysis, context)
        validate_review(result, analysis, sha(analysis_path), context["sources"])
    return result


async def execute(args):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Native pair runs require Sherlock Slurm")
    start = time.monotonic()
    root = args.output.resolve()
    inputs = prepare(args.plan, root)
    state = {"status": "prepared", "job_id": os.environ["SLURM_JOB_ID"],
             "model": "gpt-6.1-sol", "effort": "xhigh", "stages": []}
    write(root / "state.json", state)
    worker = CodexWorker(model=state["model"], effort=state["effort"], timeout_seconds=None,
                         web_search=False, network_access=False, pin_shell_environment=True)
    implementation = {str(p.resolve()): sha(p) for p in Path(__file__).parent.glob("*.py")}
    policy_path = Path(relationship_policy.__file__).resolve()
    implementation[str(policy_path)] = sha(policy_path)

    async def stage(role, analysis_path=None, feedback=None):
        attempt = root / "attempts" / f"{len(state['stages'])+1:03d}_{role}"
        attempt.mkdir(parents=True)
        schema = PairAnalysis if role == "author" else Review
        write(attempt / "schema.json", schema.model_json_schema())
        attached, delivery = stage_images([(x["source_id"], Path(x["path"])) for x in read(inputs / "images.json")], attempt)
        command = [sys.executable, str(Path(__file__).resolve())]
        check = [*command, "check", "--inputs", str(inputs), "--role", role,
                 "--artifact", str(attempt / "artifact.json")]
        if analysis_path:
            check += ["--analysis", str(analysis_path)]
        data = load_inputs(inputs)
        context = {"inputs": str(inputs), "sides": data["sides"],
            "operations": [{"id": n["id"], "label": n["original"]["label"], "method": n["record_id"]}
                           for n in data["graph"]["nodes"] if n["node_type"] == "operation"],
            "sources": {k: {"title": v.get("title"), "level": v.get("content_level"), "characters": len(v["text"])}
                        for k, v in data["sources"].items()},
            "bibliography": str(inputs / "bibliography.json"),
            "primary_bibliography_status": {side: {
                "source_id": entry["source_id"], "source_format": entry["source_format"],
                "status": entry["status"], "limitation": entry["limitation"],
                "reference_count": len(entry["references"]) if entry["references"] is not None else None}
                for side, entry in read(inputs / "bibliography.json").items()},
            "source_tool": shlex.join([*command, "read", "--inputs", str(inputs)]) + " --id SOURCE_ID [--query TEXT] [--start N] [--limit N]",
            "group_tool": shlex.join([*command, "group", "--inputs", str(inputs)]) + " --side left|right --operations GLOBAL_OPERATION_ID ...",
            "schema": str(attempt / "schema.json"), "output": str(attempt / "artifact.json"),
            "self_check": shlex.join(check)}
        if analysis_path:
            context.update(analysis=str(analysis_path), analysis_sha256=sha(analysis_path))
        if feedback:
            context["previous_review"] = str(feedback)
        prompt = COMMON + "\n" + (AUTHOR if role == "author" else REVIEWER) + "\nCONTEXT\n" + json.dumps(context, indent=2)
        (attempt / "stage_prompt.txt").write_text(prompt)
        pins = {str(p): sha(p) for p in inputs.iterdir() if p.is_file()}
        pins.update(implementation)
        for p in (attempt / "schema.json", attempt / "stage_prompt.txt", attempt / "image_delivery.json", analysis_path, feedback):
            if p:
                pins[str(p)] = sha(p)
        write(attempt / "input_pins.json", pins)
        row = {"role": role, "path": str(attempt), "status": "running"}
        state["stages"].append(row)
        state["status"] = role
        write(root / "state.json", state)
        began = time.monotonic()
        result = await worker.run(prompt, attempt, image_paths=attached)
        assessment = assess_archived_attempt(attempt)
        if (not assessment.complete or result.error or result.exit_code != 0 or
                assessment.thread_id != result.thread_id or assessment.usage != result.usage or
                any(x.get("thread_id") == assessment.thread_id for x in state["stages"][:-1])):
            raise ValueError("Native stage failed completion or fresh-session checks")
        if (attempt / "prompt.txt").read_text() != prompt:
            raise ValueError("Executed prompt changed")
        verify_stage_inputs(attempt, delivery, pins)
        document = validate(role, attempt / "artifact.json", inputs, analysis_path)
        row.update(status="validated", thread_id=assessment.thread_id, usage=assessment.usage,
                   elapsed_seconds=time.monotonic()-began, artifact_sha256=sha(attempt / "artifact.json"))
        write(root / "state.json", state)
        return document, attempt / "artifact.json"

    try:
        feedback = analysis_path = None
        for revision in range(2):
            analysis, analysis_path = await stage("author", analysis_path, feedback)
            review, feedback = await stage("review", analysis_path)
            if not review.omitted_candidates and not any(x.decision == "revise" for x in review.findings):
                break
        graph = export_pair(analysis, review, load_inputs(inputs), sha(analysis_path))
        write(root / "analysis.json", analysis.model_dump())
        write(root / "review.json", review.model_dump())
        write(root / "graph.json", graph)
        # Recheck both immutable extraction copies against their source versions.
        for side, binding in read(inputs / "sides.json").items():
            if sha(inputs / (side + "_extraction.json")) != binding["extraction_sha256"]:
                raise ValueError("Source extraction copy changed")
            parent = Path(binding["parent_attempt"])
            _load_card({"status": "supported", "attempt_directory": str(parent),
                "artifact_pins": binding["artifact_pins"],
                "candidate_sha256": graph["source_graph"]["cards"][binding["record_id"]]["candidate_sha256"],
                "review_sha256": graph["source_graph"]["cards"][binding["record_id"]]["review_sha256"]}, read(parent / "card.json"))
        state.update(status="completed", correspondences=len(graph["correspondences"]),
                     historical_links=len(graph["historical_links"]), withheld=len(graph["withheld"]),
                     extraction_bytes_unchanged=True, final_analysis_sha256=sha(analysis_path))
    except BaseException as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        for row in state["stages"]:
            if row["status"] == "running":
                row.update(status="failed", error=state["error"])
        raise
    finally:
        state["elapsed_seconds"] = time.monotonic()-start
        write(root / "state.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("check")
    check.add_argument("--inputs", type=Path, required=True)
    check.add_argument("--artifact", type=Path, required=True)
    check.add_argument("--analysis", type=Path)
    check.add_argument("--role", choices=["author", "review"], required=True)
    group = sub.add_parser("group")
    group.add_argument("--inputs", type=Path, required=True)
    group.add_argument("--side", choices=["left", "right"], required=True)
    group.add_argument("--operations", nargs="+", required=True)
    inspect = sub.add_parser("read")
    inspect.add_argument("--inputs", type=Path, required=True)
    inspect.add_argument("--id", required=True)
    inspect.add_argument("--query")
    inspect.add_argument("--start", type=int, default=0)
    inspect.add_argument("--limit", type=int, default=10000)
    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(execute(args))
    elif args.command == "check":
        validate(args.role, args.artifact, args.inputs, args.analysis)
        print("structural_and_quote_checks_passed; scientific_review_still_required")
    elif args.command == "group":
        data = load_inputs(args.inputs)
        result = operation_group(data["graph"], data["sides"][args.side]["record_id"], args.operations)
        for key in ("provenance", "full_source_registry_artifact", "artifact_pins"):
            result.pop(key, None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        registry = read(args.inputs / "sources.json")
        result = source_excerpt(registry, args.id, query=args.query, start=args.start, limit=args.limit)
        result.update(query_found=result["found"], level=registry[args.id].get("content_level"))
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

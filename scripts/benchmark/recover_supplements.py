"""Explicit, source-bound supplement recovery into a new benchmark source phase.

Run real downloads/conversions in Slurm. This is manual source curation, not a
finder, protocol extractor, or assertion of scientific source completeness.
"""

import argparse
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import quote

from genomics_discovery.curation.cached_documents import supplements
from genomics_discovery.curation.document_manifest import MAX_BYTES
from genomics_discovery.curation.extraction_batch.storage import atomic_bytes, check_pins, sha, write
from genomics_discovery.curation.protocol_benchmark import source_identity
from genomics_discovery.curation.source_acquisition.associations import biostudies_record
from genomics_discovery.curation.source_acquisition.downloads import Download, download, public_url
from genomics_discovery.curation.source_acquisition.legacy_word import OLE, word_rendition
from genomics_discovery.curation.source_acquisition.models import Document, Identity
from genomics_discovery.curation.source_acquisition.runtime import _doi, _links, _primary
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet

BOUNDARY = ("Explicit publication/attachment custody and rendition registration only; no model calls, "
    "protocol extraction, scientific applicability, source completeness, or original-byte rewriting.")


def read(path):
    return json.loads(Path(path).read_bytes())


def bind(pins, additions):
    for name, value in additions.items():
        if not Path(name).is_absolute() or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Every custody pin needs an absolute path and SHA256")
        if name in pins and pins[name] != value:
            raise ValueError(f"Conflicting custody pin: {name}")
        pins[name] = value


def retained(record, pins):
    path = Path(record["path"])
    if pins.get(str(path)) != record["original_sha256"]:
        raise ValueError("Retained document lacks its exact original source_receipts pin")
    check_pins({str(path): record["original_sha256"]})
    return path


def fetch(url, path, downloader):
    fetched = downloader(public_url(url))
    if not isinstance(fetched, Download) or not isinstance(fetched.content, bytes) or not fetched.content or len(fetched.content) > MAX_BYTES:
        raise ValueError("Download did not return bounded actual bytes")
    public_url(fetched.final_url)
    atomic_bytes(path, fetched.content)
    write(path.with_name(path.name + ".download.json"), {"requested_url": url, "final_url": fetched.final_url,
        "content_type": fetched.content_type, "path": str(path), "sha256": sha(path), "bytes": len(fetched.content)})
    return fetched


def recipe_index(document, cases):
    if document.get("format_version") != "benchmark_supplement_recovery/1.0":
        raise ValueError("Unsupported explicit recovery recipe format")
    result = {}
    for recipe in document["recipes"]:
        mid = recipe["method_id"]
        if mid not in cases or mid in result or (not recipe["supplements"] and not recipe.get("deferred_reason")):
            raise ValueError("Recovery methods need explicit supplements or an explicit deferral")
        if recipe.get("deferred_reason") and recipe["supplements"]:
            raise ValueError("Deferred recipes cannot also request downloads")
        ids = [recipe["primary_document_id"], *(row["document_id"] for row in recipe["supplements"])]
        if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", key) for key in ids):
            raise ValueError("Document IDs must be unique safe source-acquisition IDs")
        for row in recipe["supplements"]:
            if (row["format"] not in {"pdf", "doc", "docx", "text"}
                    or not re.fullmatch(r"S-EPMC[0-9]+", row["accession"])
                    or not row["file_path"] or any(part in {"", ".", ".."} for part in row["file_path"].split("/"))
                    or "\\" in row["file_path"]):
                raise ValueError("Recipe requires an exact safe BioStudies accession/path and supported format")
        result[mid] = recipe
    return result


def attachment(row, primary, links, directory, documents, prior_pins, pins, downloader, word_converter, supplement_converter):
    if len(primary.get("identifiers", {}).get("pmid", [])) != 1:
        raise ValueError("Supplement recovery requires one PMID in the actual retained primary")
    directory.mkdir(parents=True, exist_ok=False)
    root = f"https://www.ebi.ac.uk/biostudies"
    record_url = f"{root}/api/v1/studies/{row['accession']}"
    record = fetch(record_url, directory / "biostudies.json", downloader)
    if record.final_url != record_url:
        raise ValueError("BioStudies metadata must come from the exact official API URL")
    canonical = f"{root}/files/{row['accession']}/{quote(row['file_path'], safe='/')}"
    suffix = {"text": "txt"}.get(row["format"], row["format"])
    original = directory / "supplements" / f"{row['document_id']}.{suffix}"
    fetched = fetch(canonical, original, downloader)
    verified_url, association = biostudies_record(record.content, record_url, row["file_path"],
        primary, links, len(fetched.content))
    ids = primary["identifiers"]
    for key in ("pmid", "doi"):
        expected = {_doi(v) if key == "doi" else v for v in ids.get(key, [])}
        if expected and set(association["publication_identity"][key]) != expected:
            raise ValueError(f"BioStudies record must explicitly match the primary {key}")
    if association["reported_size"] != len(fetched.content) or verified_url != canonical:
        raise ValueError("BioStudies requires an exact declared size and canonical attachment URL")
    if row.get("retained_document_id"):
        old = documents[row["retained_document_id"]]
        if old["role"] != "supplement" or retained(old, prior_pins).read_bytes() != fetched.content:
            raise ValueError("Retained attachment bytes differ from the canonical BioStudies delivery")
        association["retained_original"] = {"path": old["path"], "sha256": old["original_sha256"]}
    raw, kind = fetched.content, row["format"]
    valid = {"pdf": raw.startswith(b"%PDF-"), "doc": raw.startswith(OLE),
        "docx": raw.startswith(b"PK\x03\x04"), "text": not re.search(br"<(?:!doctype|html|body|script)\b", raw, re.I)}
    if not valid[kind]:
        raise ValueError(f"Canonical attachment bytes do not have the declared {kind} signature")
    if kind == "text":
        raw.decode("utf-8")
    write(directory / "association.json", association)
    sid = f"recovered_{row['document_id']}_{sha(original)[:8]}"
    if kind in {"doc", "docx"}:
        receipt = word_converter(original, directory / "rendition.txt", kind)
        source = {"path": receipt["rendition"], "title": row["file_path"].rsplit("/", 1)[-1]}
    else:
        write(original.parent / "acquisition.json", {"pmid": ids["pmid"][0], "files": [{"status": "cached",
            "path": str(original), "sha256": sha(original), "url": fetched.final_url}]})
        converter_pins = {}
        if kind == "pdf" and supplement_converter is supplements:
            executable = shutil.which("pdftotext")
            if not executable:
                raise ValueError("PDF original retained; pdftotext is unavailable")
            executable = Path(executable).resolve()
            converter_pins[str(executable)] = sha(executable)
            bind(pins, converter_pins)
        sources, receipts, leads = supplement_converter(directory, ids["pmid"][0], directory / "renditions")
        check_pins(converter_pins)
        if leads or len(sources) != 1 or len(receipts) != 1:
            raise ValueError(f"Canonical attachment could not be rendered: {leads}")
        source, receipt = sources[0], receipts[0]
    source.update(source_id=sid, url=fetched.final_url, role="supplement")
    receipt.update(source_id=sid, association=association)
    for key in ("original", "rendition", "executable"):
        if receipt.get(key):
            bind(pins, {receipt[key]: receipt[key + "_sha256"]})
    bind(pins, receipt.get("conversion_assets", {}))
    for asset in receipt.get("intermediates", []):
        bind(pins, {asset["path"]: asset["sha256"]})
    command = receipt.get("command", [])
    executable = shutil.which(command[0]) if command and command[0] != "utf8_text_copy" else None
    if executable:
        bind(pins, {str(Path(executable).resolve()): sha(Path(executable).resolve())})
    write(directory / "receipt.json", receipt)
    return source, receipt


def recover(cases_path, prior_sources, recipes_path, output, *, downloader=download,
            word_converter=word_rendition, supplement_converter=supplements):
    if downloader is download and not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Real source recovery must run in a Slurm allocation")
    cases_path, prior_sources, recipes_path, output = (Path(p).resolve() for p in (cases_path, prior_sources, recipes_path, output))
    if output.exists() or output.is_relative_to(prior_sources):
        raise ValueError("Use a new output directory outside the prior source phase")
    recipes = read(recipes_path)
    receipts_path, report_path = prior_sources / "acquisition/source_receipts.json", prior_sources / "source_report.json"
    pins = {str(path): recipes[key] for path, key in ((cases_path, "cases_sha256"),
        (receipts_path, "source_receipts_sha256"), (report_path, "source_report_sha256"))}
    check_pins(pins)
    cases_list = read(cases_path)["cases"]
    cases = {case["method_id"]: case for case in cases_list}
    if len(cases) != len(cases_list) or len({case["case_id"] for case in cases_list}) != len(cases):
        raise ValueError("Frozen case and method IDs must be unique")
    selected = recipe_index(recipes, cases)
    custody = read(receipts_path)
    if custody["format_version"] != "public_source_acquisition/1.0":
        raise ValueError("Unsupported prior source custody format")
    bind(pins, custody["files"])
    binding_path = prior_sources / "input_bindings.json"
    bind(pins, read(binding_path))
    code = [Path(function.__code__.co_filename).resolve() for function in
        (_primary, biostudies_record, build_packet, downloader, word_converter, supplement_converter)]
    bind(pins, {str(path): sha(path) for path in (recipes_path, binding_path, Path(__file__).resolve(),
        Path(source_identity.__file__).resolve(), *code)})
    check_pins(pins)
    reports = deepcopy(read(report_path)["cases"])
    annotation = []
    output.mkdir(parents=True, exist_ok=False)
    for mid, case in cases.items():
        report = reports.setdefault(mid, {"case_id": case["case_id"], "status": "source_acquisition_needed"})
        report["supplement_recovery"] = {"status": "not_requested", "prior_status": report["status"]}
        if mid not in selected:
            continue
        recipe, directory = selected[mid], output / "methods" / mid
        if recipe.get("deferred_reason"):
            report.update(status="source_acquisition_needed", reason=recipe["deferred_reason"])
            report["supplement_recovery"].update(status="deferred", recipe=recipe)
            continue
        directory.mkdir(parents=True)
        try:
            metadata = prior_sources / "acquisition/methods" / mid / "acquisition.json"
            if str(metadata) not in custody["files"]:
                raise ValueError("Method acquisition metadata lacks a prior receipt pin")
            metadata_value = read(metadata)
            documents = {d["id"]: d for d in metadata_value["documents"]}
            if metadata_value["method_id"] != mid or len(documents) != len(metadata_value["documents"]):
                raise ValueError("Acquisition metadata must name this method with unique document IDs")
            old = deepcopy(documents[recipe["primary_document_id"]])
            if old["role"] != "primary_paper" or old["format"] not in {"jats", "html"}:
                raise ValueError("Selected primary must be retained original JATS/citation HTML")
            original = retained(old, custody["files"])
            expected = Identity(**{k: case["primary_identity"][k] for k in ("pmid", "doi", "title") if case["primary_identity"].get(k)})
            document = Document(id=old["id"], url=old["final_url"], role="primary_paper", format=old["format"],
                identity=expected, rationale="Explicit frozen publication scope; reparse the retained primary")
            primary, primary_receipt = _primary(document, old, original, expected, directory / "primary.txt")
            ledger = [primary_receipt]
            write(directory / "originals_ledger.json", ledger)
            base_path = prior_sources / "acquisition/methods" / mid / "packet.json"
            descriptors, options = [], {}
            if base_path.exists():
                if str(base_path) not in custody["files"]:
                    raise ValueError("Base packet lacks its prior source_receipts pin")
                value = read(base_path)
                if any(custody["files"].get(s["original_path"]) != s["original_sha256"] for s in value["sources"]):
                    raise ValueError("Base packet source lacks its prior source_receipts pin")
                base = load_packet(base_path)
                if any(base[k] != case[k] for k in ("method_id", "title", "scope", "variant")):
                    raise ValueError("Base packet differs from frozen case scope")
                descriptors = [{**{k: s[k] for k in ("source_id", "title", "url", "role")}, "path": s["original_path"]}
                    for s in base["sources"] if s["source_id"] != primary["source_id"]]
                options = {k: base[k] for k in ("format_version", "rendition_version") if k in base}
            descriptors.append(primary)
            scope = {k: case[k] for k in ("method_id", "title", "scope", "variant")}
            packet_path = directory / "packet.json"
            initial = build_packet(**scope, sources=descriptors, **options)
            proof = source_identity.verify_publication(initial, case, packet_path=packet_path)
            if any(name not in custody["files"] and not Path(name).is_relative_to(directory) for name in proof["pins"]):
                raise ValueError("Primary identity proof uses unbound prior bytes")
            bind(pins, {name: value for name, value in proof["pins"].items() if not Path(name).is_relative_to(directory)})
            links = _links((Path(primary["path"]) if old["format"] == "jats" else original).read_bytes(), old["final_url"])
            for row in recipe["supplements"]:
                source, receipt = attachment(row, old["host_identity"], links, directory / "documents" / row["document_id"],
                    documents, custody["files"], pins, downloader, word_converter, supplement_converter)
                descriptors.append(source)
                ledger.append(receipt)
                write(directory / "originals_ledger.json", ledger)
            packet = build_packet(**scope, sources=descriptors, **options)
            write(packet_path, packet)
            proof = source_identity.verify_publication(packet, case, packet_path=packet_path)
            bind(pins, proof["pins"])
            report.update(status="registered_for_source_first_annotation", packet=str(packet_path), packet_sha256=sha(packet_path),
                source_packet_sha256=digest(packet), publication_verification=proof)
            report.pop("reason", None)
            report["supplement_recovery"].update(status="registered", recipe=recipe, added_supplements=len(recipe["supplements"]))
            annotation.append({"case_id": case["case_id"], "method_id": mid, "family": case["family"], "split": case["split"],
                "packet": str(packet_path), "packet_sha256": sha(packet_path), "source_report": str(output / "source_report.json")})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report.update(status="source_acquisition_needed", reason=f"{type(exc).__name__}: {exc}")
            report["supplement_recovery"].update(status="withheld", recipe=recipe)
    check_pins(pins)
    write(output / "input_bindings.json", pins)
    write(output / "source_report.json", {"format_version": "benchmark_source_report/1.0", "cases": reports,
        "counts": dict(Counter(r["status"] for r in reports.values())), "boundary": BOUNDARY,
        "annotation_ready_is_not_scientific_completeness": True, "scientific_resolution": False})
    manifest = {"format_version": "protocol_benchmark_batch/1.0", "phase": "explicit_supplement_recovery_only",
        "case_design_sha256": sha(cases_path), "cases": annotation, "boundary": BOUNDARY,
        "requested_method_ids": list(selected), "recovery_recipes": {"path": str(recipes_path), "sha256": sha(recipes_path)},
        "source_report": {"path": str(output / "source_report.json"), "sha256": sha(output / "source_report.json")}}
    write(output / "annotation_manifest.json", manifest)
    files = {str(path): sha(path) for path in output.rglob("*") if path.is_file()}
    bind(files, pins)
    write(output / "source_receipts.json", {"format_version": "benchmark_supplement_recovery/1.0", "files": files,
        "scientific_resolution": False, "boundary": BOUNDARY})
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cases", "prior-sources", "recipes", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Real source recovery must run in a Slurm allocation")
    print(json.dumps(recover(args.cases, args.prior_sources, args.recipes, args.output), indent=2))


if __name__ == "__main__":
    main()

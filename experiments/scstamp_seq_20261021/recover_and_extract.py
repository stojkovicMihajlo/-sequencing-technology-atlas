"""Bounded experiment: recover actual sources, inspect the supplement, then extract.

Uses existing acquisition bytes and generic harness tools; contains no expected
assay graph, operations, scientific answers or technology-matching instructions.
"""

import asyncio
import argparse
import json
from pathlib import Path
import subprocess
import sys

from genomics_discovery.curation.cached_documents import _publish_rendition
from genomics_discovery.curation.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.curation.extraction_batch.storage import check_pins, sha, write
from genomics_discovery.curation.source_acquisition.models import Document, Identity
from genomics_discovery.curation.source_acquisition.pdf_registration import verify_pdf_text
from genomics_discovery.curation.source_acquisition.runtime import _primary
from genomics_discovery.curation.source_acquisition.supplement_archive import Inspection, prepare, register
from genomics_discovery.curation.source_originals import bind_originals
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet


def prepare_dependencies(acquisition, recovery):
    ledger = []
    # Cited/manufacturer documents are not silently treated as the target method.
    # They remain separately attributed evidence for source-first applicability review.
    records = json.loads((acquisition / "acquisition.json").read_bytes())["documents"]
    descriptors, decisions = [], []
    for row in records:
        if row["role"] not in {"cited_protocol", "manufacturer_protocol"}:
            continue
        if not row.get("path") or row["status"] not in {"retrieved_unregistered", "registered_for_fresh_source_review"}:
            write(recovery / f"dependency-failure-{row['id']}.json",
                  {"status": "needs_source_recovery", "document": row,
                   "disposition": "Stop before paid inspection; do not silently omit the requested dependency"})
            raise ValueError(f"Dependency needs retrieval before inspection: {row['id']}")
        original = Path(row["path"])
        if sha(original) != row["original_sha256"]:
            raise ValueError("Acquired dependency bytes changed")
        destination = recovery / "dependencies" / row["id"]
        destination.mkdir(parents=True)
        receipt = None
        if row["format"] in {"jats", "html"}:
            document = Document.model_validate({key: row[key] for key in Document.model_fields})
            descriptor, receipt = _primary(document, row, original,
                Identity.model_validate(row["identity"]), destination / "text.txt")
            identity = row["host_identity"]
        elif row["format"] == "pdf":
            if not original.read_bytes().startswith(b"%PDF-"):
                raise ValueError("Dependency PDF response is not a PDF")
            command = ["pdftotext", "-layout", str(original), "-"]
            text = subprocess.run(command, check=True, capture_output=True).stdout.decode("utf-8")
            if row["role"] == "cited_protocol":
                identity_command = ["pdftotext", "-enc", "UTF-8", "-eol", "unix", str(original), "-"]
                identity_text = subprocess.run(identity_command, check=True, capture_output=True).stdout.decode("utf-8")
                identity_path = destination / "identity_reading_order.txt"
                identity_path.write_text(identity_text, encoding="utf-8")
                identity = {**verify_pdf_text(identity_text, row["identity"]),
                            "reading_order_path": str(identity_path),
                            "reading_order_sha256": sha(identity_path), "command": identity_command}
            else:
                # The primary names the kit, not the manufacturer's exact revision.
                # Check the actual guide identity and retain this version qualification.
                normalized = "".join(c for c in text[:12000].casefold() if c.isalnum())
                if not all(value in normalized for value in ("chromium", "singlecell", "v31", "cg000399")):
                    raise ValueError("Manufacturer guide identity needs explicit inspection")
                identity = {"guide": "CG000399", "revision_applicability": "Primary does not specify guide revision; do not assume every guide setting was used"}
            limits = "PDF text rendition; image/table/formula fidelity and exact source applicability require independent review."
            path = destination / "text.txt"
            _publish_rendition(path, text, original, sha(original), "pdftotext -layout", limits)
            descriptor = {"path": str(path)}
            receipt = {"original": str(original), "original_sha256": sha(original),
                "rendition": str(path), "rendition_sha256": sha(path), "command": command, "limits": limits}
        else:
            raise ValueError("Unsupported acquired dependency format")
        descriptor.update(source_id="dependency_" + row["id"].replace("-", "_"),
                          title=row["identity"]["title"], url=row["final_url"], role=row["role"])
        if row["role"] == "manufacturer_protocol":
            descriptor["title"] += " [guide revision not specified by the primary]"
        if receipt:
            receipt["source_id"] = descriptor["source_id"]
            ledger.append(receipt)
        descriptors.append(descriptor)
        decisions.append({"id": row["id"], "role": row["role"], "identity_check": identity,
                          "original": str(original), "original_sha256": sha(original),
                          "boundary": "Retrieved attributed evidence, not proof of use of every procedure/condition"})
    write(recovery / "dependency_registration.json", decisions)
    return descriptors, ledger


async def main(root, recovery_attempt=1):
    if type(recovery_attempt) is not int or recovery_attempt < 1:
        raise ValueError("Recovery attempt must be a positive integer")
    extraction = root / "extraction/run_001"
    if extraction.exists() and (not extraction.is_dir() or any(extraction.iterdir())):
        raise ValueError("Extraction destination must be fresh before source recovery")
    acquisition = root / "acquisition/methods/scstamp-seq"
    custody_path = root / "acquisition/source_receipts.json"
    custody = json.loads(custody_path.read_bytes())["files"]
    check_pins(custody)
    custody[str(custody_path)] = sha(custody_path)
    custody[str(acquisition / "acquisition.json")] = sha(acquisition / "acquisition.json")
    initial = acquisition / "packet.json"
    packet = load_packet(initial)
    recovery = root / f"source_recovery/attempt_{recovery_attempt:03d}"
    recovery.mkdir(parents=True, exist_ok=False)
    descriptors, dependency_ledger = prepare_dependencies(acquisition, recovery)
    source = next(s for s in packet["sources"] if s["original_path"].endswith(".xml"))
    prepared = await asyncio.to_thread(prepare, initial, source["source_id"],
        "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC12622024/supplementaryFiles",
        "media-1.pdf", recovery / "supplement")

    # A fresh, source-scoped content/version inspector is separate from the finder.
    attempt = recovery / "inspection"
    attempt.mkdir()
    original, rendition = Path(prepared["member"]["path"]), Path(prepared["source"]["path"])
    render_command = ["pdftoppm", "-f", "1", "-l", "1", "-r", "100", "-png", "-singlefile",
                      str(original), str(attempt / "supplement-title")]
    subprocess.run(render_command, check=True, capture_output=True)
    write(attempt / "render_receipt.json", {"command": render_command, "pdf": str(original),
        "pdf_sha256": sha(original), "page": 1, "png": str(attempt / "supplement-title.png"),
        "png_sha256": sha(attempt / "supplement-title.png")})
    write(attempt / "schema.json", Inspection.model_json_schema())
    request = {"preparation": str(recovery / "supplement/preparation.json"),
        "preparation_sha256": sha(recovery / "supplement/preparation.json"),
        "method_id": packet["method_id"], "expected_variant": packet["variant"],
        "registered_primary": {k: source[k] for k in ("source_id", "document_identity", "original_path", "original_sha256")},
        "selected_pdf": str(original), "rendition": str(rendition),
        "required_inspected_files": {str(original): sha(original), str(rendition): sha(rendition)}}
    write(attempt / "request.json", request)
    pins = {str(p): sha(p) for p in attempt.iterdir() if p.is_file()}
    pins.update(prepared["files"])
    pins[str(recovery / "supplement/preparation.json")] = sha(recovery / "supplement/preparation.json")
    pins.update(custody)
    pins.update({str(p): sha(p) for p in recovery.rglob("*") if p.is_file()})
    prompt = f"""ROLE: source_attachment_inspector
Read {attempt / 'request.json'}, {attempt / 'schema.json'} and the preparation.
Inspect the attached original title-page image and the actual selected PDF/text.
Determine whether this attachment belongs to the registered primary and declared
preprint version. The title/filename or ZIP association alone is insufficient.
Check its contents and figure/page headings for a different publication or mixed
document. Do not certify scientific completeness, recipe correctness, visibility
of all image details, or applicability of every experiment. Preserve uncertainty.
Use consistent only when the actual content and version evidence justify it;
otherwise use uncertain or conflict. State the basis and exact body witness.
The image shows only page 1; further original pages can be rendered if necessary.
Do not use web/network, prior finder opinions, old extractions or reviews. Do not
launch agents or change sources, request, preparation, schema or image inputs.
Write only {attempt / 'artifact.json'} with one schema-conforming Inspection.
Copy exact frozen bindings from request; no generated source text or new protocol.
"""
    worker = CodexWorker(model="gpt-6.1-sol", effort="xhigh", timeout_seconds=None,
                         web_search=False, network_access=False)
    result = await worker.run(prompt, attempt, image_paths=[attempt / "supplement-title.png"])
    archived = assess_archived_attempt(attempt)
    check_pins(pins)
    if (not archived.complete or result.error or result.exit_code != 0
            or result.thread_id != archived.thread_id or result.usage != archived.usage
            or (attempt / "prompt.txt").read_text() != prompt):
        raise ValueError("Attachment inspector lacks a complete, unchanged native attempt")
    registered = register(recovery / "supplement/preparation.json", attempt / "artifact.json",
                          recovery / "registration", prior_ledger=acquisition / "originals_ledger.json")
    packet_path = Path(registered["packet"])
    packet = load_packet(packet_path)
    ledger = json.loads((recovery / "registration/originals_ledger.json").read_bytes())
    ledger.extend(dependency_ledger)

    options = {k: packet[k] for k in ("method_id", "title", "scope", "variant", "format_version", "rendition_version")}
    existing = [{**{k: s[k] for k in ("source_id", "title", "url", "role")}, "path": s["original_path"]}
                for s in packet["sources"]]
    expanded = build_packet(**options, sources=existing + descriptors)
    write(recovery / "packet.json", expanded)
    write(recovery / "originals_ledger.json", ledger)
    write(recovery / "original_bindings.json", bind_originals(recovery / "packet.json", recovery / "originals_ledger.json"))
    write(recovery / "readiness.json", {"status": "prepared_for_source_first_inspection",
        "packet": str(recovery / "packet.json"), "packet_sha256": sha(recovery / "packet.json"),
        "sources": len(expanded["sources"]), "scientific_completeness": False,
        "limits": "Dependency supplements remain as recorded in acquisition; image details need original inspection. Source obligations and independent review decide consequences."})
    state = await execute(recovery / "packet.json", extraction, worker=worker,
        mode="specialists", max_revisions=1, require_coverage=True,
        require_source_coverage=True, require_field_review=True)
    check_pins(pins)
    print(json.dumps({"status": state["status"], "run": state["run"], "error": state.get("error")}))
    return state["status"] not in {"failed", "cancelled"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method_root", type=Path)
    parser.add_argument("--recovery-attempt", type=int, default=1)
    args = parser.parse_args()
    if not asyncio.run(main(args.method_root.resolve(), args.recovery_attempt)):
        raise SystemExit(1)

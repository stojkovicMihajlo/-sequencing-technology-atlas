"""Replay an explicit PDF registration proof without converting or retrieving."""

import json
from pathlib import Path
import re

from genomics_discovery.curation.source_acquisition import pdf_registration as pdf


def verify_pdf_registration(source, case, expected, pin):
    directory = Path(source["original_path"]).parent
    registration_path = pin(directory / "registration.json")
    receipt_path = pin(directory / "source_receipts.json")
    registration = json.loads(registration_path.read_bytes())
    files = json.loads(receipt_path.read_bytes())["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError("PDF registration lacks file-byte custody pins")
    for name, expected_sha in files.items():
        if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise ValueError("PDF registration contains an invalid source-receipt SHA256")
        pin(name, expected_sha)

    def bound(value, expected_sha=None):
        path = Path(value)
        if str(path) not in files or expected_sha is not None and files[str(path)] != expected_sha:
            raise ValueError(f"PDF registration file is not bound by its source receipt: {path}")
        return pin(path, files[str(path)])

    bound(registration_path)
    rendition = bound(source["original_path"], source["original_sha256"])
    if (registration.get("status") != "registered_for_fresh_source_review"
            or registration.get("scientific_resolution") is not False
            or registration["rendition"] != str(rendition)
            or registration["rendition_sha256"] != source["original_sha256"]
            or registration["public_url"] != source["url"]
            or any(registration["scope"][field] != case[field] for field in ("method_id", "title", "scope", "variant"))):
        raise ValueError("PDF registration differs from the exact primary or frozen case scope")
    registered_packet = json.loads(bound(directory / "packet.json").read_bytes())
    matching = [row for row in registered_packet["sources"] if all(row[key] == source[key]
        for key in ("source_id", "original_path", "original_sha256", "role"))]
    if len(matching) != 1 or any(registered_packet[field] != case[field] for field in ("method_id", "title", "scope", "variant")):
        raise ValueError("PDF registration packet does not bind this exact source and scope")
    original = bound(registration["original"], registration["original_sha256"])
    with original.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError("Registered PDF original has no PDF signature")
    metadata = bound(registration["metadata"], registration["metadata_sha256"])
    retrieval = json.loads(bound(directory / "bibliographic_retrieval.json").read_bytes())
    if (retrieval.get("status") != "downloaded" or retrieval["path"] != str(metadata)
            or retrieval["sha256"] != files[str(metadata)]):
        raise ValueError("PDF bibliographic retrieval does not bind its original metadata bytes")
    identity = pdf.bibliography(metadata.read_bytes(), retrieval["final_url"], expected)
    if registration["identity"] != identity:
        raise ValueError("PDF registration identity differs from reparsed authoritative bibliography")
    recorded_scope = pdf.Scope.model_validate(registration["scope"])
    for key in ("pmid", "doi", "title"):
        declared = getattr(recorded_scope.primary_identity, key)
        normal = pdf._doi if key == "doi" else pdf._title if key == "title" else str
        if declared and (not identity.get(key) or normal(declared) != normal(identity[key])):
            raise ValueError("PDF registration scoped identity conflicts with authoritative " + key)
    if source["source_id"] != "primary_pdf_" + files[str(original)][:12]:
        raise ValueError("PDF source ID differs from its registered original")
    if retrieval.get("cached_receipt"):
        cached = json.loads(bound(retrieval["cached_receipt"], retrieval["cached_receipt_sha256"]).read_bytes())
        if (cached != retrieval["original_retrieval"]
                or bound(cached["path"], cached["sha256"]).read_bytes() != metadata.read_bytes()):
            raise ValueError("PDF bibliography differs from its cached original host receipt")
    acquisition, downloaded = registration.get("acquisition_binding"), registration.get("original_download_binding")
    if bool(acquisition) == bool(downloaded):
        raise ValueError("PDF registration must bind exactly one original retrieval route")
    binding = acquisition or downloaded
    original_record = json.loads(bound(binding["path"], binding["sha256"]).read_bytes())
    if acquisition:
        rows = [row for row in original_record["documents"] if row.get("id") == binding["document_id"]]
        if original_record["method_id"] != case["method_id"] or len(rows) != 1:
            raise ValueError("PDF acquisition case/document binding differs")
        original_record = rows[0]
        if original_record.get("role") != "primary_paper" or original_record.get("format") != "pdf":
            raise ValueError("PDF acquisition is not the declared primary PDF")
    elif original_record.get("status") != "downloaded":
        raise ValueError("PDF original lacks its actual host download receipt")
    if (original_record != binding["record"] or original_record["path"] != str(original)
            or original_record.get("original_sha256", original_record.get("sha256")) != files[str(original)]
            or original_record["final_url"] != source["url"]):
        raise ValueError("PDF original differs from its exact original retrieval binding")
    for key, declared in original_record.get("identity", {}).items():
        normal = pdf._doi if key == "doi" else pdf._title if key == "title" else str
        if declared and (not identity.get(key) or normal(declared) != normal(identity[key])):
            raise ValueError("PDF acquisition identity conflicts with authoritative " + key)
    selection = json.loads(bound(directory / "article_selection.json").read_bytes())
    if selection != registration["article_selection"]:
        raise ValueError("PDF article selection differs from registration")
    full_text = bound(directory / "full_pdf_text.txt").read_bytes().decode("utf-8")
    decision = {key: value for key, value in selection.items() if key not in {"start_char", "end_char"}}
    selected, prefix, suffix, replayed = pdf.select_article_text(full_text, decision)
    if (replayed != selection or bound(directory / "excluded_prefix.txt").read_bytes().decode("utf-8") != prefix
            or bound(directory / "excluded_suffix.txt").read_bytes().decode("utf-8") != suffix):
        raise ValueError("PDF text selection or retained exclusions differ")
    inspection_binding = selection["inspection_report"]
    inspection_path = bound(inspection_binding["path"], inspection_binding["sha256"])
    inspection = json.loads(inspection_path.read_bytes())
    inspected_decision = {key: value for key, value in decision.items() if key != "inspection_report"}
    if (inspection["method_id"] != case["method_id"] or inspection["original_sha256"] != files[str(original)]
            or inspection["selection"] != inspected_decision or not inspection.get("files")):
        raise ValueError("PDF inspection does not bind the same original and article selection")
    for name, expected_sha in inspection["files"].items():
        inspected = Path(name)
        bound(inspected if inspected.is_absolute() else (inspection_path.parent / inspected).resolve(), expected_sha)
    evidence_binding = registration.get("identity_evidence")
    if evidence_binding:
        evidence = json.loads(bound(evidence_binding["path"], evidence_binding["sha256"]).read_bytes())
        if evidence != evidence_binding["record"]:
            raise ValueError("PDF identity evidence differs from its recorded proof")
        check = pdf.verify_selected_pdf_text(full_text, identity, selection, evidence, original_sha256=files[str(original)])
        if check != registration["identity_check"] or "first_pages_check" in registration:
            raise ValueError("PDF selected-article identity check differs")
        route = "registered_pdf_inspected_selected_article"
    else:
        check = pdf.verify_pdf_text(full_text, identity)
        if check != registration["first_pages_check"] or "identity_check" in registration:
            raise ValueError("PDF legacy early-page identity check differs")
        route = "registered_pdf_early_page"
    header, separator, body = rendition.read_bytes().decode("utf-8").partition("\n\n")
    if (not separator or header.splitlines()[:3] != ["[Rendition provenance, not publication content]",
            f"Original cached file: {original.resolve()}", f"SHA256: {files[str(original)]}"] or body != selected):
        raise ValueError("PDF primary rendition differs from its exact selected text")
    for item in registration["implementation_snapshot"]:
        bound(item["snapshot"], item["sha256"])
    return {"identity_route": route, "actual_identity": {"title": identity["title"], "identifiers": {
            key: [identity[key]] if identity.get(key) else [] for key in ("pmid", "doi")}},
        "original": str(original), "original_sha256": files[str(original)],
        "registration": {"path": str(registration_path), "sha256": files[str(registration_path)]},
        "source_receipts": {"path": str(receipt_path), "sha256": pdf.sha(receipt_path)}, "pdf_identity_check": check,
        "article_selection": selection, "boundary": registration["identity_boundary"]}

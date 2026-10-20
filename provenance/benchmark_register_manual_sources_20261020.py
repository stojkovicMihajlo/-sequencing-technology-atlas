"""Explicit benchmark source curation after recorded visual inspection.

Case-specific selectors are source annotations, never extraction instructions.
This bounded host driver makes no model calls and must execute in Slurm.
"""

import json
import os
from pathlib import Path
import shutil

from genomics_discovery.curation.extraction_batch.storage import check_pins, sha, write
from scripts.benchmark.recover_supplements import bind
from genomics_discovery.curation.cached_documents import _publish_rendition
from genomics_discovery.curation.protocol_benchmark.source_identity import verify_publication
from genomics_discovery.curation.source_acquisition.downloads import download
from genomics_discovery.curation.source_acquisition.models import Document, Identity, Scope
from genomics_discovery.curation.source_acquisition.runtime import _primary
from genomics_discovery.curation.source_acquisition.pdf_registration import register_pdf
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet


def read(path):
    return json.loads(Path(path).read_bytes())


def witness(text, quote, start=0):
    begin = text.index(quote, start)
    return {"start_char": begin, "end_char": begin + len(quote), "quote": quote,
            "page": text[:begin].count("\f") + 1}


def main():
    if not os.environ.get("SLURM_JOB_ID"):
        raise RuntimeError("Actual source registration must execute in Slurm")
    project, runtime = Path(os.environ["ATLAS_PROJECT_ROOT"]), Path(os.environ["ATLAS_REPOSITORY"])
    base = project / "technology_atlas/v2/work/benchmark_20261020"
    prior = base / "reference_v1_01/work/sources"
    output = base / "manual_sources_01"
    output.mkdir()
    cases_path = runtime / "benchmarks/protocol_graph_v1/cases.json"
    cases = {c["method_id"]: c for c in read(cases_path)["cases"]}
    recipe = read(runtime / "benchmarks/protocol_graph_v1/source_recovery_20261020.json")
    prior_pins = {}
    bind(prior_pins, read(prior / "input_bindings.json"))
    bind(prior_pins, read(prior / "acquisition/source_receipts.json")["files"])
    bind(prior_pins, {str(prior / "source_report.json"): recipe["source_report_sha256"],
        str(prior / "acquisition/source_receipts.json"): recipe["source_receipts_sha256"]})
    prior_manifest = read(prior / "annotation_manifest.json")
    if (prior_manifest["case_design_sha256"] != sha(cases_path)
            or prior_manifest["source_report"] != {"path": str(prior / "source_report.json"), "sha256": recipe["source_report_sha256"]}):
        raise ValueError("Prior source phase differs from frozen case/report bindings")
    bind(prior_pins, {str(prior / "annotation_manifest.json"): sha(prior / "annotation_manifest.json"),
        str(prior / "input_bindings.json"): sha(prior / "input_bindings.json")})
    check_pins(prior_pins)
    common = {str(cases_path): sha(cases_path), str(Path(__file__).resolve()): sha(__file__)}
    registrations, errors = [], []

    def finish(mid, directory, packet_path, additional):
        files = {}
        for additions in (prior_pins, common, additional,
                {str(p): sha(p) for p in directory.rglob("*") if p.is_file()}):
            bind(files, additions)
        check_pins(files)
        packet = load_packet(packet_path)
        proof = verify_publication(packet, cases[mid], packet_path=packet_path, bound_pins=files)
        write(directory / "publication_verification.json", proof)
        bind(files, {str(directory / "publication_verification.json"): sha(directory / "publication_verification.json")})
        check_pins(files)
        custody = directory / "custody.json"
        write(custody, {"format_version": "benchmark_manual_registration/1.0", "files": files,
              "boundary": "Explicit host source registration; no protocol extraction or source-completeness claim."})
        registrations.append({"method_id": mid, "packet": str(packet_path), "packet_sha256": sha(packet_path),
                              "custody": {"path": str(custody), "sha256": sha(custody)}})

    # The retained official author manuscript has a documented different title.
    # Use the existing HTML documentary route; do not alter the frozen final title.
    directory = output / "atac"
    directory.mkdir()
    try:
        original = project / "technology_atlas/v2/papers/24097267/article.html"
        if sha(original) != "80310c927a0e55afd80e7a4d906a86dad0b436f198a9a4eb44c36d9ed87a418e":
            raise ValueError("Retained ATAC HTML differs from inspected source bytes")
        expected = Identity(**{k: cases["atac-seq"]["primary_identity"][k] for k in ("pmid", "doi", "title")})
        url = "https://pmc.ncbi.nlm.nih.gov/articles/PMC3959825/"
        document = Document(id="atac_documented_manuscript_html", url=url, role="primary_paper", format="html",
            identity=expected, rationale="Retained official PMC author manuscript; verify documentary title variant with existing policy")
        record = {"path": str(original), "original_sha256": sha(original), "final_url": url}
        source, receipt = _primary(document, record, original, expected, directory / "primary.txt")
        receipt["retrieval_boundary"] = "Reverified retained project cache; no new HTTP delivery or final/manuscript byte equivalence claimed"
        write(directory / "originals_ledger.json", [receipt])
        old_path = Path(read(prior / "source_report.json")["cases"]["atac-seq"]["packet"])
        old = load_packet(old_path)
        sources = [{"source_id": s["source_id"], "title": s["title"], "url": s["url"],
                    "role": s["role"], "path": s["original_path"]} for s in old["sources"] if s["role"] != "primary_paper"]
        sources.append(source)
        packet = build_packet(**{k: cases["atac-seq"][k] for k in ("method_id", "title", "scope", "variant")},
            sources=sources, **{k: old[k] for k in ("format_version", "rendition_version") if k in old})
        write(directory / "packet.json", packet)
        finish("atac-seq", directory, directory / "packet.json", {str(original): sha(original), str(old_path): sha(old_path)})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append({"method_id": "atac-seq", "error": type(exc).__name__ + ": " + str(exc)})

    directory = output / "spatial"
    directory.mkdir()
    try:
        case = cases["spatial-transcriptomics"]
        acquisition_path = prior / "acquisition/methods/spatial-transcriptomics/acquisition.json"
        row = next(d for d in read(acquisition_path)["documents"] if d["id"] == "stahl2016_supplement_compound_reprint")
        fetched = download(row["url"])
        original = directory / "original_compound.pdf"
        original.write_bytes(fetched.content)
        if sha(original) != row["original_sha256"]:
            raise ValueError("New public compound delivery differs from inspected original bytes")
        original_receipt = directory / "original_download.json"
        write(original_receipt, {"status": "downloaded", "requested_url": row["url"], "final_url": fetched.final_url,
            "path": str(original), "sha256": sha(original), "content_type": fetched.content_type,
            "prior_identical_original": {"path": row["path"], "sha256": row["original_sha256"]}})
        inspected = base / "extra_source_inspection_01"
        images = directory / "inspection"
        images.mkdir()
        manifest = read(inspected / "render_manifest.json")
        if (manifest["original_sha256"] != sha(original)
                or [p["page"] for p in manifest["pages"]] != list(range(1, 46))):
            raise ValueError("Inspection must bind the identical original and all45 ordered source pages")
        text_path = images / "full_pdf_text.txt"
        old_text = Path(manifest["source_text"]["path"])
        if sha(old_text) != manifest["source_text"]["sha256"]:
            raise ValueError("Inspected full text changed")
        shutil.copyfile(old_text, text_path)
        if sha(text_path) != manifest["source_text"]["sha256"]:
            raise ValueError("Copied inspected full text differs")
        text = text_path.read_text()
        files = {str(original): sha(original), str(text_path): sha(text_path)}
        for page in manifest["pages"]:
            new_image = images / Path(page["image"]).name
            shutil.copyfile(page["image"], new_image)
            if sha(new_image) != page["image_sha256"]:
                raise ValueError("Copied rendered page differs")
            page["image"] = str(new_image)
            files[str(new_image)] = sha(new_image)
        manifest.update(original=str(original), source_text={"path": str(text_path), "sha256": sha(text_path)},
            rendered_from_identical_original={"path": row["path"], "sha256": row["original_sha256"]})
        render_path = images / "render_manifest.json"
        write(render_path, manifest)
        files[str(render_path)] = sha(render_path)
        # Bounds follow actual rendered ownership, not the expected protocol answer.
        decision = {"kind": "exact_text_regions", "source_text_sha256": sha(text_path),
            "basis": "Root Codex source-preparer inspected all45 rendered pages as contact sheets and main/header/closing/SI/Methods pages at higher resolution. Retain all main-article text while excluding preceding bird-article blocks and the separately registered SI. This is an explicit model source-curation judgment, not automatic purity or human adjudication.",
            "regions": [
                {"start": "TRANSCRIPTION\n\nVisualization and analysis of gene", "end": "fluorescently labeled nucleotides to visualize the"},
                {"start": "synthesized cDNA (Fig. 1A and fig. S1). The tissue",
                 "end": "12 January 2016; accepted 31 May 2016\n10.1126/science.aaf2403"}]}
        observations = []
        for page in manifest["pages"]:
            number = page["page"]
            observations.append({"page": number, "image_sha256": page["image_sha256"],
                "relationship": "mixed_articles" if number == 1 else "cover" if number == 6 else "target_article",
                "reading_order": "interleaved" if number == 1 else "coherent",
                "observations": ("Top band belongs to preceding bird article; lower target header/columns retained by two exact text intervals." if number == 1 else
                    "Target main article; figures/captions and continuation belong to this publication." if number <= 5 else
                    "Exact target title/DOI SI cover, excluded from main selection and registered with supporting materials." if number == 6 else
                    "Target supporting materials continuation: Methods, figures/captions, tables or references; excluded from main selection. Contact-sheet ownership review does not certify every scientific value.")})
        inspection = images / "inspection.json"
        write(inspection, {"format_version": "article_content_inspection/1.1", "method_id": case["method_id"],
            "original_sha256": sha(original), "decision": "select", "selected_reading_order": "coherent", "selection": decision,
            "source_text": {"path": str(text_path), "sha256": sha(text_path)},
            "render_manifest": {"path": str(render_path), "sha256": sha(render_path)}, "pages": observations, "files": files})
        selection = {**decision, "inspection_report": {"path": str(inspection), "sha256": sha(inspection)}}
        title = "Visualization and analysis of gene\nexpression in tissue sections by\nspatial transcriptomics"
        closing = decision["regions"][1]["end"]
        identity_path = images / "identity.json"
        write(identity_path, {"format_version": "selected_article_identity/1.1", "original_sha256": sha(original),
            "inspection_report": selection["inspection_report"], "title_witness": witness(text, title),
            "identifier_witness": witness(text, "10.1126/science.aaf2403", text.index(closing)),
            "identifier_context": witness(text, closing), "identifier_role": "article_publication_identifier",
            "title_role": "visually_inspected_article_header", "title_page_image_sha256": manifest["pages"][0]["image_sha256"],
            "basis": "Main article's visual header and accepted/publication DOI closing block match independent PubMed; preceding bird DOI and separately covered SI remain excluded."})
        scope = Scope(**{k: case[k] for k in ("method_id", "title", "scope", "variant")},
            primary_identity=Identity(**{k: case["primary_identity"][k] for k in ("pmid", "doi", "title")}))
        registered = directory / "primary_registration"
        register_pdf(scope, original, sha(original), fetched.final_url, registered,
            original_receipt=original_receipt, selection=selection, identity_evidence=identity_path)
        primary = load_packet(registered / "packet.json")["sources"][0]
        si = directory / "supplement.txt"
        # Exactly PDF pages6..45. Whole original and page evidence are retained.
        si_text = "\f".join(text.split("\f")[5:])
        _publish_rendition(si, si_text, original, sha(original), "pdftotext + explicit source pages6..45",
            "Separate supporting-material view from the identical compound reprint. Original title/DOI cover and all page images pinned; no OCR. Inspect originals for glyphs, figures and table layout.")
        association = {"basis": "Inspected SI cover on source page6 has exact title, authors, DOI and official Science SI URL; source pages7..45 are its Methods/figures/tables/references. It is physically co-bound with independently identified main article in one unchanged public PDF.",
            "original": str(original), "original_sha256": sha(original), "source_pages": [6, 45],
            "rendition": str(si), "rendition_sha256": sha(si), "source_id": "stahl2016_original_supporting_materials",
            "inspection": {"path": str(inspection), "sha256": sha(inspection)},
            "boundary": "Model source-preparer association judgment, not publisher-host delivery or experimental completeness."}
        write(directory / "originals_ledger.json", [association])
        descriptors = [{"source_id": primary["source_id"], "title": primary["title"], "url": primary["url"],
                        "role": "primary_paper", "path": primary["original_path"]},
            {"source_id": association["source_id"], "title": "Original 2016 Spatial Transcriptomics supporting materials",
             "url": fetched.final_url, "role": "supplement", "path": str(si)}]
        packet_path = directory / "packet.json"
        write(packet_path, build_packet(**{k: case[k] for k in ("method_id", "title", "scope", "variant")}, sources=descriptors))
        finish(case["method_id"], directory, packet_path,
            {str(acquisition_path): sha(acquisition_path), **read(registered / "source_receipts.json")["files"]})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append({"method_id": "spatial-transcriptomics", "error": type(exc).__name__ + ": " + str(exc)})

    check_pins(prior_pins)
    write(output / "errors.json", errors)
    write(output / "overrides.json", {"format_version": "benchmark_source_overrides/1.0", "cases_sha256": sha(cases_path),
          "registrations": registrations, "curation_errors": errors,
          "boundary": "Source-only curated inputs. All original scientific case requirements unchanged; no protocol facts, graph labels or answers supplied to extraction."})
    print(json.dumps({"registered": [r["method_id"] for r in registrations], "errors": errors}))


if __name__ == "__main__":
    main()

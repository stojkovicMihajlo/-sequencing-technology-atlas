"""Recovery custody tests use synthetic bytes and injected converters, never retrieval."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.benchmark import recover_supplements as recovery
from genomics_discovery.curation.cached_documents import _publish_rendition
from genomics_discovery.curation.run_state import sha, write
from genomics_discovery.curation.source_acquisition.downloads import Download
from genomics_discovery.curation.source_acquisition.legacy_word import OLE
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet
from test_source_html_identity import html


@pytest.fixture
def source_phase(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Recovery tests must not construct native workers or call real converters/downloaders")

    monkeypatch.setattr("genomics_discovery.curation.source_acquisition.runtime.CodexWorker", forbidden)
    prior = tmp_path / "prior"
    method = prior / "acquisition/methods/assay"
    method.mkdir(parents=True)
    primary = method / "originals/primary/article.xml"
    primary.parent.mkdir(parents=True)
    primary.write_text('''<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>
      <article-id pub-id-type="pmid">123</article-id><article-id pub-id-type="doi">10.1234/protocol</article-id>
      <title-group><article-title>Published protocol</article-title></title-group></article-meta></front>
      <body><sec><title>Methods</title><p>Retain the selected fraction.</p>
      <supplementary-material xlink:href="https://pmc.ncbi.nlm.nih.gov/articles/PMC123/bin/protocol.pdf"/>
      </sec></body></article>''')
    case = {"case_id": "assay-original", "method_id": "assay", "family": "fixture",
        "split": "development", "title": "Assay label", "scope": "Selected original protocol",
        "variant": "Original implementation", "primary_identity": {
            "pmid": "123", "doi": "10.1234/protocol", "title": "Published protocol"}}
    cases = tmp_path / "cases.json"
    write(cases, {"cases": [case, {**case, "method_id": "untouched", "case_id": "untouched-original"}]})
    supplement = method / "existing.txt"
    supplement.write_text("Previously registered independent supplement.")
    base = build_packet(**{key: case[key] for key in ("method_id", "title", "scope", "variant")}, sources=[
        {"path": str(primary), "source_id": f"acquired_primary_{sha(primary)[:8]}", "title": "Published protocol",
            "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/", "role": "primary_paper"},
        {"path": str(supplement), "source_id": "retained_supplement", "title": "Retained supplementary source",
            "url": "https://example.org/existing.txt", "role": "supplement"}])
    write(method / "packet.json", base)
    document = {"id": "primary", "role": "primary_paper", "format": "jats", "path": str(primary),
        "original_sha256": sha(primary), "final_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"}
    write(method / "acquisition.json", {"method_id": "assay", "documents": [document]})
    write(prior / "input_bindings.json", {str(cases): sha(cases)})
    write(prior / "source_report.json", {"cases": {
        "assay": {"case_id": case["case_id"], "status": "source_acquisition_needed", "reason": "Missing supplement"},
        "untouched": {"case_id": "untouched-original", "status": "source_acquisition_needed", "reason": "Unresolved source task"}}})
    recipes = tmp_path / "recipes.json"
    row = {"document_id": "recovered_protocol", "format": "pdf", "accession": "S-EPMC123", "file_path": "protocol.pdf"}
    fixture = SimpleNamespace(root=tmp_path, prior=prior, method=method, cases=cases, case=case, primary=primary,
        document=document, recipes=recipes, row=row, output=tmp_path / "recovered", body=b"%PDF-1.7 synthetic fixture",
        calls=[], record=None)

    def reset():
        receipt = prior / "acquisition/source_receipts.json"
        files = {str(path): sha(path) for path in (prior / "acquisition/methods").rglob("*") if path.is_file()}
        write(receipt, {"format_version": "public_source_acquisition/1.0", "files": files})
        write(recipes, {"format_version": "benchmark_supplement_recovery/1.0", "cases_sha256": sha(cases),
            "source_receipts_sha256": sha(receipt), "source_report_sha256": sha(prior / "source_report.json"),
            "recipes": [{"method_id": "assay", "primary_document_id": "primary", "supplements": [fixture.row]}]})

    def fetch(url):
        fixture.calls.append(url)
        if "/api/v1/" in url:
            record = fixture.record or {"accno": "S-EPMC123", "type": "submission", "section": {"type": "Study",
                "subsections": [{"type": "Publication", "links": [
                    {"url": "123", "attributes": [{"name": "Type", "value": "PMID"}]},
                    {"url": "10.1234/protocol", "attributes": [{"name": "Type", "value": "DOI"}]}]}],
                "files": [{"path": fixture.row["file_path"], "size": len(fixture.body), "type": "file"}]}}
            import json
            return Download(json.dumps(record).encode(), url, "application/json")
        assert url == "https://www.ebi.ac.uk/biostudies/files/S-EPMC123/" + fixture.row["file_path"]
        return Download(fixture.body, url, "application/octet-stream")

    def convert(folder, pmid, output):
        register = folder / "supplements/acquisition.json"
        original = Path(recovery.read(register)["files"][0]["path"])
        destination = output / "protocol.txt"
        _publish_rendition(destination, "Supplementary protocol fixture.", original, sha(original), "injected_fixture", "No real conversion")
        return ([{"path": str(destination), "title": "protocol"}], [{"original": str(original),
            "original_sha256": sha(original), "rendition": str(destination), "rendition_sha256": sha(destination)}], [])

    fixture.reset, fixture.fetch, fixture.convert = reset, fetch, convert
    reset()
    return fixture


def run(fixture, **kwargs):
    return recovery.recover(fixture.cases, fixture.prior, fixture.recipes, fixture.output,
        downloader=fixture.fetch, supplement_converter=fixture.convert, **kwargs)


def disposition(fixture):
    return recovery.read(fixture.output / "source_report.json")["cases"]["assay"]


def test_exact_pdf_recovery_preserves_old_bytes_and_independent_sources(source_phase):
    f = source_phase
    before = {path: path.read_bytes() for path in f.prior.rglob("*") if path.is_file()}
    result = run(f)
    assert len(result["cases"]) == 1
    fresh = load_packet(Path(result["cases"][0]["packet"]))
    old = load_packet(f.method / "packet.json")
    assert next(s for s in fresh["sources"] if s["source_id"] == "retained_supplement") == old["sources"][1]
    assert len(fresh["sources"]) == 3
    report = disposition(f)
    assert report["supplement_recovery"]["added_supplements"] == 1
    assert report["publication_verification"]["primaries"][0]["actual_identity"]["identifiers"]["pmid"] == ["123"]
    rows = recovery.read(f.output / "source_report.json")["cases"]
    assert rows["untouched"]["reason"] == "Unresolved source task"
    assert all(path.read_bytes() == raw for path, raw in before.items())
    receipts = recovery.read(f.output / "source_receipts.json")
    assert receipts["scientific_resolution"] is False
    assert all(sha(Path(path)) == expected for path, expected in receipts["files"].items())
    assert len(f.calls) == 2 and not list(f.output.rglob("events.jsonl"))


@pytest.mark.parametrize("bad", ["pmid", "doi", "missing_doi", "missing_size", "size", "parent_link", "magic"])
def test_attachment_binding_failures_withhold_without_conversion(source_phase, bad):
    f = source_phase
    # Obtain the synthetic official record, then modify exactly one independent gate.
    f.record = recovery.json.loads(f.fetch("https://www.ebi.ac.uk/biostudies/api/v1/studies/S-EPMC123").content)
    f.calls.clear()
    publication = f.record["section"]["subsections"][0]
    if bad in {"pmid", "doi"}:
        publication["links"][0 if bad == "pmid" else 1]["url"] = "999" if bad == "pmid" else "10.9999/wrong"
    elif bad == "missing_doi":
        publication["links"].pop()
    elif bad == "missing_size":
        f.record["section"]["files"][0].pop("size")
    elif bad == "size":
        f.record["section"]["files"][0]["size"] += 1
    elif bad == "parent_link":
        f.row["file_path"] = "unrelated.pdf"
        f.record["section"]["files"][0]["path"] = "unrelated.pdf"
        f.reset()
    else:
        f.body = b"<html>Not the requested PDF</html>"
        f.record["section"]["files"][0]["size"] = len(f.body)
    def forbidden(*args):
        pytest.fail("Unbound or mismatched attachment must not reach conversion")
    f.convert = forbidden
    assert run(f)["cases"] == []
    assert disposition(f)["supplement_recovery"]["status"] == "withheld"
    assert not list(f.output.rglob("packet.json"))
    assert list(f.output.rglob("*.download.json"))


@pytest.mark.parametrize("kind,body", [("docx", b"PK\x03\x04synthetic"), ("doc", OLE + b"synthetic")])
def test_word_bridge_keeps_converter_assets_and_retained_canonical_equivalence(source_phase, kind, body):
    f = source_phase
    f.body = body
    f.row.update(format=kind, retained_document_id="old_word")
    retained = f.method / "old_word.bin"
    retained.write_bytes(body)
    metadata = recovery.read(f.method / "acquisition.json")
    metadata["documents"].append({"id": "old_word", "role": "supplement", "path": str(retained), "original_sha256": sha(retained)})
    write(f.method / "acquisition.json", metadata)
    f.reset()
    asset = f.root / "converter_asset"
    asset.write_text("Pinned conversion asset")
    calls = []
    def convert(original, destination, claimed):
        calls.append(claimed)
        _publish_rendition(destination, "Word rendition fixture.", original, sha(original), "injected_word", "No real conversion")
        return {"original": str(original), "original_sha256": sha(original), "rendition": str(destination),
            "rendition_sha256": sha(destination), "conversion_assets": {str(asset): sha(asset)}}
    result = run(f, word_converter=convert)
    assert len(result["cases"]) == 1 and calls == [kind]
    pins = recovery.read(f.output / "source_receipts.json")["files"]
    assert pins[str(asset)] == sha(asset)
    receipt = recovery.read(next(f.output.rglob("receipt.json")))
    assert receipt["association"]["retained_original"]["sha256"] == sha(retained)


def test_retained_attachment_is_not_assumed_equal_by_filename(source_phase):
    f = source_phase
    old = f.method / "protocol.pdf"
    old.write_bytes(b"%PDF-other version")
    metadata = recovery.read(f.method / "acquisition.json")
    metadata["documents"].append({"id": "old_pdf", "role": "supplement", "path": str(old), "original_sha256": sha(old)})
    write(f.method / "acquisition.json", metadata)
    f.row["retained_document_id"] = "old_pdf"
    f.reset()
    assert run(f)["cases"] == []
    assert "differ" in disposition(f)["reason"]


def test_rebuild_absent_packet_from_exact_html_with_documented_title_variant(source_phase):
    f = source_phase
    f.primary = f.primary.with_name("article.html")
    f.primary.write_bytes(html(title="Manuscript title", manuscript=True,
        body='<a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC123/bin/protocol.pdf">Supplement</a>'))
    f.document.update(format="html", path=str(f.primary), original_sha256=sha(f.primary))
    write(f.method / "acquisition.json", {"method_id": "assay", "documents": [f.document]})
    (f.method / "packet.json").unlink()
    f.reset()
    assert len(run(f)["cases"]) == 1
    primary = disposition(f)["publication_verification"]["primaries"][0]
    assert primary["html_assessment"]["title_variant_accepted"] is True
    assert primary["actual_identity"]["title"] == "Manuscript title"
    assert primary["expected_identity"]["title"] == "Published protocol"


@pytest.mark.parametrize("target", ["cases", "report", "receipt", "primary", "metadata"])
def test_changed_frozen_inputs_abort_before_new_phase_or_download(source_phase, target):
    f = source_phase
    path = {"cases": f.cases, "report": f.prior / "source_report.json",
        "receipt": f.prior / "acquisition/source_receipts.json", "primary": f.primary,
        "metadata": f.method / "acquisition.json"}[target]
    path.write_bytes(path.read_bytes() + b"\nchanged")
    with pytest.raises(ValueError):
        run(f)
    assert not f.output.exists() and f.calls == []


def test_consistently_pinned_wrong_primary_still_cannot_download_attachment(source_phase):
    f = source_phase
    f.primary.write_bytes(f.primary.read_bytes().replace(b">123</article-id>", b">999</article-id>"))
    f.document["original_sha256"] = sha(f.primary)
    write(f.method / "acquisition.json", {"method_id": "assay", "documents": [f.document]})
    f.reset()
    assert run(f)["cases"] == [] and f.calls == []
    assert "pmid mismatch" in disposition(f)["reason"]


def test_unbound_raw_original_and_recipe_traversal_are_rejected(source_phase):
    f = source_phase
    receipt = f.prior / "acquisition/source_receipts.json"
    value = recovery.read(receipt)
    value["files"].pop(str(f.primary))
    write(receipt, value)
    recipe = recovery.read(f.recipes)
    recipe["source_receipts_sha256"] = sha(receipt)
    write(f.recipes, recipe)
    assert run(f)["cases"] == [] and f.calls == []
    assert "pin" in disposition(f)["reason"]
    f.output = f.root / "second"
    f.row["document_id"] = "../escape"
    f.reset()
    with pytest.raises(ValueError, match="safe"):
        run(f)
    assert not f.output.exists()


def test_midrun_drift_and_output_reuse_cannot_publish(source_phase):
    f = source_phase
    original_convert = f.convert
    def changed(*args):
        result = original_convert(*args)
        f.primary.write_bytes(f.primary.read_bytes() + b"<!-- changed during run -->")
        return result
    f.convert = changed
    with pytest.raises(ValueError, match="Pinned file changed"):
        run(f)
    assert not (f.output / "annotation_manifest.json").exists()
    with pytest.raises(ValueError, match="new output"):
        run(f)


@pytest.mark.parametrize("drift", [False, True])
def test_existing_pdf_helper_pins_the_actual_executable_before_and_after_conversion(source_phase, monkeypatch, drift):
    f = source_phase
    executable = f.root / "pdftotext-fixture"
    executable.write_bytes(b"Synthetic executable never invoked")
    before = sha(executable)
    monkeypatch.setattr(recovery.shutil, "which", lambda name: str(executable))
    def no_process(command, **kwargs):
        assert command[0] == str(executable)
        Path(command[-1]).write_text("Synthetic rendered supplementary methods.")
        if drift:
            executable.write_bytes(b"Changed executable during rendition")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("genomics_discovery.curation.cached_documents.subprocess.run", no_process)
    f.convert = recovery.supplements
    if drift:
        with pytest.raises(ValueError, match="Pinned file changed"):
            run(f)
        assert not (f.output / "annotation_manifest.json").exists()
    else:
        assert len(run(f)["cases"]) == 1
        assert recovery.read(f.output / "source_receipts.json")["files"][str(executable)] == before


def test_command_requires_a_slurm_allocation(source_phase, monkeypatch):
    f = source_phase
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    with pytest.raises(ValueError, match="Slurm"):
        recovery.main(["--cases", str(f.cases), "--prior-sources", str(f.prior),
            "--recipes", str(f.recipes), "--output", str(f.output)])
    assert not f.output.exists()

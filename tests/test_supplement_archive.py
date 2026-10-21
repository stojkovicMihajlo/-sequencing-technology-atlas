"""Real ZIP/PDF fixtures test custody; authored inspection is not scientific truth."""

from io import BytesIO
import json
from pathlib import Path
import shutil
import stat
import struct
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from genomics_discovery.curation.source_acquisition import supplement_archive as helper
from genomics_discovery.curation.source_acquisition.downloads import Download
from genomics_discovery.curation.extraction_batch.storage import check_pins, sha
from genomics_discovery.curation.source_originals import load_originals
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet
from scripts.benchmark.archive_result import archive as preserve
from test_pdf_registration import make_pdf


URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/supplementaryFiles"
MEMBER = "nested/protocol.pdf"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def zipped(rows, compression=ZIP_STORED):
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=compression) as container:
        for name, content in rows:
            container.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
def fixture(tmp_path):
    primary = tmp_path / "primary.xml"
    primary.write_text('''<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>
      <article-id pub-id-type="pmid">1234</article-id><article-id pub-id-type="pmcid">PMC123</article-id>
      <article-id pub-id-type="doi">10.1234/fixture</article-id>
      <title-group><article-title>Original protocol v1</article-title></title-group>
      </article-meta></front><body><sec><title>Methods</title><p>Use the attached protocol.</p>
      <supplementary-material id="s1"><media xlink:href="protocol.pdf"/></supplementary-material>
      </sec></body></article>''')
    packet = build_packet(method_id="new-method", title="New method", scope="General assay",
        variant="preprint v1", sources=[{"source_id": "primary", "title": "Original protocol v1",
            "url": "https://example.org/primary", "role": "primary_paper", "path": str(primary)}])
    packet_path = save(tmp_path / "packet.json", packet)
    pdf = tmp_path / "member.pdf"
    make_pdf(pdf, "Original protocol v1\nSupplemental methods\nA fixture procedure.")
    raw = zipped([(MEMBER, pdf.read_bytes()), ("unselected.txt", b"Do not register this file.")])
    return packet_path, raw


def prepare(fixture, tmp_path):
    packet, raw = fixture
    directory = tmp_path / "prepared"
    result = helper.prepare(packet, "primary", URL, MEMBER, directory,
        fetch=lambda url: Download(raw, url, "application/zip"))
    return directory, result


def inspection(directory, prepared, **changes):
    record = {"format_version": "supplement_archive_inspection/1.0",
        "preparation_sha256": sha(directory / "preparation.json"),
        "method_id": "new-method", "expected_variant": "preprint v1", "decision": "consistent",
        "content_basis": "Inspected generated fixture PDF and its real Poppler rendition.",
        "version_basis": "Fixture title explicitly says v1; no real publication is certified.",
        "content_witness": "Original protocol v1",
        "inspected_files": {prepared["member"]["path"]: prepared["member"]["sha256"],
            prepared["source"]["path"]: prepared["conversion"]["rendition_sha256"]}, **changes}
    return save(directory.parent / "inspection.json", record)


requires_poppler = pytest.mark.skipif(not shutil.which("pdftotext"), reason="Poppler unavailable")


@requires_poppler
def test_prepare_is_pending_and_register_preserves_real_archive_member_custody(fixture, tmp_path):
    directory, prepared = prepare(fixture, tmp_path)
    assert prepared["status"] == "pending_content_and_version_inspection"
    assert not (directory / "packet.json").exists()
    assert prepared["source"]["url"] == URL
    assert prepared["association"]["archive_member"] == MEMBER
    assert prepared["member"]["path"].endswith("/selected.pdf")
    assert (directory / "supplementary_files.zip").read_bytes() == fixture[1]
    report = inspection(directory, prepared)
    output = tmp_path / "registered"
    result = helper.register(directory / "preparation.json", report, output)
    packet = load_packet(output / "packet.json")
    assert packet["sources"][:-1] == load_packet(fixture[0])["sources"]
    assert len(packet["sources"]) == 2
    assert packet["sources"][-1]["role"] == "supplement"
    assert packet["sources"][-1]["url"] == URL
    assert packet["sources"][-1]["title"] == MEMBER
    assert result["scientific_resolution"] is False
    bound = load_originals(output / "original_bindings.json")
    selected = bound["bindings"][prepared["source"]["source_id"]]
    assert selected["original"] == prepared["member"]["path"]
    receipt = json.loads((output / "originals_ledger.json").read_text())[-1]
    assert receipt["archive_member"] == MEMBER
    assert receipt["intermediates"] == [prepared["archive"]]
    check_pins(json.loads((output / "source_receipts.json").read_text())["files"])
    # Archive only registration; ZIP/PDF/rendition must be followed as inherited custody.
    manifest = preserve(output, tmp_path / "archive.tar.gz", tmp_path)
    originals = {path for row in manifest["members"].values() for path in row["original_paths"]}
    assert {prepared["archive"]["path"], prepared["member"]["path"], prepared["source"]["path"]} <= originals
    assert manifest["status"] == "declared_inputs_preserved"
    with pytest.raises(FileExistsError):
        helper.register(directory / "preparation.json", report, output)


@pytest.mark.parametrize("url,member,source,error", [
    (URL.replace("PMC123", "PMC999"), MEMBER, "primary", "PMCID"),
    (URL, "other.pdf", "primary", "does not cite"),
    (URL, MEMBER, "absent", "registered primary"),
    (URL.replace("https:", "http:"), MEMBER, "primary", "official HTTPS"),
    (URL + "?fake=1", MEMBER, "primary", "official HTTPS"),
])
def test_primary_endpoint_citation_checked_before_download(fixture, tmp_path, url, member, source, error):
    calls = []
    with pytest.raises(ValueError, match=error):
        helper.prepare(fixture[0], source, url, member, tmp_path / "bad",
                       fetch=lambda url: calls.append(url))
    assert not calls


@pytest.mark.parametrize("name", ["../evil.pdf", "/evil.pdf", "a/../protocol.pdf", "a\\evil.pdf",
                                  "C:evil.pdf", "a//evil.pdf", "./evil.pdf"])
def test_reject_unsafe_archive_paths_even_when_not_selected(name):
    raw = zipped([(MEMBER, b"%PDF-fixture"), (name, b"unselected")])
    with pytest.raises(ValueError, match="Unsafe ZIP"):
        helper.selected_pdf(raw, MEMBER)


def test_duplicate_member_names_symlinks_and_encrypted_members_rejected():
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = zipped([(MEMBER, b"%PDF-one"), (MEMBER, b"%PDF-two")])
    with pytest.raises(ValueError, match="Duplicate"):
        helper.selected_pdf(duplicate, MEMBER)
    with pytest.raises(ValueError, match="cited basename"):
        helper.selected_pdf(zipped([(MEMBER, b"%PDF-one"), ("other/protocol.pdf", b"%PDF-two")]), MEMBER)
    link = ZipInfo("link"); link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError, match="Unsafe ZIP member type"):
        helper.selected_pdf(zipped([(MEMBER, b"%PDF-one"), (link, b"target")]), MEMBER)
    encrypted = bytearray(zipped([(MEMBER, b"%PDF-one")]))
    local, central = encrypted.index(b"PK\x03\x04"), encrypted.index(b"PK\x01\x02")
    struct.pack_into("<H", encrypted, local + 6, 1)
    struct.pack_into("<H", encrypted, central + 8, 1)
    with pytest.raises(ValueError, match="Encrypted"):
        helper.selected_pdf(bytes(encrypted), MEMBER)


def test_truncated_crc_bad_html_and_wrong_member_bytes_rejected():
    raw = zipped([(MEMBER, b"%PDF-exact-fixture")])
    corrupted = raw.replace(b"%PDF-exact-fixture", b"%PDF-wrong-fixture", 1)
    for content in (raw[:-12], corrupted):
        with pytest.raises(ValueError, match="CRC-invalid"):
            helper.selected_pdf(content, MEMBER)
    with pytest.raises(ValueError, match="non-ZIP"):
        helper.selected_pdf(b"<html>Access challenge</html>", MEMBER)
    with pytest.raises(ValueError, match="actual PDF"):
        helper.selected_pdf(zipped([(MEMBER, b"<html>wrong bytes</html>")]), MEMBER)
    with pytest.raises(ValueError, match="exact unambiguous"):
        helper.selected_pdf(raw, "missing.pdf")


def test_expanded_archive_limit_and_member_count(monkeypatch):
    raw = zipped([(MEMBER, b"%PDF-" + b"x" * 20_000)], ZIP_DEFLATED)
    monkeypatch.setattr(helper, "MAX_BYTES", 10_000)
    with pytest.raises(ValueError, match="Expanded"):
        helper.selected_pdf(raw, MEMBER)
    monkeypatch.setattr(helper, "MAX_ENTRIES", 1)
    with pytest.raises(ValueError, match="member count"):
        helper.selected_pdf(zipped([(MEMBER, b"%PDF-fixture"), ("other", b"x")]), MEMBER)
    monkeypatch.setattr(helper, "MAX_BYTES", 16)
    with pytest.raises(ValueError, match="oversized"):
        helper.selected_pdf(zipped([(MEMBER, b"%PDF-fixture")]), MEMBER)


def test_host_keeps_invalid_archive_response_without_registering(fixture, tmp_path):
    output = tmp_path / "invalid"
    with pytest.raises(ValueError, match="non-ZIP"):
        helper.prepare(fixture[0], "primary", URL, MEMBER, output,
                       fetch=lambda url: Download(b"<html>challenge</html>", url, "text/html"))
    assert (output / "supplementary_files.zip").read_bytes() == b"<html>challenge</html>"
    assert not (output / "preparation.json").exists()
    assert not (output / "packet.json").exists()


@requires_poppler
@pytest.mark.parametrize("changes,error", [
    ({"decision": "uncertain"}, "uncertain"),
    ({"decision": "conflict"}, "conflicts"),
    ({"expected_variant": "journal v2"}, "another preparation or scope"),
    ({"preparation_sha256": "0" * 64}, "another preparation or scope"),
    ({"inspected_files": {}}, "pin both"),
    ({"content_witness": "Invented protocol statement"}, "witness is absent"),
    ({"version_basis": "   "}, "inspection bases"),
])
def test_inspection_is_required_bound_and_not_silently_waived(fixture, tmp_path, changes, error):
    directory, prepared = prepare(fixture, tmp_path)
    report = inspection(directory, prepared, **changes)
    output = tmp_path / "registration"
    with pytest.raises(ValueError, match=error):
        helper.register(directory / "preparation.json", report, output)
    assert not output.exists()


@requires_poppler
@pytest.mark.parametrize("target", ["archive", "member", "source"])
def test_input_drift_prevents_registration(fixture, tmp_path, target):
    directory, prepared = prepare(fixture, tmp_path)
    report = inspection(directory, prepared)
    Path(prepared[target]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        helper.register(directory / "preparation.json", report, tmp_path / "registration")


@requires_poppler
def test_existing_primary_ledger_is_preserved(fixture, tmp_path):
    directory, prepared = prepare(fixture, tmp_path)
    source = load_packet(fixture[0])["sources"][0]
    row = {"source_id": source["source_id"], "original": source["original_path"],
        "original_sha256": source["original_sha256"], "rendition": source["original_path"],
        "rendition_sha256": source["original_sha256"], "converter": "fixture-native-identity"}
    ledger = save(tmp_path / "old_ledger.json", [row])
    helper.register(directory / "preparation.json", inspection(directory, prepared),
                    tmp_path / "registration", prior_ledger=ledger)
    combined = json.loads((tmp_path / "registration/originals_ledger.json").read_text())
    assert combined[0] == row and len(combined) == 2
    assert load_originals(tmp_path / "registration/original_bindings.json")["unmapped_source_ids"] == []


def test_pmc_article_id_alias_is_normalized_without_changing_registered_identity(fixture):
    packet = load_packet(fixture[0])
    original = Path(packet["sources"][0]["original_path"])
    original.write_text(original.read_text().replace('pub-id-type="pmcid">PMC123', 'pub-id-type="pmc">123'))
    source = packet["sources"][0]
    rebuilt = build_packet(**{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
        sources=[{**{key: source[key] for key in ("source_id", "title", "url", "role")}, "path": str(original)}])
    binding = helper._association(rebuilt, "primary", URL, MEMBER)
    assert binding["pmcid"] == "PMC123"
    assert binding["primary_identity"]["identifiers"]["pmc"] == ["123"]

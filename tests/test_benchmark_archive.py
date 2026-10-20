"""Stopped-phase custody uses local fixtures; no native/model/network execution."""

import json
from pathlib import Path
import tarfile

import pytest

from scripts.benchmark import archive_result as archiver


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "project"
    phase = root / "run/work"
    phase.mkdir(parents=True)
    source = root / "old-cache/selected.txt"
    source.parent.mkdir()
    source.write_bytes(b"Selected original publication content\n")
    packet = write(root / "old-cache/packet.json", {"format_version": "workflow_sources/1.1",
        "sources": [{"original_path": str(source), "original_sha256": archiver.sha(source)}]})
    write(phase / "source_manifest.json", [{"packet": str(packet), "packet_sha256": archiver.sha(packet)}])
    return root, phase, source, packet, root / "durable/result.tar.gz"


def unpack(output):
    with tarfile.open(output) as archive:
        return {member.name: archive.extractfile(member).read() for member in archive.getmembers()}


def test_exact_phase_and_selected_inherited_inputs_only(setup):
    root, phase, source, packet, output = setup
    (phase / "custom_prompt.md").write_text("Bound prompt\n")
    (phase / "native.events.jsonl").write_bytes(b'{"type":"started"}\n{"incomplete":')
    (source.parent / "unselected.pdf").write_bytes(b"Do not include cache siblings")
    before = {path: path.read_bytes() for path in phase.rglob("*") if path.is_file()}
    result = archiver.archive(phase, output, root)
    payload = unpack(output)
    assert payload["phase/native.events.jsonl"] == before[phase / "native.events.jsonl"]
    assert payload["phase/custom_prompt.md"] == b"Bound prompt\n"
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not any("unselected" in member for member in payload)
    for member, pin in result["members"].items():
        assert payload[member] == Path(pin["original_paths"][0]).read_bytes()
        assert pin["sha256"] == archiver.sha(Path(pin["original_paths"][0]))
    assert json.loads(payload["archive_manifest.json"]) == result
    assert result["status"] == "incomplete_binary_provenance"
    assert "not scientific source completeness" in result["boundary"]
    assert {str(source), str(packet)} <= {p for row in result["members"].values() for p in row["original_paths"]}


@pytest.mark.parametrize("kind", ["jats", "html"])
def test_native_publication_markup_does_not_require_an_invented_pdf_ancestor(setup, kind):
    root, phase, source, packet, output = setup
    native = source.with_suffix(".xml" if kind == "jats" else ".html")
    native.write_text('<article><front><article-meta><article-id pub-id-type="pmid">12345</article-id>'
        '<title-group><article-title>Native fixture publication</article-title></title-group>'
        '</article-meta></front><body><p>Original methods.</p></body></article>' if kind == "jats" else
        '<html><head><meta name="citation_title" content="Native fixture publication">'
        '<meta name="citation_doi" content="10.1234/fixture"></head><body>Original methods.</body></html>')
    write(packet, {"format_version": "workflow_sources/1.1", "sources": [
        {"original_path": str(native), "original_sha256": archiver.sha(native)}]})
    write(phase / "source_manifest.json", [{"packet": str(packet), "packet_sha256": archiver.sha(packet)}])
    result = archiver.archive(phase, output, root)
    assert result["provenance_gaps"] == []
    assert result["native_originals"][0]["path"] == str(native)
    assert result["status"] == "declared_inputs_preserved"
    assert not any(member.endswith(".pdf") for member in unpack(output))


def test_xml_with_explicit_derivation_still_requires_its_declared_ancestor(setup):
    root, phase, source, packet, output = setup
    native = source.with_suffix(".xml")
    native.write_text('<article><front><article-meta><article-id>12345</article-id>'
        '<title-group><article-title>Selected fixture article</article-title></title-group>'
        '</article-meta></front></article>')
    write(packet, {"format_version": "workflow_sources/1.1", "sources": [
        {"original_path": str(native), "original_sha256": archiver.sha(native)}]})
    write(phase / "source_manifest.json", [{"packet": str(packet), "packet_sha256": archiver.sha(packet)}])
    write(native.parent / "conversion_receipt.json", {"rendition": str(native),
        "rendition_sha256": archiver.sha(native), "original": str(root / "missing-wrapper.xml"),
        "original_sha256": "1" * 64})
    result = archiver.archive(phase, output, root)
    assert result["native_originals"] == []
    assert result["provenance_gaps"][0]["reason"] == "missing_binary_original"


@pytest.mark.parametrize("kind", ["header", "current", "legacy", "historical"])
def test_binary_ancestor_and_matching_receipt_preserved(setup, kind):
    root, phase, source, packet, output = setup
    original = root / "old-cache/original.pdf"
    original.write_bytes(b"%PDF-exact-original")
    if kind == "header":
        source.write_text("[Rendition provenance, not publication content]\nOriginal cached file: "
            + str(original) + "\nSHA256: " + archiver.sha(original) + "\n\nRendition body")
    elif kind == "current":
        write(source.parent / "originals_ledger.json", [{"original": str(original), "original_sha256": archiver.sha(original),
            "rendition": str(source), "rendition_sha256": archiver.sha(source)},
            {"original": "/unselected/outside.pdf", "rendition": "/unselected/outside.txt"}])
    elif kind == "legacy":
        write(source.parent / "registration.json", {"original_pdf_path": str(original), "original_pdf_sha256": archiver.sha(original),
            "text_rendition_path": str(source), "text_rendition_sha256": archiver.sha(source)})
    else:
        write(phase / "text_rendition_provenance.json", [{"source_path": str(original), "source_sha256": archiver.sha(original),
            "path": str(source), "sha256": archiver.sha(source)}])
    value = json.loads(packet.read_text())
    value["sources"][0]["original_sha256"] = archiver.sha(source)
    write(packet, value)
    write(phase / "source_manifest.json", [{"packet": str(packet), "packet_sha256": archiver.sha(packet)}])
    result = archiver.archive(phase, output, root)
    payload = unpack(output)
    assert original.read_bytes() in payload.values()
    assert result["status"] == "declared_inputs_preserved"
    assert result["provenance_gaps"] == []


def test_missing_binary_ancestor_is_explicit_gap(setup):
    root, phase, source, packet, output = setup
    write(source.parent / "conversion_receipt.json", {"original": str(root / "missing.pdf"), "original_sha256": "1" * 64,
        "rendition": str(source), "rendition_sha256": archiver.sha(source)})
    result = archiver.archive(phase, output, root)
    assert output.is_file()
    assert result["status"] == "incomplete_binary_provenance"
    assert result["provenance_gaps"] == [{"path": str(root / "missing.pdf"), "sha256": "1" * 64,
        "reason": "missing_binary_original"}]


MAC_PROJECT = Path("/Users/mihajlostojkovic/Documents/qiu_lab/agents_snRNA")


def ancestor_header(setup, original, expected):
    _, phase, source, packet, _ = setup
    source.write_text("[Rendition provenance, not publication content]\nOriginal cached file: "
                      + str(original) + "\nSHA256: " + expected + "\n\nPreserved rendition body")
    value = json.loads(packet.read_text())
    value["sources"][0]["original_sha256"] = archiver.sha(source)
    write(packet, value)
    write(phase / "source_manifest.json", [{"packet": str(packet), "packet_sha256": archiver.sha(packet)}])


def test_historical_mac_header_maps_only_to_hash_verified_project_ancestor(setup):
    root, phase, source, _, output = setup
    suffix = Path("technology_atlas/v2/papers/25042786/supplements/NIHMS59406-supplement-2.pdf")
    binary = root / suffix
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"%PDF original preserved through group migration")
    ancestor_header(setup, MAC_PROJECT / suffix, archiver.sha(binary))
    before = source.read_bytes()
    result = archiver.archive(phase, output, root, ancestor_mappings=[(MAC_PROJECT, root)])
    assert source.read_bytes() == before
    assert binary.read_bytes() in unpack(output).values()
    assert result["provenance_gaps"] == []
    assert result["ancestor_mappings"] == [{"original_path": str(MAC_PROJECT / suffix),
        "mapped_path": str(binary), "old_prefix": str(MAC_PROJECT), "new_prefix": str(root),
        "sha256": archiver.sha(binary), "why": f"rendition_header:{source}", "status": "hash_verified"}]


def test_mapped_ancestor_with_different_bytes_is_rejected(setup):
    root, phase, _, _, output = setup
    binary = root / "migrated.pdf"
    binary.write_bytes(b"Wrong migrated binary")
    ancestor_header(setup, MAC_PROJECT / "migrated.pdf", "1" * 64)
    with pytest.raises(ValueError, match="SHA256 changed"):
        archiver.archive(phase, output, root, ancestor_mappings=[(MAC_PROJECT, root)])
    assert not output.exists()


def test_unmapped_absolute_external_ancestor_is_gap_and_never_opened(setup, monkeypatch):
    root, phase, source, _, output = setup
    external = root.parent / "old-original.pdf"
    external.write_bytes(b"Unmapped bytes must not be read by the archiver")
    expected = archiver.sha(external)
    ancestor_header(setup, external, expected)
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == external:
            raise AssertionError("The archiver tried to read an unmapped external ancestor")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    result = archiver.archive(phase, output, root)
    assert output.is_file() and source.read_bytes() in unpack(output).values()
    assert result["status"] == "incomplete_binary_provenance"
    assert result["provenance_gaps"] == [{"path": str(external), "sha256": expected,
        "reason": "external_binary_original_without_mapping"}]
    assert result["ancestor_mappings"] == []


@pytest.mark.parametrize("required", ["packet", "source"])
def test_ancestor_mapping_cannot_authorize_required_packet_or_source_escape(setup, required):
    root, phase, _, packet, output = setup
    external = root.parent / "required-external.json"
    if required == "packet":
        external.write_bytes(packet.read_bytes())
        write(phase / "source_manifest.json", [{"packet": str(external), "packet_sha256": archiver.sha(external)}])
    else:
        external.write_bytes(b"Required source bytes outside project")
        write(packet, {"format_version": "workflow_sources/1.1", "sources": [
            {"original_path": str(external), "original_sha256": archiver.sha(external)}]})
        write(phase / "source_manifest.json", [{"packet": str(packet), "packet_sha256": archiver.sha(packet)}])
    with pytest.raises(ValueError, match="escapes project"):
        archiver.archive(phase, output, root, ancestor_mappings=[(root.parent, root)])
    assert not output.exists()


def test_longest_ancestor_prefix_wins_and_conflicting_equal_prefixes_fail(setup):
    root, phase, _, _, output = setup
    broad, specific = root / "broad", root / "specific"
    broad.mkdir(); specific.mkdir()
    binary = specific / "original.pdf"
    binary.write_bytes(b"%PDF nested migration target")
    ancestor_header(setup, MAC_PROJECT / "pdfs/original.pdf", archiver.sha(binary))
    result = archiver.archive(phase, output, root,
        ancestor_mappings=[(MAC_PROJECT, broad), (MAC_PROJECT / "pdfs", specific)])
    assert result["ancestor_mappings"][0]["mapped_path"] == str(binary)
    with pytest.raises(ValueError, match="Conflicting ancestor mappings"):
        archiver.archive(phase, root / "conflicting.tar.gz", root,
            ancestor_mappings=[(MAC_PROJECT, broad), (MAC_PROJECT, specific)])


def test_mapping_covers_receipt_binary_and_intermediate_without_mutating_receipt(setup):
    root, phase, source, _, output = setup
    binary, intermediate = root / "old-cache/original.doc", root / "old-cache/intermediate.xml"
    binary.write_bytes(b"Original Word bytes")
    intermediate.write_bytes(b"<article>Conversion intermediate</article>")
    receipt = write(source.parent / "conversion_receipt.json", {
        "rendition": str(source), "rendition_sha256": archiver.sha(source),
        "original": str(MAC_PROJECT / "old-cache/original.doc"), "original_sha256": archiver.sha(binary),
        "intermediates": [{"path": str(MAC_PROJECT / "old-cache/intermediate.xml"),
                           "sha256": archiver.sha(intermediate)}]})
    before = receipt.read_bytes()
    result = archiver.archive(phase, output, root, ancestor_mappings=[(MAC_PROJECT, root)])
    assert receipt.read_bytes() == before
    assert {row["mapped_path"] for row in result["ancestor_mappings"]} == {str(binary), str(intermediate)}
    assert result["provenance_gaps"] == []
    assert {binary.read_bytes(), intermediate.read_bytes()} <= set(unpack(output).values())


def test_relative_ancestor_escape_is_not_remapped(setup):
    root, phase, source, _, output = setup
    write(source.parent / "conversion_receipt.json", {"original": "../../outside.pdf",
        "original_sha256": "1" * 64, "rendition": str(source), "rendition_sha256": archiver.sha(source)})
    with pytest.raises(ValueError, match="escapes project"):
        archiver.archive(phase, output, root, ancestor_mappings=[(MAC_PROJECT, root)])


@pytest.mark.parametrize("unsafe", ["symlink", "credential"])
def test_mapped_ancestors_keep_project_symlink_and_credential_restrictions(setup, unsafe):
    root, phase, _, _, output = setup
    if unsafe == "symlink":
        target = root / "real.pdf"
        target.write_bytes(b"%PDF target")
        (root / "mapped.pdf").symlink_to(target)
        ancestor_header(setup, MAC_PROJECT / "mapped.pdf", archiver.sha(target))
    else:
        ancestor_header(setup, MAC_PROJECT / ".codex/auth.json", "1" * 64)
    with pytest.raises(ValueError, match="symlink|Unsafe optional ancestor"):
        archiver.archive(phase, output, root, ancestor_mappings=[(MAC_PROJECT, root)])


def test_acquisition_ledger_above_renditions_is_followed(setup):
    root, phase, source, packet, output = setup
    rendition = source.parent / "renditions/supplement.txt"
    rendition.parent.mkdir()
    rendition.write_bytes(source.read_bytes())
    binary = source.parent / "downloads/supplement.pdf"
    binary.parent.mkdir()
    binary.write_bytes(b"%PDF cached binary")
    write(source.parent / "originals_ledger.json", [{"original": str(binary), "original_sha256": archiver.sha(binary),
        "rendition": str(rendition), "rendition_sha256": archiver.sha(rendition)}])
    write(packet, {"format_version": "workflow_sources/1.1", "sources": [
        {"original_path": str(rendition), "original_sha256": archiver.sha(rendition)}]})
    write(phase / "source_manifest.json", [{"packet": str(packet), "packet_sha256": archiver.sha(packet)}])
    result = archiver.archive(phase, output, root)
    assert binary.read_bytes() in unpack(output).values()
    assert result["status"] == "declared_inputs_preserved"


def test_conflicting_missing_binary_pins_are_fatal(setup):
    root, phase, source, packet, output = setup
    rows = [{"original": str(root / "missing.pdf"), "original_sha256": digit * 64,
        "rendition": str(source), "rendition_sha256": archiver.sha(source)} for digit in ("1", "2")]
    write(source.parent / "originals_ledger.json", rows)
    with pytest.raises(ValueError, match="Conflicting SHA256 pins"):
        archiver.archive(phase, output, root)
    assert not output.exists()


@pytest.mark.parametrize("problem", ["missing", "changed", "conflict"])
def test_bad_declared_source_cannot_publish_archive(setup, problem):
    root, phase, source, packet, output = setup
    if problem == "missing":
        source.unlink()
    elif problem == "changed":
        source.write_bytes(b"changed after registration")
    else:
        value = json.loads(packet.read_text())
        value["sources"].append({"original_path": str(source), "original_sha256": "f" * 64})
        write(packet, value)
        write(phase / "source_manifest.json", [{"packet": str(packet), "packet_sha256": archiver.sha(packet)}])
    with pytest.raises(ValueError):
        archiver.archive(phase, output, root)
    assert not output.exists()


@pytest.mark.parametrize("problem", ["outside", "credential", "symlink"])
def test_forbidden_declared_path_cannot_be_archived(setup, problem):
    root, phase, source, packet, output = setup
    forbidden = root.parent / "private.txt" if problem == "outside" else root / ".codex/auth.json"
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_bytes(b"sensitive")
    if problem == "symlink":
        link = root / "linked.txt"
        link.symlink_to(forbidden)
        forbidden = link
    write(packet, {"format_version": "workflow_sources/1.1", "sources": [
        {"original_path": str(forbidden), "original_sha256": archiver.sha(forbidden)}]})
    write(phase / "source_manifest.json", [{"packet": str(packet), "packet_sha256": archiver.sha(packet)}])
    with pytest.raises(ValueError):
        archiver.archive(phase, output, root)
    assert not output.exists()


def test_duplicate_pins_deduplicate_bytes_and_keep_all_origins(setup):
    root, phase, source, packet, output = setup
    duplicate = root / "old-cache/same-content.txt"
    duplicate.write_bytes(source.read_bytes())
    value = json.loads(packet.read_text())
    value["sources"] += [value["sources"][0], {"original_path": str(duplicate), "original_sha256": archiver.sha(duplicate)}]
    write(packet, value)
    write(phase / "source_manifest.json", [{"packet": str(packet), "packet_sha256": archiver.sha(packet)}] * 2)
    result = archiver.archive(phase, output, root)
    pins = [v for v in result["members"].values() if v["sha256"] == archiver.sha(source)]
    assert len(pins) == 1
    assert set(pins[0]["original_paths"]) == {str(source), str(duplicate)}


def test_change_during_archive_fails_without_publishing(setup, monkeypatch):
    root, phase, source, packet, output = setup
    original_add = tarfile.TarFile.add

    def changing_add(self, name, *args, **kwargs):
        result = original_add(self, name, *args, **kwargs)
        if Path(name) == source:
            source.write_bytes(b"changed while archiving")
        return result

    monkeypatch.setattr(tarfile.TarFile, "add", changing_add)
    with pytest.raises(ValueError, match="changed during archive"):
        archiver.archive(phase, output, root)
    assert not output.exists()
    assert not list(output.parent.glob("*.partial-*"))


def test_tar_bytes_are_checked_even_if_source_is_restored(setup, monkeypatch):
    root, phase, source, packet, output = setup
    original_add = tarfile.TarFile.add

    def transient_change(self, name, *args, **kwargs):
        if Path(name) != source:
            return original_add(self, name, *args, **kwargs)
        content = source.read_bytes()
        source.write_bytes(b"wrong bytes")
        original_add(self, name, *args, **kwargs)
        source.write_bytes(content)

    monkeypatch.setattr(tarfile.TarFile, "add", transient_change)
    with pytest.raises(ValueError, match="Archived bytes differ"):
        archiver.archive(phase, output, root)
    assert not output.exists()


def test_existing_archive_and_output_inside_phase_are_rejected(setup):
    root, phase, source, packet, output = setup
    output.parent.mkdir()
    output.write_bytes(b"previous result")
    with pytest.raises(FileExistsError):
        archiver.archive(phase, output, root)
    assert output.read_bytes() == b"previous result"
    with pytest.raises(ValueError, match="outside the experiment"):
        archiver.archive(phase, phase / "result.tar", root)

"""Explicit source-original bindings archive fixture bytes without guessing."""

import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from scripts.benchmark import archive_result as archiver
from genomics_discovery.curation.source_originals import bind_originals
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.sources import build_packet


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def binding(tmp_path):
    root = tmp_path / "project"
    phase = root / "phase"
    phase.mkdir(parents=True)
    source = root / "legacy/rendition.txt"
    source.parent.mkdir()
    source.write_text("Registered legacy text; no embedded original-file declaration.")
    binary = root / "downloads/original.pdf"
    binary.parent.mkdir()
    binary.write_bytes(b"%PDF-1.4\nExact retained fixture binary, never rendered.\n")
    packet = write(root / "packets/selected.json", build_packet(method_id="fixture", title="Fixture assay",
        scope="Declared fixture scope", variant="original", sources=[{"source_id": "legacy-source",
            "title": "Fixture source", "url": "https://example.org/source", "role": "primary_paper", "path": str(source)}]))
    ledger = write(root / "documentary-proof/arbitrary-links.json", [{"source_id": "legacy-source",
        "rendition": str(source), "rendition_sha256": archiver.sha(source), "original": str(binary),
        "original_sha256": archiver.sha(binary)}, {"source_id": "unselected", "rendition": "/unselected/data.txt",
        "original": "/unselected/data.pdf", "original_sha256": "f" * 64}])
    artifact = write(root / "declared-inputs/arbitrary-binding.json", bind_originals(packet, ledger))
    manifest = write(phase / "cases.json", {"cases": [{"case_id": "fixture", "method_id": "fixture",
        "packet": str(packet), "packet_sha256": archiver.sha(packet),
        "original_bindings": {"path": str(artifact), "sha256": archiver.sha(artifact)}}]})
    return SimpleNamespace(root=root, phase=phase, source=source, binary=binary, packet=packet,
        ledger=ledger, artifact=artifact, manifest=manifest, output=root / "durable/archive.tar.gz")


def archive(fixture):
    return archiver.archive(fixture.phase, fixture.output, fixture.root)


def reseal(fixture, value):
    value["revision_id"] = digest({key: item for key, item in value.items() if key != "revision_id"})
    write(fixture.artifact, value)
    manifest = json.loads(fixture.manifest.read_text())
    manifest["cases"][0]["original_bindings"]["sha256"] = archiver.sha(fixture.artifact)
    write(fixture.manifest, manifest)


def test_explicit_outside_ledger_binding_closes_only_exact_legacy_source_gap(binding):
    f = binding
    before = {path: path.read_bytes() for path in f.root.rglob("*") if path.is_file()}
    result = archive(f)
    assert result["status"] == "declared_inputs_preserved" and result["provenance_gaps"] == []
    assert result["original_bindings"][0]["source_ids"] == ["legacy-source"]
    preserved = {path for item in result["members"].values() for path in item["original_paths"]}
    assert {str(path) for path in (f.source, f.binary, f.packet, f.ledger, f.artifact)} <= preserved
    assert not any("unselected" in path for path in preserved)
    assert all(path.read_bytes() == value for path, value in before.items())
    with tarfile.open(f.output) as tar:
        for member, item in result["members"].items():
            assert tar.extractfile(member).read() == Path(item["original_paths"][0]).read_bytes()


def test_top_level_typed_phase_artifact_is_an_explicit_binding_input(binding):
    f = binding
    manifest = json.loads(f.manifest.read_text())
    manifest["cases"][0].pop("original_bindings")
    write(f.manifest, manifest)
    copied = f.phase / "typed-originals.json"
    copied.write_bytes(f.artifact.read_bytes())
    result = archive(f)
    assert result["status"] == "declared_inputs_preserved"
    assert result["original_bindings"][0]["path"] == str(copied)


def test_unlisted_outside_binding_is_not_discovered_by_filename_or_proximity(binding):
    f = binding
    manifest = json.loads(f.manifest.read_text())
    manifest["cases"][0].pop("original_bindings")
    write(f.manifest, manifest)
    result = archive(f)
    assert result["status"] == "incomplete_binary_provenance"
    assert result["original_bindings"] == []
    preserved = {path for item in result["members"].values() for path in item["original_paths"]}
    assert str(f.artifact) not in preserved and str(f.binary) not in preserved


@pytest.mark.parametrize("target", ["binary", "ledger", "artifact", "source"])
def test_changed_explicit_original_inputs_cannot_publish_archive(binding, target):
    f = binding
    path = getattr(f, target)
    path.write_bytes(path.read_bytes() + b"\nchanged after binding")
    with pytest.raises(ValueError):
        archive(f)
    assert not f.output.exists()


def test_resealed_forged_source_association_still_has_to_replay(binding):
    f = binding
    value = json.loads(f.artifact.read_text())
    value["bindings"]["foreign-source"] = value["bindings"].pop("legacy-source")
    reseal(f, value)
    with pytest.raises(ValueError, match="do not replay"):
        archive(f)
    assert not f.output.exists()


def test_binding_cannot_inject_an_undeclared_packet_or_its_sources(binding):
    f = binding
    other = f.root / "other.txt"
    other.write_text("Unselected source must not become an archive packet input.")
    extra_packet = write(f.root / "other-packet.json", build_packet(method_id="foreign", title="Other",
        scope="Other scope", variant="other", sources=[{"source_id": "foreign", "title": "Other source",
            "url": "https://example.org/other", "role": "primary_paper", "path": str(other)}]))
    value = bind_originals(extra_packet, f.ledger)
    reseal(f, value)
    with pytest.raises(ValueError, match="undeclared source packet"):
        archive(f)
    assert not f.output.exists()


def test_ledger_replay_cannot_read_an_unbound_external_original_even_with_a_map(binding, monkeypatch):
    f = binding
    external = f.root.parent / "outside.pdf"
    external.write_bytes(b"Do not open an external original from a forged ledger")
    ledger = json.loads(f.ledger.read_text())
    ledger[0].update(original=str(external), original_sha256=archiver.sha(external))
    write(f.ledger, ledger)
    value = json.loads(f.artifact.read_text())
    value["input_files"][str(f.ledger)] = archiver.sha(f.ledger)
    reseal(f, value)
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == external:
            pytest.fail("Unbound external original was opened during helper replay")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(ValueError, match="escapes project"):
        archiver.archive(f.phase, f.output, f.root, ancestor_mappings=[(f.root.parent, f.root)])
    assert not f.output.exists()

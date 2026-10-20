"""Relocation preserves evidence, and never carries a scientific acceptance forward."""

import hashlib
import json
import shutil

import pytest

from genomics_discovery.curation.source_relocation import register_relocated_packet
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet


@pytest.fixture
def migration(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    raw = old / "source.txt"
    raw.write_text("Recover tagged fragments.\n\nRetain the sample index.")
    packet = build_packet(method_id="example", title="Example", scope="General procedure",
        variant="original", sources=[{"source_id": "main", "title": "Paper",
        "role": "primary_paper", "url": "https://example.org/paper", "path": str(raw)}])
    saved = tmp_path / "historical_packet.json"
    saved.write_text(json.dumps(packet))
    shutil.copyfile(raw, new / raw.name)
    raw.unlink()  # The historical host's original is not accessible on the new host.
    return old, new, saved, tmp_path / "registration"


def test_re_registration_uses_copied_bytes_and_preserves_historical_record(migration):
    old, new, saved, output = migration
    before = saved.read_bytes()
    receipt = register_relocated_packet(saved, old_root=old, new_root=new, output=output)
    packet = load_packet(output / "packet.json")
    assert saved.read_bytes() == before
    assert packet["sources"][0]["original_path"] == str(new / "source.txt")
    assert packet["sources"][0]["passages"] == json.loads(before)["sources"][0]["passages"]
    assert receipt["historical_packet_sha256"] == hashlib.sha256(before).hexdigest()
    assert receipt["packet_sha256"] == hashlib.sha256((output / "packet.json").read_bytes()).hexdigest()
    assert "No source-coverage, review, acceptance" in receipt["boundary"]
    with pytest.raises(FileExistsError):
        register_relocated_packet(saved, old_root=old, new_root=new, output=output)


@pytest.mark.parametrize("mutation", ["bytes", "passage", "metadata", "root", "symlink"])
def test_mismatch_and_path_escape_cannot_create_a_registration(migration, tmp_path, mutation):
    old, new, saved, output = migration
    packet = json.loads(saved.read_bytes())
    if mutation == "bytes":
        (new / "source.txt").write_text("A different source.")
    elif mutation == "passage":
        packet["sources"][0]["passages"][0]["text"] = "Invented reaction."
    elif mutation == "metadata":
        packet["sources"][0]["document_identity"] = {"title": "Invented identity"}
    elif mutation == "root":
        packet["sources"][0]["original_path"] = str(tmp_path / "old-other/source.txt")
    else:
        outside = tmp_path / "outside.txt"
        shutil.move(str(new / "source.txt"), outside)
        (new / "source.txt").symlink_to(outside)
    saved.write_text(json.dumps(packet))
    with pytest.raises(ValueError):
        register_relocated_packet(saved, old_root=old, new_root=new, output=output)
    assert not output.exists()


def test_relative_roots_are_rejected(migration):
    from pathlib import Path
    old, new, saved, output = migration
    with pytest.raises(ValueError, match="absolute"):
        register_relocated_packet(saved, old_root=Path("old"), new_root=new, output=output)

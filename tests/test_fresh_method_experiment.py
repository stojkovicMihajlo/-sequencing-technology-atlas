"""Regression for the real two-column PDF identity failure in the fresh-method pilot."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from genomics_discovery.curation.extraction_batch.storage import sha


@pytest.fixture
def experiment():
    path = Path(__file__).parents[1] / "experiments/scstamp_seq_20261021/recover_and_extract.py"
    spec = importlib.util.spec_from_file_location("fresh_method_experiment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dependency(tmp_path):
    acquisition, recovery = tmp_path / "acquisition", tmp_path / "recovery"
    acquisition.mkdir(); recovery.mkdir()
    original = acquisition / "original.pdf"
    original.write_bytes(b"%PDF-1.4\nMock transport bytes; converter outputs supplied by test.\n")
    row = {"id": "example-paper", "role": "cited_protocol", "format": "pdf",
           "status": "retrieved_unregistered", "path": str(original),
           "original_sha256": sha(original), "final_url": "https://example.org/paper.pdf",
           "identity": {"title": "Example assay engineering for sequencing", "doi": "10.1234/example"}}
    (acquisition / "acquisition.json").write_text(json.dumps({"documents": [row]}))
    return acquisition, recovery


def test_identity_uses_reading_order_and_retains_layout_rendition(experiment, tmp_path, monkeypatch):
    acquisition, recovery = dependency(tmp_path)
    layout = "Example assay Adjacent column paragraph\nengineering for sequencing\ndoi:10.1234/example"
    reading = "Example assay engineering for sequencing\ndoi:10.1234/example"
    commands = []

    def convert(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=(layout if "-layout" in command else reading).encode())

    monkeypatch.setattr(experiment.subprocess, "run", convert)
    descriptors, ledger = experiment.prepare_dependencies(acquisition, recovery)
    assert "-layout" in commands[0] and "-layout" not in commands[1]
    assert layout in Path(descriptors[0]["path"]).read_text()
    decision = json.loads((recovery / "dependency_registration.json").read_bytes())[0]
    identity = decision["identity_check"]
    assert identity["full_title_match"] and identity["printed_identifier"] == "doi"
    assert Path(identity["reading_order_path"]).read_text() == reading
    assert sha(Path(identity["reading_order_path"])) == identity["reading_order_sha256"]
    assert ledger[0]["command"] == commands[0]


def test_reading_order_does_not_relax_doi_guard(experiment, tmp_path, monkeypatch):
    acquisition, recovery = dependency(tmp_path)
    text = "Example assay engineering for sequencing\ndoi:10.1234/different"
    monkeypatch.setattr(experiment.subprocess, "run",
                        lambda *args, **kwargs: SimpleNamespace(stdout=text.encode()))
    with pytest.raises(ValueError, match="DOI"):
        experiment.prepare_dependencies(acquisition, recovery)
    assert not (recovery / "dependency_registration.json").exists()


def test_restart_rejects_used_extraction_destination_before_source_or_model_work(experiment, tmp_path):
    destination = tmp_path / "extraction/run_001"
    destination.mkdir(parents=True)
    (destination / "state.json").write_text("preserved")
    with pytest.raises(ValueError, match="destination must be fresh"):
        asyncio.run(experiment.main(tmp_path, recovery_attempt=2))
    assert not (tmp_path / "source_recovery").exists()

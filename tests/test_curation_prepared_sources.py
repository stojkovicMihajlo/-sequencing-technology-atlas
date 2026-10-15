"""Prepared documents bound retrieval, never relax independent review or evidence."""

from copy import deepcopy
from pathlib import Path

import pytest
import test_curation_harness as fixtures

from genomics_discovery.curation import harness
from genomics_discovery.curation.prepared_sources import FORMAT, validate_packet


class PreparedRegistrationWorker(fixtures.FakeWorker):
    def __init__(self, *, mutation=None):
        super().__init__()
        self.mutation = mutation

    async def run(self, prompt, attempt):
        result = await super().run(prompt, attempt)
        if attempt.name.endswith("extractor"):
            lead = harness.read(attempt.parents[2] / "prepared_sources.json")[
                "methods"]["method-0"][0]
            document = harness.read(attempt / "extraction.json")
            meta = {"title": lead["title"], "primary_url": lead["url"],
                    "inspected_url": lead["url"], "sections_inspected": ["Methods"],
                    "access_limits": [], "local_evidence": [f["path"] for f in lead["files"]],
                    "sha256": {f["path"]: f["sha256"] for f in lead["files"]},
                    "artifact_roles": {f["path"]: f["role"] for f in lead["files"]}}
            document["sources"] = [{"source_id": lead["source_id"], "source_metadata": meta}]
            document["profiles"][0]["modules"][0]["source_refs"][0][
                "source_id"] = lead["source_id"]
            if self.mutation == "omit_view":
                file = meta["local_evidence"].pop()
                meta["sha256"].pop(file)
                meta["artifact_roles"].pop(file)
            elif self.mutation == "changed_title":
                meta["title"] = "Different identity"
            else:
                image = attempt / "converted.png"
                image.write_bytes(b"Synthetic converter output; not a scientific image.")
                meta["local_evidence"].append(str(image))
                meta["sha256"][str(image)] = harness.sha(image)
                meta["artifact_roles"][str(image)] = "source_rendition"
                provenance = {"path": str(image), "sha256": harness.sha(image),
                              "source_path": lead["files"][0]["path"],
                              "source_sha256": lead["files"][0]["sha256"],
                              "parser": "synthetic test converter", "parser_version": "1"}
                if self.mutation == "wrong_parent":
                    provenance["source_sha256"] = "0" * 64
                elif self.mutation == "missing_version":
                    provenance.pop("parser_version")
                elif self.mutation == "malformed_group":
                    meta["rendition_provenance"] = [provenance]
                meta["image_renditions"] = [provenance]
            harness.write(attempt / "extraction.json", document)
        else:
            state = harness.read(attempt.parents[2] / "state.json")
            document = harness.read(attempt / "review.json")
            extraction = harness.read(Path(
                state["jobs"]["method-0"]["extraction"]))
            meta = extraction["sources"][0]["source_metadata"]
            document["source_checks"][0]["files"] = [
                {"path": path, "sha256": meta["sha256"][path]}
                for path in meta["local_evidence"]]
            harness.write(attempt / "review.json", document)
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [None, "omit_view", "changed_title", "wrong_parent",
                                     "missing_version", "malformed_group"])
async def test_prepared_source_integrity_rejects_before_scientific_review(tmp_path, mutation):
    run, atlas, manifest, _, packet = setup_case(tmp_path)
    view = atlas / "papers/provided-page.png"
    view.write_bytes(b"Synthetic supplied rendition; preservation fixture only.")
    packet["methods"]["method-0"][0]["files"].append({
        "path": str(view), "sha256": harness.sha(view), "role": "source_rendition"})
    harness.write(manifest, packet)
    harness.initialize(run, atlas, prepared_sources=manifest, max_attempts=1)
    config = harness.read(run / "config.json")
    assert config["prepared_registration_policy"] == "preserve-supplied-sources/1.0"
    worker = PreparedRegistrationWorker(mutation=mutation)
    result = await harness.execute(run, worker=worker)
    if mutation is None:
        assert result["counts"] == {"accepted": 1}
        assert len(worker.calls) == 2
        assert harness.export_accepted(run)["profiles"] == 1
    else:
        assert result["counts"] == {"failed": 1}
        assert [c[1] for c in worker.calls] == ["extractor"]
        candidate = worker.calls[0][2] / "extraction.json"
        assert not harness.check_artifact(run, "method-0", candidate, "extractor")["valid"]
        assert harness.export_accepted(run)["profiles"] == 0
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    policy = harness.review_policy(config, job, {"coverage.py": "bound-implementation"})
    assert policy["prepared_registration_policy"] == config["prepared_registration_policy"]
    assert policy["implementation"]["coverage.py"] == "bound-implementation"
    assert Path(job["attempts"][0]["directory"]) == worker.calls[0][2]


def test_legacy_prepared_runs_do_not_silently_inherit_new_integrity_policy(tmp_path):
    run, atlas, manifest, _, _ = setup_case(tmp_path)
    harness.initialize(run, atlas, prepared_sources=manifest)
    config = harness.read(run / "config.json")
    config.pop("prepared_registration_policy")
    assert "prepared_sources" not in harness.validation_options(run, config)


def test_cached_download_interstitial_cannot_be_prepared_as_a_supplement(tmp_path):
    run, atlas, manifest, _, packet = setup_case(tmp_path)
    screen = atlas / "papers/supplement-response.html"
    screen.write_text('<html><head><title>Preparing to download ...</title></head>'
                      '<body><h1>Preparing to download ...</h1></body></html>')
    lead = packet["methods"]["method-0"][0]
    lead.update(title="Supplement Table S2", url="https://example.org/supplement.xlsx",
                files=[{"path": str(screen), "sha256": harness.sha(screen),
                        "role": "source_document"}])
    harness.write(manifest, packet)
    with pytest.raises(ValueError, match="Access challenge"):
        harness.initialize(run, atlas, prepared_sources=manifest)
    assert not run.exists()


def setup_case(tmp_path):
    prior, atlas = fixtures.make_run.__wrapped__(tmp_path)()
    source = atlas / "papers/original.txt"
    source.parent.mkdir()
    source.write_text("Original scientific source, methods section.")
    packet = {"format_version": FORMAT, "methods": {"method-0": [{
        "source_id": "original", "title": "Original document",
        "url": "https://example.org/original", "files": [{
            "path": str(source), "sha256": harness.sha(source), "role": "source_document",
        }],
    }]}}
    manifest = atlas / "prepared.json"
    harness.write(manifest, packet)
    return prior.with_name("prepared-run"), atlas, manifest, source, packet


def test_prepared_packet_freezes_sources_and_binds_review_context(tmp_path):
    run, atlas, manifest, source, packet = setup_case(tmp_path)
    harness.initialize(run, atlas, prepared_sources=manifest, source_only=True)
    config, state = harness.read(run / "config.json"), harness.read(run / "state.json")
    assert config["prepared_sources_sha256"] == harness.sha(run / "prepared_sources.json")
    task = harness.read(run / "jobs/method-0/input.json")
    assert task["prepared_sources"] == packet["methods"]["method-0"]
    policy = harness.review_policy(config, state["jobs"]["method-0"], {})
    assert policy["prepared_sources_sha256"] == config["prepared_sources_sha256"]
    harness.check_frozen(run, state, config)
    manifest.write_text("changed preparation notes")
    harness.check_frozen(run, state, config)
    source.write_text("Changed original bytes")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        harness.check_frozen(run, state, config)


@pytest.mark.parametrize("case", ["empty", "unknown_method", "answer", "outside", "hash",
                                  "role", "summary", "duplicate", "relative"])
def test_invalid_prepared_sources_fail_before_run_creation(tmp_path, case):
    run, atlas, manifest, source, packet = setup_case(tmp_path)
    lead = packet["methods"]["method-0"][0]
    file = lead["files"][0]
    if case == "empty":
        packet["methods"]["method-0"] = []
    elif case == "unknown_method":
        packet["methods"]["different"] = packet["methods"].pop("method-0")
    elif case in {"answer", "outside"}:
        other = (atlas / "extraction.json" if case == "answer" else tmp_path / "outside.txt")
        other.write_text(source.read_text())
        file["path"] = str(other)
    elif case == "hash":
        file["sha256"] = "0" * 64
    elif case == "role":
        file["role"] = "agent_summary"
    elif case == "summary":
        lead["previous_protocol"] = "a prior answer"
    elif case == "duplicate":
        packet["methods"]["method-0"].append(deepcopy(lead))
    else:
        file["path"] = "papers/original.txt"
    harness.write(manifest, packet)
    with pytest.raises(ValueError):
        harness.initialize(run, atlas, prepared_sources=manifest)
    assert not run.exists()


def test_prepared_source_packet_tampering_is_rejected(tmp_path):
    run, atlas, manifest, _, _ = setup_case(tmp_path)
    harness.initialize(run, atlas, prepared_sources=manifest)
    packet = harness.read(run / "prepared_sources.json")
    packet["methods"]["method-0"][0]["title"] = "Changed title"
    harness.write(run / "prepared_sources.json", packet)
    with pytest.raises(ValueError, match="Frozen prepared sources changed"):
        harness.check_frozen(
            run, harness.read(run / "state.json"), harness.read(run / "config.json"))


@pytest.mark.asyncio
async def test_prepared_mode_disables_retrieval_for_both_roles_and_still_requires_review(
    tmp_path, monkeypatch
):
    run, atlas, manifest, _, _ = setup_case(tmp_path)
    harness.initialize(run, atlas, prepared_sources=manifest, max_attempts=1)
    worker = fixtures.FakeWorker()
    configured = {}

    def create_worker(**kwargs):
        configured.update(kwargs)
        return worker

    monkeypatch.setattr(harness, "CodexWorker", create_worker)
    result = await harness.execute(run)
    assert configured["web_search"] is False
    assert configured["network_access"] is False
    assert len(worker.calls) == 2
    assert result["counts"] == {"accepted": 1}
    for call in worker.calls:
        prompt = call[3]
        assert "PREPARED-SOURCE PASS" in prompt
        assert "Missing essential evidence remains blocking" in prompt
        assert "do not fill from memory" in prompt
        assert "do not limit retrieval to Europe PMC" not in prompt
    assert "Reopen the primary passages supporting every module" in worker.calls[1][3]


async def test_prepared_seeds_can_opt_into_discovery_without_relaxing_integrity(
    tmp_path, monkeypatch
):
    run, atlas, manifest, _, _ = setup_case(tmp_path)
    harness.initialize(run, atlas, prepared_sources=manifest, timeout=None,
                       allow_source_discovery=True, max_attempts=1)
    worker, configured = fixtures.FakeWorker(), {}

    def create_worker(**kwargs):
        configured.update(kwargs)
        return worker

    monkeypatch.setattr(harness, "CodexWorker", create_worker)
    result = await harness.execute(run)
    assert configured["web_search"] is configured["network_access"] is True
    assert configured["timeout_seconds"] is None
    assert result["counts"] == {"accepted": 1}
    assert len(worker.calls) == 2
    for call in worker.calls:
        assert "CHECKED SOURCE SEEDS WITH PRIMARY-SOURCE DISCOVERY" in call[3]
        assert "disabled for this prepared-source pass" not in call[3]
        assert "This session has a hard limit" not in call[3]
    assert harness.read(run / "config.json")["prepared_registration_policy"] == (
        "preserve-supplied-sources/1.0")


def test_prepared_packet_validation_does_not_modify_input(tmp_path):
    _, atlas, _, _, packet = setup_case(tmp_path)
    before = deepcopy(packet)
    validate_packet(packet, atlas, ["method-0"])
    assert packet == before

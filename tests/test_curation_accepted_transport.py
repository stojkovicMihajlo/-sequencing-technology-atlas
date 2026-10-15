"""Admission gates bind current native archives without inventing provenance.

Synthetic workers and CLI-shaped fixtures test software enforcement, not real
model calls or scientific correctness. All fault mutations use temporary files.
"""

import asyncio
import contextlib
import io
from copy import deepcopy
from pathlib import Path

import pytest
from test_curation_codex_worker import fake_codex
from test_curation_coverage import scoped_case
from test_curation_scientific_detail_runtime import DetailedWorker

from genomics_discovery.curation import graph_import, harness
from genomics_discovery.curation.accepted_transport import (
    INJECTED,
    NATIVE,
    native_archive_hashes,
    validate_accepted_transports,
)
from genomics_discovery.curation.codex_worker import CodexWorker
from genomics_discovery.curation.coverage import _record_pointer
from genomics_discovery.curation.validation import validate_review


@pytest.fixture
def case(tmp_path):
    with contextlib.redirect_stdout(io.StringIO()):
        run, atlas, _, _ = scoped_case(
            tmp_path, connection_reviews=True, scientific_detail_reviews=True)
        run, atlas = run.resolve(), atlas.resolve()
        result = asyncio.run(harness.execute(run, worker=DetailedWorker()))
    assert result["counts"] == {"accepted": 1}
    config, state = harness.read(run / "config.json"), harness.read(run / "state.json")
    return run, atlas, config, state["jobs"]["method-0"]


def native_shape(job, config, *, marked=True):
    """Create a declared synthetic CLI-shaped archive, without any process."""
    job = deepcopy(job)
    directory = Path(job["review"]).parent
    attempt = next(a for a in job["attempts"] if Path(a["directory"]) == directory)
    if marked:
        attempt["worker_transport"] = NATIVE
    else:
        attempt.pop("worker_transport", None)
    argv = CodexWorker("synthetic-unused-cli", model=config["model"], effort=config["effort"],
                       web_search=False, network_access=False)._command(directory)
    harness.write(directory / "command.json", {"argv": argv, "cwd": str(directory)})
    harness.write(directory / "started.json", {"spawned": True})
    if marked:
        attempt["native_archive_sha256"] = native_archive_hashes(directory)
        attempt["native_archive_pin_origin"] = "synthetic_control_not_real_native"
    return job, attempt, directory


@pytest.mark.parametrize("suffix", ["", "/data_or_material_product", "/basis"])
def test_exact_substantive_internal_handoff_is_a_valid_coverage_pointer(case, suffix):
    run, _, _, job = case
    extraction = harness.read(Path(job["extraction"]))
    _record_pointer(extraction, "/profiles/0/modules/0/internal_handoffs/0" + suffix)
    assert "scientific_detail_policy" in harness.read(run / "config.json")


@pytest.mark.parametrize("suffix", ["/source_step_index", "/target_step_index",
                                     "/source_step_sha256", "/target_step_sha256",
                                     "/source_refs/0/locator", "/relation"])
def test_identity_citation_or_type_alone_cannot_discharge_handoff_requirement(case, suffix):
    extraction = harness.read(Path(case[3]["extraction"]))
    with pytest.raises(ValueError, match="substantive scientific content"):
        _record_pointer(extraction, "/profiles/0/modules/0/internal_handoffs/0" + suffix)


def test_handoff_conditions_and_whole_list_are_substantive_but_empty_condition_is_not(case):
    extraction = harness.read(Path(case[3]["extraction"]))
    root = "/profiles/0/modules/0/internal_handoffs"
    _record_pointer(extraction, root)
    with pytest.raises(ValueError, match="empty content"):
        _record_pointer(extraction, root + "/0/condition")
    extraction["profiles"][0]["modules"][0]["internal_handoffs"][0]["condition"] = (
        "When the synthetic signal is recorded.")
    _record_pointer(extraction, root + "/0/condition")


def test_exact_handoff_pointer_survives_full_opt_in_review_and_import(case, tmp_path):
    run, atlas, config, job = case
    review = harness.read(Path(job["review"]))
    review["source_to_record_check"]["item_checks"][0]["record_paths"] = [
        "/profiles/0/modules/0/internal_handoffs/0/data_or_material_product"]
    path = tmp_path / "exact.json"
    harness.write(path, review)
    assert validate_review(path, Path(job["extraction"]), "method-0", (run, atlas, tmp_path),
                           source_checklist=Path(job["source_checklist"]),
                           **harness.validation_options(run, config)) == []
    assert graph_import.export_graph_candidate(run)["profiles"] == 1


class ReservedWorker(DetailedWorker):
    async def run(self, prompt, attempt):
        result = await super().run(prompt, attempt)
        if attempt.name.endswith("_extractor"):
            path = attempt / "extraction.json"
            value = harness.read(path)
            value["profiles"][0]["verification"] = {"outcome": "worker-owned claim"}
            harness.write(path, value)
        return result


def test_host_owned_verification_fails_before_review_or_candidate_work(tmp_path):
    with contextlib.redirect_stdout(io.StringIO()):
        run, _, _, _ = scoped_case(
            tmp_path, connection_reviews=True, scientific_detail_reviews=True)
        worker = ReservedWorker()
        result = asyncio.run(harness.execute(run, worker=worker))
    assert result["counts"] == {"failed": 1}
    assert [call[1] for call in worker.calls] == ["source_checklist", "extractor"]
    assert harness.export_accepted(run)["profiles"] == 0


def test_injected_workers_are_explicit_and_do_not_become_native(case):
    run, _, config, job = case
    assert all(a["worker_transport"] == INJECTED for a in job["attempts"])
    rows = validate_accepted_transports(job, config)
    assert all(not row["native_completion_established"] for row in rows)
    (Path(job["review"]).parent / "events.jsonl").unlink()
    exported = harness.export_accepted(run)
    assert exported["profiles"] == 1
    assert "native transport not established" in harness.read(Path(exported["output"]))[
        "profiles"][0]["verification"]["reviewer"]
    assert graph_import.export_graph_candidate(run)["profiles"] == 1


def test_legacy_unmarked_test_worker_is_compatible_without_native_claim(case):
    _, _, config, job = case
    for attempt in job["attempts"]:
        attempt.pop("worker_transport")
    rows = validate_accepted_transports(job, config)
    assert all(row["kind"] == "unclassified_legacy_worker" for row in rows)
    assert not any(row["native_completion_established"] for row in rows)


def test_injected_review_cannot_be_relabeled_as_native_proof(case):
    run, _, config, _ = case
    bundle = harness.read(Path(harness.export_accepted(run)["output"]))
    bundle["profiles"][0]["verification"]["reviewer"] = "independent Codex session"
    with pytest.raises(ValueError, match="stale review provenance"):
        graph_import._check_bundle(run, harness.read(run / "state.json"), config, bundle)


@pytest.mark.parametrize("marked", [True, False])
def test_complete_cli_shaped_archive_and_incomplete_terminal_fault(case, marked):
    _, _, config, job = case
    job, _, directory = native_shape(job, config, marked=marked)
    rows = validate_accepted_transports(job, config)
    row = next(row for row in rows if row["role"] == "independent_verifier")
    assert row["native_completion_established"] is True
    assert row["host_archive_hashes_established"] is marked
    result = harness.read(directory / "result.json")
    result.update(exit_code=124, error="Synthetic stopped attempt")
    harness.write(directory / "result.json", result)
    with pytest.raises(ValueError, match="Incomplete native archive"):
        validate_accepted_transports(job, config)


def test_native_original_hash_binding_catches_still_complete_archive_rewrite(case):
    _, _, config, job = case
    job, _, directory = native_shape(job, config)
    (directory / "final.txt").write_text("Changed but still nonempty synthetic final.")
    with pytest.raises(ValueError, match="original host completion hashes"):
        validate_accepted_transports(job, config)


def test_new_native_marker_without_completion_hashes_fails(case):
    _, _, config, job = case
    job, attempt, _ = native_shape(job, config)
    attempt.pop("native_archive_sha256")
    with pytest.raises(ValueError, match="lacks original completion archive hashes"):
        validate_accepted_transports(job, config)


def test_native_command_binding_and_unknown_transport_fail_closed(case):
    _, _, config, job = case
    job, attempt, directory = native_shape(job, config)
    command = harness.read(directory / "command.json")
    command["argv"][-1] = "different-input"
    harness.write(directory / "command.json", command)
    with pytest.raises(ValueError, match="differs from frozen policy"):
        validate_accepted_transports(job, config)
    attempt["worker_transport"] = "invented-transport"
    with pytest.raises(ValueError, match="Unknown host worker_transport"):
        validate_accepted_transports(job, config)


def test_export_and_direct_bundle_admission_recheck_declared_native(case):
    run, _, config, job = case
    job, _, directory = native_shape(job, config)
    state = harness.read(run / "state.json")
    state["jobs"]["method-0"] = job
    harness.write(run / "state.json", state)
    bundle = harness.read(Path(harness.export_accepted(run)["output"]))
    result = harness.read(directory / "result.json")
    result.update(exit_code=124, error="Synthetic terminal fault")
    harness.write(directory / "result.json", result)
    with pytest.raises(ValueError, match="Incomplete native archive"):
        harness.export_accepted(run)
    with pytest.raises(ValueError, match="Incomplete native archive"):
        graph_import._check_bundle(run, state, config, bundle)


@pytest.mark.parametrize("previously_pinned", [True, False])
def test_completed_archive_recovery_preserves_existing_or_establishes_absent_pin(
        case, previously_pinned):
    run, _, config, job = case
    job, attempt, _ = native_shape(job, config)
    existing_hashes = deepcopy(attempt["native_archive_sha256"])
    if not previously_pinned:
        attempt.pop("native_archive_sha256")
        attempt.pop("native_archive_pin_origin")
    job["status"] = "verifying"
    job.pop("review")
    job.pop("review_sha256")
    state = harness.read(run / "state.json")
    state["jobs"]["method-0"] = job
    records = harness._recover_completed(run, state, config, [("method-0", job)], {})
    assert len(records) == 1 and job["status"] == "accepted"
    rows = validate_accepted_transports(job, config)
    reviewer = next(row for row in rows if row["role"] == "independent_verifier")
    assert reviewer["host_archive_hashes_established"] is True
    assert reviewer["archive_sha256"] == existing_hashes
    assert reviewer["archive_pin_origin"] == (
        "synthetic_control_not_real_native" if previously_pinned else "completed_archive_recovery")


@pytest.mark.parametrize("fault", ["rewritten_final", "empty_pin", "null_pin"])
def test_recovery_cannot_replace_an_existing_pin_after_still_complete_archive_drift(case, fault):
    run, _, config, job = case
    job, attempt, directory = native_shape(job, config)
    if fault == "rewritten_final":
        (directory / "final.txt").write_text("Rewritten but still completed synthetic final.")
    else:
        attempt["native_archive_sha256"] = {} if fault == "empty_pin" else None
    job["status"] = "verifying"
    job.pop("review")
    job.pop("review_sha256")
    state = harness.read(run / "state.json")
    state["jobs"]["method-0"] = job
    harness.write(run / "state.json", state)
    before_job, before_bytes = deepcopy(job), (run / "state.json").read_bytes()
    assert harness._recover_completed(run, state, config, [("method-0", job)], {}) == []
    assert job == before_job and job["status"] == "verifying"
    assert (run / "state.json").read_bytes() == before_bytes
    assert harness.export_accepted(run)["profiles"] == 0


@pytest.mark.asyncio
async def test_actual_worker_class_captures_return_hashes_with_local_synthetic_cli(case, tmp_path):
    """Run local scripted processes, never the Codex service or a model."""
    run, _, config, job = case
    payloads = {role: harness.read(Path(job[field])) for role, field in (
        ("source_checklist", "source_checklist"), ("extractor", "extraction"),
        ("independent_verifier", "review"))}
    packet = tmp_path / "synthetic-worker-payloads.json"
    harness.write(packet, payloads)
    # Keep the disposable prior synthetic archives, then give the native worker
    # fresh attempt paths. CodexWorker correctly refuses to overwrite archives.
    previous = tmp_path / "previous-synthetic-attempts"
    previous.mkdir()
    for attempt in job["attempts"]:
        directory = Path(attempt["directory"])
        directory.rename(previous / directory.name)
    executable = fake_codex(tmp_path,
        f"payloads = json.loads(pathlib.Path({str(packet)!r}).read_text())\n"
        "role = next(r for r in payloads if pathlib.Path.cwd().name.endswith('_' + r))\n"
        "filename = {'source_checklist': 'checklist.json', 'extractor': 'extraction.json', "
        "'independent_verifier': 'review.json'}[role]\n"
        "pathlib.Path(filename).write_text(json.dumps(payloads[role], ensure_ascii=False, "
        "indent=2) + '\\n')\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': 'synthetic-' + role}))\n"
        "print(json.dumps({'type': 'turn.started'}))\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 5}}))\n"
        "output.write_text('Local synthetic worker completed, not a model judgment.')\n")
    state = harness.read(run / "state.json")
    current = state["jobs"]["method-0"]
    current.update(status="pending", attempts=[])
    current.pop("review_baseline", None)
    for field in ("source_checklist", "extraction", "review"):
        current.pop(field, None)
        current.pop(field + "_sha256", None)
    harness.write(run / "state.json", state)
    worker = CodexWorker(executable, model=config["model"], effort=config["effort"],
                         web_search=False, network_access=False)
    result = await harness.execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    current = harness.read(run / "state.json")["jobs"]["method-0"]
    assert len(current["attempts"]) == 3
    for attempt in current["attempts"]:
        assert attempt["worker_transport"] == NATIVE
        assert attempt["native_archive_pin_origin"] == "worker_return"
        assert attempt["native_archive_sha256"] == native_archive_hashes(attempt["directory"])
    bundle = harness.read(Path(harness.export_accepted(run)["output"]))
    assert bundle["profiles"][0]["verification"]["reviewer"] == "independent Codex session"
    graph_import._check_bundle(run, harness.read(run / "state.json"), config, bundle)

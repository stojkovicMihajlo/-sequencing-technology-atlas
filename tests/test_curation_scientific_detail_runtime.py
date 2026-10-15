"""Detail reviews must survive the actual queue and graph admission boundary.

Synthetic worker judgments test policy enforcement, not scientific accuracy.
"""

from copy import deepcopy
from pathlib import Path

import pytest
from test_curation_connection_policy_runtime import ConnectedWorker
from test_curation_coverage import scoped_case

from genomics_discovery.curation import harness
from genomics_discovery.curation.graph_import import _check_bundle, export_graph_candidate
from genomics_discovery.curation.validation import (
    canonical_hash,
    validate_extraction,
    validate_review,
)
from genomics_discovery.knowledge.atlas_graph import TechnologyKnowledgeGraph


class DetailedWorker(ConnectedWorker):
    def __init__(self, fault=None):
        super().__init__()
        self.fault = fault

    async def run(self, prompt, attempt):
        result = await super().run(prompt, attempt)
        if attempt.name.endswith("_extractor"):
            path = attempt / "extraction.json"
            document = harness.read(path)
            module = document["profiles"][0]["modules"][0]
            module["steps"].append("Interpret the recorded synthetic signal.")
            module["internal_handoffs"] = [{
                "source_step_index": 0, "target_step_index": 1,
                "source_step_sha256": canonical_hash(module["steps"][0]),
                "target_step_sha256": canonical_hash(module["steps"][1]),
                "relation": "information", "data_or_material_product": "Synthetic signal record",
                "basis": "Synthetic fixture specifies this consumed input", "condition": None,
                "source_refs": deepcopy(module["source_refs"]),
            }]
            if self.fault == "stale_internal_endpoint":
                module["internal_handoffs"][0]["target_step_sha256"] = "0" * 64
            harness.write(path, document)
        elif attempt.name.endswith("_independent_verifier"):
            path = attempt / "review.json"
            document = harness.read(path)
            scaffold = harness.read(attempt / "review_scaffold.json")
            document["scientific_detail_policy"] = scaffold["scientific_detail_policy"]
            checks = deepcopy(scaffold["assertion_checks"])
            refs = [{"source_id": "original", "locator": "Synthetic fixture Methods"}]
            for check in checks:
                check.update(outcome="accepted", context_outcome="supported",
                             observations=["Synthetic supported judgment; not paper accuracy."],
                             source_refs_checked=deepcopy(refs))
            document["assertion_checks"] = checks
            scans = deepcopy(scaffold["topology_check"]["module_handoff_checks"])
            for scan in scans:
                scan.update(outcome="accepted", observations=["Synthetic module freshly scanned."],
                            source_refs_checked=deepcopy(refs), external_connection_indices=[0],
                            disposition="represented_both" if scan["internal_handoff_checks"]
                            else "represented_external")
                for check in scan["internal_handoff_checks"]:
                    check.update(outcome="accepted", observations=["Synthetic handoff checked."],
                                 source_refs_checked=deepcopy(refs))
            document["topology_check"]["module_handoff_checks"] = scans
            if self.fault == "missing_assertion":
                document["assertion_checks"].pop()
            elif self.fault == "missing_scan":
                scans.pop()
            elif self.fault == "stale_assertion":
                checks[1]["field_bindings"][0]["value_sha256"] = "0" * 64
            elif self.fault in {"unsupported", "not_inspected"}:
                check = checks[1]
                unsupported = self.fault == "unsupported"
                check.update(outcome="needs_revision" if unsupported else "unresolved_source_gap",
                             context_outcome=self.fault,
                             affected_field_paths=["/modules/0/steps/1"] if unsupported else [],
                             observations=["Specific unsupported action." if unsupported
                                           else "Passage not inspected."],
                             source_refs_checked=deepcopy(refs) if unsupported else [])
                document.update(outcome=check["outcome"], errors=[{
                    "finding": "Specific action still requires source-grounded review."}])
            harness.write(path, document)
        return result


@pytest.mark.asyncio
async def test_detail_policy_runs_three_stages_and_preserves_reviewed_subgraph(tmp_path):
    run, _, _, _ = scoped_case(tmp_path, connection_reviews=True, scientific_detail_reviews=True)
    worker = DetailedWorker()
    result = await harness.execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert [call[1] for call in worker.calls] == [
        "source_checklist", "extractor", "independent_verifier"]
    config = harness.read(run / "config.json")
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    assert harness.validation_options(run, config)["require_scientific_detail_reviews"] is True
    assert "assertion_checks" not in worker.calls[1][3]
    assert "SCIENTIFIC DETAIL REVIEW PILOT" in worker.calls[1][3]
    assert "SOURCE-FIRST HANDOFF AUDIT" in worker.calls[0][3]
    assert "assertion_checks" in worker.calls[2][3]
    for name in ("assertion_review.py", "internal_handoffs.py"):
        policy = harness.review_policy(config, job, {name: "implementation-pin"})
        assert policy["implementation"][name] == "implementation-pin"
    artifact = harness.read(Path(job["extraction"]))
    review = harness.read(Path(job["review"]))
    exported = harness.export_accepted(run)
    bundle = harness.read(Path(exported["output"]))
    detail = bundle["profiles"][0]["verification"]["scientific_detail_review"]
    assert detail["assertion_groups"] == 7
    assert detail["module_handoff_scans"] == 2
    assert detail["assertion_checks_sha256"] == canonical_hash(review["assertion_checks"])
    checked = harness.check_artifact(run, "method-0", Path(job["review"]),
                                    "independent_verifier")
    assert checked["draft_progress"]["assertion_groups"]["expected"] == 7
    assert checked["draft_progress"]["module_handoff_scans"]["expected"] == 2
    assert "not scientific validation" in checked["draft_progress"]["scope"]
    candidate = export_graph_candidate(run)
    graph = TechnologyKnowledgeGraph.from_path(Path(candidate["output"]))
    profile = graph.export_snapshot()["data"]["component_profiles"][0]
    assert profile["components"][0]["source_module"]["internal_handoffs"] == (
        artifact["profiles"][0]["modules"][0]["internal_handoffs"])
    assert graph.validate()["valid"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_assertion", "missing_scan", "stale_assertion",
                                       "stale_internal_endpoint"])
async def test_detail_integrity_failures_cannot_export(tmp_path, fault):
    run, _, _, _ = scoped_case(tmp_path, connection_reviews=True, scientific_detail_reviews=True)
    result = await harness.execute(run, worker=DetailedWorker(fault))
    assert result["counts"] == {"failed": 1}
    assert harness.export_accepted(run)["profiles"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["unsupported", "not_inspected"])
async def test_honest_nonaccepting_detail_review_finishes_without_fake_reading(tmp_path, fault):
    run, _, _, _ = scoped_case(tmp_path, connection_reviews=True, scientific_detail_reviews=True)
    # One critique is sufficient for this diagnostic. No repair is requested.
    config = harness.read(run / "config.json")
    config["max_revisions"] = 0
    harness.write(run / "config.json", config)
    state = harness.read(run / "state.json")
    state["config_sha256"] = harness.sha(run / "config.json")
    harness.write(run / "state.json", state)
    result = await harness.execute(run, worker=DetailedWorker(fault))
    assert result["counts"] == {"research_needed": 1}
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    assert harness.read(Path(job["review"]))["assertion_checks"][1]["context_outcome"] == fault
    assert harness.export_accepted(run)["profiles"] == 0


@pytest.mark.asyncio
async def test_forged_detail_export_provenance_rejected(tmp_path):
    run, _, _, _ = scoped_case(tmp_path, connection_reviews=True, scientific_detail_reviews=True)
    await harness.execute(run, worker=DetailedWorker())
    bundle = harness.read(Path(harness.export_accepted(run)["output"]))
    bundle["profiles"][0]["verification"]["scientific_detail_review"][
        "assertion_checks_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="stale scientific detail review provenance"):
        _check_bundle(run, harness.read(run / "state.json"),
                      harness.read(run / "config.json"), bundle)


@pytest.mark.parametrize("options", [{}, {"connection_reviews": True},
                                       {"scientific_detail_reviews": "yes"}])
def test_detail_policy_cannot_silently_run_without_prerequisites(tmp_path, options):
    # scoped_case supplies frozen sources and scope; per-connection review is still required.
    options.setdefault("scientific_detail_reviews", True)
    with pytest.raises(ValueError, match="Scientific detail review requires|must be boolean"):
        if not options.get("connection_reviews"):
            scoped_case(tmp_path, **options)
        else:
            harness.initialize(tmp_path / "bad-run", tmp_path / "atlas", **options)


def test_legacy_run_does_not_inherit_new_policy_and_unknown_version_rejected(tmp_path):
    run, _, _, _ = scoped_case(tmp_path)
    config = harness.read(run / "config.json")
    assert "scientific_detail_policy" not in config
    assert "require_scientific_detail_reviews" not in harness.validation_options(run, config)
    config["scientific_detail_policy"] = "unrecognized"
    with pytest.raises(ValueError, match="Unsupported scientific detail policy"):
        harness.validation_options(run, config)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["extractor", "independent_verifier"])
@pytest.mark.parametrize("missing", ["require_connection_reviews", "evidence_policy",
                                     "prepared_sources", "review_scopes"])
async def test_direct_validator_cannot_silently_weaken_detail_policy(tmp_path, role, missing):
    run, atlas, _, _ = scoped_case(
        tmp_path, connection_reviews=True, scientific_detail_reviews=True)
    await harness.execute(run, worker=DetailedWorker())
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    options = harness.validation_options(run, harness.read(run / "config.json"))
    options.pop(missing)
    extraction = Path(job["extraction"])
    if role == "extractor":
        errors = validate_extraction(extraction, "method-0", (atlas, run), **options)
    else:
        errors = validate_review(Path(job["review"]), extraction, "method-0", (atlas, run),
                                 source_checklist=Path(job["source_checklist"]), **options)
    assert errors and "Scientific detail" in errors[0]


@pytest.mark.asyncio
async def test_direct_detail_review_requires_preoutput_source_checklist(tmp_path):
    run, atlas, _, _ = scoped_case(
        tmp_path, connection_reviews=True, scientific_detail_reviews=True)
    await harness.execute(run, worker=DetailedWorker())
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    errors = validate_review(Path(job["review"]), Path(job["extraction"]),
                             "method-0", (atlas, run),
                             **harness.validation_options(run, harness.read(run / "config.json")))
    assert errors and "Scientific detail review requires" in errors[0]


@pytest.mark.asyncio
async def test_legacy_review_validates_optional_internal_handoffs_without_claiming_detail_policy(
        tmp_path):
    run, atlas, _, _ = scoped_case(
        tmp_path, connection_reviews=True, scientific_detail_reviews=True)
    await harness.execute(run, worker=DetailedWorker())
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    extraction = harness.read(Path(job["extraction"]))
    profile = extraction["profiles"][0]
    profile["modules"][0]["internal_handoffs"][0]["source_step_index"] = 99
    verdict = harness.read(Path(job["review"]))
    verdict["profile_sha256"] = canonical_hash(profile)
    verdict["module_checks"][0]["module_sha256"] = canonical_hash(profile["modules"][0])
    bad_extraction, fresh_review = tmp_path / "bad.json", tmp_path / "fresh_review.json"
    harness.write(bad_extraction, extraction)
    harness.write(fresh_review, verdict)
    errors = validate_review(fresh_review, bad_extraction, "method-0", (atlas, run, tmp_path))
    assert errors and "source_step_index" in errors[0]

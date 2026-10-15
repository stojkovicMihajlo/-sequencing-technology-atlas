"""A global topology verdict cannot approve unchecked individual connections."""

from copy import deepcopy
from pathlib import Path

import pytest
from test_curation_coverage import CoverageWorker, scoped_case

from genomics_discovery.curation import harness
from genomics_discovery.curation.graph_import import export_graph_candidate
from genomics_discovery.curation.scaffolds import make_fresh_review_scaffold
from genomics_discovery.curation.validation import EVIDENCE_POLICY, canonical_hash
from genomics_discovery.knowledge.atlas_graph import TechnologyKnowledgeGraph


class ConnectedWorker(CoverageWorker):
    def __init__(self, *, missing_checks=False, stale_edge=False):
        super().__init__()
        self.missing_checks, self.stale_edge = missing_checks, stale_edge

    async def run(self, prompt, attempt):
        result = await super().run(prompt, attempt)
        if attempt.name.endswith("_extractor"):
            path = attempt / "extraction.json"
            document = harness.read(path)
            profile = document["profiles"][0]
            second = deepcopy(profile["modules"][0])
            second["module_id"] = "m2"
            second["input_state"]["description"] = "Signal record"
            second["operation_description"] = "Interpret the synthetic signal record"
            profile["modules"].append(second)
            profile["function_coverage"][0]["module_ids"].append("m2")
            profile["topology"] = {
                "default_order": ["m1", "m2"], "connections": [{
                    "source": "m1", "target": "m2", "relation": "information",
                    "basis": "Synthetic signal record is the interpretation input",
                    "source_refs": second["source_refs"],
                }],
            }
            harness.write(path, document)
        elif attempt.name.endswith("_independent_verifier"):
            path = attempt / "review.json"
            document = harness.read(path)
            job = harness.read(attempt.parents[2] / "state.json")["jobs"]["method-0"]
            extraction = harness.read(Path(job["extraction"]))
            edge = extraction["profiles"][0]["topology"]["connections"][0]
            if not self.missing_checks:
                document["topology_check"]["connection_checks"] = [{
                    "connection_index": 0,
                    "connection_sha256": "0" * 64 if self.stale_edge else canonical_hash(edge),
                    "outcome": "accepted", "observations": [
                        "Synthetic fixture: the signal record is retained and consumed."],
                    "source_refs_checked": edge["source_refs"],
                }]
            harness.write(path, document)
        return result


@pytest.mark.asyncio
async def test_connection_policy_accepts_and_exports_complete_current_checks(tmp_path):
    run, atlas, _, _ = scoped_case(tmp_path, connection_reviews=True)
    worker = ConnectedWorker()
    result = await harness.execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert harness.export_accepted(run)["profiles"] == 1
    config = harness.read(run / "config.json")
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    assert config["connection_review_policy"] == "per-connection/1.0"
    assert harness.validation_options(run, config)["require_connection_reviews"] is True
    policy = harness.review_policy(config, job, {"connection_review.py": "fixture-pin"})
    assert policy["implementation"]["connection_review.py"] == "fixture-pin"
    assert "PER-CONNECTION REVIEW" in worker.calls[-1][3]
    assert "PER-CONNECTION REVIEW" not in worker.calls[1][3]
    scaffold = make_fresh_review_scaffold(
        Path(job["extraction"]), "method-0", (atlas, run), evidence_policy=EVIDENCE_POLICY,
        require_connection_reviews=True)
    row = scaffold["topology_check"]["connection_checks"][0]
    assert row["connection_index"] == 0
    assert row["observations"] == []
    assert row["source_refs_checked"] == []
    assert row["outcome"] not in harness.ACCEPTED
    candidate = export_graph_candidate(run)
    graph = TechnologyKnowledgeGraph.from_path(Path(candidate["output"]))
    data = graph.export_snapshot()["data"]
    stored = data["component_profiles"][0]
    reviewed = harness.read(Path(job["review"]))["topology_check"]["connection_checks"]
    assert stored["verification"]["connection_review"] == {
        "policy": "per-connection/1.0", "checked_connections": 1,
        "checks_sha256": canonical_hash(reviewed),
    }
    original = harness.read(Path(job["extraction"]))["profiles"][0]
    assert stored["connections"][0]["source_edge"] == original["topology"]["connections"][0]
    assert graph.validate()["valid"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_checks", "stale_edge"])
async def test_aggregate_acceptance_or_stale_connection_cannot_export(tmp_path, fault):
    run, _, _, _ = scoped_case(tmp_path, connection_reviews=True)
    result = await harness.execute(run, worker=ConnectedWorker(**{fault: True}))
    assert result["counts"] == {"failed": 1}
    assert harness.export_accepted(run)["profiles"] == 0
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    assert "connection" in str(job["last_error"]).lower()


def test_legacy_policy_stays_absent_and_unknown_policy_rejects(tmp_path):
    run, _, _, _ = scoped_case(tmp_path)
    config = harness.read(run / "config.json")
    assert "connection_review_policy" not in config
    assert "require_connection_reviews" not in harness.validation_options(run, config)
    config["connection_review_policy"] = "unknown-version"
    with pytest.raises(ValueError, match="Unsupported connection review policy"):
        harness.validation_options(run, config)

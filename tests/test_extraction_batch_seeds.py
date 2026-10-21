"""Explicit scientific continuations start real fresh runtime/batch attempts."""

import asyncio
import copy
import json
from pathlib import Path

import pytest

from genomics_discovery.curation.batch_graph import publish
from genomics_discovery.curation.extraction_batch import BatchConfig, Job, plan, run, status
from genomics_discovery.curation.extraction_batch.storage import PinDrift, read, sha, write
from genomics_discovery.curation.workflow_extraction.models import digest, WorkflowReview
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute
from genomics_discovery.curation.workflow_extraction.sources import build_packet
from test_workflow_extraction_runtime import FakeWorker, source_case
from test_workflow_source_obligations import SourceWorker


async def prior(case, tmp_path, *, source_first=False, coverage_only=False):
    worker = SourceWorker(case, omit_wash=coverage_only) if source_first else FakeWorker(case)
    if not coverage_only:
        worker.review_verdicts = ["unsupported"]
    origin = tmp_path / "prior"
    state = await execute(case[1], origin, worker=worker, mode="baseline", max_revisions=0,
                          require_source_coverage=source_first)
    assert state["status"] == "needs_revision"
    return origin, state


def inventory(tmp_path, case, origin, mode="repair"):
    path = tmp_path / "inventory.json"
    write(path, {"jobs": [{"id":"method", "status":"ready", "packet":str(case[1]),
                           "title":"Method", "seed_runtime":str(origin), "seed_mode":mode}]})
    return path


async def test_primary_negative_same_packet_repair_is_fresh_batch_attempt_and_graph_admissible(source_case,tmp_path):
    origin, state = await prior(source_case,tmp_path)
    originals = {str(p):sha(p) for p in origin.rglob("*") if p.is_file()}
    destination = tmp_path / "batch"
    planned = plan(inventory(tmp_path,source_case,origin),destination)
    assert planned["jobs"]["method"]["status"] == "queued"
    frozen = read(destination / "plan.json")["jobs"][0]["seed"]
    assert frozen["scientific_verdict_inherited"] is False
    worker = FakeWorker(source_case)
    result = await run(destination,worker_factory=lambda:worker)
    row = result["jobs"]["method"]
    assert row["status"] == "supported"
    assert [a["mode"] for a in row["attempts"]] == ["repair"]
    assert [c["role"] for c in worker.calls] == ["integrator","reviewer"]
    current = read(Path(row["result"]["runtime_run"]) / "config.json")
    assert current["initial_workflow"]["path"] == frozen["workflow"]
    assert current["repair_review"]["path"] == frozen["review"]
    assert all(sha(Path(p))==value for p,value in originals.items())
    atlas = tmp_path / "atlas.json"
    write(atlas,{"methods":[{"id":"method","name":"Method"}],"claims":[],"revision_id":"seed-test"})
    graph = publish(destination,atlas,tmp_path / "publication")
    assert len(graph["workflows"]) == 1
    await run(destination,worker_factory=lambda:pytest.fail("A terminal scientific job must not retry implicitly"))


async def test_source_obligations_origin_survives_copy_and_graph_admission(source_case,tmp_path):
    origin, state = await prior(source_case,tmp_path,source_first=True)
    destination = tmp_path / "batch"
    plan(inventory(tmp_path,source_case,origin),destination,
         config=BatchConfig(require_source_coverage=True))
    frozen = read(destination / "plan.json")["jobs"][0]["seed"]
    expected = state["artifacts"]["source_obligations"]["path"]
    assert frozen["source_obligations_origin"] == expected
    assert frozen["source_obligations_snapshot"] != expected
    worker = SourceWorker(source_case)
    result = await run(destination,worker_factory=lambda:worker)
    row = result["jobs"]["method"]
    assert row["status"] == "supported"
    config = read(Path(row["result"]["runtime_run"]) / "config.json")
    assert config["source_obligations_input"]["path"] == expected
    assert "source_obligations" not in [c["role"] for c in worker.calls]
    assert set(row["result"]["accepted_source_coverage"]) == {"source_obligations","source_coverage_review"}
    atlas = tmp_path / "atlas.json"
    write(atlas,{"methods":[{"id":"method","name":"Method"}],"claims":[],"revision_id":"source-seed-test"})
    graph = publish(destination,atlas,tmp_path / "publication")
    assert len(graph["workflows"]) == 1


async def test_coverage_only_missing_cannot_supply_repair_feedback_but_explicit_review_only_can_repair(source_case,tmp_path):
    origin, state = await prior(source_case,tmp_path,source_first=True,coverage_only=True)
    review = WorkflowReview.model_validate_json(Path(state["review"]).read_bytes())
    assert all(c.verdict == "supported" for c in review.checks)
    config = BatchConfig(require_source_coverage=True)
    rejected = plan(inventory(tmp_path,source_case,origin),tmp_path / "rejected",config=config)
    assert rejected["jobs"]["method"]["status"] == "input_error"
    assert "coverage-only" in rejected["jobs"]["method"]["error"]
    manifest = inventory(tmp_path,source_case,origin,mode="review_only")
    destination = tmp_path / "review-batch"
    plan(manifest,destination,config=config)
    worker = SourceWorker(source_case,omit_wash=True,fix_on_revision=True)
    worker.integrations = 1  # The first integrator in this continuation is a repair.
    result = await run(destination,worker_factory=lambda:worker)
    row = result["jobs"]["method"]
    assert row["status"] == "supported"
    assert [c["role"] for c in worker.calls] == ["reviewer","integrator","reviewer"]
    current = read(Path(row["result"]["runtime_run"]) / "config.json")
    assert current["repair_review"] is None
    atlas = tmp_path / "atlas.json"
    write(atlas,{"methods":[{"id":"method","name":"Method"}],"claims":[],"revision_id":"coverage-seed-test"})
    assert len(publish(destination,atlas,tmp_path / "publication")["workflows"]) == 1


async def test_transport_failure_retries_seed_then_preserves_new_repaired_candidate(source_case,tmp_path):
    origin, _ = await prior(source_case,tmp_path)
    destination = tmp_path / "batch"
    plan(inventory(tmp_path,source_case,origin),destination,config=BatchConfig(transport_retries=1))
    class FailReview(FakeWorker):
        def __init__(self,case):
            super().__init__(case); self.failed=False
        async def run(self,prompt,attempt):
            if "ROLE: reviewer" in prompt and not self.failed:
                self.failed=True
                raise RuntimeError("Interrupted review")
            return await super().run(prompt,attempt)
    worker = FailReview(source_case)
    result = await run(destination,worker_factory=lambda:worker)
    assert result["jobs"]["method"]["status"] == "supported"
    assert [a["mode"] for a in result["jobs"]["method"]["attempts"]] == ["repair","review_only"]
    assert worker.integrations == 1 and worker.reviews == 1


async def test_seed_before_any_new_candidate_remains_seed_on_transport_retry(source_case,tmp_path):
    origin, _ = await prior(source_case,tmp_path)
    destination = tmp_path / "batch"
    plan(inventory(tmp_path,source_case,origin),destination,config=BatchConfig(transport_retries=1))
    class FailFirst(FakeWorker):
        def __init__(self,case):super().__init__(case);self.failed=False
        async def run(self,prompt,attempt):
            if not self.failed:
                self.failed=True;raise RuntimeError("No candidate completed")
            return await super().run(prompt,attempt)
    worker = FailFirst(source_case)
    result = await run(destination,worker_factory=lambda:worker)
    assert result["jobs"]["method"]["status"] == "supported"
    assert [a["mode"] for a in result["jobs"]["method"]["attempts"]] == ["repair","repair"]


@pytest.mark.parametrize("target",["origin","snapshot","obligations"])
async def test_seed_hash_drift_fails_only_affected_job_before_native(source_case,tmp_path,target):
    origin,_ = await prior(source_case,tmp_path,source_first=True)
    manifest = inventory(tmp_path,source_case,origin)
    raw = read(manifest)
    raw["jobs"].append({"id":"healthy","status":"ready","packet":str(source_case[1])})
    write(manifest,raw)
    destination = tmp_path / "batch"
    plan(manifest,destination,config=BatchConfig(require_source_coverage=True))
    seed = read(destination / "plan.json")["jobs"][0]["seed"]
    changed = (Path(seed["source_obligations_origin"]) if target == "obligations" else
               Path(seed["workflow"]) if target == "snapshot" else origin / "state.json")
    changed.write_bytes(changed.read_bytes()+b" ")
    workers=[]
    def factory():
        native=SourceWorker(source_case);workers.append(native);return native
    result = await run(destination,worker_factory=factory)
    assert result["jobs"]["method"]["status"] == "pin_drift"
    assert result["jobs"]["healthy"]["status"] == "supported"
    assert len(workers) == 1


async def test_changed_packet_rejected_instead_of_rebinding_feedback_or_obligations(source_case,tmp_path):
    origin,_ = await prior(source_case,tmp_path)
    packet = copy.deepcopy(source_case[0])
    extra = tmp_path / "extra.txt";extra.write_text("Additional source findings.")
    updated = build_packet(**{k:packet[k] for k in ("method_id","title","scope","variant")},sources=[
        *[{**{k:s[k] for k in ("source_id","title","url","role")},"path":s["original_path"]} for s in packet["sources"]],
        {"source_id":"new","title":"Supplement","url":"https://example.org/supp","role":"supplement","path":str(extra)}])
    path=tmp_path/"expanded.json";write(path,updated)
    case=(updated,path,source_case[2])
    state=plan(inventory(tmp_path,case,origin),tmp_path/"batch")
    assert state["jobs"]["method"]["status"] == "input_error"
    assert "same canonical source packet" in state["jobs"]["method"]["error"]


@pytest.mark.parametrize("job",[
    {"status":"ready","packet":"packet.json","seed_runtime":"runtime"},
    {"status":"ready","packet":"packet.json","seed_mode":"repair"},
    {"status":"missing_sources","reason":"Missing","next_evidence":"Retrieve", "seed_runtime":"runtime","seed_mode":"repair"}])
def test_seed_manifest_requires_explicit_mode_and_ready_packet(job):
    with pytest.raises(ValueError):Job.model_validate({"id":"method",**job})


async def test_code_upgrade_rejects_old_batch_resume_but_retains_previous_publication(source_case,tmp_path,monkeypatch):
    from genomics_discovery.curation.extraction_batch import controller
    # A representative exact implementation pin exercises the real plan/load
    # contract without mutating the repository or any live source file.
    implementation = tmp_path / "controller_version.py"
    implementation.write_text("version = 1\n")
    monkeypatch.setattr(controller,"_implementation_pins",lambda:{str(implementation):sha(implementation)})
    old_inventory = tmp_path / "old_inventory.json"
    write(old_inventory,{"jobs":[{"id":"first","title":"First","status":"ready","packet":str(source_case[1])}]})
    first = tmp_path / "first"
    config = BatchConfig(require_source_coverage=True)
    plan(old_inventory,first,config=config)
    old_state = await run(first,worker_factory=lambda:SourceWorker(source_case))
    atlas = tmp_path / "atlas.json"
    write(atlas,{"methods":[{"id":"first","name":"First"},{"id":"second","name":"Second"}],
                 "claims":[],"revision_id":"code-upgrade-test"})
    prior_publication = tmp_path / "publication_v1"
    prior_graph = publish(first,atlas,prior_publication)
    assert str(implementation) not in prior_graph["input_files"]
    runtime = Path(old_state["jobs"]["first"]["result"]["runtime_run"])
    runtime_code = {row["path"] for row in read(runtime / "config.json")["implementation"]}
    assert not runtime_code.intersection(prior_graph["input_files"])
    preserved = {str(path):sha(path) for path in prior_publication.iterdir() if path.is_file()}
    implementation.write_text("version = 2\n")
    saved_state = (first / "state.json").read_bytes()
    observed = status(first)
    assert observed["input_status"] == "pin_drift"
    assert observed["global_input_health"][str(implementation)] == f"Pinned file changed: {implementation}"
    assert observed["input_health"]["first"] == "unchanged"
    assert observed["recorded_status"] == old_state["status"] == "completed"
    assert (first / "state.json").read_bytes() == saved_state
    with pytest.raises(PinDrift):
        await run(first,worker_factory=lambda:pytest.fail("Never execute an old batch under changed code"))
    assert (first / "state.json").read_bytes() == saved_state
    new_packet = copy.deepcopy(source_case[0]);new_workflow = copy.deepcopy(source_case[2])
    new_packet["method_id"] = new_workflow["method_id"] = "second_implementation"
    path = tmp_path / "new_packet.json";write(path,new_packet)
    second_case = (new_packet,path,new_workflow)
    new_inventory = tmp_path / "new_inventory.json"
    write(new_inventory,{"jobs":[{"id":"second","title":"Second","status":"ready","packet":str(path)}]})
    second = tmp_path / "second"
    plan(new_inventory,second,config=config)
    await run(second,worker_factory=lambda:SourceWorker(second_case))
    current = publish(second,atlas,tmp_path / "publication_v2",previous=prior_publication)
    assert len(current["workflows"]) == 2
    assert set(read(tmp_path / "publication_v2/extractions.json")) == {"first","second"}
    assert all(sha(Path(path)) == expected for path,expected in preserved.items())


@pytest.mark.parametrize("mode,coverage_only",[("repair",False),("review_only",True)])
async def test_current_deployment_driver_runs_explicit_seed_then_publishes_and_restarts_without_native(source_case,tmp_path,mode,coverage_only):
    from genomics_discovery.curation import deployment
    origin,old_state = await prior(source_case,tmp_path,source_first=True,coverage_only=coverage_only)
    old_hashes = {str(path):sha(path) for path in origin.rglob("*") if path.is_file()}
    manifest = inventory(tmp_path,source_case,origin,mode=mode)
    original = read(manifest)
    original["jobs"][0]["seed_runtime"] = str(origin.relative_to(manifest.parent))
    write(manifest,original)
    atlas = tmp_path / "atlas.json"
    write(atlas,{"methods":[{"id":"method","name":"Method"}],"claims":[],"revision_id":"seed-deployment"})
    destination = tmp_path / "deployment"
    deployment.plan_deployment(manifest,atlas,destination,batch_size=1,
        config=BatchConfig(require_source_coverage=True,concurrency=2))
    child = Path(read(destination / "plan.json")["chunks"][0]["batch"])
    assert read(child / "plan.json")["jobs"][0]["seed"]["mode"] == mode
    worker = SourceWorker(source_case,omit_wash=coverage_only,fix_on_revision=coverage_only)
    if coverage_only:worker.integrations=1
    result = await deployment.run_deployment(destination,worker_factory=lambda:worker)
    assert result["status"] == "completed"
    assert result["methods"]["method"]["status"] == "supported"
    publication = Path(result["latest_publication"]["path"])
    assert read(publication / "publication.json")["accepted_methods"] == ["method"]
    assert len(read(publication / "graph.json")["workflows"]) == 1
    assert all(sha(Path(path))==value for path,value in old_hashes.items())
    await deployment.run_deployment(destination,worker_factory=lambda:pytest.fail("Completed seeded deployment must not launch native"))

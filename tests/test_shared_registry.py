"""Concurrent admission contracts. Fixtures do not establish scientific accuracy."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from test_workflow_transformations import cases as cases
from test_operation_registry import assignment, review, decision_for, admission_for, registry as registry

from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.operation_registry.admission import VocabularyProposal, CurrentVocabularyReview
from genomics_discovery.curation.operation_registry.definition_comparison import (
    ComparisonReport, PairComparison, FieldComparison, FIELDS, outcome, validate_report,
)
from genomics_discovery.curation.operation_registry.proposal_queue import (
    enqueue, attach_review, plan, attach_comparisons, missing_plan, submission,
)
from genomics_discovery.curation.operation_registry.publication import publish, reuse_proposal
from genomics_discovery.curation.operation_registry.store import Registry, catalogue
from genomics_discovery.curation.operation_registry.storage import connect, migrate


def bundle(cases, snapshot, tmp_path, index=0, *, cid=None, name=None, output=None):
    a = assignment(cases, snapshot, index=index)
    if cid or name or output:
        a = a.model_copy(update={"definition": a.definition.model_copy(update={
            **({"id": cid} if cid else {}), **({"name": name} if name else {}),
            **({"output": output} if output else {})})})
    d = decision_for(a)
    p = VocabularyProposal(decisions=(d,))
    r = CurrentVocabularyReview(proposal_sha256=digest(p), reviews=(admission_for(d, review(a, cases)),))
    ident = d.occurrence_id
    method = cases[0][ident].method_id
    manifest = {ident: {k: str(tmp_path / f"{method}.{k}.json") for k in ("workflow", "packet", "review", "occurrence")}}
    return p, r, manifest


def queue(registry, cases, tmp_path, index=0, **kwargs):
    snapshot = registry.snapshot()
    p, r, m = bundle(cases, snapshot, tmp_path, index, **kwargs)
    key = enqueue(registry.path, p, catalogue(snapshot), m)
    attach_review(registry.path, key, r)
    return key


def compare_all(registry, key, verdict="same"):
    p = missing_plan(registry.path, key)
    r = ComparisonReport(plan_sha256=digest(p), comparisons=tuple(PairComparison(
        pair_id=pair["pair_id"], fields=tuple(FieldComparison(field=field,
        comparison=verdict if field == "output" else "same",
        rationale="Synthetic scope comparison; no scientific accuracy claim") for field in FIELDS)) for pair in p["pairs"]))
    attach_comparisons(registry.path, key, p, r)
    return p, r


def test_normalized_storage_and_lossless_legacy_migration(cases, registry, tmp_path):
    key = queue(registry, cases, tmp_path)
    assert publish(registry.path, key)["status"] == "committed"
    snapshot = registry.snapshot()
    old = tmp_path / "legacy.sqlite"
    with sqlite3.connect(old) as db:
        db.execute("CREATE TABLE registry(id INTEGER PRIMARY KEY,payload TEXT)")
        db.execute("INSERT INTO registry VALUES(1,?)", (json.dumps(snapshot),))
    before = old.read_bytes()
    target = tmp_path / "migrated.sqlite"
    migrate(old, target)
    assert Registry(target).snapshot() == snapshot
    assert old.read_bytes() == before
    with sqlite3.connect(target) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='registry'").fetchone()
        assert db.execute("SELECT count(*) FROM concepts").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM assignments").fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        migrate(old, target)


def test_parallel_synonyms_one_identity_then_fresh_membership_review(cases, registry, tmp_path):
    a = queue(registry, cases, tmp_path, cid="alpha", name="Synthetic first wording")
    b = queue(registry, cases, tmp_path, index=1, cid="beta", name="Synthetic second wording")
    for key in (a, b):
        compare_all(registry, key)
    barrier = Barrier(2)
    def publish_together(key):
        barrier.wait()
        return publish(registry.path, key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish_together, (a, b)))
    assert sorted(r["status"] for r in results) == ["committed", "reuse_review_required"]
    assert len(registry.snapshot()["concepts"]) == len(registry.snapshot()["assignments"]) == 1
    waiting = next(r for r in results if r["status"] == "reuse_review_required")
    with connect(registry.path) as db:
        saved = submission(db, waiting["submission_id"])
    current = catalogue(registry.snapshot())
    revised = reuse_proposal(saved, waiting["targets"], current)
    new_key = enqueue(registry.path, revised, current, saved["manifest"])
    assert publish(registry.path, new_key)["status"] == "awaiting_source_review"
    with pytest.raises(ValueError, match="exact proposal"):
        attach_review(registry.path, new_key, CurrentVocabularyReview.model_validate(saved["review"]["document"]))
    d = revised.decisions[0]
    fresh = CurrentVocabularyReview(proposal_sha256=digest(revised), reviews=(admission_for(d, review(d.assignment,cases)),))
    attach_review(registry.path, new_key, fresh)
    compare_all(registry, new_key)
    assert publish(registry.path, new_key)["status"] == "committed"
    assert len(registry.snapshot()["concepts"]) == 1
    assert len(registry.snapshot()["assignments"]) == 2


def test_late_arrival_requires_only_new_comparison_and_keeps_source_review(cases, registry, tmp_path):
    a = queue(registry, cases, tmp_path, cid="alpha")
    assert not plan(registry.path, a)["pairs"]
    b = queue(registry, cases, tmp_path, index=1, cid="beta", name="Different endpoint fixture",
              output="Synthetic different endpoint for the concurrency control")
    pending = publish(registry.path, a)
    assert pending["status"] == "awaiting_comparisons"
    with connect(registry.path) as db:
        before = submission(db, a)["review"]
    compare_all(registry, a, "different")
    compare_all(registry, b, "different")
    assert publish(registry.path, b)["status"] == "committed"
    assert publish(registry.path, a)["status"] == "committed"
    with connect(registry.path) as db:
        assert submission(db, a)["review"] == before
    assert len(registry.snapshot()["concepts"]) == 2


def test_submission_and_publication_retries_are_idempotent(cases, registry, tmp_path):
    snapshot = registry.snapshot()
    p, r, m = bundle(cases,snapshot,tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = list(pool.map(lambda _: enqueue(registry.path,p,catalogue(snapshot),m), range(4)))
    assert len(set(keys)) == 1
    key=keys[0]; attach_review(registry.path,key,r)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _: publish(registry.path,key),range(4)))
    assert all(r["status"] == "committed" for r in results)
    assert len(registry.snapshot()["assignments"]) == 1
    assert sum(r.get("idempotent_replay",False) for r in results)==3


def test_restart_preserves_pending_reviews_and_unknown_never_admits(cases,registry,tmp_path):
    a=queue(registry,cases,tmp_path,cid="alpha")
    queue(registry,cases,tmp_path,index=1,cid="beta",name="Ambiguous boundary")
    compare_all(registry,a,"unknown")
    reopened=Registry(registry.path)
    assert publish(reopened.path,a)["status"]=="unresolved"
    assert not reopened.snapshot()["concepts"]


def test_broader_scope_is_not_an_alias_and_requires_usefulness_reconsideration(cases,registry,tmp_path):
    a=queue(registry,cases,tmp_path,cid="alpha")
    b=queue(registry,cases,tmp_path,index=1,cid="beta",name="Broader boundary fixture",
            output="Synthetic broader endpoint for the concurrency control")
    compare_all(registry,a,"narrower")
    compare_all(registry,b,"broader")
    assert publish(registry.path,a)["status"]=="committed"
    assert publish(registry.path,b)["status"]=="context_review_required"
    assert len(registry.snapshot()["concepts"])==1


def test_opposite_direction_disagreement_cannot_create_two_identities(cases,registry,tmp_path):
    a=queue(registry,cases,tmp_path,cid="alpha")
    b=queue(registry,cases,tmp_path,index=1,cid="beta",name="Second definition")
    pa,pb=plan(registry.path,a),plan(registry.path,b)
    def report(p,verdict):
        return ComparisonReport(plan_sha256=digest(p),comparisons=tuple(PairComparison(
            pair_id=x['pair_id'],fields=tuple(FieldComparison(field=f,
            comparison=verdict if f=='output' else 'same',rationale='Synthetic disagreement')
            for f in FIELDS)) for x in p['pairs']))
    attach_comparisons(registry.path,a,pa,report(pa,'same'))
    attach_comparisons(registry.path,b,pb,report(pb,'different'))
    assert publish(registry.path,a)['status']=='unresolved'
    assert publish(registry.path,b)['status']=='unresolved'
    assert not registry.snapshot()['concepts']


def test_incomplete_or_changed_comparison_cannot_publish(cases,registry,tmp_path):
    a=queue(registry,cases,tmp_path,cid="alpha")
    queue(registry,cases,tmp_path,index=1,cid="beta",name="Another definition")
    p,r=compare_all(registry,a)
    bad=r.model_copy(update={"comparisons":()})
    with pytest.raises(ValueError,match="every planned pair"):
        validate_report(bad,p)
    row=r.comparisons[0].model_copy(update={"fields":r.comparisons[0].fields[:-1]})
    with pytest.raises(ValueError,match="every definition field"):
        outcome(row)
    with sqlite3.connect(registry.path) as db:
        ident,record=db.execute("SELECT id,payload FROM comparison_reports").fetchone()
        changed=json.loads(record);changed['report']['comparisons'][0]['fields'][0]['rationale']='Changed'
        db.execute("UPDATE comparison_reports SET payload=? WHERE id=?",(json.dumps(changed),ident))
    with pytest.raises(ValueError,match="comparison report changed"):
        publish(registry.path,a)


def test_candidate_routes_overlap_and_pending_entries_are_searchable(cases,registry,tmp_path):
    a=queue(registry,cases,tmp_path,cid="alpha")
    queue(registry,cases,tmp_path,index=1,cid="beta",name="Synonym")
    candidate=plan(registry.path,a)['pairs'][0]
    assert {'substrate','operation','output'} <= set(candidate['routes'])
    assert 'exhaustive_small_catalogue' in candidate['routes']
    assert candidate['query_sha256'] != candidate['target_sha256']
    assert not registry.snapshot()['concepts']


def test_candidate_arriving_between_validation_and_commit_is_not_missed(cases, registry, tmp_path, monkeypatch):
    from genomics_discovery.curation.operation_registry import publication
    a = queue(registry,cases,tmp_path,cid="alpha")
    original = publication.prepare_commit
    injected = []
    def inject(*args, **kwargs):
        result = original(*args, **kwargs)
        if not injected:
            injected.append(queue(registry,cases,tmp_path,index=1,cid="late",name="Late candidate"))
        return result
    monkeypatch.setattr(publication,"prepare_commit",inject)
    assert publish(registry.path,a)["status"] == "awaiting_comparisons"
    assert not registry.snapshot()["concepts"]


def test_separate_processes_publish_same_submission_once(cases,registry,tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context
    a = queue(registry,cases,tmp_path)
    with ProcessPoolExecutor(max_workers=2,mp_context=get_context("spawn")) as pool:
        futures = [pool.submit(publish,registry.path,a) for _ in range(2)]
        results = [f.result(timeout=30) for f in futures]
    assert all(r["status"] == "committed" for r in results)
    assert sum(r.get("idempotent_replay",False) for r in results) == 1


def test_negative_source_review_cannot_publish_even_when_comparison_is_positive(cases,registry,tmp_path):
    p,r,m = bundle(cases,registry.snapshot(),tmp_path)
    d=p.decisions[0]
    r=CurrentVocabularyReview(proposal_sha256=digest(p),reviews=(admission_for(d,review(d.assignment,cases,'unsupported')),))
    key=enqueue(registry.path,p,catalogue(registry.snapshot()),m)
    attach_review(registry.path,key,r)
    assert publish(registry.path,key)["status"] == "completed_without_admission"
    assert not registry.snapshot()["concepts"]


async def test_shared_live_runs_canonical_recheck_end_to_end(cases,registry,tmp_path):
    import asyncio
    from dataclasses import asdict
    from test_operation_registry_live import WorkerFixture
    from genomics_discovery.curation.codex_worker import WorkerResult
    from playground.workflow_shared_live import execute

    first=queue(registry,cases,tmp_path,cid="alpha")
    assert publish(registry.path,first)["status"] == "committed"
    p,_,m=bundle(cases,registry.snapshot(),tmp_path,index=1,cid="beta",name="Synonym fixture",
                 output="Synthetic paraphrase of repaired adapter-bearing DNA")
    proposal_path=tmp_path/'supplied.json';proposal_path.write_text(p.model_dump_json())
    manifest_path=tmp_path/'manifest.json';manifest_path.write_text(json.dumps(m))

    class Worker(WorkerFixture):
        async def run(self,instruction,path):
            await asyncio.sleep(0)
            if not instruction.startswith('Compare proposed definitions'):
                return await super().run(instruction,path)
            self.calls+=1
            plan=json.loads((path/'plan.json').read_text())
            report=ComparisonReport(plan_sha256=digest(plan), comparisons=tuple(PairComparison(
                pair_id=pair['pair_id'], fields=tuple(FieldComparison(field=f,comparison='same',
                rationale='Synthetic end-to-end control') for f in FIELDS)) for pair in plan['pairs']))
            (path/'artifact.json').write_text(report.model_dump_json())
            (path/'prompt.txt').write_text(instruction)
            result=WorkerResult(0,f'thread-{self.calls}',{'input_tokens':1,'output_tokens':1},.01,None)
            (path/'result.json').write_text(json.dumps(asdict(result)))
            events=[{'type':'thread.started','thread_id':result.thread_id},{'type':'turn.started'},
                    {'type':'turn.completed','usage':result.usage}]
            (path/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n')
            for filename in ('command.json','started.json'):
                (path/filename).write_text('{}')
            (path/'stderr.log').write_text('');(path/'final.txt').write_text('Saved fixture')
            return result
    worker=Worker(cases,registry.snapshot());worker.calls=1
    result=await execute(manifest_path,registry.path,tmp_path/'shared-run',worker=worker,
                         supplied_proposal=proposal_path)
    assert result['status']=='committed'
    assert len(result['source_runs'])==2  # Original definition, then canonical definition.
    assert len(registry.snapshot()['concepts'])==1
    assert len(registry.snapshot()['assignments'])==2


def test_withdrawn_draft_stops_blocking_without_erasing_reviews(cases,registry,tmp_path):
    from genomics_discovery.curation.operation_registry.proposal_queue import withdraw
    a=queue(registry,cases,tmp_path,cid='alpha')
    b=queue(registry,cases,tmp_path,index=1,cid='beta',name='Retired draft')
    assert publish(registry.path,a)['status']=='awaiting_comparisons'
    assert withdraw(registry.path,b,'Draft superseded after inspection')['status']=='withdrawn'
    assert not missing_plan(registry.path,a)['pairs']
    assert publish(registry.path,a)['status']=='committed'
    assert publish(registry.path,b)['status']=='withdrawn'
    with connect(registry.path) as db:
        assert submission(db,b)['review'] is not None
    with pytest.raises(ValueError,match='completed'):
        withdraw(registry.path,a,'Cannot remove published evidence')


def test_canonical_revision_rebuilds_catalogue_argument_preserving_scientific_evidence(cases,registry,tmp_path):
    from genomics_discovery.curation.operation_registry.admission import validate_decision
    first=queue(registry,cases,tmp_path,cid='alpha')
    assert publish(registry.path,first)['status']=='committed'
    p,_,m=bundle(cases,registry.snapshot(),tmp_path,index=1,cid='beta',name='Alternative name',
                 output='Synthetic equivalent wording for the source-preservation control')
    original=p.decisions[0]
    stale=original.model_copy(update={
        'explanation':'The catalogue is empty, so create a new label.',
        'case':original.case.model_copy(update={
            'existing_plus_filters':'The catalogue is empty.',
            'lost_distinction_if_merged':'No shared definitions exist.'})})
    p=VocabularyProposal(decisions=(stale,))
    before=p.model_dump(mode='json')
    current=catalogue(registry.snapshot())
    revised=reuse_proposal({'proposal':before},{'beta':current['concepts']['alpha']},current)
    d=revised.decisions[0]
    assert d.assignment.decision=='reuse'
    assert d.case.filters_sufficient is True
    assert 'already admitted' in d.case.existing_plus_filters
    assert 'empty' not in d.explanation+d.case.existing_plus_filters+d.case.lost_distinction_if_merged
    assert d.case.question==stale.case.question
    assert d.case.properties_to_preserve==stale.case.properties_to_preserve
    assert d.case.boundary_examples==stale.case.boundary_examples
    assert d.assignment.members==stale.assignment.members
    assert p.model_dump(mode='json')==before
    validate_decision(d,current,cases[0],cases[1])

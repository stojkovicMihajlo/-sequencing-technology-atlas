"""Recovery integrity tests; synthetic verdicts do not validate biochemical truth."""

import json
import pytest
from test_workflow_transformations import cases as cases
from test_operation_registry import registry as registry
from test_shared_registry import bundle, queue, compare_all

from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.operation_registry.admission import VocabularyProposal, CurrentVocabularyReview
from genomics_discovery.curation.operation_registry.proposal_queue import enqueue, attach_review, submission, withdraw, missing_plan
from genomics_discovery.curation.operation_registry.publication import publish
from genomics_discovery.curation.operation_registry.reconciliation import request, replace_drafts, unresolved_components
from genomics_discovery.curation.operation_registry.storage import connect
from genomics_discovery.curation.operation_registry.store import catalogue


def setup_pending(cases, registry, tmp_path):
    keys = [queue(registry, cases, tmp_path, cid='alpha'),
            queue(registry, cases, tmp_path, index=1, cid='beta', name='Independent wording')]
    compare_all(registry, keys[0], 'unknown')
    return keys


def replacement(cases, registry, tmp_path, *, indices=(0, 1), approve=True):
    parts = [bundle(cases, registry.snapshot(), tmp_path, i, cid='shared', name='Revised shared scope') for i in indices]
    p = VocabularyProposal(decisions=tuple(p.decisions[0] for p, r, m in parts))
    checks = tuple(r.reviews[0] for p, r, m in parts)
    if not approve:
        checks = (checks[0].model_copy(update={'vocabulary_verdict': 'provisional'}), *checks[1:])
    r = CurrentVocabularyReview(proposal_sha256=digest(p), reviews=checks)
    m = {k: v for p, r, m in parts for k, v in m.items()}
    key = enqueue(registry.path, p, catalogue(registry.snapshot()), m)
    attach_review(registry.path, key, r)
    return key


def test_unknown_schedules_research_then_complete_revision_can_publish(cases, registry, tmp_path):
    keys = setup_pending(cases, registry, tmp_path)
    assert unresolved_components(registry.path) == [sorted(keys)]
    context = request(registry.path, keys)
    assert len(context['manifest']) == 2 and len(context['comparisons']) == 1
    assert all('review' not in row for row in context['drafts'])
    before = registry.snapshot()
    with connect(registry.path) as db:
        old = {k: submission(db, k) for k in keys}
    key = replacement(cases, registry, tmp_path)
    assert replace_drafts(registry.path, keys, key)['status'] == 'drafts_replaced'
    assert registry.snapshot() == before  # Retirement is NOT graph admission.
    assert not missing_plan(registry.path, key)['pairs']
    assert publish(registry.path, key)['status'] == 'committed'
    assert len(registry.snapshot()['concepts']) == 1
    assert len(registry.snapshot()['assignments']) == 2
    with connect(registry.path) as db:
        for ident in keys:
            saved = submission(db, ident)
            assert saved['proposal'] == old[ident]['proposal']
            assert saved['review'] == old[ident]['review']
            assert saved['result']['replacement_submission_id'] == key
        assert db.execute('SELECT count(*) FROM comparison_reports').fetchone()[0] == 1
    assert replace_drafts(registry.path, keys, key)['idempotent_replay']


def test_incomplete_or_unapproved_replacement_keeps_every_parent(cases, registry, tmp_path):
    keys = setup_pending(cases, registry, tmp_path)
    incomplete = replacement(cases, registry, tmp_path, indices=(0,))
    with pytest.raises(ValueError, match='exactly all original'):
        replace_drafts(registry.path, keys, incomplete)
    rejected = replacement(cases, registry, tmp_path, approve=False)
    assert replace_drafts(registry.path, keys, rejected)['status'] == 'replacement_not_approved'
    with connect(registry.path) as db:
        assert all(submission(db, k)['result'] is None for k in keys)
    assert not registry.snapshot()['concepts']


def test_outside_definitions_still_require_comparison(cases, registry, tmp_path):
    keys = setup_pending(cases, registry, tmp_path)
    queue(registry, cases, tmp_path, cid='outside', name='Not part of this revision')
    key = replacement(cases, registry, tmp_path)
    replace_drafts(registry.path, keys, key)
    assert publish(registry.path, key)['status'] == 'awaiting_comparisons'
    compare_all(registry, key, 'unknown')
    assert publish(registry.path, key)['status'] == 'unresolved'
    assert not registry.snapshot()['concepts']


def test_parent_completion_blocks_stale_replacement(cases, registry, tmp_path):
    keys = setup_pending(cases, registry, tmp_path)
    key = replacement(cases, registry, tmp_path)
    withdraw(registry.path, keys[0], 'Another coordinator superseded this work')
    with pytest.raises(ValueError, match='uncommitted drafts'):
        replace_drafts(registry.path, keys, key)
    with connect(registry.path) as db:
        assert submission(db, keys[1])['result'] is None


def test_missing_source_recheck_cannot_retire_drafts(cases, registry, tmp_path):
    keys = setup_pending(cases, registry, tmp_path)
    key = replacement(cases, registry, tmp_path)
    (tmp_path / 'a.txt').write_text('Changed source after review')
    with pytest.raises(ValueError):
        replace_drafts(registry.path, keys, key)
    with connect(registry.path) as db:
        assert all(submission(db, k)['result'] is None for k in keys)


async def test_recovery_failure_retires_only_new_failed_draft(cases, registry, tmp_path, monkeypatch):
    from playground import workflow_reconciliation_live as runner
    keys = setup_pending(cases, registry, tmp_path)
    async def source(manifest, database, run, **kwargs):
        assert kwargs['reconciliation_context']['submission_ids'] == sorted(keys)
        key = replacement(cases, registry, tmp_path, approve=False)
        run.mkdir()
        (run / 'state.json').write_text(json.dumps({'submission_id': key}))
        return {'submission_id': key, 'attempts': []}
    monkeypatch.setattr(runner, 'source_review', source)
    state = await runner.execute(registry.path, keys, tmp_path / 'recovery', worker=object())
    assert state['status'] == 'replacement_not_approved'
    with connect(registry.path) as db:
        assert all(submission(db, k)['result'] is None for k in keys)
        assert submission(db, state['replacement_submission_id'])['result']['status'] == 'withdrawn'


async def test_recovery_runner_publishes_reviewed_replacement(cases, registry, tmp_path, monkeypatch):
    from playground import workflow_reconciliation_live as runner
    keys = setup_pending(cases, registry, tmp_path)
    async def source(manifest, database, run, **kwargs):
        run.mkdir()
        key = replacement(cases, registry, tmp_path)
        (run / 'state.json').write_text(json.dumps({'submission_id': key}))
        return {'submission_id': key, 'attempts': []}
    monkeypatch.setattr(runner, 'source_review', source)
    state = await runner.execute(registry.path, keys, tmp_path / 'recovery', worker=object())
    assert state['status'] == 'committed'
    assert len(registry.snapshot()['assignments']) == 2


@pytest.mark.parametrize('external_peer', [False, True])
async def test_batch_recovers_once_and_does_not_take_over_external_drafts(
        cases, registry, tmp_path, monkeypatch, external_peer):
    from playground import workflow_shared_live as batch_runner
    from playground import workflow_reconciliation_live as recovery_runner
    keys = setup_pending(cases, registry, tmp_path)
    if external_peer:
        queue(registry, cases, tmp_path, cid='external', name='Another experiment owns this draft')
        compare_all(registry, keys[0], 'unknown')
    jobs = tmp_path / 'jobs.json'
    jobs.write_text(json.dumps([{'id': f'job-{i}', 'manifest': f'{i}.json'} for i in range(2)]))
    async def extract(manifest, database, run, **kwargs):
        i = int(manifest.stem)
        state = {'status': 'unresolved', 'source_runs': [{'submission_id': keys[i]}]}
        run.mkdir()
        (run / 'state.json').write_text(json.dumps(state))
        return state
    calls = []
    async def recover(database, selected, run, **kwargs):
        calls.append(selected)
        return {'status': 'committed', 'replacement_submission_id': 'fixture-result'}
    monkeypatch.setattr(batch_runner, 'execute', extract)
    monkeypatch.setattr(recovery_runner, 'execute', recover)
    state = await batch_runner.batch(jobs, registry.path, tmp_path / 'batch')
    assert calls == ([] if external_peer else [sorted(keys)])
    if not external_peer:
        assert len(state['reconciliations']) == 1
        assert all(j['status'] == 'unresolved' and j['recovery_status'] == 'committed'
                   for j in state['jobs'].values())


def test_recovery_prompt_does_not_tell_reviewer_to_follow_prior_comparisons(tmp_path):
    from genomics_discovery.curation.operation_registry.prompts import build_prompt
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'reconciliation.json').write_text('{}')
    packets = {'selection': tmp_path / 'packet.json'}
    compiler = build_prompt('compiler', run, run / 'compiler', packets)
    reviewer = build_prompt('reviewer', run, run / 'reviewer', packets, proposal_hash='0'*64)
    assert str(run / 'reconciliation.json') in compiler
    assert str(run / 'reconciliation.json') not in reviewer
    assert 'no required merge' in compiler and 'source_review' in reviewer

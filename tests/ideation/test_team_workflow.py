"""Offline four-designer handoffs; fake science must not become real validation."""
import asyncio
import copy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from genomics_discovery.ideation.models import parse_candidate
from genomics_discovery.ideation.review import required_specialist_targets, specialist_issue_id
from genomics_discovery.ideation.run import (
    NativeStages, pipeline, prepare, read, sha, stage_input_paths, write,
)
from genomics_discovery.illumina.models import digest
from tests.ideation.test_context import context
from tests.ideation.test_run import FakeStages


@pytest.fixture
def team_run(context, tmp_path, monkeypatch):
    request = tmp_path/'request.json'
    write(request, {'prompt': 'Develop a sequencing assay for the requested molecular distinction.',
        'constraints': ['Keep biological identity linked to the readout.']})
    directory = tmp_path/'team_run'
    prepare(request, context.path, directory, team=True, expert_count=4, max_parallel=4)
    monkeypatch.setitem(sys.modules, 'genomics_discovery.ideation.report', SimpleNamespace(
        export_report=lambda output, config, files, panel: write(output/'report_handoff.json',
            {'candidate_files': files, 'panel': panel})))
    return directory


class TeamStages(FakeStages):
    """Reuse existing author/literature/final-review fixtures, add team stages."""
    def __init__(self, *, fault=None, drop=None, issue_severity='blocking', **kwargs):
        super().__init__(**kwargs)
        self.fault, self.drop = fault, drop
        self.issue_severity = issue_severity
        self.active = self.max_active = 0
        self.events = []

    async def __call__(self, contract, attempt):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.events.append(('start', contract['stage'], attempt.name))
        await asyncio.sleep(0)
        try:
            return await self.execute(contract, attempt)
        finally:
            self.events.append(('end', contract['stage'], attempt.name))
            self.active -= 1

    async def execute(self, contract, attempt):
        role = contract['stage']
        if role in {'plan', 'propose', 'literature', 'review'}:
            receipt = await super().__call__(contract, attempt)
            payload = read(attempt/'artifact.json')
            if role == 'plan':
                while len(payload['experts']) < contract['expert_count']:
                    expert = copy.deepcopy(payload['experts'][0])
                    expert.update(id='expert_'+chr(ord('a')+len(payload['experts'])),
                        expertise='Alternative physical measurement', approach='A different fixture design route.')
                    payload['experts'].append(expert)
            elif role == 'review' and contract['revision_round'] == 0:
                payload['specialist_issue_resolutions'] = []
                for path in contract['specialist_report_files']:
                    specialist = read(path)
                    for assessed in specialist['reviews']:
                        review = next(r for r in payload['reviews'] if r['candidate_id'] == assessed['candidate_id'])
                        for issue in assessed['issues']:
                            copied = {key: issue[key] for key in ('id', 'target', 'severity', 'reason', 'next_test')}
                            copied['id'] = specialist_issue_id(specialist['specialism'], issue['id'])
                            review['issues'].append(copied)
                            payload['specialist_issue_resolutions'].append({
                                'specialism': specialist['specialism'], 'candidate_id': assessed['candidate_id'],
                                'issue_id': issue['id'], 'status': 'unresolved',
                                'reason': 'The same fixture dependency is still unresolved.'})
                if self.drop:
                    next(r for r in payload['reviews'] if r['candidate_id'] == self.drop).update(
                        disposition='drop', summary='Explicit fixture closure: no supported path for the assigned design.')
                if self.fault == 'drop_specialist_issue':
                    payload['reviews'][0]['issues'].pop()
                if self.fault == 'drop_specialist_resolution':
                    payload['specialist_issue_resolutions'].pop()
            elif role == 'review' and contract['revision_round'] == 1:
                if self.fault == 'downgrade_final_blocker':
                    payload['reviews'][0]['issues'][0]['severity'] = 'minor'
                if self.fault == 'repeat_old_specialist_ledger':
                    payload['specialist_issue_resolutions'] = read(contract['review_file'])['specialist_issue_resolutions']
                if self.fault == 'omit_final_open_issue':
                    payload['reviews'][0]['issues'].pop(0)
                if self.fault == 'rewrite_final_reason':
                    payload['reviews'][0]['issues'][0]['reason'] = 'Only a harmless documentation concern now.'
                if self.fault in {'resolved_no_basis', 'resolved_unknown_target', 'resolved_with_basis', 'resolved_still_open'}:
                    payload['previous_issue_resolutions'][0].update(status='resolved',
                        reason='The current exact candidate target is offered as a design-internal rebuttal.')
                    if self.fault != 'resolved_no_basis':
                        payload['previous_issue_resolutions'][0]['resolution_targets'] = [
                            'operation:invented' if self.fault == 'resolved_unknown_target' else 'operation:prepare']
                    if self.fault != 'resolved_still_open':
                        payload['reviews'][0]['issues'].pop(0)
            write(attempt/'artifact.json', payload)
            return receipt

        self.contracts.append(copy.deepcopy(contract))
        candidates = [parse_candidate(read(path)) for path in contract['candidate_files']]
        if role == 'peer':
            author = next(c for c in candidates if c.id == contract['expert_id'])
            target = candidates[(candidates.index(author)+1) % len(candidates)]
            payload = {'author_id': author.id, 'author_candidate_sha256': digest(author),
                'comments': [{'id': 'interface', 'target_candidate_id': target.id,
                    'target_candidate_sha256': digest(target), 'target': 'operation:prepare',
                    'kind': 'question', 'text': 'What supports the required template compatibility?'}]}
            if self.fault == 'peer_failure' and author.id == 'expert_a':
                raise ValueError('Fixture peer execution failed')
            if len(candidates) == 1 or self.fault == 'no_peer_feedback':
                payload.update(comments=[], no_feedback_reason='No decision-relevant peer comment in this fixture.')
        elif role == 'specialist':
            lens = contract['specialism']
            payload = {'specialism': lens, 'reviews': [], 'summary': 'Independent specialist fixture.'}
            for candidate in candidates:
                payload['reviews'].append({'candidate_id': candidate.id, 'candidate_sha256': digest(candidate),
                    'reviewed_targets': sorted(required_specialist_targets(candidate, lens) | {'operation:prepare'}),
                    'issues': [{'id': 'interface', 'target': 'operation:prepare', 'severity': self.issue_severity,
                        'reason': lens+' identifies an unsupported fixture dependency.',
                        'next_test': 'Retrieve evidence or redesign the identified dependency.'}],
                    'summary': 'No assay validation in this test.'})
            if self.fault == 'stale_specialist' and lens == 'chemistry_interfaces':
                payload['reviews'][0]['candidate_sha256'] = 'f'*64
        elif role == 'revise':
            previous = read(contract['previous_candidate_file'])
            review = next(r for r in read(contract['review_file'])['reviews'] if r['candidate_id'] == previous['id'])
            payload = {'candidate': copy.deepcopy(previous), 'responses': [
                {'issue_id': issue['id'], 'status': 'unresolved', 'explanation': 'The specialist dependency still requires evidence.'}
                for issue in review['issues']], 'peer_responses': []}
            # An actual changed scientific claim: final review cannot borrow the old hash.
            payload['candidate']['expected_benefit'] = 'A revised untested benefit after peer inspection.'
            for path in contract['peer_report_files']:
                peer = read(path)
                for comment in peer['comments']:
                    if comment['target_candidate_id'] == previous['id']:
                        payload['peer_responses'].append({'peer_author_id': peer['author_id'],
                            'comment_id': comment['id'], 'status': 'unresolved',
                            'explanation': 'Compatibility is still a specific evidence question; the proposed gain is qualified.'})
            if self.fault == 'omit_peer_reply' and previous['id'] == 'expert_a':
                payload['peer_responses'] = []
        else:
            raise AssertionError('Unexpected fixture stage: '+role)
        write(attempt/'artifact.json', payload)
        return {'thread_id': 'offline-team-'+attempt.name, 'usage': {},
            'web_search_event_observed': False, 'elapsed_seconds': 0}


@pytest.mark.asyncio
async def test_four_designers_discuss_receive_two_lenses_and_answer_exact_feedback(team_run):
    stages = TeamStages()
    state = await pipeline(team_run, stages)
    assert state['status'] == 'completed'
    assert state['designers_requested'] == state['valid_initial_candidates'] == state['peer_reports_received'] == 4
    assert stages.max_active == 4
    drafts = [c for c in stages.contracts if c['stage'] == 'propose']
    assert len(drafts) == 4 and all(not c['candidate_files'] for c in drafts)
    assert len({c['sources_file'] for c in drafts}) == 4
    events = stages.events
    for earlier, later in [({'propose'}, {'peer', 'literature'}),
                           ({'peer', 'literature'}, {'specialist'})]:
        assert max(i for i, event in enumerate(events) if event[0] == 'end' and event[1] in earlier) < min(
            i for i, event in enumerate(events) if event[0] == 'start' and event[1] in later)
    assert max(i for i, event in enumerate(events) if event[:2] == ('end', 'specialist')) < events.index(
        ('start', 'review', '05_synthesis_review'))
    assert max(i for i, event in enumerate(events) if event[:2] == ('end', 'revise')) < events.index(
        ('start', 'review', '07_final_review'))
    specialists = [c for c in stages.contracts if c['stage'] == 'specialist']
    assert {c['specialism'] for c in specialists} == {'chemistry_interfaces', 'measurement_benefit'}
    assert all(len(c['candidate_files']) == len(c['peer_report_files']) == 4 for c in specialists)
    reviews = [c for c in stages.contracts if c['stage'] == 'review']
    assert len(reviews) == 2
    initial = read(team_run/'initial_review.json')
    assert len(initial['specialist_issue_resolutions']) == 8
    assert all(len(r['issues']) == 2 for r in initial['reviews'])
    final = read(team_run/'final_review.json')
    assert len(final['previous_issue_resolutions']) == 8
    assert final['specialist_issue_resolutions'] == []
    assert all(issue['severity'] == 'blocking' for r in final['reviews'] for issue in r['issues'])
    assert set(state['final_dispositions'].values()) == {'hold'}
    conversation = read(team_run/'conversation.json')
    assert conversation['rounds'] == 1 and len(conversation['comments']) == 4
    assert all(c['response_status'] == 'unresolved' for c in conversation['comments'])
    for comment in conversation['comments']:
        replies = read(comment['response_file'])
        assert any(r['peer_author_id'] == comment['peer_author_id'] and r['comment_id'] == comment['id'] for r in replies)
    final_contract = reviews[-1]
    assert len(final_contract['peer_response_files']) == 4
    assert set(final_contract['peer_response_files']) <= stage_input_paths(final_contract)
    assert set(final_contract['peer_report_files']) <= stage_input_paths(final_contract)
    assert set(final_contract['specialist_report_files']) <= stage_input_paths(final_contract)
    assert all(Path(p).name == 'revised.json' for p in state['candidate_files'])
    assert all(not value['eligible'] for value in final_contract['inherited_search_coverage'].values())
    assert not (team_run/'atlas.json').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault,stage_fragment', [
    ('stale_specialist', '04_chemistry_interfaces'),
    ('drop_specialist_issue', '05_synthesis_review'),
    ('drop_specialist_resolution', '05_synthesis_review'),
    ('downgrade_final_blocker', '07_final_review'),
    ('repeat_old_specialist_ledger', '07_final_review'),
])
async def test_stale_or_erased_specialist_objections_stop_the_run(team_run, fault, stage_fragment):
    with pytest.raises(ValueError):
        await pipeline(team_run, TeamStages(fault=fault))
    state = read(team_run/'state.json')
    assert state['status'] == 'failed'
    assert any(f['stage'] == stage_fragment for f in state['failures'])
    assert not (team_run/'final_review.json').exists()


@pytest.mark.asyncio
async def test_final_review_must_account_for_prior_specialist_issues(team_run):
    with pytest.raises(ValueError, match='every previous issue'):
        await pipeline(team_run, TeamStages(omit_final_resolution=True))
    assert (team_run/'candidates/expert_a/revised.json').exists()
    assert not (team_run/'final_review.json').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault,error', [
    ('resolved_no_basis', 'evidence or exact current candidate targets'),
    ('resolved_unknown_target', 'valid current candidate targets'),
    ('resolved_still_open', 'resolved'),
])
async def test_final_rebuttal_requires_exact_basis_and_consistent_open_issues(team_run, fault, error):
    with pytest.raises(ValueError, match=error):
        await pipeline(team_run, TeamStages(fault=fault))
    state = read(team_run/'state.json')
    assert state['status'] == 'failed'
    assert any(row['stage'] == '07_final_review' for row in state['failures'])


@pytest.mark.asyncio
async def test_explicit_final_rebuttal_is_located_but_not_claimed_scientifically_verified(team_run):
    state = await pipeline(team_run, TeamStages(fault='resolved_with_basis'))
    assert state['status'] == 'completed'
    final = read(team_run/'final_review.json')
    assert final['previous_issue_resolutions'][0]['status'] == 'resolved'
    assert final['previous_issue_resolutions'][0]['resolution_targets'] == ['operation:prepare']
    assert len(final['reviews'][0]['issues']) == 1
    assert final['reviews'][0]['disposition'] == 'hold'


@pytest.mark.asyncio
@pytest.mark.parametrize('severity', ['major', 'minor'])
@pytest.mark.parametrize('fault', ['omit_final_open_issue', 'rewrite_final_reason'])
async def test_unresolved_nonblocking_objections_cannot_be_erased_or_rewritten(team_run, severity, fault):
    with pytest.raises(ValueError, match='[Uu]nresolved'):
        await pipeline(team_run, TeamStages(fault=fault, issue_severity=severity))
    assert not (team_run/'final_review.json').exists()


@pytest.mark.asyncio
async def test_failed_author_is_visible_and_three_successful_designs_continue(team_run):
    stages = TeamStages(fail_expert='expert_d')
    state = await pipeline(team_run, stages)
    assert state['status'] == 'completed_with_stage_failures'
    assert state['designers_requested'] == 4 and state['valid_initial_candidates'] == 3
    assert {read(p)['id'] for p in state['candidate_files']} == {'expert_a', 'expert_b', 'expert_c'}
    assert any(row['stage'] == '02_expert_d' for row in state['failures'])
    assert all(len(c['candidate_files']) == 3 for c in stages.contracts if c['stage'] == 'specialist')


@pytest.mark.asyncio
async def test_retained_design_with_major_issues_still_gets_author_response_without_peer_comment(team_run):
    stages = TeamStages(retain_first=True, issue_severity='major', fault='no_peer_feedback')
    state = await pipeline(team_run, stages)
    assert state['status'] == 'completed'
    initial = read(team_run/'initial_review.json')
    first = next(r for r in initial['reviews'] if r['candidate_id'] == 'expert_a')
    assert first['disposition'] == 'retain_for_testing'
    assert {i['severity'] for i in first['issues']} == {'major'}
    assert read(team_run/'conversation.json')['comments'] == []
    assert any(c['stage'] == 'revise' and c['expert_id'] == 'expert_a' for c in stages.contracts)
    responses = read(team_run/'candidates/expert_a/issue_responses.json')
    assert {r['issue_id'] for r in responses} == {i['id'] for i in first['issues']}
    assert state['outstanding_issues']['expert_a'] == {'blocking': 0, 'major': 2, 'minor': 0}
    assert state['final_dispositions']['expert_a'] == 'retain_for_testing'


@pytest.mark.asyncio
async def test_preparation_failure_is_visible_when_other_peer_branches_continue(team_run, monkeypatch):
    import genomics_discovery.ideation.run as runner
    original_merge = runner.merge_sources
    failed_once = False

    def fail_first_peer_merge(paths):
        nonlocal failed_once
        if not failed_once and len(paths) == 5:
            failed_once = True
            raise ValueError('Fixture source merge failed before peer worker dispatch')
        return original_merge(paths)

    monkeypatch.setattr(runner, 'merge_sources', fail_first_peer_merge)
    stages = TeamStages()
    state = await pipeline(team_run, stages)
    assert state['status'] == 'completed_with_stage_failures'
    failed = [row for row in state['stages'] if row.get('failure_phase') == 'stage_preparation']
    assert len(failed) == 1 and failed[0]['role'] == 'peer'
    assert failed[0]['status'] == 'failed'
    assert len(state['failures']) == 1
    assert state['failures'][0]['stage'] == '03_peer_expert_a'
    assert not any(c['stage'] == 'peer' and c['expert_id'] == 'expert_a' for c in stages.contracts)
    assert state['peer_reports_received'] == 3
    assert read(team_run/'conversation.json')['failed_peer_authors'] == ['expert_a']


@pytest.mark.asyncio
async def test_missing_peer_reply_is_a_recorded_author_failure_not_silent_completion(team_run):
    state = await pipeline(team_run, TeamStages(fault='omit_peer_reply'))
    assert state['status'] == 'completed_with_stage_failures'
    assert any(row['stage'] == '06_expert_a' for row in state['failures'])
    assert not (team_run/'candidates/expert_a/revised.json').exists()
    comments = read(team_run/'conversation.json')['comments']
    assert {c['response_status'] for c in comments if c['target_candidate_id'] == 'expert_a'} == {'author_stage_failed'}
    assert all(c['response_status'] == 'unresolved' for c in comments if c['target_candidate_id'] != 'expert_a')


@pytest.mark.asyncio
async def test_failed_peer_is_recorded_without_fabricating_discussion(team_run):
    state = await pipeline(team_run, TeamStages(fault='peer_failure'))
    conversation = read(team_run/'conversation.json')
    assert state['status'] == 'completed_with_stage_failures'
    assert conversation['failed_peer_authors'] == ['expert_a']
    assert state['peer_reports_received'] == 3
    assert not any(c['peer_author_id'] == 'expert_a' for c in conversation['comments'])


@pytest.mark.asyncio
async def test_dropped_design_gets_explicit_comment_closure_instead_of_fabricated_reply(team_run):
    stages = TeamStages(drop='expert_d')
    await pipeline(team_run, stages)
    revisions = [c['expert_id'] for c in stages.contracts if c['stage'] == 'revise']
    assert 'expert_d' not in revisions
    comments = read(team_run/'conversation.json')['comments']
    closed = [c for c in comments if c['target_candidate_id'] == 'expert_d']
    assert closed and all(c['response_status'] == 'closed_with_dropped_candidate' and c['closure_reason'] for c in closed)
    assert all('response_file' not in c for c in closed)


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_key', ['peer_report_files', 'peer_response_files', 'specialist_report_files'])
async def test_native_transport_pins_team_feedback_and_response_files(tmp_path, monkeypatch, changed_key):
    import genomics_discovery.ideation.run as runner
    attempt = tmp_path/'attempt'; attempt.mkdir()
    contract = {'stage': 'review', 'format_version': 'ideation_run/3'}
    for key in ['request_file', 'atlas_file', 'schema_file', 'sources_file']:
        path = tmp_path/(key+'.json'); write(path, {'sources': {}})
        contract[key] = str(path)
    for key in ['peer_report_files', 'peer_response_files', 'specialist_report_files']:
        path = tmp_path/(key+'.json'); write(path, {'fixture': key})
        contract[key] = [str(path)]
    write(attempt/'contract.json', contract)
    changed_path = Path(contract[changed_key][0])
    initial_pin = sha(changed_path)

    async def fake_run(prompt, directory):
        changed_path.write_text('{"tampered_feedback": true}')
        return SimpleNamespace(error=None, exit_code=0, thread_id='fixture', usage={}, elapsed_seconds=0)

    native = NativeStages.__new__(NativeStages)
    native.worker, native.threads = SimpleNamespace(run=fake_run), set()
    monkeypatch.setattr(runner, 'assess_archived_attempt', lambda _: SimpleNamespace(complete=True, thread_id='fixture', usage={}))
    with pytest.raises(ValueError, match='registered stage input changed'):
        await native(contract, attempt)
    assert read(attempt/'input_pins.json')[str(changed_path)] == initial_pin

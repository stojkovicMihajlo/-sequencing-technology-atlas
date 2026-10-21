"""Source-first omission mutation tests with injected jobs, never native models."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest

from test_workflow_extraction_runtime import FakeWorker, make_review, source_case
from genomics_discovery.curation.codex_worker import WorkerResult
from genomics_discovery.curation.workflow_extraction.models import Workflow, WorkflowReview, digest
from genomics_discovery.curation.workflow_extraction.obligations import (
    SourceObligations, SourceCoverageReview, source_coverage_outcome,
    validate_source_obligations, validate_source_coverage_review,
)
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute


def obligations_for(packet):
    source = packet['sources'][0]
    passage = source['passages'][0]
    ref = {'source_id': source['source_id'], 'passage_id': passage['passage_id']}
    ev = {**ref, 'quote': 'Wash nuclei with buffer at 4 C.', 'support': 'reported',
          'rationale': 'The source explicitly reports washing and its conditions.'}
    return {
        'source_packet_sha256': digest(packet),
        **{k: packet[k] for k in ('method_id', 'scope', 'variant')},
        'source_scope': [{'registered_source_id': source['source_id'], 'disposition': 'inspected',
                          'inspected_passages': [ref], 'rationale': 'Read the scoped source.',
                          'blocks_completeness': False}],
        'obligations': [{'id': 'wash_required', 'requirement': 'Wash nuclei with buffer at 4 C.',
                         'applicability': 'required', 'rationale': 'Required source operation.',
                         'evidence': [ev]}],
    }


def coverage_for(obligations, workflow, review, *, missing=False):
    return {
        'source_packet_sha256': obligations.source_packet_sha256,
        'obligations_sha256': digest(obligations), 'workflow_sha256': digest(workflow),
        'workflow_review_sha256': digest(review),
        'inspected_passages': [ref.model_dump() for ref in obligations.source_scope[0].inspected_passages],
        'checks': [{'obligation_id': 'wash_required',
                    'disposition': 'missing' if missing else 'satisfied', 'selected': True,
                    'targets': [] if missing else ['operation:wash'],
                    'rationale': 'The candidate omits the source-required wash.' if missing else
                                 'The actual wash operation retains the source action/resources.',
                    'evidence': [ev.model_dump() for ev in obligations.obligations[0].evidence]}],
    }


class SourceWorker(FakeWorker):
    def __init__(self, case, *, omit_wash=False, stale=False, fix_on_revision=False):
        super().__init__(case)
        self.full_workflow = copy.deepcopy(self.workflow)
        self.omit_wash = omit_wash
        self.stale = stale
        self.fix_on_revision = fix_on_revision
        if omit_wash:
            self.workflow['operations'] = [o for o in self.workflow['operations'] if o['id'] != 'wash']
            self.workflow['flows'] = [f for f in self.workflow['flows']
                                      if 'wash' not in (f['source'], f['target'])]
            self.workflow['modules'] = [m for m in self.workflow['modules']
                                        if 'wash' not in m['operation_ids']]

    async def run(self, prompt, directory):
        role = re.search(r'ROLE:\s*(\w+)', prompt).group(1)
        directory = Path(directory)
        if role == 'source_obligations':
            assert 'Read the candidate:' not in prompt
            assert not (directory.parents[1] / 'initial_workflow.json').exists()
            self.calls.append({'role': role, 'prompt': prompt, 'directory': directory})
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'prompt.txt').write_text(prompt)
            (directory / 'artifact.json').write_text(json.dumps(obligations_for(self.packet)))
            thread = f'prep-{len(self.calls)}'
            events = [{'type': 'thread.started', 'thread_id': thread}, {'type': 'turn.started'},
                      {'type': 'turn.completed', 'usage': {}}]
            (directory / 'events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in events))
            (directory / 'final.txt').write_text('Saved source obligations.')
            result = WorkerResult(0, thread, {}, .01, None)
            (directory / 'result.json').write_text(json.dumps(asdict(result)))
            return result
        if role == 'integrator' and self.integrations and self.fix_on_revision:
            self.workflow = copy.deepcopy(self.full_workflow)
        result = await super().run(prompt, directory)
        if role == 'reviewer':
            obligations = SourceObligations.model_validate_json(
                (directory / 'source_obligations.json').read_bytes())
            workflow = Workflow.model_validate_json((directory / 'candidate.json').read_bytes())
            review = WorkflowReview.model_validate_json((directory / 'artifact.json').read_bytes())
            missing = not any(o.id == 'wash' for o in workflow.operations)
            coverage = coverage_for(obligations, workflow, review, missing=missing)
            if self.stale:
                coverage['workflow_review_sha256'] = '0' * 64
            (directory / 'source_coverage_review.json').write_text(json.dumps(coverage))
        return result


async def test_baseline_source_preparation_is_blind_and_not_specialists(source_case, tmp_path):
    worker = SourceWorker(source_case)
    run = tmp_path / 'source_first'
    state = await execute(source_case[1], run, worker=worker, mode='baseline',
                          require_source_coverage=True, max_revisions=0)
    assert state['status'] == 'supported', state.get('error')
    assert [c['role'] for c in worker.calls] == ['source_obligations', 'integrator', 'reviewer']
    assert worker.max_active_readers == 0
    assert (run / 'graph_candidate.json').is_file()
    assert state['artifacts']['source_obligations']['document_sha256']
    assert state['artifacts']['source_coverage_review']['document_sha256']
    assert 'Methods references' in worker.calls[0]['prompt']
    assert 'document_missing' in worker.calls[0]['prompt']
    assert 'source_coverage_review.json' in worker.calls[-1]['prompt']
    assert (run / 'implementation/genomics_discovery/curation/workflow_extraction/obligations.py').is_file()


async def test_omitted_required_source_operation_blocks_positive_primary_export(source_case, tmp_path):
    worker = SourceWorker(source_case, omit_wash=True)
    run = tmp_path / 'omitted'
    state = await execute(source_case[1], run, worker=worker, mode='baseline',
                          require_source_coverage=True, max_revisions=0)
    assert state['status'] == 'needs_revision', state.get('error')
    assert not (run / 'graph_candidate.json').exists()
    assert not (run / 'protocol.txt').exists()
    assert 'wash' not in {o['id'] for o in worker.workflow['operations']}
    primary = WorkflowReview.model_validate_json(Path(state['review']).read_bytes())
    assert all(c.verdict == 'supported' for c in primary.checks)


async def test_source_review_is_regenerated_after_repair(source_case, tmp_path):
    worker = SourceWorker(source_case, omit_wash=True, fix_on_revision=True)
    state = await execute(source_case[1], tmp_path / 'fixed', worker=worker, mode='baseline',
                          require_source_coverage=True, max_revisions=1)
    assert state['status'] == 'supported', state.get('error')
    assert [c['role'] for c in worker.calls].count('source_obligations') == 1
    assert worker.integrations == worker.reviews == 2
    assert 'previous_source_coverage_review' in worker.calls[-2]['prompt']


async def test_stale_source_review_binding_is_rejected(source_case, tmp_path):
    state = await execute(source_case[1], tmp_path / 'stale', worker=SourceWorker(source_case, stale=True),
                          mode='baseline', require_source_coverage=True, max_revisions=0)
    assert state['status'] == 'failed'
    assert 'workflow_review_sha256' in state['error']
    assert not (tmp_path / 'stale/graph_candidate.json').exists()


async def test_review_only_reuses_and_revalidates_frozen_source_obligations(source_case, tmp_path):
    prior = tmp_path / 'frozen.json'
    prior.write_text(json.dumps(obligations_for(source_case[0])))
    seed = tmp_path / 'seed.json'
    seed.write_text(json.dumps(source_case[2]))
    worker = SourceWorker(source_case)
    state = await execute(source_case[1], tmp_path / 'resumed', worker=worker, mode='review_only',
                          initial_workflow=seed, require_source_coverage=True,
                          source_obligations=prior, max_revisions=0)
    assert state['status'] == 'supported', state.get('error')
    assert [c['role'] for c in worker.calls] == ['reviewer']
    bad = obligations_for(source_case[0]); bad['source_packet_sha256'] = '0'*64
    prior.write_text(json.dumps(bad))
    worker = SourceWorker(source_case)
    failed = await execute(source_case[1], tmp_path / 'bad_resume', worker=worker, mode='review_only',
                           initial_workflow=seed, require_source_coverage=True,
                           source_obligations=prior, max_revisions=0)
    assert failed['status'] == 'failed' and not worker.calls
    assert 'packet digest' in failed['error']


def bound_case(source_case):
    packet, _, raw = source_case
    obligations = SourceObligations.model_validate(obligations_for(packet))
    workflow = Workflow.model_validate(raw)
    review = WorkflowReview.model_validate(make_review(raw, packet))
    coverage = SourceCoverageReview.model_validate(coverage_for(obligations, workflow, review))
    return packet, obligations, workflow, review, coverage


@pytest.mark.parametrize('binding', ['source_packet_sha256', 'obligations_sha256',
                                    'workflow_sha256', 'workflow_review_sha256'])
def test_all_source_review_bindings_are_checked(source_case, binding):
    packet, obligations, workflow, review, coverage = bound_case(source_case)
    assert validate_source_coverage_review(coverage, obligations, review, workflow, packet) == []
    bad = coverage.model_dump(); bad[binding] = '0'*64
    errors = validate_source_coverage_review(SourceCoverageReview.model_validate(bad),
                                              obligations, review, workflow, packet)
    assert any(binding in error for error in errors)


@pytest.mark.parametrize('mutation', ['missing_check', 'duplicate_check', 'bad_target', 'bad_quote',
                                      'waived_required'])
def test_source_accounting_and_claim_integrity(source_case, mutation):
    packet, obligations, workflow, review, coverage = bound_case(source_case)
    raw = coverage.model_dump(mode='json')
    if mutation == 'missing_check':
        raw['checks'][0]['obligation_id'] = 'not_the_obligation'
    elif mutation == 'duplicate_check':
        raw['checks'].append(copy.deepcopy(raw['checks'][0]))
    elif mutation == 'bad_target':
        raw['checks'][0]['targets'] = ['operation:fiction']
    elif mutation == 'bad_quote':
        raw['checks'][0]['evidence'][0]['quote'] = 'Fabricated source evidence'
    else:
        raw['checks'][0].update(selected=False, disposition='not_applicable', targets=[])
    assert validate_source_coverage_review(SourceCoverageReview.model_validate(raw),
                                           obligations, review, workflow, packet)


@pytest.mark.parametrize('blocking', [False, True])
def test_missing_document_and_detail_limits_have_explicit_criticality(source_case, blocking):
    packet, obligations, workflow, review, _ = bound_case(source_case)
    raw = obligations.model_dump(mode='json')
    raw['source_limits'] = [{'document_or_fact': 'Decision-relevant supplement S1',
        'cause': 'document_missing', 'consequence': 'Unknown interface' if blocking else
        'Unselected optional internal detail', 'blocking': blocking,
        'next_evidence': 'Retrieve original supplement S1 from the primary paper.',
        'evidence': raw['obligations'][0]['evidence']}]
    obligations = SourceObligations.model_validate(raw)
    coverage = SourceCoverageReview.model_validate(coverage_for(obligations, workflow, review))
    assert validate_source_coverage_review(coverage, obligations, review, workflow, packet) == []
    assert source_coverage_outcome(coverage, obligations, review, workflow) == (
        'research_needed' if blocking else 'supported')


def test_optional_route_can_be_unselected_but_selected_omission_blocks(source_case):
    packet, obligations, workflow, review, _ = bound_case(source_case)
    raw = obligations.model_dump(mode='json')
    raw['obligations'][0]['applicability'] = 'optional'
    obligations = SourceObligations.model_validate(raw)
    raw = coverage_for(obligations, workflow, review)
    raw['checks'][0].update(selected=False, disposition='not_applicable', targets=[])
    coverage = SourceCoverageReview.model_validate(raw)
    assert validate_source_coverage_review(coverage, obligations, review, workflow, packet) == []
    assert source_coverage_outcome(coverage, obligations, review, workflow) == 'supported'
    raw['checks'][0].update(selected=True, disposition='missing')
    coverage = SourceCoverageReview.model_validate(raw)
    assert source_coverage_outcome(coverage, obligations, review, workflow) == 'needs_revision'


def test_registered_source_scope_and_access_limits_are_explicit(source_case):
    packet, obligations, workflow, review, _ = bound_case(source_case)
    raw = obligations.model_dump(mode='json')
    raw['source_scope'][0].update(disposition='access_limited', next_evidence=None,
                                  blocks_completeness=True)
    assert any('next evidence' in x for x in validate_source_obligations(
        SourceObligations.model_validate(raw), packet))
    raw['source_scope'][0]['next_evidence'] = 'Readable original Methods pages.'
    obligations = SourceObligations.model_validate(raw)
    coverage = SourceCoverageReview.model_validate(coverage_for(obligations, workflow, review))
    assert validate_source_coverage_review(coverage, obligations, review, workflow, packet) == []
    assert source_coverage_outcome(coverage, obligations, review, workflow) == 'research_needed'
    raw['source_scope'][0]['registered_source_id'] = 'not_in_packet'
    assert validate_source_obligations(SourceObligations.model_validate(raw), packet)


def test_cli_source_integrity_and_optional_flag(source_case, tmp_path, monkeypatch, capsys):
    import sys
    from genomics_discovery.curation.workflow_extraction import __main__ as cli
    frozen = tmp_path / 'obligations.json'
    frozen.write_text(json.dumps(obligations_for(source_case[0])))
    monkeypatch.setattr(sys, 'argv', ['atlas-extract', 'check', str(frozen),
        '--kind', 'source_obligations', '--packet', str(source_case[1])])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    assert json.loads(capsys.readouterr().out)['valid'] is True
    worker = SourceWorker(source_case)
    monkeypatch.setattr(cli, 'CodexWorker', lambda **kwargs: worker)
    monkeypatch.setattr(sys, 'argv', ['atlas-extract', 'run', str(source_case[1]),
        '--run', str(tmp_path / 'cli_run'), '--mode', 'baseline', '--max-revisions', '0',
        '--require-source-coverage', '--source-obligations', str(frozen)])
    cli.main()
    state = json.loads(capsys.readouterr().out)
    assert state['status'] == 'supported'
    assert [x['role'] for x in worker.calls] == ['integrator', 'reviewer']

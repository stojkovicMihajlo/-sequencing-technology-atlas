"""Source-mode admission and few-shot blinding; synthetic data, no model calls."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from genomics_discovery.execution.codex_worker import WorkerResult
from genomics_discovery.illumina import extraction as batch
from genomics_discovery.illumina.graph import _load_card, protocol_text
from genomics_discovery.illumina.models import parse_extraction, parse_review, validate_extraction
from genomics_discovery.illumina.prompts import build_prompt
from tests.illumina.test_illumina_diagrams import example
from tests.illumina.test_representation_contract import typed
from tests.illumina.test_representation_pipeline import fresh_review


@pytest.fixture
def paper_case(typed, tmp_path):
    data, sources, _ = copy.deepcopy(typed)
    evidence = copy.deepcopy(data['paper_matches'][0]['evidence'][0])
    evidence['locator'] = 'Methods, synthetic text fixture'

    def convert(value):
        if isinstance(value, dict):
            if value.get('basis') == 'image':
                return copy.deepcopy(evidence)
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, list):
            return [convert(v) for v in value]
        return value

    data = convert(data)
    data['inspected_sources'] = ['paper:1']
    sources = {'paper:1': sources['paper:1']}
    path = tmp_path / 'article.txt'; path.write_text(sources['paper:1']['text'])
    sources['paper:1'].update(kind='text', content_level='fulltext',
        document_role='primary_article', path=str(path), sha256=batch.sha(path))
    card = {'record_id': data['record_id'], 'source_title': data['title'],
        'verified_method_names': data['method_names'], 'source_mode': 'paper',
        'implementation_scope': 'Synthetic paper-only transport fixture'}
    return data, sources, evidence, card


def test_explicit_paper_mode_does_not_weaken_default_diagram_gate(paper_case):
    data, sources, _, _ = paper_case
    candidate = parse_extraction(data)
    assert any('inspected registered image' in e for e in validate_extraction(candidate, sources))
    assert not validate_extraction(candidate, sources, source_mode='paper')
    assert validate_extraction(candidate, sources, source_mode='unknown')


@pytest.mark.parametrize('mutation', [
    {'content_level': 'abstract'}, {'content_level': 'metadata'},
    {'document_role': 'supplement'}, {'kind': 'image'},
])
def test_primary_fulltext_is_required(paper_case, mutation):
    data, sources, _, _ = paper_case
    sources['paper:1'].update(mutation)
    assert any('full primary paper' in e for e in validate_extraction(parse_extraction(data), sources, source_mode='paper'))


def test_example_source_cannot_become_target_evidence(paper_case):
    data, sources, _, _ = paper_case
    data['states'][0]['evidence'][0]['source_id'] = 'worked-example:unregistered'
    assert validate_extraction(parse_extraction(data), sources, source_mode='paper')


def test_examples_are_literal_author_only_inputs():
    context = {'source_mode': 'paper', 'representation_version': 'illumina_diagram/1.1'}
    worked = [{'record_id': 'EXAMPLE_ONLY_SENTINEL'}]
    text = build_prompt('author', context, {'type': 'object'}, examples=worked)
    assert json.dumps(worked, separators=(',', ':')) in text
    assert 'EXAMPLE_ONLY_SENTINEL' not in build_prompt('reviewer', context, {'type': 'object'})
    with pytest.raises(ValueError, match='paper-mode author'):
        build_prompt('reviewer', context, {'type': 'object'}, examples=worked)
    with pytest.raises(ValueError, match='paper-mode author'):
        build_prompt('author', {}, {'type': 'object'}, examples=worked)


class PaperWorker:
    def __init__(self, candidate, sources, evidence):
        self.candidate, self.sources, self.evidence = candidate, sources, evidence
        self.calls = []

    async def run(self, prompt, attempt, *, image_paths=()):
        assert not image_paths
        role = 'author' if attempt.name.endswith('author') else 'reviewer'
        self.calls.append((role, prompt))
        document = (self.candidate if role == 'author' else
            fresh_review(parse_extraction(batch.read(attempt / 'candidate.json')), self.sources, self.evidence))
        batch.write(attempt / 'artifact.json', document.model_dump(mode='json'))
        thread, usage = 'paper-fake-' + str(len(self.calls)), {'input_tokens': 10, 'output_tokens': 5}
        (attempt / 'prompt.txt').write_text(prompt)
        batch.write(attempt / 'command.json', {'argv': ['fake-worker', 'exec', '-'], 'cwd': str(attempt), 'image_inputs': []})
        batch.write(attempt / 'started.json', {'spawned': True, 'hostname': 'unit-test'})
        events = [{'type': 'thread.started', 'thread_id': thread}, {'type': 'turn.started'},
            {'type': 'turn.completed', 'usage': usage}]
        (attempt / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        (attempt / 'final.txt').write_text('Synthetic transport only.')
        batch.write(attempt / 'result.json', {'exit_code': 0, 'thread_id': thread, 'usage': usage,
            'elapsed_seconds': .1, 'error': None})
        return WorkerResult(0, thread, usage, .1, None)


def test_paper_author_review_export_reopen_and_blinding(paper_case, tmp_path):
    data, sources, evidence, card = paper_case
    worker = PaperWorker(parse_extraction(data), sources, evidence)
    directory = tmp_path / 'run'
    result = asyncio.run(batch.extract_registered_workflow(card, sources, [], directory, worker,
        max_revisions=0, author_examples=[{'record_id': 'EXAMPLE_ONLY_SENTINEL'}]))
    assert result['status'] == 'supported'
    assert 'EXAMPLE_ONLY_SENTINEL' in worker.calls[0][1]
    assert 'EXAMPLE_ONLY_SENTINEL' not in worker.calls[1][1]
    assert [r for r, _ in worker.calls] == ['author', 'reviewer']
    result['attempt_directory'] = str(directory)
    graph, reopened = _load_card(result, card)
    assert graph['source_mode'] == 'paper'
    assert reopened == sources
    assert graph['workflow']['states'] == parse_extraction(data).model_dump(mode='json')['states']
    assert 'OPERATION split' in protocol_text(graph, sources=sources)
    batch.revalidate_completed(result)
    # A later byte change cannot preserve admission even for a paper-only run.
    Path(sources['paper:1']['path']).write_text('Changed after admission')
    with pytest.raises(ValueError):
        _load_card(result, card)

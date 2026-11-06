"""Reuse native author work, never reuse an uncompleted call or scientific verdict."""
import asyncio
import copy

import pytest

from genomics_discovery.illumina import extraction as batch
from genomics_discovery.illumina import prompts
from tests.illumina.test_illumina_batch_controls import acquired, FakeWorker


@pytest.fixture
def completed_author(acquired, tmp_path):
    root, card, sources, _ = acquired
    worker = FakeWorker()
    original = tmp_path / 'original'
    asyncio.run(batch.extract_card(card, sources, root, original, worker))
    registry, images = batch.card_sources(card, sources, root)
    seed = {'directory': str(original), 'pins': {n: batch.sha(original / n)
            for n in ('card.json', 'source_registry.json', 'stages.json')}}
    return card, registry, images, worker, seed


def test_completed_author_gets_a_new_fresh_review_without_a_new_author(completed_author, tmp_path):
    card, registry, images, worker, seed = completed_author
    output = tmp_path / 'resumed'
    result = asyncio.run(batch.extract_registered_workflow(card, registry, images, output, worker,
        max_revisions=0, representation='illumina_diagram/1.0', author_seed=seed))
    assert result['status'] == 'supported'
    assert [x['role'] for x in worker.calls] == ['author', 'reviewer', 'reviewer']
    stages = batch.read(output / 'stages.json')
    assert stages[0]['reused_from_directory'] == seed['directory']
    assert stages[0]['thread_id'] != stages[1]['thread_id']
    assert 'author_seed.json' in result['artifact_pins']


@pytest.mark.parametrize('defect', ['source', 'card', 'instructions', 'unfinished', 'artifact'])
def test_changed_or_unfinished_author_cannot_be_reused(completed_author, tmp_path, monkeypatch, defect):
    card, registry, _, worker, seed = completed_author
    card, registry = copy.deepcopy(card), copy.deepcopy(registry)
    original = tmp_path / 'original'
    if defect == 'source':
        registry['paper:1']['text'] += '\nNew evidence'
    elif defect == 'card':
        card['source_title'] = 'Different scope'
    elif defect == 'instructions':
        monkeypatch.setattr(prompts, 'SHARED_INSTRUCTIONS', prompts.SHARED_INSTRUCTIONS + '\nChanged scientific requirement.')
    elif defect == 'unfinished':
        stages = batch.read(original / 'stages.json'); stages[0]['status'] = 'running'
        batch.write(original / 'stages.json', stages)
        seed['pins']['stages.json'] = batch.sha(original / 'stages.json')
    else:
        path = original / 'attempts/001_author/artifact.json'
        raw = batch.read(path); raw['scope'] += ' Changed assertion'
        batch.write(path, raw)
    with pytest.raises(ValueError):
        batch.load_author_seed(seed, card, registry, 'illumina_diagram/1.0')
    assert len(worker.calls) == 2

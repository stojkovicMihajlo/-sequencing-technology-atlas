"""Control the experimental contrast, not the truth of model-written biology."""
import json
from pathlib import Path

import pytest

from genomics_discovery.illumina.prompts import build_prompt, INFORMATION_EFFECT_LOCAL_CONTRACT
from experiments.information_effect_20261108.run import trial_plan
from tests.illumina.test_illumina_batch_controls import acquired, FakeWorker


@pytest.mark.parametrize('mode', ['diagram', 'paper'])
def test_only_author_definition_changes(mode):
    context = {'source_mode': mode, 'representation_version': 'illumina_diagram/1.1'}
    baseline = build_prompt('author', context, {'type': 'object'})
    refined = build_prompt('author', context, {'type': 'object'}, information_effect_contract='local_conditions_v1')
    assert refined.replace(INFORMATION_EFFECT_LOCAL_CONTRACT + '\n\n', '') == baseline
    assert INFORMATION_EFFECT_LOCAL_CONTRACT not in build_prompt('reviewer', context, {'type': 'object'})
    with pytest.raises(ValueError, match='author-only'):
        build_prompt('reviewer', context, {'type': 'object'}, information_effect_contract='local_conditions_v1')


def test_replicates_share_sources_and_have_distinct_trial_ids():
    plan = json.loads(Path('experiments/information_effect_20261108/plan.json').read_text())
    trials = [trial_plan(plan, row['id']) for row in plan['trials']]
    assert len({t['trial']['id'] for t in trials}) == 6
    assert sum(t['author_information_effect_contract'] == 'current' for t in trials) == 3
    assert sum(t['author_information_effect_contract'] == 'local_conditions_v1' for t in trials) == 3
    assert all(t['methods'] == trials[0]['methods'] for t in trials)
    assert all(t['model'] == 'gpt-6.1-sol' and t['effort'] == 'xhigh' for t in trials)


def test_production_handoff_keeps_both_review_rounds_unchanged(acquired, tmp_path):
    import asyncio
    from genomics_discovery.illumina import extraction as batch
    root, card, sources, _ = acquired
    registry, images = batch.card_sources(card, sources, root)
    worker = FakeWorker(request_correction=True)
    result = asyncio.run(batch.extract_registered_workflow(
        card, registry, images, tmp_path / 'trial', worker,
        representation='illumina_diagram/1.0',
        author_information_effect_contract='local_conditions_v1'))
    assert result['revision'] == 1
    assert [x['role'] for x in worker.calls] == ['author', 'reviewer', 'author', 'reviewer']
    for call in worker.calls:
        assert (INFORMATION_EFFECT_LOCAL_CONTRACT in call['prompt']) == (call['role'] == 'author')

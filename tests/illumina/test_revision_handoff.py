"""Large correction feedback remains complete and available without inline copies."""
import asyncio
import json

import pytest

from genomics_discovery.illumina import extraction as batch
from genomics_discovery.illumina.models import CurrentDiagramReview, digest, review_targets
from genomics_discovery.illumina.prompts import build_prompt
from tests.illumina.test_illumina_batch_controls import acquired, candidate, FakeWorker


@pytest.mark.parametrize('role', ['author', 'reviewer'])
def test_artifact_stage_requires_truthful_final_acknowledgement(role):
    prompt = build_prompt(role, {}, 'schema.json')
    assert 'Do not substitute a prose final answer\nfor the artifact' in prompt
    assert 'send one nonempty final\nresponse sentence' in prompt
    assert 'actual check result' in prompt
    assert 'If unable\nto finish, state that truthfully' in prompt
    assert 'Do not write final.txt yourself' in prompt


def feedback(registry, *, large=False):
    value = candidate(registry)
    checks = []
    for target in sorted(review_targets(value, format_version='illumina_diagram_review/1.1')):
        checks.append({'target': target, 'action': 'revise', 'verdict': 'unsupported',
                       'rationale': ('Long repeated explanation. ' * 8000) if large else 'Resolve this assertion.',
                       'evidence': value.states[0].evidence})
    review = CurrentDiagramReview(record_id=value.record_id, candidate_sha256=digest(value),
                                  inspected_sources=value.inspected_sources, checks=checks)
    return value, review


def test_large_feedback_is_complete_addressable_and_not_in_prompt(acquired, tmp_path):
    root, card, sources, _ = acquired
    registry, _ = batch.card_sources(card, sources, root)
    value, review = feedback(registry, large=True)
    old = build_prompt('author', {}, 'schema.json', value.model_dump(mode='json'), review.model_dump(mode='json'))
    assert len(old) > 1_048_576
    attempt = tmp_path/'revision'; attempt.mkdir()
    files = batch.revision_handoff(attempt, value, review)
    prompt = build_prompt('author', {'revision_feedback': files, 'artifact_helper': 'tool artifact'}, 'schema.json')
    assert len(prompt) < 100_000
    assert batch.read(attempt/'previous_candidate.json') == value.model_dump(mode='json')
    assert batch.read(attempt/'previous_review.json') == review.model_dump(mode='json')
    index = batch.read(attempt/'feedback_index.json')
    assert {row['target'] for row in index['checks']} == {row.target for row in review.checks}
    for row in index['checks']:
        ref = files['previous_review']
        chunks, start = [], 0
        while start is not None:
            result = batch.artifact_excerpt(ref['path'], ref['sha256'], pointer=row['pointer'], start=start, limit=16000)
            chunks.append(result['text']); start = result['next_start']
        reconstructed = json.loads(''.join(chunks))
        assert reconstructed == review.checks[int(row['pointer'].split('/')[-1])].model_dump(mode='json')


def test_feedback_refuses_a_review_bound_to_another_candidate(acquired, tmp_path):
    root, card, sources, _ = acquired
    registry, _ = batch.card_sources(card, sources, root)
    value, review = feedback(registry)
    changed = value.model_copy(update={'scope': 'Different scientific scope'})
    with pytest.raises(ValueError, match='bound to the prior candidate'):
        batch.revision_handoff(tmp_path, changed, review)


def test_artifact_reader_uses_exact_pointer_and_rejects_changed_bytes(tmp_path, capsys):
    path = tmp_path/'artifact.json'
    batch.write(path, {'a/b': {'~key': ['first', 'second']}})
    pin = batch.sha(path)
    result = batch.artifact_excerpt(path, pin, pointer='/a~1b/~0key/1', limit=1000)
    assert json.loads(result['text']) == 'second'
    assert result['next_start'] is None
    assert batch.main(['artifact', '--path', str(path), '--sha256', pin,
                       '--pointer', '/a~1b/~0key/0']) == 0
    assert json.loads(capsys.readouterr().out)['text'] == '"first"'
    with pytest.raises(ValueError, match='nonnegative'):
        batch.artifact_excerpt(path, pin, start=-1)
    with pytest.raises(ValueError, match='nonnegative integer'):
        batch.artifact_excerpt(path, pin, pointer='/a~1b/~0key/-1')
    path.write_text('{}')
    with pytest.raises(ValueError, match='SHA256'):
        batch.artifact_excerpt(path, pin)


def test_prompt_preflight_fails_before_spending_a_native_call(acquired, tmp_path):
    root, card, sources, _ = acquired
    card['implementation_scope'] = 'x'*110_000
    worker = FakeWorker()
    directory = tmp_path/'method'
    with pytest.raises(batch.StageFailure, match='100,000'):
        asyncio.run(batch.extract_card(card, sources, root, directory, worker))
    assert not worker.calls
    stage, = batch.read(directory/'stages.json')
    assert stage['status'] == 'failed' and stage['failure_category'] == 'prompt_preflight'


def test_mutating_prior_feedback_cannot_pass_revision_admission(acquired, tmp_path):
    root, card, sources, _ = acquired

    class MutatingWorker(FakeWorker):
        async def run(self, prompt, attempt_dir, *, image_paths=()):
            result = await super().run(prompt, attempt_dir, image_paths=image_paths)
            path = attempt_dir/'previous_review.json'
            if path.exists():
                path.write_text('{}')
            return result

    with pytest.raises(ValueError, match='Read-only stage input changed'):
        asyncio.run(batch.extract_card(card, sources, root, tmp_path/'method',
                                      MutatingWorker(request_correction=True)))


def seed_attempt(acquired, path):
    root, card, sources, _ = acquired
    registry, images = batch.card_sources(card, sources, root)
    value, review = feedback(registry)
    path.mkdir()
    for name, content in [('card.json', card), ('source_registry.json', registry),
                          ('extraction.json', value.model_dump(mode='json')),
                          ('review.json', review.model_dump(mode='json'))]:
        batch.write(path/name, content)
    seed = {'directory': str(path), 'pins': {name: batch.sha(path/name)
        for name in ('card.json', 'source_registry.json', 'extraction.json', 'review.json')}}
    return seed, card, registry, images


def test_explicit_seed_runs_only_one_correction_and_fresh_review(acquired, tmp_path):
    seed, card, registry, images = seed_attempt(acquired, tmp_path/'seed')
    worker = FakeWorker()
    output = tmp_path/'correction'
    result = asyncio.run(batch.extract_registered_workflow(card, registry, images, output,
        worker, max_revisions=1, representation='illumina_diagram/1.0', revision_seed=seed))
    assert [c['role'] for c in worker.calls] == ['author', 'reviewer']
    stages = batch.read(output/'stages.json')
    assert [s['revision'] for s in stages] == [1, 1]
    assert len({s['thread_id'] for s in stages}) == 2
    assert result['status'] == 'supported'
    assert result['artifact_pins']['revision_seed.json'] == batch.sha(output/'revision_seed.json')
    for name in ('001_author', '002_reviewer'):
        assert batch.read(output/'attempts'/name/'previous_candidate.json') == batch.read(tmp_path/'seed'/'extraction.json')
        assert batch.read(output/'attempts'/name/'previous_review.json') == batch.read(tmp_path/'seed'/'review.json')
    assert all(batch.sha(tmp_path/'seed'/name) == pin for name, pin in seed['pins'].items())


@pytest.mark.parametrize('change', ['bytes', 'card', 'registry', 'revision_count'])
def test_explicit_seed_rejects_changed_inputs_before_native_call(acquired, tmp_path, change):
    seed, card, registry, images = seed_attempt(acquired, tmp_path/'seed')
    worker = FakeWorker()
    revisions = 1
    if change == 'bytes':
        (tmp_path/'seed'/'extraction.json').write_text('{}')
    elif change == 'card':
        card = {**card, 'source_title': 'changed assigned implementation'}
    elif change == 'registry':
        registry = {**registry, 'new:metadata': {'kind': 'text', 'content_level': 'metadata', 'text': 'new'}}
    else:
        revisions = 2
    with pytest.raises(ValueError):
        asyncio.run(batch.extract_registered_workflow(card, registry, images, tmp_path/'correction',
            worker, max_revisions=revisions, representation='illumina_diagram/1.0', revision_seed=seed))
    assert not worker.calls and not (tmp_path/'correction').exists()

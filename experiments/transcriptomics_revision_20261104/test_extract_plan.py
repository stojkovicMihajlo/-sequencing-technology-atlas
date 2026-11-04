"""Fake-native custody and typed method dispatch; no live scientific evaluation."""
import copy
import json

import pytest

from genomics_discovery.execution.codex_worker import WorkerResult
from genomics_discovery.illumina.models import parse_extraction
from genomics_discovery.illumina.representation import StructuredDiagramExtraction, StructuredDiagramReview
from tests.illumina import test_illumina_batch_controls as transport

from experiments.transcriptomics_revision_20261104 import extract_plan as runner
from experiments.transcriptomics_revision_20261104 import native_planning as native
from experiments.transcriptomics_revision_20261104.planning import WorkPlan, digest, validate_plan
from experiments.transcriptomics_revision_20261104.test_native_planning import source_fixture


def archive(attempt, prompt, image_paths, payload, thread):
    """Emit the same minimal archive shape used by existing native transport tests."""
    native.write(attempt / 'artifact.json', payload)
    (attempt / 'prompt.txt').write_text(prompt)
    argv = ['fake-worker', 'exec']
    for image in image_paths:
        argv.extend(['--image', str(image)])
    native.write(attempt / 'command.json', {'argv': [*argv, '-'], 'cwd': str(attempt),
        'image_inputs': [{'path': str(p), 'sha256': native.sha(p)} for p in image_paths]})
    usage = {'input_tokens': 10, 'output_tokens': 5}
    native.write(attempt / 'started.json', {'spawned': True, 'hostname': 'unit-test'})
    events = [{'type': 'thread.started', 'thread_id': thread}, {'type': 'turn.started'},
              {'type': 'turn.completed', 'usage': usage}]
    (attempt / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    (attempt / 'final.txt').write_text('Synthetic test; no native model call.')
    native.write(attempt / 'result.json', {'exit_code': 0, 'thread_id': thread,
        'usage': usage, 'elapsed_seconds': 0.1, 'error': None})
    return WorkerResult(0, thread, usage, 0.1, None)


async def planned(tmp_path, monkeypatch):
    census_path, plan, _ = source_fixture(tmp_path)
    census = native.read(census_path)
    for row in census['rows']:
        path = tmp_path / row['record_id']
        image = path / 'diagram.png'; image.write_bytes(transport.png())
        registry = native.read(path / 'source_registry.json')
        registry['diagram'] = {'kind': 'image', 'path': str(image), 'sha256': native.sha(image)}
        if row['record_id'] == 'web':
            registry['paper:1'] = {'kind': 'text', 'content_level': 'abstract', 'pmid': '1',
                                   'title': 'Capture study', 'text': 'RNA is captured.'}
            card = native.read(path / 'card.json')
            card['references'] = [{'pmid': '1', 'reference_id': 'web:references:0', 'role': 'references'}]
            native.write(path / 'card.json', card)
        native.write(path / 'source_registry.json', registry)
        row['registered_sources'] = {sid: {k: v for k, v in src.items() if k != 'text'}
                                     for sid, src in registry.items()}
    native.write(census_path, census)
    raw = plan.model_dump(mode='json'); raw['census_sha256'] = digest(census)
    plan = WorkPlan.model_validate(raw)
    monkeypatch.setenv('SLURM_JOB_ID', 'synthetic-plan')

    class PlanningWorker:
        async def run(self, prompt, attempt, image_paths=()):
            context = json.loads(prompt.split('\nCONTEXT\n', 1)[1])
            if context['role'] == 'author':
                payload = plan.model_dump(mode='json')
            else:
                payload = {'plan_sha256': digest(plan.model_dump(mode='json')), 'assessments': [
                    {'target': t, 'decision': 'accept', 'reason': 'Synthetic fixture review',
                     'evidence': [e.model_dump(mode='json') for e in plan.identity_decisions[0].evidence]}
                    for t in validate_plan(census, plan)]}
            return archive(attempt, prompt, image_paths, payload, 'planning-' + attempt.name)

    directory = tmp_path / 'planning'
    await native.execute(census_path, directory, worker=PlanningWorker())
    return directory


@pytest.mark.asyncio
async def test_reopen_reviewed_plan_consolidates_two_depictions(tmp_path, monkeypatch):
    directory = await planned(tmp_path, monkeypatch)
    worklist, bundles = runner.load_plan(directory)
    assert len(worklist['units']) == len(bundles) == 1
    assert {b['record_id'] for b in worklist['units'][0]['bindings']} == {'web', 'poster'}
    assert native.read(bundles[0]['card_path'])['reference_source_prefix'] == 'web::'


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation, message', [
    ('bundle_bytes', 'source bundle changed'),
    ('bundle_duplicate', 'coverage differs'),
    ('registry_pointer', 'Bundle paths'),
    ('manifest_pin', 'manifest differs'),
    ('native_thread', 'custody/freshness'),
])
async def test_plan_reopen_rejects_stale_or_ambiguous_inputs(tmp_path, monkeypatch, mutation, message):
    directory = await planned(tmp_path, monkeypatch)
    manifest_path = directory / 'workflow_bundles/manifest.json'
    manifest = native.read(manifest_path)
    bundle = manifest['bundles'][0]
    if mutation == 'bundle_bytes':
        registry = native.read(bundle['registry_path'])
        registry['web::paper:1']['text'] = 'Substituted unsupported evidence.'
        native.write(bundle['registry_path'], registry)
    elif mutation == 'bundle_duplicate':
        manifest['bundles'].append(copy.deepcopy(bundle))
        native.write(manifest_path, manifest)
    elif mutation in {'registry_pointer', 'manifest_pin'}:
        replacement = tmp_path / 'unbound_registry.json'
        native.write(replacement, native.read(bundle['registry_path']))
        manifest['bundles'][0]['registry_path'] = str(replacement)
        native.write(manifest_path, manifest)
    else:
        state = native.read(directory / 'state.json')
        state['stages'][-1]['thread_id'] = state['stages'][0]['thread_id']
        native.write(directory / 'state.json', state)
    if mutation in {'bundle_duplicate', 'registry_pointer'}:
        # Also reject a host-created inconsistent manifest, even with a matching
        # manifest pin. The unrepinned mutation exercises custody separately.
        state = native.read(directory / 'state.json')
        state['bundles_sha256'] = native.sha(manifest_path)
        native.write(directory / 'state.json', state)
    with pytest.raises(ValueError, match=message):
        runner.load_plan(directory)


def typed_transport(monkeypatch):
    template = transport.candidate({'diagram': {'kind': 'image'}}).model_dump(mode='json')

    def candidate(sources, *, revised=False):
        raw = copy.deepcopy(template)
        image_id = next(k for k, v in sources.items() if v['kind'] == 'image')
        def remap(value):
            if isinstance(value, dict):
                if value.get('source_id') == 'diagram': value['source_id'] = image_id
                for child in value.values(): remap(child)
            elif isinstance(value, list):
                for child in value: remap(child)
        remap(raw)
        raw['inspected_sources'] = list(sources)
        if revised: raw['operations'][0]['description'] = 'Corrected capture description'
        raw.update(format_version='illumina_diagram/1.1', record_id='one_method', title='Example',
                   method_names=['Example'])
        raw['method_scopes'][0]['method'] = 'Example'
        raw['paper_matches'][0].update(source_id='web::paper:1', methods=['Example'])
        raw['paper_matches'][0]['evidence'][0]['source_id'] = 'web::paper:1'
        for state in raw['states']:
            state['properties'] = [{'id': 'identity', 'subject': 'self', 'property': 'identity',
                                    'value': state['label'], 'evidence': state['evidence']}]
        for operation in raw['operations']:
            operation.update(resolution='single_event', output_grouping='one_output',
                input_roles=[{'state_id': sid, 'subject': 'self', 'roles': ['sample'],
                              'explanation': 'Fixture input', 'evidence': operation['evidence']}
                             for sid in operation['inputs']],
                effects=[{'aspect': 'selection', 'input_state_ids': operation['inputs'],
                          'output_state_ids': operation['outputs'], 'statement': operation['description'],
                          'evidence': operation['evidence']}])
        return parse_extraction(raw)

    monkeypatch.setattr(transport, 'candidate', candidate)
    monkeypatch.setattr(transport, 'DiagramExtraction', StructuredDiagramExtraction)
    monkeypatch.setattr(transport, 'CurrentDiagramReview', StructuredDiagramReview)
    return transport.FakeWorker()


@pytest.mark.asyncio
async def test_one_method_typed_author_review_publication_and_no_automatic_history(tmp_path, monkeypatch):
    directory = await planned(tmp_path, monkeypatch)
    worker = typed_transport(monkeypatch)
    output = tmp_path / 'extraction'
    result = await runner.execute(directory, output, workers=2, worker=worker)
    assert result['passed'], result
    assert result['extraction_counts'] == {'supported': 1}
    assert [c['role'] for c in worker.calls] == ['author', 'reviewer']
    assert all(len(c['images']) == 2 for c in worker.calls)
    assert '--extraction-format illumina_diagram/1.1' in worker.calls[0]['prompt']
    assert '--review-format illumina_diagram_review/1.2' in worker.calls[1]['prompt']
    graph = native.read(output / 'atlas.json')
    assert graph['cards']['one_method']['admission'] == 'supported'
    assert not any(e['kind'] in {'method_development', 'component_adoption', 'shared_mechanism'} for e in graph['edges'])
    assert any(n.get('original', {}).get('properties') for n in graph['nodes'])
    text = (output / 'cards/one_method/attempt_001/workflow.txt').read_text()
    assert 'Property identity:' in text


@pytest.mark.asyncio
async def test_research_hold_remains_diagnostic_and_does_not_retry(tmp_path, monkeypatch):
    directory = await planned(tmp_path, monkeypatch)
    worker = typed_transport(monkeypatch)
    original_run = worker.run

    async def hold(prompt, attempt, image_paths=()):
        result = await original_run(prompt, attempt, image_paths=image_paths)
        if attempt.name.endswith('reviewer'):
            review = native.read(attempt / 'artifact.json')
            construction = next(c for c in review['checks'] if c['target'] == 'construction')
            construction.update(verdict='unresolved', action='research',
                                rationale='Synthetic source ambiguity requiring new information')
            native.write(attempt / 'artifact.json', review)
        return result

    monkeypatch.setattr(worker, 'run', hold)
    output = tmp_path / 'held_extraction'
    report = await runner.execute(directory, output, worker=worker)
    assert report['passed'] is False
    assert report['extraction_counts'] == {'research_needed': 1}
    assert len(worker.calls) == 2
    graph = native.read(output / 'atlas.json')
    assert graph['cards']['one_method']['admission'] == 'diagnostic'
    assert all(n['admission'] == 'diagnostic' for n in graph['nodes']
               if n['node_type'] in {'state', 'operation', 'source_workflow'})


@pytest.mark.asyncio
async def test_unplanned_grouped_member_is_rejected_before_paying_for_review(tmp_path, monkeypatch):
    directory = await planned(tmp_path, monkeypatch)
    worker = typed_transport(monkeypatch)
    assigned_candidate = transport.candidate

    def expanded(sources, *, revised=False):
        raw = assigned_candidate(sources, revised=revised).model_dump(mode='json')
        raw['method_names'].append('Unassigned grouped method')
        extra = copy.deepcopy(raw['method_scopes'][0]); extra['method'] = raw['method_names'][-1]
        raw['method_scopes'].append(extra)
        raw['paper_matches'][0]['methods'].append(extra['method'])
        return parse_extraction(raw)

    monkeypatch.setattr(transport, 'candidate', expanded)
    report = await runner.execute(directory, tmp_path / 'wrong_scope', worker=worker)
    assert report['passed'] is False
    assert report['extraction_counts'] == {'failed': 1}
    assert [c['role'] for c in worker.calls] == ['author']

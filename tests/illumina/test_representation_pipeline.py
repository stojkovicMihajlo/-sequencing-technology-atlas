"""Exercise typed author/review/export/reopen paths with explicit fake transport."""
import asyncio
import copy

import pytest

from genomics_discovery.illumina import extraction as batch
from genomics_discovery.illumina.graph import build_graph, get_record, operation_group, protocol_text
from genomics_discovery.illumina.models import (
    parse_extraction, parse_review, property_target, review_targets,
    validate_review, validate_extraction, digest, review_outcome, export_graph,
)
from genomics_discovery.illumina.representation import StructuredDiagramExtraction, StructuredDiagramReview
from tests.illumina import test_illumina_batch_controls as transport
from tests.illumina.test_illumina_batch_controls import acquired
from tests.illumina.test_illumina_diagrams import example
from tests.illumina.test_representation_contract import typed


def fresh_review(candidate, sources, image):
    checks = []
    for target in sorted(review_targets(candidate, format_version='illumina_diagram_review/1.2')):
        evidence = candidate.paper_matches[0].evidence if target.startswith('paper:') else [image]
        checks.append(dict(target=target, verdict='supported', action='accept',
                           rationale='Synthetic positive review for transport testing.', evidence=evidence))
    return StructuredDiagramReview(record_id=candidate.record_id, candidate_sha256=digest(candidate),
        inspected_sources=candidate.inspected_sources, checks=checks)


def test_version_dispatch_does_not_upgrade_or_erase_assertions(typed):
    data, _, _ = typed
    candidate = parse_extraction(data)
    assert isinstance(candidate, StructuredDiagramExtraction)
    assert parse_extraction(candidate.model_dump_json()).model_dump() == candidate.model_dump()
    with pytest.raises(ValueError, match='Unknown extraction'):
        parse_extraction({**data, 'format_version': 'illumina_diagram/9'})


@pytest.mark.parametrize('target', ['construction', 'state_properties:rna',
    'state_property:rna:identity', 'operation_semantics:split'])
def test_review_cannot_skip_new_interpretation_obligations(typed, target):
    data, sources, image = typed
    candidate = parse_extraction(data)
    raw = fresh_review(candidate, sources, image).model_dump(mode='json')
    raw['checks'] = [c for c in raw['checks'] if c['target'] != target]
    assert any('every record target' in e for e in validate_review(parse_review(raw), candidate, sources))


def test_legacy_review_cannot_admit_structured_record(typed):
    data, sources, image = typed
    candidate = parse_extraction(data)
    raw = fresh_review(candidate, sources, image).model_dump(mode='json')
    raw['format_version'] = 'illumina_diagram_review/1.1'
    raw['checks'] = [c for c in raw['checks'] if c['target'] in review_targets(candidate, format_version=raw['format_version'])]
    with pytest.raises(ValueError, match='property and operation-semantics'):
        review_outcome(parse_review(raw), candidate, sources)


def test_nested_assertion_cannot_hide_metadata_under_supported_state(typed):
    data, sources, _ = typed
    data['states'][1]['properties'][0]['evidence'] = [{'source_id': 'metadata:2',
        'locator': 'title', 'basis': 'text', 'support': 'reported', 'quote': 'A paper title'}]
    errors = validate_extraction(parse_extraction(data), sources)
    assert any('Scientific assertion' in e and 'state_property:rna:identity' in e for e in errors)


def test_state_correction_does_not_require_paper_on_unrelated_diagram_property(typed):
    data, sources, image = typed
    paper = data['paper_matches'][0]['evidence'][0]
    state = data['states'][1]
    state['evidence'].append(paper)
    state['properties'][0]['evidence'] = [paper]
    state['properties'].append(dict(id='context', subject='self', property='physical_context',
                                    value='Depicted separate fraction', evidence=[image]))
    data['corrections'] = [dict(id='correct_identity', target_id='state:rna',
        diagram_observation='Source caption ambiguity', primary_statement='Source-specific correction',
        disposition='use_primary', evidence=[image, paper])]
    candidate = parse_extraction(data)
    raw = fresh_review(candidate, sources, image).model_dump(mode='json')
    targets = {'state:rna', 'state_label:rna', 'state_properties:rna',
               'state_property:rna:identity', 'correction:correct_identity'}
    for check in raw['checks']:
        if check['target'] in targets:
            check['evidence'] = [image, paper]
    assert not validate_review(parse_review(raw), candidate, sources)
    own = next(c for c in raw['checks'] if c['target'] == 'state_property:rna:identity')
    own['evidence'] = [image]
    assert validate_review(parse_review(raw), candidate, sources)


def test_readable_projection_preserves_structured_facts_and_support(typed):
    data, sources, image = typed
    candidate = parse_extraction(data)
    review = fresh_review(candidate, sources, image)
    text = protocol_text(export_graph(candidate, review, sources))
    assert 'Property identity:' in text and '"subject": "self"' in text
    assert 'Input role cell: sample' in text
    assert 'Effect [selection]' in text
    assert 'diagram:a @ split arrows' in text


def test_property_review_ids_are_unambiguous():
    assert property_target('s:a', 'p') != property_target('s', 'a:p')


def test_fresh_structured_batch_survives_export_reopen_and_query(acquired, tmp_path, monkeypatch):
    root, card, acquired_sources, _ = acquired
    registry, _ = batch.card_sources(card, acquired_sources, root)
    legacy = transport.candidate(registry).model_dump(mode='json')

    def typed_factory(sources, *, revised=False):
        raw = copy.deepcopy(legacy)
        raw['format_version'] = 'illumina_diagram/1.1'
        for state in raw['states']:
            state['properties'] = [dict(id='identity', subject='self', property='identity',
                                       value=state['label'], evidence=state['evidence'])]
        for op in raw['operations']:
            op.update(resolution='single_event', output_grouping='one_output',
                input_roles=[dict(state_id=s, roles=['sample'], explanation='Depicted input.', evidence=op['evidence']) for s in op['inputs']],
                effects=[dict(aspect='selection', input_state_ids=op['inputs'], output_state_ids=op['outputs'],
                              statement=op['description'], evidence=op['evidence'])])
        return parse_extraction(raw)

    monkeypatch.setattr(transport, 'candidate', typed_factory)
    monkeypatch.setattr(transport, 'DiagramExtraction', StructuredDiagramExtraction)
    monkeypatch.setattr(transport, 'CurrentDiagramReview', StructuredDiagramReview)
    worker = transport.FakeWorker()
    monkeypatch.setattr(batch, 'CodexWorker', lambda **_: worker)
    monkeypatch.setenv('SLURM_JOB_ID', 'fake-structured-contract-test')
    output = tmp_path / 'batch'
    result = asyncio.run(batch.run(root / 'sources.json', output, workers=1, representation='illumina_diagram/1.1'))
    assert result['counts'] == {'supported': 1}, result['cards']
    assert result['policy']['representation_version'] == 'illumina_diagram/1.1'
    assert 'representation.py' in result['policy']['implementation']
    graph = build_graph(output, root / 'inventory.json')
    assert graph['cards']['example']['status'] == 'supported'
    state_id = next(n['id'] for n in graph['nodes'] if n['node_type'] == 'state')
    node = get_record(graph, 'state', state_id)
    assert node['property_view']['identity']['status'] == 'recorded'
    assert node['property_view']['terminal_group']['status'] == 'not_recorded'
    assert 'construction' in node['semantic_review']
    selected = operation_group(graph, 'example', graph['cards']['example']['operation_ids'])
    assert 'operation_semantics:capture' in selected['semantic_checks']
    assert 'state_property:before:identity' in selected['semantic_checks']
    assert '--extraction-format illumina_diagram/1.1' in worker.calls[0]['prompt']
    assert 'CONSTRUCT THE WORKFLOW IN THIS ORDER' in worker.calls[0]['prompt']
    assert 'State.features records supported distinctions' not in worker.calls[0]['prompt']
    assert '--review-format illumina_diagram_review/1.2' in worker.calls[1]['prompt']
    count = len(worker.calls)
    asyncio.run(batch.run(root / 'sources.json', output, workers=1, representation='illumina_diagram/1.1', resume=True))
    assert len(worker.calls) == count

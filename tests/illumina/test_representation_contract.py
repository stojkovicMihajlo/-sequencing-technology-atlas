"""Typed interpretation controls, not claims that validation proves chemistry."""
import copy

import pytest
from pydantic import ValidationError

from genomics_discovery.illumina.models import DiagramExtraction, State, digest, validate_extraction
from genomics_discovery.illumina.representation import (
    StructuredDiagramExtraction, StructuredState, StructuredDiagramReview,
    property_targets, property_view,
)
from tests.illumina.test_illumina_diagrams import example


@pytest.fixture
def typed(example):
    data, sources, image, _ = copy.deepcopy(example)
    data['format_version'] = 'illumina_diagram/1.1'
    for state in data['states']:
        state['properties'] = [
            {'id': 'identity', 'subject': 'self', 'property': 'identity',
             'value': state['label'], 'evidence': [image]},
        ]
        if state['kind'] == 'data':
            state['properties'].append({'id': 'type', 'subject': 'self', 'property': 'data_type',
                                        'value': 'sequence_reads', 'evidence': [image]})
    for op in data['operations']:
        op.update(resolution='single_event',
                  input_roles=[{'state_id': s, 'roles': ['sample'], 'explanation': 'Shown input',
                                'evidence': [image]} for s in op['inputs']],
                  effects=[{'aspect': 'selection' if op['id'] == 'split' else 'readout',
                            'input_state_ids': op['inputs'], 'output_state_ids': op['outputs'],
                            'statement': op['description'], 'evidence': [image]}],
                  output_grouping='one_output' if len(op['outputs']) == 1 else 'distinct_outputs')
    return data, sources, image


def test_contract_accepts_routing_and_preserves_exact_local_inputs(typed):
    data, sources, _ = typed
    candidate = StructuredDiagramExtraction.model_validate(data)
    assert not validate_extraction(candidate, sources)
    assert candidate.operations[0].outputs == ('rna', 'dna')
    assert candidate.format_version == 'illumina_diagram/1.1'
    assert 'state_property:reads:type' in property_targets(candidate)
    # Version-specific schema, not an in-place interpretation of old feature prose.
    with pytest.raises(ValidationError):
        DiagramExtraction.model_validate(data)


def test_legacy_record_and_digest_are_unchanged(example):
    data, _, image, _ = example
    legacy = DiagramExtraction.model_validate(data)
    before = digest(legacy)
    old = State(id='legacy', label='RNA', kind='material', features=['source-specific prose'], evidence=[image])
    assert old.features == ('source-specific prose',)
    assert 'properties' not in old.model_dump()
    assert digest(DiagramExtraction.model_validate(legacy.model_dump())) == before
    with pytest.raises(ValidationError):
        StructuredDiagramExtraction.model_validate(legacy.model_dump())


def test_mixture_components_do_not_create_fraction_states(typed):
    data, _, image = typed
    rna = data['states'][1]
    rna['components'] = [{'id': 'cyclic', 'label': 'RNA population with cyclic phosphate', 'evidence': [image]},
                         {'id': 'hydroxyl', 'label': 'RNA population with hydroxyl end', 'evidence': [image]}]
    rna['properties'].extend([
        {'id': 'end_c', 'subject': 'cyclic', 'property': 'terminal_group', 'end': 'three_prime',
         'value': '2-prime,3-prime cyclic phosphate', 'evidence': [image]},
        {'id': 'end_h', 'subject': 'hydroxyl', 'property': 'terminal_group', 'end': 'three_prime',
         'value': 'hydroxyl', 'evidence': [image]},
    ])
    c = StructuredDiagramExtraction.model_validate(data)
    assert len(c.states) == 4
    assert c.operations[0].outputs == ('rna', 'dna')
    assert len(c.states[1].components) == 2
    assert {f.target for f in c.flows if f.source == 'split'} == {'rna', 'dna'}


def test_component_properties_need_real_subjects_and_ends(typed):
    data, _, image = typed
    state = data['states'][1]
    prop = {'id': 'end', 'subject': 'not_named', 'property': 'terminal_group',
            'end': 'three_prime', 'value': 'phosphate', 'evidence': [image]}
    state['properties'].append(prop)
    with pytest.raises(ValidationError, match='exact local component'):
        StructuredDiagramExtraction.model_validate(data)
    prop['subject'] = 'self'
    del prop['end']
    with pytest.raises(ValidationError, match='end'):
        StructuredDiagramExtraction.model_validate(data)


def test_property_absence_is_not_a_claim_of_absence_or_retention(typed):
    data, _, _ = typed
    state = StructuredDiagramExtraction.model_validate(data).states[2]
    views = property_view(state.model_dump(mode='json'))
    assert views['identity']['status'] == 'recorded'
    assert views['modification'] == {'status': 'not_recorded', 'claims': []}
    assert views['terminal_group'] == {'status': 'not_recorded', 'claims': []}
    # Cap selection in an identity does not synthesize a cap modification on DNA.
    data['states'][2]['label'] = 'cap-selected cDNA'
    data['states'][2]['properties'][0]['value'] = 'cap-selected cDNA'
    c = StructuredDiagramExtraction.model_validate(data)
    assert property_view(c.states[2].model_dump())['modification']['claims'] == []


@pytest.mark.parametrize('mutation,pattern', [
    (lambda d: d['states'][0].update(features=['capped']), 'at most 0'),
    (lambda d: d['states'][0]['properties'][0].update(subject='RNA'), 'exact local component'),
    (lambda d: d['states'][0]['properties'].append(copy.deepcopy(d['states'][0]['properties'][0])), 'Property IDs'),
    (lambda d: d['states'][3]['properties'].pop(), 'data_type'),
    (lambda d: d['operations'][0]['input_roles'].clear(), 'role assessment'),
    (lambda d: d['operations'][0]['input_roles'][0].update(state_id='absent'), 'role assessment'),
    (lambda d: d['operations'][0]['input_roles'][0].update(roles=['sample', 'unspecified']), 'unspecified'),
    (lambda d: d['operations'][0]['effects'][0].update(output_state_ids=['reads']), 'own declared'),
    (lambda d: d['operations'][0]['effects'][0].update(output_state_ids=['rna']), 'Every declared output'),
    (lambda d: d['operations'][1].update(output_grouping='distinct_outputs'), 'number of output'),
    (lambda d: d['operations'][0].update(output_grouping='one_output'), 'Multiple outputs'),
    (lambda d: d['operations'][0].update(output_grouping='unresolved'), 'explicit bound Unknown'),
])
def test_invalid_semantic_structure_is_rejected(typed, mutation, pattern):
    data, _, _ = typed
    mutation(data)
    with pytest.raises(ValidationError, match=pattern):
        StructuredDiagramExtraction.model_validate(data)


def test_data_does_not_acquire_chemistry(typed):
    data, _, image = typed
    data['states'][3]['properties'].append({'id': 'end', 'subject': 'self',
        'property': 'terminal_group', 'end': 'three_prime', 'value': 'hydroxyl', 'evidence': [image]})
    with pytest.raises(ValidationError, match='Data cannot carry physical'):
        StructuredDiagramExtraction.model_validate(data)


def test_material_does_not_acquire_dataset_fields(typed):
    data, _, image = typed
    data['states'][0]['properties'].append({'id': 'content', 'subject': 'self',
        'property': 'data_content', 'value': 'Alignment columns', 'evidence': [image]})
    with pytest.raises(ValidationError, match='Material cannot carry dataset'):
        StructuredDiagramExtraction.model_validate(data)


def test_end_conflict_is_not_silently_accepted_as_mixture(typed):
    data, _, image = typed
    data['states'][1]['properties'].extend([
        {'id': 'end1', 'subject': 'self', 'property': 'terminal_group', 'end': 'three_prime',
         'value': 'hydroxyl', 'evidence': [image]},
        {'id': 'end2', 'subject': 'self', 'property': 'terminal_group', 'end': 'three_prime',
         'value': 'cyclic phosphate', 'evidence': [image]},
    ])
    with pytest.raises(ValidationError, match='Conflicting/repeated single-valued'):
        StructuredDiagramExtraction.model_validate(data)


def test_binding_partner_is_addressed_not_an_unchecked_name(typed):
    data, _, image = typed
    data['states'][1]['properties'].append({'id': 'bind', 'subject': 'self',
        'property': 'association', 'value': 'hybridized_to', 'partner': 'missing_primer', 'evidence': [image]})
    with pytest.raises(ValidationError, match='Association partner'):
        StructuredDiagramExtraction.model_validate(data)


def test_dna_can_be_an_operation_local_template(typed):
    data, _, image = typed
    # Change the read block to operate on the already represented DNA branch.
    op = data['operations'][1]
    op.update(inputs=['dna'])
    op['input_roles'] = [{'state_id': 'dna', 'roles': ['template', 'sample'],
                         'explanation': 'Supported DNA template', 'evidence': [image]}]
    op['effects'][0]['input_state_ids'] = ['dna']
    next(f for f in data['flows'] if f['id'] == 'f4')['source'] = 'dna'
    c = StructuredDiagramExtraction.model_validate(data)
    assert c.operations[1].input_roles[0].roles == ('template', 'sample')


def test_ligated_probe_reporter_does_not_require_rna_copy(typed):
    data, _, _ = typed
    # The schema permits a DNA reporter identity without manufacturing an RT step.
    data['states'][2]['label'] = 'ligated DNA probe reporter'
    data['states'][2]['properties'][0]['value'] = 'ligated DNA probe reporter'
    c = StructuredDiagramExtraction.model_validate(data)
    assert c.states[2].properties[0].value == 'ligated DNA probe reporter'
    assert len(c.operations) == 2
    # This tests representability, NOT that the fixture establishes RASL chemistry.


def test_defined_other_is_explicit_local_extension(typed):
    data, _, image = typed
    prop = {'id': 'history', 'subject': 'self', 'property': 'defined_other',
            'name': 'boundary preparation history', 'definition': 'Reported selection before the depicted boundary',
            'applicable_to': 'material', 'value': 'Selected in source-described procedure', 'evidence': [image]}
    data['states'][0]['properties'].append(prop)
    c = StructuredDiagramExtraction.model_validate(data)
    assert property_view(c.states[0].model_dump())['defined_other']['status'] == 'recorded'
    prop['applicable_to'] = 'data'
    with pytest.raises(ValidationError, match='applicability'):
        StructuredDiagramExtraction.model_validate(data)


def test_unresolved_interface_has_addressed_question(typed):
    data, _, _ = typed
    data['operations'][0]['resolution'] = 'unresolved_interface'
    data['unknowns'] = [{'id': 'missing_interface', 'question': 'What establishes the displayed separation?',
                         'blocking': True, 'affected_ids': ['operation:split']}]
    c = StructuredDiagramExtraction.model_validate(data)
    assert c.unknowns[0].blocking


def test_schema_exposes_property_meanings_and_review_version():
    schema = StructuredDiagramExtraction.model_json_schema()
    assert schema['$defs']['TerminalGroupProperty']['properties']['end']['enum'] == ['five_prime', 'three_prime']
    assert schema['$defs']['StructuredState']['properties']['properties']['items']['discriminator']['propertyName'] == 'property'
    assert StructuredDiagramReview.model_fields['format_version'].default == 'illumina_diagram_review/1.2'


def test_coexisting_template_and_primer_have_distinct_local_roles(typed):
    data, _, image = typed
    state = data['states'][1]
    state['components'] = [
        {'id': 'rna_template', 'label': 'RNA template', 'evidence': [image]},
        {'id': 'dna_primer', 'label': 'Hybridized DNA primer', 'evidence': [image]},
    ]
    operation = data['operations'][1]
    operation['input_roles'] = [
        {'state_id': 'rna', 'subject': 'rna_template', 'roles': ['template'],
         'explanation': 'Supported template role in preparation', 'evidence': [image]},
        {'state_id': 'rna', 'subject': 'dna_primer', 'roles': ['primer'],
         'explanation': 'Supported primer role in same preparation', 'evidence': [image]},
    ]
    c = StructuredDiagramExtraction.model_validate(data)
    assert c.operations[1].inputs == ('rna',)
    assert len(c.operations[1].input_roles) == 2
    assert len(c.states) == 4
    operation['input_roles'][0]['subject'] = 'unrecorded_component'
    with pytest.raises(ValidationError, match='role subject'):
        StructuredDiagramExtraction.model_validate(data)

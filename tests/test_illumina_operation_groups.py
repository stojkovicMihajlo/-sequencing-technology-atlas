"""Structural selection for comparison must retain branches and source context."""
import copy
import json

import pytest

from genomics_discovery.curation.illumina_graph import operation_group, main


@pytest.fixture
def source_graph():
    prefix = 'illumina:fixture:'
    ids = {key: prefix + ('operation:' if key.startswith('op') else 'state:') + key
           for key in ('op1','op2','op3','op4','input','mid','branch','final','side','extra')}
    specs = [('input','op1','material_input'), ('op1','mid','material_output'),
             ('op1','branch','material_output'), ('mid','op2','material_input'),
             ('op2','final','material_output'), ('branch','op3','material_input'),
             ('op3','side','material_output'), ('mid','op4','material_input'),
             ('op4','extra','material_output')]
    nodes = [{'id': value, 'node_type': 'operation' if key.startswith('op') else 'state',
        'record_id':'fixture','admission':'supported','original':{'id':key,'label':key,
        **({} if key.startswith('op') else {'kind':'material'})}}
        for key,value in ids.items()]
    edges = [{'id':'f'+str(i),'source':ids[a],'target':ids[b],'kind':kind,
        'record_id':'fixture','admission':'supported','original':{'id':'f'+str(i),
        'evidence':[{'source_id':'diagram:fixture','basis':'image','support':'depicted','locator':'arrow'}]}}
        for i,(a,b,kind) in enumerate(specs)]
    nodes.append({'id':'correction:1','node_type':'correction','record_id':'fixture',
        'admission':'supported','original':{'id':'c1','target_id':'operation:op1',
        'diagram_observation':'Literal caption','primary_statement':'Supported qualification'}})
    card={'record_id':'fixture','status':'supported','admission':'supported','scope':'Exact source outline',
        'operation_ids':[ids[k] for k in ('op1','op2','op3','op4')], 'publication_ids':[],
        'source_registry':{'diagram:fixture':{'kind':'image','sha256':'a'*64}},
        'candidate_sha256':'b'*64,'review_sha256':'c'*64,'artifact_pins':{},
        'unknowns':[{'id':'u1','affected_ids':['state:mid'],'question':'Unreported end group'}]}
    return {'cards':{'fixture':card},'nodes':nodes,'edges':edges},ids


def test_group_preserves_side_outputs_shared_intermediates_and_surrounding_routes(source_graph):
    graph,ids=source_graph;before=copy.deepcopy(graph)
    result=operation_group(graph,'fixture',[ids['op1'],ids['op2']])
    assert result['input_state_ids']==[ids['input']]
    assert set(result['output_state_ids'])=={ids['mid'],ids['branch'],ids['final']}
    assert result['internal_state_ids']==[ids['mid']]
    assert {n['id'] for n in result['surrounding_operations']}=={ids['op3'],ids['op4']}
    assert len(result['boundary_flows'])==2
    assert result['corrections'][0]['primary_statement']=='Supported qualification'
    assert result['unknowns'][0]['question']=='Unreported end group'
    assert all(e['original']['evidence'] for e in result['flows']+result['boundary_flows'])
    assert graph==before


def test_group_identity_is_order_independent_and_not_changed_by_unrelated_card(source_graph):
    graph,ids=source_graph
    a=operation_group(graph,'fixture',[ids['op1'],ids['op2']])
    graph['cards']['unrelated']={'record_id':'unrelated','status':'pending'}
    b=operation_group(graph,'fixture',[ids['op2'],ids['op1']])
    assert a['selection_sha256']==b['selection_sha256']
    graph['cards']['fixture']['candidate_sha256']='d'*64
    assert operation_group(graph,'fixture',[ids['op1'],ids['op2']])['selection_sha256']!=a['selection_sha256']


def test_disconnected_operations_cannot_be_grouped_through_an_unselected_operation(source_graph):
    graph,ids=source_graph
    with pytest.raises(ValueError,match='connected'):
        operation_group(graph,'fixture',[ids['op2'],ids['op3']])
    graph['edges'].extend([{'id':key,'source':ids[a],'target':ids[b],'kind':'precedes',
        'record_id':'fixture','admission':'supported','original':{'id':key,
        'evidence':[{'source_id':'diagram:fixture','basis':'image','support':'depicted','locator':'arrow'}]}}
        for key,a,b in [('p1','op2','op4'),('p2','op4','op3')]])
    with pytest.raises(ValueError,match='connected'):
        operation_group(graph,'fixture',[ids['op2'],ids['op3']])


@pytest.mark.parametrize('selected',[[],['Capture'],['other:operation:op1']])
def test_names_unknown_ids_and_empty_selections_are_not_component_identity(source_graph,selected):
    graph,_=source_graph
    with pytest.raises(ValueError,match='exact Operation IDs'):
        operation_group(graph,'fixture',selected)


def test_diagnostic_access_is_explicit_and_never_upgraded_by_grouping(source_graph):
    graph,ids=source_graph
    graph['cards']['fixture']['admission']='diagnostic'
    for node in graph['nodes']:node['admission']='diagnostic'
    with pytest.raises(ValueError,match='Diagnostic'):
        operation_group(graph,'fixture',[ids['op1']])
    result=operation_group(graph,'fixture',[ids['op1']],include_diagnostic=True)
    assert result['admission']=='diagnostic'
    assert result['status']=='source_group_only; scientific_comparison_not_performed'


def test_duplicate_operation_ids_do_not_create_two_component_members(source_graph):
    graph,ids=source_graph
    with pytest.raises(ValueError,match='distinct exact Operation IDs'):
        operation_group(graph,'fixture',[ids['op1'],ids['op1']])


def test_cli_group_does_not_generate_any_scientific_edge(source_graph,tmp_path,capsys):
    graph,ids=source_graph;p=tmp_path/'graph.json';p.write_text(json.dumps(graph))
    assert main(['group',str(p),'fixture','--operation',ids['op1']])==0
    result=json.loads(capsys.readouterr().out)
    assert result['operation_ids']==[ids['op1']]
    assert set(e['kind'] for e in result['flows'])=={'material_input','material_output'}

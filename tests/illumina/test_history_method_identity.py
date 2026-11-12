import copy
import hashlib

from genomics_discovery.illumina.history_method_identity import bind_reviewed_methods,connectivity_report,catalogue_cards
from genomics_discovery.illumina.relationships import catalogue_entry_sha256


def edge(id,source,target,mid,pmid,pin):
    return {'id':id,'source':source,'target':target,'relation':'documented_reuse',
        'claim':{'reference_id':'R1','contributor_kind':'assay','claim_scope':'seed_workflow'},
        'review':{'decision':'accept','method_match':{'status':'resolved','record_id':mid}},
        'artifact':{'source_registry':{'sources':{'R1':{'pmid':pmid}}}}}


def fixture(tmp_path):
    p=tmp_path/'a.json';p.write_text('{}');pin=hashlib.sha256(p.read_bytes()).hexdigest()
    registry={'format_version':'reviewed_history_method_registry/1','methods':{
        'a':{'name':'A','method_pmids':['1'],'catalogue_entry_sha256':'scope-a','extraction':{'path':str(p),'sha256':pin}},
        'b':{'name':'B','method_pmids':['2'],'catalogue_entry_sha256':'scope-b','extraction':None}}}
    nodes={'external-a':{'name':'A contribution','method_identity_basis':{'record_id':'a','catalogue_entry_sha256':'scope-a'}},
        'external-b':{'name':'B contribution','method_identity_basis':{'record_id':'b','catalogue_entry_sha256':'scope-b'}},
        'later':{'name':'C','record_id':'c','kind':'bound_workflow'}}
    return registry,nodes,pin


def test_existing_method_join_connects_previously_separate_contribution_stars(tmp_path):
    registry,nodes,pin=fixture(tmp_path)
    # A is already a later-method node in one neighborhood, and a cited donor in another.
    nodes['workflow:a:'+pin]={'name':'A','record_id':'a','kind':'bound_workflow'}
    edges=[edge('b-a','external-b','workflow:a:'+pin,'b','2','scope-b'),
           edge('a-c','external-a','later','a','1','scope-a')]
    assert bind_reviewed_methods(nodes,edges,registry)==[]
    assert edges[1]['source']=='workflow:a:'+pin
    assert edges[1]['source_contribution']['name']=='A contribution'
    report=connectivity_report({'nodes':nodes,'edges':edges},['a','c'])
    assert report['weak_component_sizes']==[3]
    assert report['resolved_method_relationships']==2


def test_wrong_paper_changed_scope_and_self_identity_do_not_force_connections(tmp_path):
    registry,nodes,pin=fixture(tmp_path)
    e=edge('bad','external-a','later','a','999','scope-a')
    assert bind_reviewed_methods(nodes,[e],registry)[0]['reason'].startswith('Cited publication')
    assert e['source']=='external-a'
    registry['methods']['a']['catalogue_entry_sha256']='different implementation'
    assert 'changed' in bind_reviewed_methods(nodes,[e],registry)[0]['reason']
    registry['methods']['a']['catalogue_entry_sha256']='scope-a';nodes['later']['record_id']='a'
    e['artifact']['source_registry']['sources']['R1']['pmid']='1'
    assert 'version' in bind_reviewed_methods(nodes,[e],registry)[0]['reason']


def test_reviewed_absent_method_is_shared_placeholder_and_curation_queue(tmp_path):
    registry,nodes,pin=fixture(tmp_path)
    nodes['external-a']['method_identity_basis']={'paper_pmid':'1','method_name':'Absent assay','identity_status':'identified_unextracted_method'}
    e=edge('new','external-a','later',None,'1','')
    e['review']['method_match']={'status':'not_in_atlas','record_id':None,'method_name':'Absent assay'}
    second=copy.deepcopy(e);second['id']='second'
    assert bind_reviewed_methods(nodes,[e,second],registry)==[]
    assert e['source']==second['source']
    report=connectivity_report({'nodes':nodes,'edges':[e,second]})
    assert len(report['atlas_addition_queue'])==1
    assert report['weak_component_sizes']==[2]


def test_catalogue_scope_names_and_primary_ids_are_stable_when_unrelated_entries_added():
    card={'record_id':'a','source_title':'A','references':[{'pmid':'1','reference_id':'a:assigned'},
        {'pmid':'2','reference_id':'a:dependency:2'}],'implementation_scope':'Specific implementation'}
    first=catalogue_cards({'cards':[card]})[0]
    second=catalogue_cards({'cards':[card,{'record_id':'b'}]})[0]
    assert first['method_pmids']==['1']
    assert catalogue_entry_sha256(first)==catalogue_entry_sha256(second)

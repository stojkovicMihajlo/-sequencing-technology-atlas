"""Inspection indexing does not merge assay versions or use PMID as method ID."""
import copy
import json

import pytest

from scripts.illumina.library import assemble, owner, seed_entries, bind_history
from scripts.illumina.review_site.build import standalone


def inputs():
    legacy={'technologies':[{'id':'example','name':'Example','record_ids':['card'],
                            'origin_paper_ids':['pmid:123'], 'outside_catalogue':False}]}
    rows=[{'record_id':'card','title':'Example','batch':'catalogue','status':'supported',
           'card':{'source_method_labels':['Example']},'sources':{
               'metadata':{'pmid':'123','content_level':'metadata'},
               'text':{'pmid':'123','title':'A paper','content_level':'fulltext'}}}]
    methods=[{'method_id':n,'name':name,'primary_record_id':'card','source_record_ids':['card'],
              'primary_pmid':'123','scope':scope} for n,name,scope in [
        ('version_a','Example','Original implementation'),
        ('version_b','Example protocol','Later explicit protocol')]]
    snapshot={'records':{'extractions/state.json':{'data':{'cards':{}}}},'source_inventories':{}}
    for m in methods:
        mid=m['method_id'];prefix=f'extractions/cards/{mid}/attempt_001/'
        snapshot['records']['extractions/state.json']['data']['cards'][mid]={'status':'failed'}
        snapshot['records'][prefix+'card.json']={'data':{}}
        snapshot['source_inventories'][prefix+'source_registry.json']={'entries':{}}
    return legacy,rows,{'methods':methods},snapshot


def test_complementary_depictions_share_entry_but_versions_remain_distinct():
    l,r,s,p=inputs()
    r.append({'record_id':'poster','title':'Example','batch':'posters','status':'supported',
              'card':{'source_method_labels':['Example']},'sources':{}})
    s['methods'][0]['source_record_ids'].append('poster')
    before=copy.deepcopy((l,r,s,p))
    result=assemble(l,r,s,p)
    assert len(result['technologies'])==1
    assert result['technologies'][0]['workflow_versions']==['version_a','version_b']
    assert len(result['technologies'][0]['source_occurrences'])==2
    assert result['counts']['source_depictions']==2
    assert result['counts']['combined_registered_pmids']==1
    assert result['papers'][0]['highest_registered_level']=='fulltext'
    assert (l,r,s,p)==before


def test_shared_paper_does_not_collapse_distinct_technology_targets():
    l,r,s,p=inputs()
    s['methods'][1].update(name='Different method',primary_record_id='poster',source_record_ids=['poster'])
    r.append({'record_id':'poster','title':'Different method','batch':'posters','status':'failed',
              'card':{'source_method_labels':['Different method']},'sources':{}})
    result=assemble(l,r,s,p)
    assert len(result['technologies'])==2
    assert len(result['papers'])==1


def test_unmatched_poster_label_is_retained_as_resolution_task():
    l,r,s,p=inputs()
    r.append({'record_id':'new','title':'New source','batch':'posters','status':'supported',
              'card':{'source_method_labels':['Unresolved name']},'sources':{}})
    result=assemble(l,r,s,p)
    assert result['identity_queue'][0]['label']=='Unresolved name'
    assert len(result['technologies'])==1


def test_ambiguous_display_parent_is_not_guessed():
    l,_,_,_=inputs()
    l['technologies'].append({'id':'other','name':'Other','aliases':['Example'],'record_ids':['card']})
    assert owner({'name':'Example','primary_record_id':'card'},seed_entries(l))[0] is None


def test_duplicate_work_units_cannot_generate_duplicate_method_folders():
    l,r,s,p=inputs();s['methods'].append(copy.deepcopy(s['methods'][0]))
    with pytest.raises(ValueError,match='Duplicate scoped'):assemble(l,r,s,p)


def test_missing_supported_candidate_is_not_presented_as_admitted_graph():
    l,r,s,p=inputs();p['records']['extractions/state.json']['data']['cards']['version_a']['status']='supported'
    with pytest.raises(ValueError,match='no saved extraction'):assemble(l,r,s,p)


def test_standalone_page_preserves_data_and_cannot_close_its_script(tmp_path):
    record={'title':'<test>','candidate':None,'payload':'</script><script>attack()</script>'}
    output=tmp_path/'graph.html';standalone(record,output)
    page=output.read_text()
    assert '<title>&lt;test&gt;</title>' in page
    assert record['payload'] not in page
    payload=page.split('<script id="record-data" type="application/json">',1)[1].split('</script>',1)[0]
    assert json.loads(payload)==record
    assert 'fetch(' not in payload


@pytest.mark.parametrize('mutation',[None,'version','claim','endpoint','refinement'])
def test_historical_views_preserve_reviewed_claim_and_exact_versions(mutation):
    claim={'id':'c','direction':'left_to_right','relation':'component_adoption'}
    review={'decision':'accept'}
    edge={'id':'edge','kind':'component_adoption','source':'illumina:a','target':'illumina:b',
          'pair_graph':'/archive/pair.json','pair_graph_sha256':'pair-pin','claim':claim,'review':review}
    pair={'path':'/archive/pair.json','sha256':'pair-pin','data':{
        'bindings':{s:{'record_id':mid,'extraction_sha256':mid+'-pin'} for s,mid in [('left','a'),('right','b')]},
        'historical_links':[{'claim':copy.deepcopy(claim),'review':review}],
        'diagram_adequacy':[{'verdict':'sufficient'}]}}
    library={'counts':{},'workflows':{mid:{'metadata':{'native_extraction':{'sha256':mid+'-pin'}}} for mid in ['a','b']}}
    snapshot={'records':{'history/pairs/pair/graph.json':pair}}
    if mutation=='version':library['workflows']['a']['metadata']['native_extraction']['sha256']='new-version'
    if mutation=='claim':edge['claim']['unverified_new_fact']='unsupported'
    if mutation=='endpoint':edge['source']='illumina:wrong'
    if mutation=='refinement':pair['data']['diagram_adequacy'][0]['verdict']='needs_finer_extraction'
    if mutation:
        with pytest.raises(ValueError):bind_history(library,{'edges':[edge]},snapshot)
    else:
        bind_history(library,{'edges':[edge]},snapshot)
        assert library['counts']['historical_edges']==1

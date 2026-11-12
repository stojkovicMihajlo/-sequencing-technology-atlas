import json
from collections import Counter
from copy import deepcopy
import pytest

from experiments.history_parallel_20261112 import prepare as module


def cohort(count=60):
    cards, methods = {}, {}
    for i in range(count):
        mid = f'method-{i:02d}'
        cards[mid] = {'record_id': mid, 'category': ['Genome', f'Leaf-{i % 3}'],
                      'curation_binding': {'primary_paper_id': f'pmid-{100+i}'}}
        methods[mid] = {'status': 'ready', 'selected_primary': {'format': 'JATS', 'seed_eligible': True, 'pmid': str(100+i)}}
    return cards, {'methods': methods}


def test_exact_fifty_unique_ready_methods_balanced_across_curated_leaf_categories():
    cards, index = cohort()
    ready, excluded = module.eligible_methods(index, cards)
    selected = module.choose_methods(ready, cards)
    assert len(selected) == len(set(selected)) == 50
    assert sorted(Counter(module.category(cards[mid]) for mid in selected).values()) == [16,17,17]
    assert selected == module.choose_methods(dict(reversed(list(ready.items()))), cards)
    assert not excluded


def test_running_pilot_provider_blocks_pdf_and_unassigned_sources_do_not_enter_selection():
    cards, index = cohort()
    for mid in module.EXCLUSIONS:
        cards[mid] = deepcopy(cards['method-00']);cards[mid]['record_id'] = mid
        index['methods'][mid] = deepcopy(index['methods']['method-00'])
    index['methods']['method-00']['selected_primary']['format'] = 'PDF'
    index['methods']['method-01']['status'] = 'held'
    del cards['method-02']['curation_binding']['primary_paper_id']
    ready, excluded = module.eligible_methods(index, cards)
    assert module.EXCLUSIONS.isdisjoint(ready)
    assert {'method-00','method-01','method-02'}.isdisjoint(ready)
    assert len(ready) == 57 and len(module.choose_methods(ready,cards)) == 50
    assert all(mid in excluded for mid in module.EXCLUSIONS)


def test_insufficient_ready_methods_fails_instead_of_pretending_fifty_agents_started():
    cards,index = cohort(49)
    ready,_ = module.eligible_methods(index,cards)
    with pytest.raises(module.SelectionNotReady,match='Only 49.*50 required'):
        module.choose_methods(ready,cards)


def test_reference_hints_rank_selection_without_creating_or_requiring_history_edges():
    cards,index = cohort(6)
    ready,_ = module.eligible_methods(index,cards)
    hints={'method-03':['101','104']}
    selected = module.choose_methods(ready,cards,count=3,citation_hints=hints)
    assert selected[0] == 'method-03'
    assert index['methods']['method-03'].get('edges') is None
    assert module.choose_methods(ready,cards,count=3)[0] == 'method-00'


def test_shared_publication_does_not_merge_distinct_canonical_methods():
    cards,index = cohort(50)
    index['methods']['method-01']['selected_primary'] = deepcopy(index['methods']['method-00']['selected_primary'])
    ready,_ = module.eligible_methods(index,cards)
    assert len(module.choose_methods(ready,cards)) == 50


def test_blocked_preflight_writes_explicit_zero_model_call_receipt(monkeypatch,tmp_path):
    batch=tmp_path/'batch';batch.mkdir();(batch/'state.json').write_text(json.dumps({'slurm_job_id':'52055656'}))
    (batch/'inventory.json').write_text(json.dumps({'cards':[], 'boundary':'Fixture canonical inventory'}))
    cards,index=cohort(49)
    library=tmp_path/'library';library.mkdir()
    (library/'canonical_inventory.json').write_text(json.dumps({'methods':{
        mid:{'id':mid,'name':mid,'entry_type':'named_method','identity_status':'catalogue_identity_reviewed'} for mid in cards}}))
    def snapshot(batch,output):
        output.mkdir();(output/'inventory.json').write_text(json.dumps({'cards':list(cards.values())}));return {'accepted_methods':list(cards)}
    monkeypatch.setenv('SLURM_JOB_ID','fixture-preflight')
    monkeypatch.setattr(module,'snapshot_accepted',snapshot)
    monkeypatch.setattr(module,'prepare_index',lambda *a:index)
    output=tmp_path/'preflight'
    with pytest.raises(module.SelectionNotReady):module.prepare(batch,output,library=library)
    receipt=json.loads((output/'PREFLIGHT.json').read_text())
    assert receipt['status']=='blocked' and receipt['source_ready_methods']==49
    assert receipt['model_calls']==0 and receipt['selected_methods']==[]
    assert (output/'original_method_inventory.json').read_bytes()==(batch/'inventory.json').read_bytes()
    assert not (output/'selection.json').exists()


def test_curated_folder_classification_is_preserved():
    card={'curation_binding':{'package':'/scratch/project/methods/Transcriptome/RNA Structure/example/source_package.json'}}
    assert module.category(card)==('Transcriptome','RNA Structure')
    assert module.category({**card,'category':[]})==('Transcriptome','RNA Structure')


def test_full_named_catalogue_lookup_keeps_273_ids_and_existing_fields_without_new_seeds():
    methods={}
    for i in range(273):
        mid=f'curated-{i:03d}'
        methods[mid]={'id':mid,'name':f'Method {i}','aliases':['Shared alias'],
            'entry_type':'named_method','identity_status':'catalogue_identity_reviewed',
            'identity_decisions':[{'action':'retain','canonical_id':mid,'review_level':'catalogue_identity_review'}],
            'description':{'text':f'Original description {i}','source_id':mid},
            'method_references':[{'reference_id':mid+':references:0','pmid':str(1000+i),'role':'method_reference'}]}
    for kind in ['family_overview','commercial_kit','reusable_component']:
        methods[kind]={'id':kind,'entry_type':kind,'name':kind}
    methods['curated-001']['name']=methods['curated-002']['name']
    methods['curated-001']['method_references']=deepcopy(methods['curated-002']['method_references'])
    original={'cards':[{'record_id':'curated-000','source_title':'Keep exact original title',
        'source_bindings':[{'source_id':'original-card'}],
        'references':[{'reference_id':'curated-000:dependency:1','pmid':'999'}],
        'opaque_source_field':{'nested':['unchanged']}}]}
    untouched=deepcopy(original)
    result=module.expanded_method_inventory(original,{'methods':methods},canonical_sha256='fixture-library-pin',
        assigned_methods=[{'method_id':'curated-000','assigned_paper_ids':['pmid-123','external-doi'], 'primary_paper_id':'pmid-456'}])
    assert len(result['cards'])==273
    assert len({c['record_id'] for c in result['cards']})==273
    assert original==untouched
    existing=result['cards'][0]
    assert {k:existing[k] for k in untouched['cards'][0]}==untouched['cards'][0]
    assert existing['method_pmids']==['1000','123','456']
    assert '999' not in existing['method_pmids']
    added=next(c for c in result['cards'] if c['record_id']=='curated-001')
    assert added['catalogue_only'] and added['description_verbatim']=='Original description 1'
    assert added['identity_decisions']==methods['curated-001']['identity_decisions']
    assert added['curation_provenance']['canonical_inventory_sha256']=='fixture-library-pin'
    assert 'operations' not in added and 'implementation_scope' not in added
    assert 'seed_methods' not in result
    assert result['method_catalogue_provenance']['curated_named_methods']==273

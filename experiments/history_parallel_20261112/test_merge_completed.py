import copy
import json
from pathlib import Path
import shutil

import pytest

from experiments.history_parallel_20261112.merge_completed import (
    merge_completed,sha,completed_history_registry,select_uninvestigated_histories)
from tests.illumina.test_history_identity_compiler import inputs
BLOCK='This content was flagged for possible biological risk. If this seems wrong, try rephrasing your request. We are continuously refining our work in detecting biological risk, and you can read more about our approach in our blog post: https://openai.com/index/preparing-for-future-ai-capabilities-in-biology'

def terminal_archive(path, events):
    path.mkdir(parents=True,exist_ok=True)
    (path/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
    (path/'result.json').write_text(json.dumps({'exit_code':1,'thread_id':'synthetic-policy-thread','usage':{},'elapsed_seconds':1.,'error':'Codex exited with code 1'}))
    (path/'final.txt').write_text('')



def base(tmp_path):
    folder=tmp_path/'base';folder.mkdir()
    manifest,_,_=inputs(folder)
    return manifest


def completed(folder,mid):
    folder.mkdir(parents=True)
    manifest,_,_=inputs(folder)
    graph=json.loads((folder/'graph.json').read_text())
    extraction=folder/'extraction.json';value=json.loads(extraction.read_text());value['record_id']=mid
    extraction.write_text(json.dumps(value))
    seed=graph['nodes'][0]
    seed['name']=mid;seed['bindings'].update(record_id=mid,parent_attempt=str(folder),
        parent_artifact_pins={'extraction.json':sha(extraction)})
    seed['method_contribution_summary']={'text':'Source-supported method context','status':'model-authored summary'}
    (folder/'graph.json').write_text(json.dumps(graph))
    (folder/'inputs').mkdir();shutil.copyfile(folder/'sources.json',folder/'inputs/sources.json')
    (folder/'state.json').write_text(json.dumps({'status':'completed','stages':[]}))
    return folder


def validated(folder):
    assert json.loads((folder/'state.json').read_text())['status']=='completed'


def test_completion_join_uses_all_catalog_sources_versions_and_zero_edge_outcomes(tmp_path):
    def native(folder,mid,workflow,*,links=(),policy='full'):
        (folder/'inputs').mkdir(parents=True)
        text='Primary method '+mid
        binding={'record_id':mid,'parent_artifact_pins':{'extraction.json':workflow},
                 'seed_article_sha256':'article-'+mid}
        (folder/'graph.json').write_text(json.dumps({'nodes':[{'id':'seed','bindings':binding}],
            'links':list(links),'format_version':'citation_lineage_pilot/4','classification_policy':'assay_contribution_scope/1'}))
        (folder/'state.json').write_text(json.dumps({'status':'completed','reading_policy':policy,
            'model':'gpt-6.1-sol','effort':'xhigh','stages':[]}))
        (folder/'inputs/sources.json').write_text(json.dumps({'seed':{'text':text}}))
        (folder/'analysis.json').write_text(json.dumps({'format_version':'citation_analysis/4'}))
        (folder/'review.json').write_text(json.dumps({'format_version':'citation_change_review/3'}))
        import hashlib
        return {'status':'ready','extraction_sha256':workflow,'selected_primary':{
            'article_sha256':'article-'+mid,'text_sha256':hashlib.sha256(text.encode()).hexdigest()}}
    first=tmp_path/'in_merge';other=tmp_path/'other_batch/citations/other'
    methods={'first':native(first,'first','workflow-first',links=[{'id':'existing'}]),
             'other':native(other,'other','workflow-other')}
    # A failed parent does not erase a validated completed child absent from main merge.
    (other.parents[1]/'state.json').write_text(json.dumps({'status':'failed','citation_runs':{
        'other':{'path':str(other),'status':'completed'}}}))
    merged=tmp_path/'merged.json';merged.write_text(json.dumps({'format_version':'illumina_history_inputs/1',
        'overlays':[{'path':str(first/'graph.json')}]}))
    catalog=tmp_path/'catalog.json';catalog.write_text(json.dumps({'format_version':'atlas_run_catalog/1',
        'remote_project_root':str(tmp_path),'history_completion_sources':[{'path':str(merged)}],
        'runs':[{'stage':'evolution','output':str(other.parents[1])}]}))
    registry=completed_history_registry(catalog,validate_run=validated)
    assert {r['record_id'] for r in registry['entries']}=={'first','other'}
    assert next(r for r in registry['entries'] if r['record_id']=='other')['positive_relationships']==0
    assert all(r['policy_id'] and r['policy']['reading_policy']=='full' for r in registry['entries'])
    selected,decisions=select_uninvestigated_histories(methods,['first','other'],registry)
    assert selected==[] and decisions['other']['reason']=='completed_exact_version_reused'
    # Same name is not enough: a genuinely different accepted workflow needs history.
    changed=copy.deepcopy(methods);changed['other']['extraction_sha256']='new-workflow'
    selected,decisions=select_uninvestigated_histories(changed,['other'],registry)
    assert selected==['other'] and decisions['other']['reason']=='new_current_version_history'
    # Changed policy alone never silently authorizes repeat work.
    selected,decisions=select_uninvestigated_histories(methods,['other'],registry,
        policy_revisits={'other':'Explicit matched comparison of new selective nomination policy'})
    assert selected==['other'] and decisions['other']['reason']=='explicit_policy_revisit'
    with pytest.raises(ValueError,match='explicit reasons'):
        select_uninvestigated_histories(methods,['other'],registry,policy_revisits={'other':''})
    partial=tmp_path/'interrupted';(partial/'inputs').mkdir(parents=True)
    (partial/'state.json').write_text(json.dumps({'status':'failed','stages':[
        {'role':'nominate','status':'validated'}]}))
    (partial/'inputs/seed_binding.json').write_text(json.dumps({'record_id':'fresh',
        'parent_artifact_pins':{'extraction.json':'workflow-fresh'}}))
    catalog_value=json.loads(catalog.read_text());catalog_value['runs'].append({'stage':'evolution','output':str(partial)})
    catalog.write_text(json.dumps(catalog_value))
    registry=completed_history_registry(catalog,validate_run=validated)
    fresh={'fresh':{'status':'ready','extraction_sha256':'workflow-fresh','selected_primary':{}}}
    selected,decisions=select_uninvestigated_histories(fresh,['fresh'],registry)
    assert selected==[] and decisions['fresh']['reason']=='interrupted_history_requires_recovery_reason'
    selected,decisions=select_uninvestigated_histories(fresh,['fresh'],registry,
        recovery_reasons={'fresh':'Parent cancelled for a cohort-selection bug; resume valid unfinished current-version research.'})
    assert selected==['fresh'] and decisions['fresh']['reason']=='interrupted_history_recovery'
    corrupt=copy.deepcopy(registry);corrupt['excluded'].append({'record_id':'fresh',
        'extraction_sha256':'workflow-fresh','reason':'completed_history_validation_failed'})
    selected,decisions=select_uninvestigated_histories(fresh,['fresh'],corrupt,
        recovery_reasons={'fresh':'A recovery reason cannot bypass corrupt completion custody.'})
    assert selected==[] and decisions['fresh']['reason']=='prior_completion_requires_validation'


def test_completed_native_history_extends_index_and_keeps_identity_summary_and_sources(tmp_path):
    manifest=base(tmp_path);native=completed(tmp_path/'new','new-seed')
    original=(native/'graph.json').read_bytes()
    out=tmp_path/'merged';receipt=merge_completed(manifest,[native],out,validate_run=validated,render=False)
    assert receipt['reviewed_method_histories']==2
    assert receipt['new_model_calls']==0 and not receipt['scientific_records_changed']
    index=json.loads((out/'history_index.json').read_text())
    assert len(index['edges'])==2
    assert all(e['method_identity_resolution']['status']=='independently_reviewed_method_identity' for e in index['edges'])
    node=next(n for n in index['nodes'].values() if n.get('record_id')=='new-seed')
    assert node['method_contribution_summaries'][0]['text']=='Source-supported method context'
    assert json.loads((out/'CONNECTIVITY.json').read_text())['resolved_method_relationships']==2
    assert (native/'graph.json').read_bytes()==original


def test_exact_duplicates_do_not_multiply_history_but_conflicting_seed_is_refused(tmp_path):
    manifest=base(tmp_path);native=completed(tmp_path/'new','new-seed')
    out=tmp_path/'merged'
    first=merge_completed(manifest,[native,native],out,validate_run=validated,render=False)
    assert len(first['duplicates'])==1 and first['reviewed_method_histories']==2
    conflicting=tmp_path/'alternative';shutil.copytree(native,conflicting)
    graph=json.loads((conflicting/'graph.json').read_text());graph['nodes'][0]['name']='Conflicting revised display'
    (conflicting/'graph.json').write_text(json.dumps(graph))
    second=merge_completed(out/'history_inputs.json',[conflicting],tmp_path/'conflict',validate_run=validated,render=False)
    assert second['reviewed_method_histories']==2
    assert second['excluded'][0]['reason']=='conflicting_seed_version'
    with pytest.raises(FileExistsError):
        merge_completed(manifest,[],out,validate_run=validated,render=False)


def test_batch_checkpoint_excludes_unfinished_and_explicit_provider_block_without_retries(tmp_path):
    manifest=base(tmp_path);batch=tmp_path/'batch';native=completed(batch/'citations'/'ready','ready')
    waiting=batch/'citations'/'waiting';waiting.mkdir();(waiting/'state.json').write_text(json.dumps({'status':'analyse'}))
    blocked=batch/'citations'/'blocked';attempt=blocked/'attempts'/'002_analyse'
    terminal_archive(attempt,[{'type':'thread.started','thread_id':'synthetic-policy-thread'},
        {'type':'turn.failed','error':{'message':BLOCK}}])
    (blocked/'state.json').write_text(json.dumps({'status':'failed','error':'Native stage failed',
        'stages':[{'status':'failed','path':str(attempt)}]}))
    (batch/'state.json').write_text(json.dumps({'status':'citation_research','seeds':['ready','waiting','blocked'],
        'citation_runs':{mid:{'path':str(path)} for mid,path in [('ready',native),('waiting',waiting),('blocked',blocked)]}}))
    calls=[]
    def check(folder):calls.append(folder);validated(folder)
    receipt=merge_completed(manifest,[batch],tmp_path/'merged',validate_run=check,render=False)
    assert calls==[native]
    assert len(receipt['excluded'])==2
    refusal=next(x for x in receipt['excluded'] if x['method']=='blocked')
    assert refusal['failure_category']=='provider_biological_risk_block'
    assert receipt['reviewed_method_histories']==2

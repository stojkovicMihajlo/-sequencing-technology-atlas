import copy
import json
from pathlib import Path

import pytest

from experiments.pilot50_20261104 import evaluation as ev
from genomics_discovery.illumina.models import DiagramExtraction, DiagramReview, digest, review_targets


def attempt(tmp_path, name='first', prefix='', text='RNA is copied into DNA in this schematic.'):
    path=tmp_path/name; path.mkdir(parents=True)
    image={'source_id':prefix+'diagram','locator':'middle arrow','basis':'image','support':'depicted'}
    sources={prefix+'diagram':{'kind':'image','content_level':'diagram','path':'/original/diagram.png','sha256':'a'*64},
             prefix+'paper':{'kind':'text','content_level':'fulltext','text':text,'pmid':'123'}}
    c=DiagramExtraction(record_id=name,title='Assay',method_names=['Assay'],scope='Original implementation',
        inspected_sources=list(sources),states=[{'id':s,'label':s,'kind':'material','evidence':[image]} for s in ('RNA','DNA')],
        operations=[{'id':'copy','label':'Copy RNA','description':'RNA is copied into DNA','inputs':['RNA'],'outputs':['DNA'],'evidence':[image]}],
        flows=[{'id':'f1','source':'RNA','target':'copy','kind':'material_input','evidence':[image]},
               {'id':'f2','source':'copy','target':'DNA','kind':'material_output','evidence':[image]}],
        method_scopes=[{'method':'Assay','operation_ids':['copy'],'status':'resolved','rationale':'Original route','evidence':[image]}],
        paper_matches=[],corrections=[],unknowns=[])
    r=DiagramReview(record_id=name,candidate_sha256=digest(c),inspected_sources=list(sources),checks=[{
        'target':target,'verdict':'supported','action':'accept','rationale':'Source-supported schematic','evidence':[image]}
        for target in sorted(review_targets(c))])
    objects={'extraction.json':c.model_dump(mode='json'),'review.json':r.model_dump(mode='json'),
             'source_registry.json':sources,'stages.json':[{'role':'author','revision':0,'elapsed_seconds':5,'usage':{'output_tokens':20}}]}
    for filename,value in objects.items(): ev.write(path/filename,value)
    return {'attempt_directory':str(path),'declared_status':'supported',
            'artifact_pins':{filename:ev.sha(path/filename) for filename in objects}}


def pair(tmp_path):
    return {'method_id':'assay','scope_target':'Original implementation',
        'identity_match':{'status':'reviewed_same_implementation','rationale':'Explicitly selected same scoped implementation'},
        'baseline':attempt(tmp_path,'old'),'candidate':attempt(tmp_path,'new',prefix='new:')}


def test_renamed_source_ids_and_paths_do_not_look_like_more_evidence(tmp_path):
    row=pair(tmp_path)
    result=ev.compare_pair(row)
    assert result['comparison_status']=='same_registered_sources'
    assert result['source_delta']=={'added':[],'removed':[]}
    assert result['semantic_outcome']=='not_assessed'
    assert result['harness_improvement_established'] is False
    assert result['candidate']['current_contract_valid'] is True


def test_added_paper_content_is_a_confounder_not_harness_improvement(tmp_path):
    row=pair(tmp_path)
    row['candidate']=attempt(tmp_path,'expanded',text='RNA is copied into DNA; an additional consequence is reported.')
    result=ev.compare_pair(row)
    assert result['comparison_status']=='sources_changed'
    assert result['source_delta']['added'] and result['source_delta']['removed']
    assert result['harness_improvement_established'] is False


def test_grouped_or_unreviewed_identity_stays_pending(tmp_path):
    row=pair(tmp_path); row['identity_match']['status']='candidate_only'
    assert ev.compare_pair(row)['comparison_status']=='pending_identity'
    row.pop('baseline')
    assert ev.compare_pair(row)['comparison_status']=='unpaired'


def test_unreviewed_output_edit_is_not_a_usable_baseline(tmp_path):
    row=pair(tmp_path)
    path=Path(row['baseline']['attempt_directory'])/'extraction.json'
    value=ev.read(path); value['operations'][0]['description']='Invented supported procedure'; ev.write(path,value)
    report=ev.compare_pair(row)
    assert report['comparison_status']=='artifact_or_schema_issue'
    with pytest.raises(ValueError,match='intact parseable'): ev.prepare_blinded_pair(row,tmp_path/'evaluation')


def test_ab_packet_hides_prior_reviews_and_preserves_scientific_content(tmp_path):
    row=pair(tmp_path); root=ev.prepare_blinded_pair(row,tmp_path/'evaluation',seed=3)
    packet=ev.read(root/'packet.json'); sources=ev.read(root/'sources.json')
    assert packet['source_condition']=='same_content'
    assert len(sources)==2
    assert 'baseline' not in json.dumps(packet) and 'declared_status' not in json.dumps(packet)
    assert all(c['record_id']==label for label,c in packet['candidates'].items())
    assert all(c['operations'][0]['description']=='RNA is copied into DNA' for c in packet['candidates'].values())
    assert all(set(c['inspected_sources'])==set(sources) for c in packet['candidates'].values())
    assert set(ev.read(root/'private_assignment.json')['labels'].values())=={'baseline','candidate'}


def assessment(root):
    packet=ev.read(root/'packet.json'); sources=ev.read(root/'sources.json')
    sid=next(k for k,v in sources.items() if v['kind']=='text')
    finding={'aspect':'transformation','finding':'Both records preserve the source-described RNA-to-DNA transformation.',
        'comparison':'equivalent','a_targets':['operation:copy'],'b_targets':['operation:copy'],
        'evidence':[{'source_id':sid,'locator':'registered source','basis':'text','support':'reported','quote':sources[sid]['text']}]}
    doc=ev.SemanticComparison(packet_sha256=ev.sha(root/'packet.json'),scope_alignment='same_implementation',
        scope_rationale='Both records describe this same supported scoped implementation.',scope_evidence=finding['evidence'],outcome='equivalent',
        findings=[finding],important_unchecked_questions=['Full biochemical compatibility is outside this diagnostic.'],
        summary='Equivalent with respect to the checked source-supported transformation.')
    return doc,packet,sources


@pytest.mark.parametrize('mutation,error',[
    ('quote','quotation'),('target','target'),('scope','implementation'),('winner','Directional'),('metadata','Metadata'),('packet','packet')])
def test_semantic_assessment_cannot_pass_invalid_bindings_or_inconsistent_verdict(tmp_path,mutation,error):
    root=ev.prepare_blinded_pair(pair(tmp_path),tmp_path/'evaluation'); doc,packet,sources=assessment(root)
    if mutation=='quote': doc.findings[0].evidence[0]=doc.findings[0].evidence[0].model_copy(update={'quote':'An unsupported invented quotation.'})
    elif mutation=='target': doc.findings[0].a_targets=['operation:invented']
    elif mutation=='scope': doc.scope_alignment='different_implementation'
    elif mutation=='winner': doc.outcome='B_better'
    elif mutation=='metadata': sources[doc.findings[0].evidence[0].source_id]['content_level']='metadata'
    elif mutation=='packet': doc.packet_sha256='changed'
    with pytest.raises(ValueError,match=error): ev.validate_comparison(doc,packet,sources,ev.sha(root/'packet.json'))


def test_valid_assessment_remains_source_bounded_and_no_native_call_on_mac(tmp_path,monkeypatch):
    import asyncio
    row=pair(tmp_path); root=ev.prepare_blinded_pair(row,tmp_path/'evaluation'); doc,packet,sources=assessment(root)
    ev.validate_comparison(doc,packet,sources,ev.sha(root/'packet.json'))
    monkeypatch.delenv('SLURM_JOB_ID',raising=False)
    with pytest.raises(ValueError,match='Sherlock Slurm'):
        asyncio.run(ev.evaluate_pair_native(row,tmp_path/'native'))


def test_manifest_counts_unpaired_and_pending_without_automatic_winner(tmp_path):
    row=pair(tmp_path); unmatched=copy.deepcopy(row); unmatched['method_id']='other'; unmatched.pop('baseline')
    p=tmp_path/'manifest.json'; ev.write(p,{'format_version':'atlas_pilot_comparison/1','methods':[row,unmatched]})
    result=ev.evaluate_manifest(p,tmp_path/'report.json')
    assert result['comparison_counts']=={'same_registered_sources':1,'unpaired':1}
    assert result['semantic_evaluations_completed']==0
    assert all(not r['harness_improvement_established'] for r in result['methods'])


def test_baseline_inventory_is_source_occurrences_not_identity_admission():
    rows=ev.build_baseline_inventory({'rows':[{'record_id':'a','title':'Assay','source_method_labels':['Assay']},
                                              {'record_id':'b','title':'Assay','source_method_labels':['Assay']}]})
    assert len(rows)==2
    assert all(r['identity_status']=='source_occurrence_not_method_identity' for r in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize('fail',[False,True])
async def test_native_schedule_hides_label_key_then_restores_receipt(tmp_path,monkeypatch,fail):
    from types import SimpleNamespace
    from PIL import Image
    from genomics_discovery.execution import codex_worker
    row=pair(tmp_path)
    image=tmp_path/'source.png'; Image.new('RGB',(2,2),'white').save(image)
    for side in ('baseline','candidate'):
        registry_path=Path(row[side]['attempt_directory'])/'source_registry.json'
        registry=ev.read(registry_path)
        for source in registry.values():
            if source['kind']=='image': source.update(path=str(image),sha256=ev.sha(image))
        ev.write(registry_path,registry)
        row[side]['artifact_pins']['source_registry.json']=ev.sha(registry_path)
    root=tmp_path/'native'
    class FakeWorker:
        async def run(self,prompt,attempt,image_paths):
            assert not (root/'private_assignment.json').exists()
            assert 'private_assignment' not in prompt
            assert row['baseline']['attempt_directory'] not in prompt
            if fail: raise RuntimeError('Synthetic transport failure')
            doc,_,_=assessment(root)
            ev.write(attempt/'artifact.json',doc.model_dump(mode='json'))
            (attempt/'prompt.txt').write_text(prompt)
            ev.write(attempt/'command.json',{'image_inputs':[{'path':str(p),'sha256':ev.sha(p)} for p in image_paths],
                'argv':['codex','exec',*[v for p in image_paths for v in ('--image',str(p))]]})
            return SimpleNamespace(exit_code=0,error=None,thread_id='fresh-evaluator-thread',usage={})
    monkeypatch.setenv('SLURM_JOB_ID','offline-fake-worker-test')
    monkeypatch.setattr(codex_worker,'assess_archived_attempt',lambda p: SimpleNamespace(
        complete=True,thread_id='fresh-evaluator-thread',usage={}))
    if fail:
        with pytest.raises(RuntimeError,match='Synthetic'): await ev.evaluate_pair_native(row,root,worker=FakeWorker())
    else:
        result=await ev.evaluate_pair_native(row,root,worker=FakeWorker())
        assert result['model_assessed_outcome']=='equivalent'
        assert result['harness_improvement_established'] is False
    assert (root/'private_assignment.json').exists()
    assert ev.read(root/'state.json')['status']==('failed' if fail else 'completed')

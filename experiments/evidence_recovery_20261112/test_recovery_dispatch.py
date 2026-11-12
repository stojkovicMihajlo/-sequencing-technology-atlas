from experiments.evidence_recovery_20261112.run import retry_methods
from experiments.diagram_paper_pilot_20261106.run import dependency_reference
from argparse import Namespace
import json
import pytest
from experiments.evidence_recovery_20261112.run import execute


def case(mid,category):
    return {'selection':{'method_id':mid},'hold':{'failure_category':category}}


def test_unavailable_primary_or_unchanged_bundle_is_not_force_retried():
    cases=[case('a','evidence_recovery_failed'),case('b','evidence_request_unresolved'),case('c','provider_policy_block')]
    eligible,held=retry_methods(cases,{'a':{}},{})
    assert eligible==[] and set(held)=={'a','b','c'}


def test_registered_addition_allows_fresh_review_but_does_not_assign_acceptance(monkeypatch):
    monkeypatch.setattr('experiments.evidence_recovery_20261112.run.verify_registry_files',lambda r:None)
    cases=[case('a','evidence_recovery_failed'),case('b','evidence_request_unresolved')]
    eligible,held=retry_methods(cases,{'b':{'supplement':{'kind':'text'}}},{'a':{'paper':{'kind':'text'}}})
    assert [r['method_id'] for r in eligible]==['a','b'] and held=={}


def test_doi_only_dependency_has_explicit_source_assessment_address():
    r=dependency_reference('m','protocol:verified',{'pmid':None,'doi':'10.1234/example'})
    assert r['source_id']=='protocol:verified' and r['pmid'] is None
    assert r['reference_id']=='m:dependency:protocol:verified'


@pytest.mark.asyncio
async def test_one_shot_handoff_starts_isolated_retries_and_preserves_live_state(tmp_path,monkeypatch):
    from genomics_discovery.illumina.extraction import write
    import experiments.evidence_recovery_20261112.run as pipeline
    monkeypatch.setenv('SLURM_JOB_ID','test')
    monkeypatch.setattr(pipeline,'verify_registry_files',lambda r:None)
    base=tmp_path/'base';current=base/'run/extraction';current.mkdir(parents=True)
    live={'cards':{'a':{'status':'source_recovery_needed'},'epiradseq':{
        'status':'failed','reason':"TypeError: unhashable type: 'list'"},'maine-seq':{'status':'failed'}}}
    write(current/'state.json',live);before=(current/'state.json').read_bytes()
    methods=[{'method_id':'epiradseq'}];write(current.parent/'selected.json',{'methods':methods})
    write(base/'recovery_02/registered_additions.json',{});write(base/'recovery_02/primary_overrides.json',{})
    row=case('a','evidence_request_unresolved');row['paper']={'paper_id':'pmid-1'}
    write(tmp_path/'snapshot.json',{'source_release':str(base),'cases':[row]})
    write(tmp_path/'ids.json',['a'])
    calls=[]
    async def fake_extract(library,vault,selection_path,output,workers,**kwargs):
        selected=json.loads(selection_path.read_text());calls.append([m['method_id'] for m in selected['methods']])
        assert kwargs['max_revisions']==2 and kwargs['stop_on_first_issue'] is False
        return {'counts':{'supported':1}}
    async def fake_investigate(snapshot,library,vault,output,**kwargs):
        await kwargs['on_recovered']('pmid-1',[row],{'a':{'new':{'kind':'text'}}},{'a':{}},{})
    monkeypatch.setattr(pipeline.extractor,'execute',fake_extract)
    monkeypatch.setattr(pipeline,'investigate',fake_investigate)
    await execute(Namespace(current=current,snapshot=tmp_path/'snapshot.json',methods=tmp_path/'ids.json',
        library=tmp_path/'library',vault=tmp_path/'vault',output=tmp_path/'output',workers=1,hints=None))
    assert sorted(calls)==[['a'],['epiradseq']]
    assert (current/'state.json').read_bytes()==before
    outcome=json.loads((tmp_path/'output/state.json').read_text())
    assert outcome['status']=='finished' and outcome['retries']['pmid-1']['status']=='completed'

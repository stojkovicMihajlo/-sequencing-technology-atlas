import copy
import json
from argparse import Namespace
from pathlib import Path
import pytest

from experiments.metadata_retry3_20261112 import run as retry


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data))


def pre_model(attempt,mid):
    write(attempt/'card.json',{'record_id':mid,'references':[]})
    registry={'paper':{'kind':'text','content_level':'fulltext','doi':['10.1234/fixture'], 'text':'Fixture registered text'}}
    write(attempt/'source_registry.json',registry)
    write(attempt/'attempts/001_author/schema.json',{})
    write(attempt/'attempts/001_author/image_delivery.json',[])
    (attempt/'attempts/001_author/diagram_00.png').write_bytes(b'fixture')
    return registry


def states(tmp_path):
    cards={mid:{'status':'failed','reason':retry.FAILURE,'attempt_directory':str(tmp_path/mid)} for mid in retry.TARGETS}
    return {'combined':copy.deepcopy(cards),'active_methods':[]},{'slurm_job_id':'52055656','cards':cards}


@pytest.mark.parametrize('change', ['accepted','different_failure','newer_attempt','already_active'])
def test_retry_requires_exact_failed_reason_and_current_unstarted_scope(tmp_path,change):
    checkpoint,original=states(tmp_path)
    assert set(retry.verify_failed_scope(checkpoint,original))==set(retry.TARGETS)
    mid=retry.TARGETS[0]
    if change=='accepted':checkpoint['combined'][mid]['status']='supported'
    if change=='different_failure':checkpoint['combined'][mid]['reason']='Native provider block'
    if change=='newer_attempt':checkpoint['combined'][mid]['attempt_directory']='newer-attempt'
    if change=='already_active':checkpoint['active_methods']=[mid]
    with pytest.raises(ValueError):retry.verify_failed_scope(checkpoint,original)


def test_pre_model_metadata_failure_is_proved_and_native_trace_rejected(tmp_path):
    pre_model(tmp_path,'fixture')
    receipt=retry.verify_pre_model_failure(tmp_path)
    assert receipt['unhashable_metadata_sources']==['paper'] and receipt['native_calls_observed']==0
    write(tmp_path/'attempts/001_author/command.json',{'command':'not evidence of a pre-model failure'})
    with pytest.raises(ValueError,match='Native traces'):retry.verify_pre_model_failure(tmp_path)


@pytest.mark.asyncio
async def test_isolated_retry_preserves_sources_and_only_dispatches_three_fresh_parallel_methods(tmp_path,monkeypatch):
    monkeypatch.setenv('SLURM_JOB_ID','retry-test')
    release=tmp_path/'release';current=release/'run_02/extraction'
    checkpoint,original=states(tmp_path/'old-attempts')
    bundles=[]
    for mid in retry.TARGETS:
        attempt=Path(original['cards'][mid]['attempt_directory'])
        registry=pre_model(attempt,mid);card=retry.read(attempt/'card.json')
        bundles.append((card,registry,[]))
    write(current/'state.json',original)
    selected=[{'method_id':mid,'name':mid} for mid in [*retry.TARGETS,'epiradseq','maine-seq']]
    write(current.parent/'selected.json',{'methods':selected})
    write(release/'recovery_02/registered_additions.json',{})
    write(release/'recovery_02/primary_overrides.json',{})
    checkpoint_path=tmp_path/'checkpoint.json';write(checkpoint_path,checkpoint)
    original_bytes=(current/'state.json').read_bytes()
    calls=[]
    monkeypatch.setattr(retry.extractor,'prepare_independently',lambda *a,**k:(copy.deepcopy(bundles),{},[]))
    monkeypatch.setattr(retry,'verify_registry_files',lambda registry:None)
    async def extract(library,vault,selection_path,output,workers,**kwargs):
        calls.append((retry.read(selection_path),workers,kwargs))
        return {'fixture':'No actual native model calls'}
    monkeypatch.setattr(retry.extractor,'execute',extract)
    args=Namespace(current=current,checkpoint=checkpoint_path,library=tmp_path/'library',vault=tmp_path/'vault',output=tmp_path/'retry')
    await retry.execute(args)
    assert len(calls)==1
    selection,workers,options=calls[0]
    assert {m['method_id'] for m in selection['methods']}==set(retry.TARGETS)
    assert workers==3 and options['max_revisions']==2 and options['stop_on_first_issue'] is False
    assert options.get('resume_authors') is None
    assert (current/'state.json').read_bytes()==original_bytes
    receipt=retry.read(args.output/'PREFLIGHT.json')
    assert set(receipt['source_equivalence'])==set(retry.TARGETS)
    assert receipt['model_calls_before_preflight']==0
    with pytest.raises(FileExistsError):await retry.execute(args)

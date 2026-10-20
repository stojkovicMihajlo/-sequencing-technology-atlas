"""Exercise durable extraction through real graph admission and source queries."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest

from test_workflow_extraction_runtime import FakeWorker, source_case as source_case
from test_workflow_matching import example as example
from test_workflow_transformations import cases as cases
from genomics_discovery.curation.batch_graph import publish
from genomics_discovery.curation.extraction_batch import BatchConfig, plan, run
from genomics_discovery.curation.extraction_batch.storage import read, sha, write


async def complete(tmp_path, source_case, *, mid="test-assay"):
    tmp_path.mkdir(parents=True,exist_ok=True)
    _, packet, _ = source_case
    manifest = tmp_path / "inventory.json"
    manifest.write_text(json.dumps({"format_version": "extraction_inventory/1.0", "jobs": [
        {"id": mid, "status": "ready", "packet": str(packet), "title": "Test assay"},
        {"id": "pending", "status": "missing_sources", "reason": "Missing supplement",
         "next_evidence": "Retrieve source appendix"}]}))
    destination = tmp_path / "batch"
    plan(manifest, destination, config=BatchConfig(max_revisions=0))
    state = await run(destination, worker_factory=lambda: FakeWorker(source_case))
    assert state["status"] == "partial"
    atlas = tmp_path / "atlas.json"
    atlas.write_text(json.dumps({"methods": [{"id": mid, "name": "Test assay"}],
                                "claims": [], "revision_id": "test-revision"}))
    return destination, atlas, state


async def test_supported_workflow_enters_graph_without_invented_cross_method_edges(tmp_path, source_case):
    batch, atlas, state = await complete(tmp_path, source_case)
    graph = publish(batch, atlas, tmp_path / "publication")
    assert len(graph["workflows"]) == 1
    assert all(edge["layer"] != "history" for edge in graph["edges"])
    assert any(edge["kind"] == "material_input" for edge in graph["edges"])
    assert len([n for n in graph["nodes"] if n["kind"] == "technology"]) == 1
    receipt = json.loads((tmp_path / "publication/publication.json").read_text())
    assert receipt["accepted_methods"] == ["test-assay"]
    assert receipt["revision_id"] == graph["revision_id"]


async def test_publish_revalidates_actual_review_instead_of_trusting_status(tmp_path, source_case):
    batch, atlas, state = await complete(tmp_path, source_case)
    parent = state["accepted_parents"]["test-assay"]
    from pathlib import Path
    review = Path(parent["review"])
    review.write_text(review.read_text() + " ")
    with pytest.raises(ValueError):
        publish(batch, atlas, tmp_path / "bad-publication")
    assert not (tmp_path / "bad-publication/graph.json").exists()


def batch_snapshot(batch):
    # Acquiring the controller lock refreshes its ownership receipt only.
    return {str(p.relative_to(batch)): sha(p) for p in batch.rglob('*')
            if p.is_file() and p.name != '.controller.lock'}


def interrupted_case(source_case, tmp_path):
    cases = {mid: distinct_case(source_case, tmp_path / mid, mid)
             for mid in ('first', 'second', 'third')}
    manifest = tmp_path / 'inventory.json'
    write(manifest, {'jobs': [{'id': mid, 'status': 'ready', 'packet': str(case[1]),
                              'title': mid} for mid, case in cases.items()]})
    batch = tmp_path / 'batch'
    plan(manifest, batch, config=BatchConfig(concurrency=1, max_revisions=0,
                                           transport_retries=0))
    atlas = tmp_path / 'atlas.json'
    write(atlas, {'methods': [{'id': mid, 'name': mid} for mid in cases],
                  'claims': [], 'revision_id': 'interruption-test'})
    return cases, batch, atlas


async def cancel_at_third(batch, workers):
    reached = asyncio.Event()

    class RoutedWorker:
        async def run(self, prompt, attempt_dir):
            parts = Path(attempt_dir).parts
            mid = parts[parts.index('methods') + 1]
            if mid == 'third':
                reached.set()
                await asyncio.Event().wait()
            return await workers[mid].run(prompt, attempt_dir)

    task = asyncio.create_task(run(batch, worker_factory=RoutedWorker))
    try:
        await asyncio.wait_for(reached.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    return read(batch / 'state.json')


async def test_first_interruption_publishes_supported_rows_without_parent_cache(source_case, tmp_path):
    cases, batch, atlas = interrupted_case(source_case, tmp_path)
    workers = {mid: FakeWorker(case, review_verdicts=('unsupported',) if mid == 'second' else ())
               for mid, case in cases.items()}
    state = await cancel_at_third(batch, workers)
    assert state['status'] == 'interrupted'
    assert {mid: row['status'] for mid, row in state['jobs'].items()} == {
        'first': 'supported', 'second': 'needs_revision', 'third': 'interrupted'}
    assert state['accepted_parents'] == {}
    before = batch_snapshot(batch)
    output = tmp_path / 'publication'
    graph = publish(batch, atlas, output)
    assert len(graph['workflows']) == 1
    assert set(read(output / 'extractions.json')) == {'first'}
    assert read(output / 'publication.json')['new_batch_methods'] == ['first']
    assert batch_snapshot(batch) == before


async def test_resumed_interruption_does_not_omit_new_supported_row_from_stale_index(source_case, tmp_path):
    cases, batch, atlas = interrupted_case(source_case, tmp_path)

    class FirstPass:
        def __init__(self):
            self.workers = {mid: FakeWorker(case, fault='worker_exit' if mid != 'first' else None)
                            for mid, case in cases.items()}

        async def run(self, prompt, attempt_dir):
            parts = Path(attempt_dir).parts
            return await self.workers[parts[parts.index('methods') + 1]].run(prompt, attempt_dir)

    first = await run(batch, worker_factory=FirstPass)
    assert {mid: row['status'] for mid, row in first['jobs'].items()} == {
        'first': 'supported', 'second': 'failed', 'third': 'failed'}
    assert set(first['accepted_parents']) == {'first'}
    workers = {mid: FakeWorker(case) for mid, case in cases.items()}
    state = await cancel_at_third(batch, workers)
    assert state['jobs']['second']['status'] == 'supported'
    assert state['jobs']['third']['status'] == 'interrupted'
    assert set(state['accepted_parents']) == {'first'}
    before = batch_snapshot(batch)
    output = tmp_path / 'publication'
    graph = publish(batch, atlas, output)
    assert len(graph['workflows']) == 2
    assert set(read(output / 'extractions.json')) == {'first', 'second'}
    assert read(output / 'publication.json')['new_batch_methods'] == ['first', 'second']
    assert batch_snapshot(batch) == before


async def test_supported_row_without_verified_pair_rejects_publication(source_case, tmp_path):
    batch, atlas, _ = await complete(tmp_path, source_case)
    state = read(batch / 'state.json')
    state['jobs']['test-assay']['attempts'] = []
    write(batch / 'state.json', state)
    before = batch_snapshot(batch)
    output = tmp_path / 'rejected'
    with pytest.raises(ValueError, match='lacks a verified supported workflow/review pair'):
        publish(batch, atlas, output)
    assert not output.exists()
    assert batch_snapshot(batch) == before


def distinct_case(source_case,directory,method_id,*,updated=False):
    directory.mkdir(parents=True,exist_ok=True)
    packet,_,workflow=deepcopy(source_case)
    packet['method_id']=workflow['method_id']=method_id
    packet['title']=workflow['title']=f'Test {method_id}'
    if updated:
        workflow['operations'][1]['action']+='; retain the declared aliquot routes'
    packet_path=directory/'packet.json'
    packet_path.write_text(json.dumps(packet))
    return packet,packet_path,workflow


async def disjoint_batches(source_case,tmp_path):
    case_a=distinct_case(source_case,tmp_path/'case-a','assay-a')
    case_b=distinct_case(source_case,tmp_path/'case-b','assay-b')
    first,_,_=await complete(tmp_path/'first',case_a,mid='assay-a')
    second,_,_=await complete(tmp_path/'second',case_b,mid='assay-b')
    atlas=tmp_path/'canonical.json'
    atlas.write_text(json.dumps({'methods':[{'id':m,'name':m} for m in ['assay-a','assay-b']],
                                 'claims':[],'revision_id':'test-atlas'}))
    return first,second,atlas


async def test_previous_publication_grows_graph_across_disjoint_batches(source_case,tmp_path):
    from genomics_discovery.curation.run_state import sha
    first,second,atlas=await disjoint_batches(source_case,tmp_path)
    prior=tmp_path/'publication-one'
    graph_one=publish(first,atlas,prior)
    snapshots={p.name:sha(p) for p in prior.iterdir() if p.is_file()}
    current=tmp_path/'publication-two'
    graph_two=publish(second,atlas,current,previous=prior)
    assert len(graph_one['workflows'])==1
    assert len(graph_two['workflows'])==2
    assert set(graph_one['workflows'])<=graph_two['workflows'].keys()
    assert set(json.loads((current/'extractions.json').read_text()))=={'assay-a','assay-b'}
    assert json.loads((current/'indexes.json').read_text())==[]
    receipt=json.loads((current/'publication.json').read_text())
    assert receipt['accepted_methods']==['assay-a','assay-b']
    assert receipt['retained_methods']==['assay-a']
    assert receipt['new_batch_methods']==['assay-b']
    assert receipt['previous_publication']['publication_sha256']==snapshots['publication.json']
    assert all(sha(prior/name)==value for name,value in snapshots.items())
    assert not any(e['layer'] in {'history','cross_protocol'} for e in graph_two['edges'])
    graph_three=publish(second,atlas,tmp_path/'publication-three',previous=current)
    assert set(graph_two['workflows'])==set(graph_three['workflows'])


@pytest.mark.parametrize('changed',['extractions.json','bindings.json','indexes.json','input_pin','artifact_pin'])
async def test_previous_registry_and_hash_pins_cannot_be_tampered(source_case,tmp_path,changed):
    first,second,atlas=await disjoint_batches(source_case,tmp_path)
    prior=tmp_path/'prior';publish(first,atlas,prior)
    if changed in {'input_pin','artifact_pin'}:
        receipt=json.loads((prior/'publication.json').read_text())
        if changed=='input_pin':
            receipt['input_files'][str(atlas)]='0'*64
        else:
            receipt['files']['bindings.json']='0'*64
        (prior/'publication.json').write_text(json.dumps(receipt))
    else:
        (prior/changed).write_text('{}')
    output=tmp_path/'rejected'
    with pytest.raises(ValueError):
        publish(second,atlas,output,previous=prior)
    assert not output.exists()


async def test_prior_workflow_review_is_revalidated_not_just_registry(source_case,tmp_path):
    first,second,atlas=await disjoint_batches(source_case,tmp_path)
    prior=tmp_path/'prior';publish(first,atlas,prior)
    state=json.loads((first/'state.json').read_text())
    review=Path(state['accepted_parents']['assay-a']['review'])
    review.write_text(review.read_text()+' ')
    with pytest.raises(ValueError):
        publish(second,atlas,tmp_path/'rejected',previous=prior)


async def test_updated_method_requires_explicit_replacement_and_preserves_snapshot(source_case,tmp_path):
    from genomics_discovery.curation.run_state import sha
    original=distinct_case(source_case,tmp_path/'case-v1','method-one')
    updated=distinct_case(source_case,tmp_path/'case-v2','method-one',updated=True)
    first,atlas,_=await complete(tmp_path/'first',original,mid='method-one')
    second,_,_=await complete(tmp_path/'second',updated,mid='method-one')
    prior=tmp_path/'prior';old_graph=publish(first,atlas,prior)
    old_registry=(prior/'extractions.json').read_bytes()
    old_graph_hash=sha(prior/'graph.json')
    with pytest.raises(ValueError,match='another accepted version'):
        publish(second,atlas,tmp_path/'implicit-replacement',previous=prior)
    assert not (tmp_path/'implicit-replacement').exists()
    with pytest.raises(ValueError,match='both previous and current'):
        publish(second,atlas,tmp_path/'unknown-replacement',previous=prior,replace_methods={'unknown'})
    new_graph=publish(second,atlas,tmp_path/'new-version',previous=prior,replace_methods={'method-one'})
    assert len(new_graph['workflows'])==1
    assert set(old_graph['workflows']).isdisjoint(new_graph['workflows'])
    receipt=json.loads((tmp_path/'new-version/publication.json').read_text())
    assert receipt['replaced_methods']==['method-one']
    assert (prior/'extractions.json').read_bytes()==old_registry
    assert sha(prior/'graph.json')==old_graph_hash


async def test_same_completed_method_is_idempotent_and_output_is_immutable(source_case,tmp_path):
    batch,atlas,_=await complete(tmp_path/'batch-inputs',source_case)
    prior=tmp_path/'prior';old=publish(batch,atlas,prior)
    current=tmp_path/'next';new=publish(batch,atlas,current,previous=prior)
    assert set(old['workflows'])==set(new['workflows'])
    assert json.loads((current/'publication.json').read_text())['replaced_methods']==[]
    with pytest.raises(FileExistsError,match='immutable'):
        publish(batch,atlas,current,previous=prior)


def reviewed_index(example,directory):
    """Serialize the existing synthetic review fixture with fake transport evidence."""
    from genomics_discovery.curation.run_state import sha
    from genomics_discovery.curation.workflow_extraction.models import digest
    context,profiles,alignments,cases,review,parents,manifest=example
    directory.mkdir()
    files={'parents':parents,'manifest':manifest,'profiles':directory/'profiles.json',
           'alignments':directory/'alignments.json','cases':directory/'cases.json',
           'review':directory/'002_reviewer/artifact.json'}
    files['review'].parent.mkdir()
    files['profiles'].write_text(profiles.model_dump_json())
    files['alignments'].write_text(alignments.model_dump_json())
    files['cases'].write_text(json.dumps([c.model_dump(mode='json') for c in cases]))
    files['review'].write_text(review.model_dump_json())
    attempts=[]
    for number,role in enumerate(('compiler','reviewer'),1):
        native=directory/f'{number:03d}_{role}'; native.mkdir(exist_ok=True)
        thread=f'fake-matching-{role}'
        prompt=f'ROLE: source_bound_workflow_{role}\nSynthetic archive fixture.'
        (native/'prompt.txt').write_text(prompt)
        (native/'instruction.json').write_text(json.dumps({'prompt':prompt}))
        (native/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in [
            {'type':'thread.started','thread_id':thread},
            {'type':'turn.started'},{'type':'turn.completed','usage':{}}])+'\n')
        (native/'final.txt').write_text('Synthetic fixture, no native model launched.')
        (native/'result.json').write_text(json.dumps({'exit_code':0,'thread_id':thread,
                                                   'usage':{},'elapsed_seconds':.01,'error':None}))
        if role=='compiler':
            for name in ('profiles','alignments'):
                (native/f'{name}.json').write_bytes(files[name].read_bytes())
        attempts.append({'role':role,'path':str(native),'status':'transport_verified',
                         'thread_id':thread,'usage':{},'archive_sha256':{
                             name:sha(native/name) for name in ('prompt.txt','events.jsonl','result.json','final.txt')}})
    (directory/'state.json').write_text(json.dumps({
        'status':'completed','review_status':'supported_local_comparisons','review_sha256':digest(review),
        'profile_count':len(profiles.profiles),'alignment_count':len(alignments.alignments),
        'review_checks':len(review.checks),'outcomes':{a.case_id:a.disposition for a in alignments.alignments},
        'attempts':attempts}))
    (directory/'config.json').write_text(json.dumps({'files':{
        str(path):sha(path) for path in {*context.files,files['cases']}}}))
    index=directory/'index.json'
    index.write_text(json.dumps({'format_version':'workflow_matching_index/1.1',
        'files':{k:str(p) for k,p in files.items()},'profiles':profiles.model_dump(mode='json'),
        'profiles_sha256':digest(profiles),'alignments_sha256':digest(alignments),'review_sha256':digest(review)}))
    return index


async def test_reviewed_indexes_replay_across_batches_and_stay_pinned(source_case,example,tmp_path):
    batches=[]
    for mid in ('a','b','c'):
        case=distinct_case(source_case,tmp_path/f'case-{mid}',mid)
        batch,_,_=await complete(tmp_path/f'batch-{mid}',case,mid=mid)
        batches.append(batch)
    atlas=tmp_path/'canonical.json'
    atlas.write_text(json.dumps({'methods':[{'id':m,'name':m} for m in ('a','b','c')],
                                'claims':[],'revision_id':'matching-test-atlas'}))
    first=tmp_path/'first-publication';publish(batches[0],atlas,first)
    index=reviewed_index(example,tmp_path/'matching')
    second=tmp_path/'second-publication'
    compared=publish(batches[1],atlas,second,previous=first,indexes=[index])
    assert compared['comparisons']
    third=tmp_path/'third-publication'
    retained=publish(batches[2],atlas,third,previous=second)
    assert set(compared['comparisons'])==set(retained['comparisons'])
    assert json.loads((third/'indexes.json').read_text())==[str(index)]
    index.write_text(index.read_text()+' ')
    with pytest.raises(ValueError):
        publish(batches[2],atlas,tmp_path/'changed-index',previous=second)


async def test_failed_post_assembly_pin_check_does_not_expose_graph(source_case,tmp_path,monkeypatch):
    from genomics_discovery.curation import batch_graph
    first,second,atlas=await disjoint_batches(source_case,tmp_path)
    prior=tmp_path/'prior';publish(first,atlas,prior)
    original=batch_graph.build_graph

    def change_after_assembly(*args,**kwargs):
        graph=original(*args,**kwargs)
        atlas.write_text(atlas.read_text()+' ')
        return graph

    monkeypatch.setattr(batch_graph,'build_graph',change_after_assembly)
    output=tmp_path/'failed-publication'
    with pytest.raises(ValueError):
        publish(second,atlas,output,previous=prior)
    assert not (output/'graph.json').exists()
    assert not (output/'publication.json').exists()

"""Scientific-content gates and parallel scheduling; no model or image calls."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from experiments.diagram_generation10_20261112 import models,run
from genomics_discovery.illumina.extraction import write,sha,source_excerpt
from genomics_discovery.illumina.paper_reads import record_read


TEXT='RNA is copied into cDNA. Cell barcode and transcript insert are sequenced to assign expression to a cell.'
E={'source_id':'paper','quote':TEXT}


def plan(mid='fixture',name='Fixture assay'):
    return {'format_version':'assay_diagram_plan/1','method_id':mid,'method_name':name,
        'measurement_goal':'Measure transcript expression per cell','starting_specimen':'Separated cells',
        'measurement_evidence':[E], 'stages':[
            {'id':'copy','caption':'Copy RNA and record cell identity','inputs':['Cellular RNA'],
             'transformation':'Synthesize barcoded cDNA','outputs':['Cell-barcoded cDNA'],
             'identity_carriers':['Cell barcode linked to cDNA'],
             'illustration':'Show an RNA strand copied to DNA with a barcode block','evidence':[E]},
            {'id':'read','caption':'Sequence the library','inputs':['Cell-barcoded cDNA'],
             'transformation':'Acquire sequence reads','outputs':['Barcode and transcript reads'],
             'identity_carriers':['Cell barcode in sequencing reads'],
             'illustration':'Show barcode and transcript sections in read output','evidence':[E]}],
        'connections':[{'source':'copy','target':'read','carries':'Cell-barcoded cDNA','evidence':[E]}],
        'distinguishing_design':'Record the cell barcode with the cDNA','design_stage_ids':['copy'],'design_evidence':[E],
        'readouts':[{'id':'expression','stage_id':'read','detected_signal':'DNA sequencing reads',
            'sequenced_molecule':'Cell-barcoded cDNA','read_fields':['Cell barcode','Transcript insert'],
            'assignment':'Use the barcode for cell assignment and transcript sequence for gene assignment',
            'biological_quantity':'Transcript expression per cell','evidence':[E]}],
        'legend':['Green block: cell barcode'],'unknowns':[],
        'image_generation_prompt':'Generate one molecular assay strip: RNA to cell-barcoded cDNA, then sequencing with cell barcode and transcript insert shown. White background, Illumina reference style, no framed cards.'}


def review(candidate):
    targets=['measurement','design','image_prompt',*[s['id'] for s in candidate['stages']],
             *['readout:'+r['id'] for r in candidate['readouts']]]
    return {'format_version':'assay_diagram_review/2','candidate_digest':models.digest(candidate),
        'checks':[{'target':t,'decision':'accept',
        'explanation':'Synthetic fixture content matches its source passage','evidence':[E]} for t in targets],
        'summary':'Synthetic software fixture, not an evaluated assay.'}


def sources():return {'paper':{'kind':'text','content_level':'fulltext','document_role':'primary','text':TEXT}}


def test_plan_keeps_molecular_reporter_and_explicit_readout_assignment():
    candidate=models.validate_plan(plan(),'fixture',sources())
    assert candidate.readouts[0].sequenced_molecule=='Cell-barcoded cDNA'
    assert candidate.readouts[0].read_fields==['Cell barcode','Transcript insert']
    bad=plan();bad['readouts'][0]['assignment']=''
    with pytest.raises(ValueError):models.validate_plan(bad,'fixture',sources())
    bad=plan();bad['readouts'][0]['read_fields']=[]
    with pytest.raises(ValueError,match='informative read fields'):models.validate_plan(bad,'fixture',sources())


@pytest.mark.parametrize('change',['unknown_source','unsupported_quote','wrong_method','missing_stage'])
def test_reference_example_facts_and_invalid_graph_references_cannot_pass_target_evidence_gate(change):
    candidate=plan()
    if change=='unknown_source':candidate['stages'][0]['evidence']=[{**E,'source_id':'style:example'}]
    if change=='unsupported_quote':candidate['readouts'][0]['evidence']=[{**E,'quote':'Unsupported protein signal'}]
    if change=='wrong_method':candidate['method_id']='different-target'
    if change=='missing_stage':candidate['connections'][0]['target']='invented-stage'
    with pytest.raises(ValueError):models.validate_plan(candidate,'fixture',sources())


def test_independent_review_covers_every_readout_and_critical_plan_target():
    candidate=models.validate_plan(plan(),'fixture',sources())
    result=models.validate_review(review(plan()),candidate,sources())
    assert all(c.decision=='accept' for c in result.checks)
    missing=review(plan());missing['checks']=[c for c in missing['checks'] if c['target']!='readout:expression']
    with pytest.raises(ValueError,match='every Stage/readout'):models.validate_review(missing,candidate,sources())


def test_review_is_bound_to_complete_plan_even_if_target_ids_and_quotes_match():
    original=plan(); changed=copy.deepcopy(original)
    changed['readouts'][0]['assignment']='A different interpretation with identical target IDs'
    with pytest.raises(ValueError,match='candidate digest'):
        models.validate_review(review(original),models.DiagramPlan.model_validate(changed),sources())
    # A new matching receipt passes the mechanical gate; this does not establish
    # that this altered scientific interpretation is correct.
    models.validate_review(review(changed),models.DiagramPlan.model_validate(changed),sources())
    old=review(original);old['format_version']='assay_diagram_review/1';del old['candidate_digest']
    with pytest.raises(ValueError,match='new reviews require version2'):
        models.validate_review(old,models.DiagramPlan.model_validate(original),sources())
    assert models.validate_review(old,models.DiagramPlan.model_validate(original),sources(),
        allow_archived=True).format_version=='assay_diagram_review/1'


def test_source_tool_delivery_must_cover_primary_before_plan_is_eligible(tmp_path):
    registry=tmp_path/'sources.json';write(registry,sources())
    artifact=tmp_path/'artifact.json';write(artifact,plan());log=tmp_path/'reads.jsonl'
    method={'method_id':'fixture','name':'Fixture assay','primary_source_id':'paper'}
    with pytest.raises(ValueError,match='Full paper text not delivered'):
        run.validate_stage('author',artifact,registry,method,log)
    record_read(log,registry,sources(),source_excerpt(sources(),'paper'))
    assert run.validate_stage('author',artifact,registry,method,log).method_id=='fixture'


def test_staged_style_manifest_files_dictionary_is_actually_supported_and_hash_checked(tmp_path):
    examples=[]
    for i in range(10):
        folder=tmp_path/'examples'/f'{i:02d}';folder.mkdir(parents=True)
        files={}
        for name,content in [('diagram_on_white.png',b'PNG fixture'),('illumina_description.txt',b'Original Illumina prose'),('explanation.txt',b'Optional old explanation; not target evidence')]:
            path=folder/name;path.write_bytes(content);files[name]={'relative_path':str(path.relative_to(tmp_path)),'sha256':sha(path)}
        examples.append({'method_id':f'e{i}','name':f'Example {i}','files':files})
    manifest=tmp_path/'manifest.json';write(manifest,{'format_version':'diagram_style_examples/1','examples':examples})
    refs=run.style_references(manifest)
    assert len(refs)==10 and refs[0]['description']=='Original Illumina prose'
    assert refs[0]['path']==str((tmp_path/'examples/00/diagram_on_white.png').resolve())
    (tmp_path/'examples/00/illumina_description.txt').write_text('Changed prose')
    with pytest.raises(ValueError,match='Style example input changed'):run.style_references(manifest)


def method_input(tmp_path,mid):
    paper=tmp_path/(mid+'.txt');paper.write_text(TEXT)
    original=tmp_path/(mid+'.xml');original.write_text(f'<fixture-paper id="{mid}"/>')
    return {'method_id':mid,'name':mid,'publication_year':2020,'primary_source_id':'paper','missing_supplements':[],
        'sources':{'paper':{'kind':'text','content_level':'fulltext','document_role':'primary','path':str(paper),
            'sha256':sha(paper),'title':'Fixture paper','original':{'path':str(original),'sha256':sha(original)}}}}


@pytest.mark.asyncio
async def test_all_ten_sources_are_prepared_before_calls_and_ten_authors_start_in_parallel(tmp_path,monkeypatch):
    methods=[method_input(tmp_path,f'm{i}') for i in range(10)];manifest=tmp_path/'manifest.json'
    write(manifest,{'format_version':'assay_diagram_sources/1','methods':methods})
    style=tmp_path/'style.png';style.write_bytes(b'fixture PNG; no native image calls in this test')
    styles=[{'method_id':f'example-{i}','name':f'Example {i}','path':str(style),'sha256':sha(style),'description':'Style fixture only'} for i in range(10)]
    monkeypatch.setattr(run,'style_references',lambda path:styles)
    monkeypatch.setenv('SLURM_JOB_ID','content-fixture')
    barrier=asyncio.Event();seen={'active':0,'peak':0,'calls':0}
    class Worker:
        def __init__(self,**kwargs):assert kwargs['model']=='gpt-6.1-sol' and kwargs['effort']=='xhigh'
        async def run(self,prompt,attempt,image_paths):
            seen['calls']+=1
            assert len(image_paths)==10 and all(Path(p).parent==attempt for p in image_paths)
            method=run.read(attempt.parents[1]/'method.json');source=attempt.parents[1]/'source_registry.json'
            (attempt/'prompt.txt').write_text(prompt)
            if attempt.name.endswith('author'):
                seen['active']+=1;seen['peak']=max(seen['peak'],seen['active'])
                if seen['active']==10:barrier.set()
                await barrier.wait();seen['active']-=1
                document=plan(method['method_id'],method['name'])
            else:document=review(run.read(attempt/'candidate_plan.json'))
            write(attempt/'artifact.json',document)
            registry=run.read(source);record_read(attempt/'source_reads.jsonl',source,registry,source_excerpt(registry,'paper'))
            return SimpleNamespace(exit_code=0,error=None,thread_id=str(attempt),usage={},elapsed_seconds=0)
    monkeypatch.setattr(run,'CodexWorker',Worker)
    monkeypatch.setattr(run,'assess_archived_attempt',lambda a:SimpleNamespace(complete=True,thread_id=str(a),usage={}))
    args=SimpleNamespace(manifest=manifest,examples=manifest,output=tmp_path/'out',agents=10)
    result=await asyncio.wait_for(run.execute(args),timeout=10)
    assert seen['peak']==10 and seen['calls']==20
    assert result['counts']=={'ready_for_image_generation':10}
    requests=run.read(args.output/'render_requests.json')
    assert requests['ready_requests']==10
    assert all(len(r['arguments']['referenced_image_paths'])==10 for r in requests['requests'])
    assert all('Cell-barcoded cDNA' in r['arguments']['prompt'] for r in requests['requests'])
    # Approved plans are reopened without additional model calls.
    previous=args.output;args.previous=previous;args.output=tmp_path/'approved_reuse'
    reused=await run.execute(args)
    assert seen['calls']==20 and reused['counts']=={'ready_for_image_generation':10}
    assert len(run.read(args.output/'CONTINUATION.json')['reused_approved'])==10
    # Two source-fixable reviews start exactly two authors and two fresh reviewers.
    state=run.read(previous/'state.json')
    for mid in ['m0','m1']:
        row=state['methods'][mid];row['status']='needs_revision'
        stage=row['stages'][-1];artifact=Path(stage['path'])/'artifact.json'
        document=run.read(artifact);document['checks'][0]['decision']='revise'
        write(artifact,document);stage['artifact_sha256']=sha(artifact)
    write(previous/'state.json',state)
    args.output=tmp_path/'two_corrections';args.agents=2
    corrected=await run.execute(args)
    assert seen['calls']==24 and corrected['counts']=={'ready_for_image_generation':10}
    continuation=run.read(args.output/'CONTINUATION.json')
    assert continuation['continued_revision']==['m0','m1']
    assert len(continuation['reused_approved'])==8
    # A bad tenth source blocks every method before any author/renderer starts.
    methods[-1]['sources']['paper']['sha256']='0'*64;write(manifest,{'format_version':'assay_diagram_sources/1','methods':methods})
    args.output=tmp_path/'blocked'
    with pytest.raises(ValueError,match='Readable source bytes changed'):await run.execute(args)
    assert seen['calls']==24
    assert run.read(args.output/'PREFLIGHT.json')['model_calls']==0

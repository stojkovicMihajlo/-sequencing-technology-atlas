"""BLESS-like exact-linker scope dispute; required-operation controls remain separate."""
import copy
from pathlib import Path

import pytest

from genomics_discovery.curation.batch_graph import publish
from genomics_discovery.curation.extraction_batch import BatchConfig, plan, run
from genomics_discovery.curation.extraction_batch.storage import read, sha, write
from genomics_discovery.curation.source_worklist import build_worklist
from genomics_discovery.curation.workflow_extraction.models import Workflow, WorkflowReview, digest
from genomics_discovery.curation.workflow_extraction.obligations import (
    SourceObligations, SourceCoverageReview, source_coverage_outcome,
    validate_source_obligations, validate_source_coverage_review,
)
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute
from genomics_discovery.curation.workflow_extraction.sources import build_packet
from test_workflow_extraction_runtime import make_review
from test_workflow_source_obligations import SourceWorker


# Short primary-paper excerpts from PMID 23503052. Literal linker sequences are
# unavailable; their distinct roles, chemistry and barcode-recognition interface
# are directly described. This tests the disputed general-description veto.
TEXT = (
    'After purification of intact nuclei, DSBs are blunted, 5′-phosphorylated, and '
    'finally ligated to a biotinylated linker (proximal) using the highly specific '
    'T4 ligase enzyme, which can only ligate double-stranded but not single-stranded breaks. '
    'Genomic DNA (gDNA) is extracted and fragmented, and labeled fragments are captured '
    'by streptavidin. A second linker (distal) also containing the XhoI site is attached '
    'to the free extremity of captured genomic fragments, enabling PCR amplification '
    'and sequencing. Linkers and primers used are listed in Supplementary Table 5. '
    'We retained all reads with length ≥ 34 nt as high-quality filtered reads, and '
    'scanned them for the presence of the exact proximal or distal barcode.'
)


@pytest.fixture
def bless_case(tmp_path):
    original = tmp_path / 'bless_excerpt.txt'; original.write_text(TEXT)
    packet = build_packet(method_id='bless', title='BLESS',
        scope='General descriptive nuclei-to-barcode readout; not a synthesis-ready oligo recipe.',
        variant='2013_BLESS', sources=[{'source_id':'pmid_23503052','title':'BLESS primary excerpts',
            'url':'https://doi.org/10.1038/nmeth.2408','role':'primary_paper','path':str(original)}])
    path = tmp_path / 'packet.json'; write(path, packet)
    passage = packet['sources'][0]['passages'][0]
    ref = {'source_id':'pmid_23503052','passage_id':passage['passage_id']}

    def ev(quote):
        return {**ref,'quote':quote,'support':'reported','rationale':'Primary text supplies the named molecular/data boundary.'}

    mark = ev('DSBs are blunted, 5′-phosphorylated, and finally ligated to a biotinylated linker (proximal)')
    production = ev('Genomic DNA (gDNA) is extracted and fragmented, and labeled fragments are captured by streptavidin.')
    distal = ev('A second linker (distal) also containing the XhoI site is attached to the free extremity of captured genomic fragments, enabling PCR amplification and sequencing.')
    recognition = ev('scanned them for the presence of the exact proximal or distal barcode.')
    table = ev('Linkers and primers used are listed in Supplementary Table 5.')
    definitions = [('mark','Label DSBs in situ',['nuclei'],['marked'],mark),
        ('produce','Extract, fragment, capture, attach distal linker, amplify and sequence',['marked'],['reads'],distal),
        ('recognize','Recognize exact proximal/distal barcode identities',['reads'],['qualified'],recognition)]
    workflow = {**{k:packet[k] for k in ('method_id','title','scope','variant')},
        'states':[{'id':sid,'kind':kind,'description':desc,'evidence':[evidence]}
            for sid,kind,desc,evidence in [('nuclei','material','Purified nuclei',mark),
                ('marked','material','Proximal-biotin-labeled DNA ends',mark),
                ('reads','data','Reads retaining proximal/distal identity',distal),
                ('qualified','data','Reads classified using exact proximal/distal barcode matches',recognition)]],
        'operations':[{'id':oid,'name':action,'action':action,'inputs':ins,'outputs':outs,
            'resources':[],'information_effect':'Preserve the named linker roles and retained identity.',
            'evidence':[evidence,production] if oid=='produce' else [evidence]}
            for oid,action,ins,outs,evidence in definitions],
        'flows':[{'id':f'{oid}-{direction}','source':ins[0] if direction=='in' else oid,
            'target':oid if direction=='in' else outs[0],
            'kind':('material_' if (oid=='mark' or oid=='produce' and direction=='in') else 'data_')+
                   ('input' if direction=='in' else 'output'),
            'basis':'Primary text connects these retained identities.','evidence':[evidence]}
            for oid,_,ins,outs,evidence in definitions for direction in ('in','out')],
        'modules':[{'id':'assay','name':'General assay boundaries','operation_ids':['mark','produce','recognize'],
                    'boundary_rationale':'Broad transformations, not an executable reconstruction.'}],
        'unresolved':[{'id':'linker_sequences','description':'Literal proximal/distal barcode sequences and platform primers remain unknown; named roles and interfaces are supported.',
            'blocking':False,'next_evidence':'Acquire Supplementary Table 5 for the exact sequence/modification inventory.'}]}
    obligations = {**{k:packet[k] for k in ('method_id','scope','variant')},'source_packet_sha256':digest(packet),
        'source_scope':[{'registered_source_id':ref['source_id'],'disposition':'inspected',
            'inspected_passages':[ref],'rationale':'Read the scoped primary excerpts.','blocks_completeness':False}],
        'obligations':[{'id':oid,'requirement':action,'applicability':'required',
            'rationale':'Consequential general interface.','evidence':[evidence]}
            for oid,action,_,_,evidence in definitions],
        'source_limits':[{'document_or_fact':'Supplementary Table 5 exact linkers and primers.',
            'cause':'document_missing','consequence':'A complete sequence-defined implementation cannot be certified.',
            'blocking':True,'next_evidence':'Acquire Supplementary Table 5.','evidence':[table]}]}
    correction = {'source_pointer':'/source_limits/0','disposition':'retain_nonblocking_detail',
        'scope_rationale':'Frozen scope is a general description. Primary text supports distinct proximal/distal roles, capture and exact-barcode recognition; exact oligos remain unknown, not synthesis-ready.',
        'evidence':[mark,distal,recognition],'retained_gap_ids':['linker_sequences'],
        'targets':['operation:mark','operation:produce','operation:recognize']}
    return packet,path,workflow,obligations,correction


def coverage_for_case(case, workflow=None, review=None):
    packet,_,raw,obligations,_ = case
    workflow = workflow or Workflow.model_validate(raw)
    review = review or WorkflowReview.model_validate(make_review(workflow.model_dump(mode='json'),packet))
    return {'source_packet_sha256':digest(packet),'obligations_sha256':digest(SourceObligations.model_validate(obligations)),
        'workflow_sha256':digest(workflow),'workflow_review_sha256':digest(review),
        'inspected_passages':obligations['source_scope'][0]['inspected_passages'],
        'checks':[{'obligation_id':item['id'],'disposition':'satisfied','selected':True,
            'targets':['operation:'+item['id']],'rationale':'Actual operation preserves the reported interface.',
            'evidence':item['evidence']} for item in obligations['obligations']]}


def bound(case, coverage=None):
    packet,_,raw,obligations,_ = case
    workflow = Workflow.model_validate(raw); review = WorkflowReview.model_validate(make_review(raw,packet))
    return packet,SourceObligations.model_validate(obligations),workflow,review,SourceCoverageReview.model_validate(coverage or coverage_for_case(case))


def test_bless_exact_oligo_veto_is_fail_closed_without_optional_adjudication(bless_case):
    packet,ob,workflow,review,coverage = bound(bless_case)
    assert validate_source_obligations(ob,packet) == []
    assert validate_source_coverage_review(coverage,ob,review,workflow,packet) == []
    assert source_coverage_outcome(coverage,ob,review,workflow) == 'research_needed'
    coverage = coverage.model_copy(update={'source_limit_adjudications':()})
    assert source_coverage_outcome(coverage,ob,review,workflow) == 'research_needed'


def test_bless_scope_correction_retains_unknowns_and_original_obligations(bless_case):
    raw = coverage_for_case(bless_case); raw['source_limit_adjudications'] = [bless_case[4]]
    packet,ob,workflow,review,coverage = bound(bless_case,raw)
    original = digest(ob)
    assert validate_source_coverage_review(coverage,ob,review,workflow,packet) == []
    assert source_coverage_outcome(coverage,ob,review,workflow) == 'supported'
    assert digest(ob) == original and ob.source_limits[0].blocking
    assert workflow.unresolved[0].id == 'linker_sequences' and not workflow.unresolved[0].blocking


@pytest.mark.parametrize('mutation',['pointer','obligation_pointer','duplicate','quote','uninspected','gap','no_gap','blocking_gap','target','resolved_no_target','obligations_sha256','workflow_sha256','required_na'])
def test_corrections_cannot_lose_evidence_bindings_unknowns_or_required_operations(bless_case,mutation):
    case = copy.deepcopy(bless_case); raw = coverage_for_case(case)
    raw['source_limit_adjudications'] = [case[4]]; correction = raw['source_limit_adjudications'][0]
    if mutation=='pointer':correction['source_pointer']='/source_limits/99'
    elif mutation=='obligation_pointer':correction['source_pointer']='/obligations/1'
    elif mutation=='duplicate':raw['source_limit_adjudications'].append(copy.deepcopy(correction))
    elif mutation=='quote':correction['evidence'][0]['quote']='A fabricated statement.'
    elif mutation=='uninspected':raw['inspected_passages']=[{'source_id':'pmid_23503052','passage_id':'missing'}]
    elif mutation=='gap':correction['retained_gap_ids']=['unrelated_missing_id']
    elif mutation=='no_gap':correction['retained_gap_ids']=[]
    elif mutation=='blocking_gap':case[2]['unresolved'][0]['blocking']=True
    elif mutation=='target':correction['targets']=['operation:missing']
    elif mutation=='resolved_no_target':correction.update(disposition='resolved_by_registered_evidence',targets=[])
    elif mutation=='required_na':
        correction['disposition']='outside_selected_scope'
        raw['checks'][0].update(disposition='not_applicable',selected=False,targets=[])
    else:raw[mutation]='f'*64
    packet,ob,workflow,review,coverage = bound(case,raw)
    errors=validate_source_coverage_review(coverage,ob,review,workflow,packet)
    assert errors
    if mutation=='blocking_gap':assert any('retained gap must exist and remain nonblocking' in e for e in errors)
    if mutation=='no_gap':assert any('unknown detail needs retained workflow gaps' in e for e in errors)
    if mutation=='required_na':assert any('unconditional required item cannot be excluded' in e for e in errors)
    if mutation=='resolved_no_target':assert any('resolved fact needs actual workflow targets' in e for e in errors)


def test_omission_mutation_still_rejected_despite_scope_correction(bless_case):
    packet,_,raw,obligations,correction = copy.deepcopy(bless_case)
    raw['operations']=[o for o in raw['operations'] if o['id']!='mark']
    raw['flows']=[f for f in raw['flows'] if 'mark' not in (f['source'],f['target'])]
    raw['modules'][0]['operation_ids'].remove('mark')
    workflow=Workflow.model_validate(raw); review=WorkflowReview.model_validate(make_review(raw,packet))
    ob=SourceObligations.model_validate(obligations)
    case=(packet,None,raw,obligations,correction); coverage=coverage_for_case(case,workflow,review)
    coverage['checks'][0].update(disposition='missing',targets=[])
    correction['targets']=['operation:produce','operation:recognize']
    coverage['source_limit_adjudications']=[correction]; coverage=SourceCoverageReview.model_validate(coverage)
    assert validate_source_coverage_review(coverage,ob,review,workflow,packet)==[]
    assert source_coverage_outcome(coverage,ob,review,workflow)=='needs_revision'


def test_one_correction_cannot_remove_other_source_vetoes(bless_case):
    case=copy.deepcopy(bless_case)
    case[3]['source_limits'].append(copy.deepcopy(case[3]['source_limits'][0]))
    coverage=coverage_for_case(case); coverage['source_limit_adjudications']=[case[4]]
    packet,ob,workflow,review,coverage=bound(case,coverage)
    assert validate_source_coverage_review(coverage,ob,review,workflow,packet)==[]
    assert source_coverage_outcome(coverage,ob,review,workflow)=='research_needed'


def test_resolved_fact_elsewhere_keeps_literal_sequences_unknown(bless_case):
    case=copy.deepcopy(bless_case)
    case[3]['source_limits'][0].update(document_or_fact='The proximal linker affinity label is unknown.',
        cause='not_reported_in_inspected_sources',consequence='Affinity identity was not recovered in the first source pass.')
    case[4].update(disposition='resolved_by_registered_evidence',retained_gap_ids=[],targets=['operation:mark'],
        scope_rationale='Primary text explicitly identifies a biotinylated proximal linker. That fact is resolved; the separate literal-sequence gap remains unknown.')
    raw=coverage_for_case(case);raw['source_limit_adjudications']=[case[4]]
    packet,ob,workflow,review,coverage=bound(case,raw)
    assert validate_source_coverage_review(coverage,ob,review,workflow,packet)==[]
    assert source_coverage_outcome(coverage,ob,review,workflow)=='supported'
    assert workflow.unresolved[0].id=='linker_sequences'


def test_source_scope_access_correction_is_exact_and_preserves_unknowns(bless_case):
    case=copy.deepcopy(bless_case)
    case[3]['source_scope'][0].update(disposition='access_limited',blocks_completeness=True,
        rationale='Exact graphical linker layout is unreadable, but the named roles are readable.',
        next_evidence='Inspect original linker diagram and Table 5.')
    raw=coverage_for_case(case)
    raw['source_limit_adjudications']=[case[4],{**case[4],'source_pointer':'/source_scope/0'}]
    packet,ob,workflow,review,coverage=bound(case,raw)
    assert validate_source_coverage_review(coverage,ob,review,workflow,packet)==[]
    assert source_coverage_outcome(coverage,ob,review,workflow)=='supported'


@pytest.mark.parametrize('changed',['method_id','title','scope','variant'])
def test_outside_scope_label_cannot_rebind_the_workflow_to_another_scope(bless_case,changed):
    case=copy.deepcopy(bless_case);case[2][changed]+=' changed'
    case[4].update(disposition='outside_selected_scope',retained_gap_ids=[],
        scope_rationale='The reviewer claims a different scope; this cannot alter the frozen contract.')
    raw=coverage_for_case(case);raw['source_limit_adjudications']=[case[4]]
    packet,ob,workflow,review,coverage=bound(case,raw)
    # All candidate/review hashes have been rebound, so scope identity itself
    # must reject this attempted exclusion rather than a merely stale digest.
    errors=validate_source_coverage_review(coverage,ob,review,workflow,packet)
    assert any(f'cannot change frozen workflow {changed}' in error for error in errors)


@pytest.mark.parametrize('primary_block',['unsupported_claim','critical_workflow_gap'])
def test_source_correction_never_overrides_primary_rejection(bless_case,primary_block):
    packet,_,raw,obligations,correction=copy.deepcopy(bless_case)
    if primary_block=='critical_workflow_gap':
        raw['unresolved'].append({'id':'core_missing','description':'A required capture interface remains unknown.',
            'blocking':True,'next_evidence':'Acquire core capture interface evidence.'})
    workflow=Workflow.model_validate(raw);review_raw=make_review(raw,packet)
    if primary_block=='unsupported_claim':review_raw['checks'][0]['verdict']='unsupported'
    review=WorkflowReview.model_validate(review_raw);ob=SourceObligations.model_validate(obligations)
    coverage=coverage_for_case((packet,None,raw,obligations,correction),workflow,review)
    coverage['source_limit_adjudications']=[correction];coverage=SourceCoverageReview.model_validate(coverage)
    assert validate_source_coverage_review(coverage,ob,review,workflow,packet)==[]
    assert source_coverage_outcome(coverage,ob,review,workflow) in {'needs_revision','research_needed'}


def test_legacy_defaults_and_canonical_digest_are_exactly_preserved():
    raw={'source_packet_sha256':'0'*64,'obligations_sha256':'1'*64,'workflow_sha256':'2'*64,'workflow_review_sha256':'3'*64,
        'inspected_passages':[{'source_id':'primary','passage_id':'p1'}],
        'checks':[{'obligation_id':'required','disposition':'satisfied','selected':True,'targets':['operation:label'],
            'rationale':'The reported operation is retained.','evidence':[{'source_id':'primary','passage_id':'p1',
            'quote':'The source reports the operation.','support':'reported','rationale':'Directly reports this operation.'}]}]}
    # Obtained from the unchanged active SourceCoverageReview implementation.
    expected='5a665e32622a610889e8f165799b98d10f335810ca14163940a8a2ec5fa67310'
    for document in (raw,{**raw,'source_limit_adjudications':None}):
        coverage=SourceCoverageReview.model_validate(document)
        assert digest(coverage)==expected
        assert 'source_limit_adjudications' not in coverage.model_dump(mode='json')


class AdjudicatingWorker(SourceWorker):
    def __init__(self,case,*,adjudicate):
        super().__init__(case[:3]); self.case=case; self.adjudicate=adjudicate

    async def run(self,prompt,directory):
        result=await super().run(prompt,directory); directory=Path(directory)
        if 'ROLE: source_obligations' in prompt:write(directory/'artifact.json',self.case[3])
        if 'ROLE: reviewer' in prompt:
            workflow=Workflow.model_validate_json((directory/'candidate.json').read_bytes())
            review=WorkflowReview.model_validate_json((directory/'artifact.json').read_bytes())
            coverage=coverage_for_case(self.case,workflow,review)
            if self.adjudicate:coverage['source_limit_adjudications']=[self.case[4]]
            write(directory/'source_coverage_review.json',coverage)
        return result


async def test_existing_native_reviewer_adjudicates_and_graph_gate_revalidates(bless_case,tmp_path):
    manifest=tmp_path/'inventory.json'; write(manifest,{'jobs':[{'id':'bless','status':'ready','packet':str(bless_case[1])}]})
    batch=tmp_path/'batch'; plan(manifest,batch,config=BatchConfig(require_source_coverage=True,max_revisions=0))
    worker=AdjudicatingWorker(bless_case,adjudicate=True)
    state=await run(batch,worker_factory=lambda:worker)
    assert state['jobs']['bless']['status']=='supported',state['jobs']['bless'].get('error')
    assert [c['role'] for c in worker.calls]==['source_obligations','integrator','reviewer']
    result=state['jobs']['bless']['result']; source=Path(result['accepted_source_coverage']['source_obligations']['path'])
    frozen_sha=sha(source); assert read(source)['source_limits'][0]['blocking'] is True
    review_prompt=worker.calls[-1]['prompt']
    assert 'source_limit_adjudications' in review_prompt and 'cannot waive required' in review_prompt
    atlas=tmp_path/'atlas.json'; write(atlas,{'methods':[{'id':'bless','name':'BLESS'}],'claims':[],'revision_id':'adjudication-test'})
    graph=publish(batch,atlas,tmp_path/'publication')
    assert len(graph['workflows'])==1 and sha(source)==frozen_sha


async def test_absent_adjudication_remains_negative_in_full_runtime(bless_case,tmp_path):
    state=await execute(bless_case[1],tmp_path/'runtime',worker=AdjudicatingWorker(bless_case,adjudicate=False),
        mode='baseline',require_source_coverage=True,max_revisions=0)
    assert state['status']=='research_needed'
    assert not (tmp_path/'runtime/graph_candidate.json').exists()


@pytest.mark.parametrize('other_blocker',[False,True])
async def test_worklist_retains_original_veto_and_exact_recovery_after_validated_correction(bless_case,tmp_path,other_blocker):
    case=copy.deepcopy(bless_case)
    if other_blocker:
        case[3]['source_limits'].append({**copy.deepcopy(case[3]['source_limits'][0]),
            'document_or_fact':'A separate required core interface is unavailable.'})
    manifest=tmp_path/'inventory.json';write(manifest,{'jobs':[{'id':'bless','status':'ready','packet':str(case[1])}]})
    batch=tmp_path/'batch';plan(manifest,batch,config=BatchConfig(require_source_coverage=True,max_revisions=0))
    state=await run(batch,worker_factory=lambda:AdjudicatingWorker(case,adjudicate=True))
    assert state['jobs']['bless']['status']==('research_needed' if other_blocker else 'supported')
    runtime=Path(state['jobs']['bless']['result']['runtime_run'])
    before={path:path.read_bytes() for path in (batch/'state.json',runtime/'state.json')}
    method=build_worklist(batch=batch)['methods'][0]
    assert all(path.read_bytes()==raw for path,raw in before.items())
    task=next(t for t in method['tasks'] if t['origin']['json_pointer']=='/source_limits/0')
    assert task['kind']=='acquire_document' and task['blocking'] is False
    assert task['next_evidence']=='Acquire Supplementary Table 5.'
    assert task['evidence']==case[3]['source_limits'][0]['evidence']
    assert task['details']['original_blocking'] is True
    assert task['details']['original_source_pointer']=='/source_limits/0'
    assert task['details']['effective_blocking'] is False
    correction=task['details']['validated_adjudication']
    assert correction['source_pointer']=='/source_limits/0'
    assert correction['json_pointer']=='/source_limit_adjudications/0'
    assert correction['disposition']=='retain_nonblocking_detail'
    runtime_state=read(runtime/'state.json')
    coverage_path=Path(runtime_state['artifacts']['source_coverage_review']['path'])
    coverage=SourceCoverageReview.model_validate(read(coverage_path))
    assert correction['path']==str(coverage_path) and correction['sha256']==sha(coverage_path)
    assert correction['document_sha256']==digest(coverage)
    assert correction['workflow_sha256']==coverage.workflow_sha256
    assert correction['workflow_review_sha256']==coverage.workflow_review_sha256
    assert any(t['origin']['original_id']=='linker_sequences' and t['blocking'] is False for t in method['tasks'])
    if other_blocker:
        other=next(t for t in method['tasks'] if t['origin']['json_pointer']=='/source_limits/1')
        assert other['blocking'] is True and 'validated_adjudication' not in other['details']


@pytest.mark.parametrize('mutation',['quote','workflow_binding','review_binding','in_progress_stale'])
async def test_worklist_refuses_invalid_or_stale_adjudication_even_with_rebound_file_hash(bless_case,tmp_path,mutation):
    manifest=tmp_path/'inventory.json';write(manifest,{'jobs':[{'id':'bless','status':'ready','packet':str(bless_case[1])}]})
    batch=tmp_path/'batch';plan(manifest,batch,config=BatchConfig(require_source_coverage=True,max_revisions=0))
    state=await run(batch,worker_factory=lambda:AdjudicatingWorker(bless_case,adjudicate=True))
    runtime=Path(state['jobs']['bless']['result']['runtime_run'])
    runtime_state=read(runtime/'state.json')
    record=runtime_state['artifacts']['source_coverage_review'];path=Path(record['path'])
    coverage=read(path)
    if mutation=='quote':coverage['source_limit_adjudications'][0]['evidence'][0]['quote']='Fabricated recovered fact.'
    elif mutation=='review_binding':coverage['workflow_review_sha256']='f'*64
    else:coverage['workflow_sha256']='f'*64
    if mutation=='in_progress_stale':runtime_state['status']='reviewing'
    write(path,coverage)
    record.update(sha256=sha(path),document_sha256=digest(SourceCoverageReview.model_validate(coverage)))
    write(runtime/'state.json',runtime_state)
    method=build_worklist(batch=batch)['methods'][0]
    assert len(method['tasks'])==1 and not method['retained_unknowns']
    task=method['tasks'][0]
    assert task['owner_target']['role']=='controller'
    assert task['details']['cause']=='input_or_artifact_binding_error'
    assert 'validated_adjudication' not in task['details']

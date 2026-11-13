"""Continue pinned registered workflows through the existing extraction/review gates."""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

from genomics_discovery.execution.codex_worker import CodexWorker, classify_archived_terminal_failure
from genomics_discovery.illumina.extraction import (
    extract_registered_workflow, read, write, sha, revalidate_completed, verify_registry_files,
    stage_images, verify_stage_inputs, revision_handoff)
from genomics_discovery.illumina.models import (
    parse_extraction, parse_review, review_model, validate_review, review_outcome,
    review_targets, digest, export_graph)
from genomics_discovery.illumina.prompts import build_prompt
from genomics_discovery.protocols.extraction.field_review import _resolve


def pinned(descriptor):
    path=Path(descriptor['path'])
    if sha(path)!=descriptor['sha256']:raise ValueError('Pinned input changed: '+str(path))
    return read(path)


def check_workers(workers):
    if type(workers)!=int or not 1<=workers<=50:raise ValueError('Use 1-50 explicit native workers')


def text_identity(source):
    return hashlib.sha256(' '.join(source.get('text','').split()).encode()).hexdigest()


def reconcile(config):
    """Explicit state precedence; accepted records must reopen their original gates."""
    rows={};history={}
    for state_ref in config['states']:
        state=pinned(state_ref)
        runtime=state_ref['runtime_manifest'];pinned(runtime)
        for mid,row in state['cards'].items():
            history.setdefault(mid,[]).append({'state':{k:state_ref[k] for k in ('path','sha256')},
                'runtime_manifest':runtime,'status':row['status'],'attempt_directory':row.get('attempt_directory')})
            rows[mid]=copy.deepcopy(row)
    if len(rows)!=config['expected_methods']:raise ValueError('Method coverage changed')
    for mid,descriptor in config['provider_attempts'].items():
        attempt=Path(descriptor['path'])
        if sha(attempt/'events.jsonl')!=descriptor['events_sha256']:raise ValueError('Provider evidence changed')
        diagnostic=classify_archived_terminal_failure(attempt)
        if diagnostic['category']!='provider_biological_risk_block':
            raise ValueError('Not the recorded provider policy hold: '+mid)
        rows[mid].update(status='provider_policy_block',provider_diagnostic=diagnostic,
                         provider_attempt=str(attempt))
    for mid,row in rows.items():
        if row['status'] in {'supported','research_needed','needs_revision'}:revalidate_completed(row)
        row['provenance']=history[mid]
    counts=dict(Counter(r['status'] for r in rows.values()))
    if counts!=config['expected_counts']:raise ValueError('Reconciled counts differ: '+str(counts))
    return {'cards':rows,'counts':counts,'scope':'Current explicit method ledger; original attempts unchanged.'}


def prepare_recovery(config,ledger,output):
    """Deliver exact latest requests and available registries to the existing finder."""
    old=pinned(config['held_snapshot']);old_cases={c['selection']['method_id']:c for c in old['cases']}
    selected={m['method_id']:m for m in pinned(config['selection'])['methods']}
    existing=pinned(config['original_additions'])
    source_root=output/'source_inputs';source_root.mkdir(parents=True)
    additions={};cases=[]
    for mid,row in ledger['cards'].items():
        if row['status'] in {'supported','provider_policy_block'}:continue
        method=selected[mid];case=copy.deepcopy(old_cases.get(mid,{}))
        if not case:
            package=read(Path(config['library'])/method['canonical_directory']/'source_package.json')
            papers=[p for p in package['method_papers'] if p['paper_id']==method['primary_paper_id']]
            if len(papers)!=1:raise ValueError('Assigned publication is not unique: '+mid)
            case={'selection':method,'paper':papers[0],'audit':{}}
        registry=copy.deepcopy(existing.get(mid,{}))
        if row.get('attempt_directory'):
            directory=Path(row['attempt_directory']);registry.update(read(directory/'source_registry.json'))
        verify_registry_files(registry);additions[mid]=registry
        requests=row.get('research_questions') or case.get('audit',{}).get('research_requests',[])
        if not requests:requests=[{'target':'record','reason':row.get('reason','Assigned primary evidence unavailable')}]
        case['audit']={'research_requests':requests,'current_attempt':row.get('attempt_directory'),
            'current_provenance':row['provenance']}
        category=('evidence_recovery_failed' if row.get('failure_category')=='evidence_recovery_failed'
                  else 'evidence_request_unresolved')
        case['hold']={'status':'source_recovery_needed','failure_category':category,
            'reason':row.get('reason',row['status']),'evidence':row['provenance'][-1]}
        receipt=source_root/'recovery_02/methods'/mid/'RECOVERY.json'
        write(receipt,{'prior_hold':old_cases.get(mid,{}).get('hold'),
            'current_questions':requests,'meaning':'Pointers to prior acquisition and current scientific questions; not new evidence.'})
        cases.append(case)
    write(source_root/'recovery_02/registered_additions.json',additions)
    snapshot={'source_release':str(source_root),'cases':cases,
        'observed_source_leads':old.get('observed_source_leads',[])}
    write(output/'recovery_snapshot.json',snapshot)
    return snapshot


async def run_prepared(manifest,output,*,workers=25,max_revisions=2,worker=None):
    """Generic pinned-card/registry dispatch; no new scientific prompts or schema."""
    if not os.environ.get('SLURM_JOB_ID'):raise ValueError('Prepared extraction requires Qiu Slurm')
    check_workers(workers)
    if not 0<=max_revisions<=2:raise ValueError('Use 0-2 fresh correction/review rounds')
    document=read(manifest);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    state={'policy':{'workers':workers,'max_revisions':max_revisions,'model':'gpt-6.1-sol','effort':'xhigh',
        'manifest_sha256':sha(manifest)},'cards':{}}
    worker=worker or CodexWorker(model='gpt-6.1-sol',effort='xhigh',timeout_seconds=None,
        web_search=False,network_access=False,pin_shell_environment=True)
    semaphore=asyncio.Semaphore(workers)
    def save():
        state['counts']=dict(Counter(r['status'] for r in state['cards'].values()));write(output/'state.json',state)
    async def one(bundle):
        mid=bundle['method_id'];row=state['cards'][mid]={'record_id':mid,'status':'pending'};save()
        async with semaphore:
            try:
                if bundle.get('prior_status')=='supported':raise ValueError('Accepted methods are excluded from continuation')
                if not bundle.get('reason'):raise ValueError('Fresh extraction requires a recorded reason')
                card=pinned(bundle['card']);registry=pinned(bundle['registry'])
                if card['record_id']!=mid:raise ValueError('Card/method identity differs')
                images=[(sid,Path(s['path'])) for sid,s in registry.items() if s.get('kind')=='image']
                attempt=output/'cards'/mid/'attempt_001';row.update(status='running',attempt_directory=str(attempt));save()
                row.update(await extract_registered_workflow(card,registry,images,attempt,worker,
                    max_revisions=max_revisions,representation='illumina_diagram/1.1'))
            except Exception as exc:row.update(status='failed',reason=f'{type(exc).__name__}: {exc}')
            save()
    ids=[b['method_id'] for b in document['bundles']]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate prepared method')
    await asyncio.gather(*(one(b) for b in document['bundles']));return state


async def recover(config_path,output,*,workers=25):
    """One bounded finder then sequential eligible extractions within its slot."""
    if not os.environ.get('SLURM_JOB_ID'):raise ValueError('Recovery requires Qiu Slurm')
    check_workers(workers);config=read(config_path);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    ledger=reconcile(config);write(output/'METHOD_LEDGER.json',ledger)
    snapshot=prepare_recovery(config,ledger,output)
    # Reuse the investigator/registration contract that produced the prior native run.
    from experiments.card_methods_full_20261112.investigate_holds import run as investigate
    from experiments.diagram_paper_pilot_20261106.run import prepare_method,dependency_reference
    async def handoff(pid,cases,additions,overrides,finding):
        result={}
        for case in cases:
            method=case['selection'];mid=method['method_id'];old=ledger['cards'][mid]
            folder=output/'prepared'/mid;folder.mkdir(parents=True)
            try:
                if finding.get('status')=='investigation_failed':raise ValueError('Finder failed; no unchanged retry')
                if old.get('attempt_directory'):
                    prior=Path(old['attempt_directory']);card=read(prior/'card.json');registry=read(prior/'source_registry.json')
                else:
                    card,registry,_=prepare_method(Path(config['library'])/method['canonical_directory']/'source_package.json',
                        Path(config['vault']),method,primary_overrides=overrides.get(mid,{}))
                baseline={text_identity(s) for s in registry.values() if s.get('kind')=='text'}
                supplied=additions.get(mid,{})
                distinct={sid:s for sid,s in supplied.items() if s.get('kind')=='text' and text_identity(s) not in baseline}
                if not distinct and not overrides.get(mid):raise ValueError('No distinct registered evidence; retain source/adjudication hold')
                for sid,s in distinct.items():
                    if sid in registry and registry[sid]!=s:raise ValueError('New source ID conflicts with earlier registration')
                    registry[sid]=s
                    if s.get('document_role')!='supplement':card['references'].append(dependency_reference(mid,sid,s))
                verify_registry_files(registry)
                write(folder/'card.json',card);write(folder/'source_registry.json',registry)
                bundles={'bundles':[{'method_id':mid,'prior_status':old['status'],
                    'reason':'New identity-checked registered evidence after the recorded request; fresh whole-artifact review required.',
                    'card':{'path':str(folder/'card.json'),'sha256':sha(folder/'card.json')},
                    'registry':{'path':str(folder/'source_registry.json'),'sha256':sha(folder/'source_registry.json')}}]}
                write(folder/'manifest.json',bundles)
                state=await run_prepared(folder/'manifest.json',folder/'extraction',workers=1,max_revisions=2)
                result[mid]=state['cards'][mid]
            except Exception as exc:result[mid]={'status':'source_recovery_needed','reason':str(exc)}
            ledger['cards'][mid]['continuation']=result[mid]
            ledger['continuation_counts']=dict(Counter(r.get('continuation',r)['status'] for r in ledger['cards'].values()))
            write(output/'METHOD_LEDGER.json',ledger)
        return result
    await investigate(output/'recovery_snapshot.json',Path(config['library']),Path(config['vault']),output/'source_work',
        workers=workers,on_recovered=handoff)
    return {'counts':ledger.get('continuation_counts',ledger['counts']),'native_slot_limit':workers,
            'methods_investigated':len(snapshot['cases']),'ledger':str(output/'METHOD_LEDGER.json')}


def validate_adjudication_request(request,candidate,registry):
    """Bind the curation question, never certify its proposed scientific answer."""
    if not request.get('question') or not request.get('scope_rationale'):
        raise ValueError('Adjudication requires a focused question and scope rationale')
    if not request.get('field_bindings') or not request.get('source_premises'):
        raise ValueError('Adjudication requires exact candidate fields and source premises')
    value=candidate.model_dump(mode='json')
    for binding in request['field_bindings']:
        if digest(_resolve(value,binding['pointer']))!=binding['value_sha256']:
            raise ValueError('Adjudication candidate field changed')
    for evidence in request['source_premises']:
        source=registry.get(evidence['source_id'],{})
        if not evidence.get('quote') or evidence['quote'] not in source.get('text',''):
            raise ValueError('Adjudication premise is not in registered text')


async def adjudicate(manifest,output,*,workers=7,worker=None):
    """Fresh full review, then existing revision_seed only for an actual repair verdict.

    This is curation of real held records, not agreement scoring. Every slot is
    reserved through its reviewer, any author correction and final fresh review.
    """
    if not os.environ.get('SLURM_JOB_ID'):raise ValueError('Adjudication requires Qiu Slurm')
    check_workers(workers);config=read(manifest);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    worker=worker or CodexWorker(model='gpt-6.1-sol',effort='xhigh',timeout_seconds=None,
        web_search=False,network_access=False,pin_shell_environment=True)
    state={'cards':{},'native_slot_limit':workers,'manifest_sha256':sha(manifest)}
    semaphore=asyncio.Semaphore(workers)
    def save():
        state['counts']=dict(Counter(r['status'] for r in state['cards'].values()));write(output/'state.json',state)
    async def one(case):
        mid=case['method_id'];row=state['cards'][mid]={'record_id':mid,'status':'pending'};save()
        async with semaphore:
            try:
                prior=case['prior'];old=Path(prior['attempt_directory'])
                if prior['status']!='research_needed':raise ValueError('Adjudicate a completed research hold only')
                for name,pin in prior['artifact_pins'].items():
                    if sha(old/name)!=pin:raise ValueError('Prior reviewed artifact changed')
                card=read(old/'card.json');registry=read(old/'source_registry.json');verify_registry_files(registry)
                candidate=parse_extraction((old/'extraction.json').read_bytes());previous=parse_review((old/'review.json').read_bytes())
                if validate_review(previous,candidate,registry):raise ValueError('Invalid previous review')
                request=case['request'];validate_adjudication_request(request,candidate,registry)
                folder=output/mid;attempt=folder/'adjudication_reviewer';attempt.mkdir(parents=True)
                for name,value in [('card.json',card),('source_registry.json',registry),
                    ('candidate.json',candidate.model_dump(mode='json')),('adjudication_request.json',request)]:write(attempt/name,value)
                images=[(sid,Path(s['path'])) for sid,s in registry.items() if s.get('kind')=='image']
                attached,delivery=stage_images(images,attempt)
                schema=attempt/'schema.json';write(schema,review_model('illumina_diagram_review/1.2').model_json_schema())
                last=Path(read(old/'stages.json')[-1]['path'])
                context,_=json.JSONDecoder().raw_decode((last/'stage_prompt.txt').read_text().split('CALLER CONTEXT\n',1)[1])
                context.update(candidate=str(attempt/'candidate.json'),candidate_sha256=digest(candidate),
                    source_registry=str(attempt/'source_registry.json'),card=str(attempt/'card.json'),
                    attached_images=delivery,output=str(attempt/'artifact.json'),
                    review_format_version='illumina_diagram_review/1.2',
                    review_targets=sorted(review_targets(candidate,format_version='illumina_diagram_review/1.2')),
                    adjudication_request={'path':str(attempt/'adjudication_request.json'),
                        'sha256':sha(attempt/'adjudication_request.json'),
                        'question':request['question'],'scope_rationale':request['scope_rationale'],
                        'field_bindings':request['field_bindings'],'source_premises':request['source_premises'],
                        'boundary':'Explicit curation question/premises, not source evidence or permission to waive facts. Preserve method, focal implementation and scientific scope; retain unresolved if the registered sources do not answer.'})
                command=[sys.executable,'-m','genomics_discovery.illumina.extraction']
                context['source_helper']=shlex.join(command+['source','--registry',str(attempt/'source_registry.json')])
                context['self_check']=shlex.join(command+['check',str(attempt/'artifact.json'),'--registry',str(attempt/'source_registry.json'),
                    '--record-id',mid,'--card',str(attempt/'card.json'),'--candidate',str(attempt/'candidate.json'),
                    '--review-format','illumina_diagram_review/1.2'])
                feedback=revision_handoff(attempt,candidate,previous);context['revision_feedback']=feedback
                context['artifact_helper']=shlex.join(command+['artifact'])
                prompt=build_prompt('reviewer',context,schema);(attempt/'stage_prompt.txt').write_text(prompt)
                fixed=[schema,attempt/'stage_prompt.txt',attempt/'image_delivery.json',attempt/'card.json',
                    attempt/'source_registry.json',attempt/'candidate.json',attempt/'adjudication_request.json']
                fixed.extend(Path(d['path']) for d in feedback.values());pins={str(p):sha(p) for p in fixed}
                write(attempt/'input_pins.json',pins);row.update(status='running',active_role='adjudication_reviewer');save()
                result=await worker.run(prompt,attempt,image_paths=attached)
                from genomics_discovery.execution.codex_worker import assess_archived_attempt
                assessment=assess_archived_attempt(attempt)
                if result.error or result.exit_code or not assessment.complete:raise ValueError('Adjudication native review did not complete')
                if result.thread_id!=assessment.thread_id or result.usage!=assessment.usage:raise ValueError('Review transport differs')
                if (attempt/'prompt.txt').read_text()!=prompt:raise ValueError('Archived review prompt differs')
                verify_stage_inputs(attempt,delivery,pins);verify_registry_files(registry)
                review=parse_review((attempt/'artifact.json').read_bytes())
                if errors:=validate_review(review,candidate,registry):raise ValueError('; '.join(errors))
                outcome=review_outcome(review,candidate,registry,source_mode=card.get('source_mode','diagram'))
                old_checks={c.target:(i,c) for i,c in enumerate(previous.checks)}
                closure={'candidate_sha256':digest(candidate),'previous_review_sha256':sha(old/'review.json'),
                    'fresh_review_sha256':sha(attempt/'artifact.json'),'request_sha256':sha(attempt/'adjudication_request.json'),
                    'scope_rationale':request['scope_rationale'],'field_bindings':request['field_bindings'],
                    'outcome':outcome,'decisions':[{'target':c.target,'previous_pointer':'/checks/'+str(old_checks[c.target][0]),
                        'fresh_pointer':'/checks/'+str(i),'previous_action':old_checks[c.target][1].action,
                        'fresh_action':c.action,'rationale':c.rationale,'evidence':[e.model_dump(mode='json') for e in c.evidence]}
                        for i,c in enumerate(review.checks) if old_checks[c.target][1].action!='accept']}
                write(folder/'closure.json',closure)
                if outcome=='needs_revision':
                    seed=folder/'repair_seed';seed.mkdir()
                    for name,value in [('card.json',card),('source_registry.json',registry),
                        ('extraction.json',candidate.model_dump(mode='json')),('review.json',review.model_dump(mode='json'))]:write(seed/name,value)
                    binding={'directory':str(seed),'pins':{n:sha(seed/n) for n in ['card.json','source_registry.json','extraction.json','review.json']}}
                    row.update(active_role='author_correction');save()
                    row.update(await extract_registered_workflow(card,registry,images,folder/'corrected',worker,
                        max_revisions=1,representation='illumina_diagram/1.1',revision_seed=binding))
                    row['attempt_directory']=str(folder/'corrected')
                elif outcome=='supported':
                    reviewed=folder/'reviewed';reviewed.mkdir()
                    for name,value in [('card.json',card),('source_registry.json',registry),
                        ('extraction.json',candidate.model_dump(mode='json')),('review.json',review.model_dump(mode='json'))]:write(reviewed/name,value)
                    graph=export_graph(candidate,review,registry,source_mode=card.get('source_mode','diagram'))
                    write(reviewed/'graph.json',graph)
                    write(reviewed/'stages.json',[{'role':'reviewer','status':'validated','path':str(attempt),
                        'thread_id':assessment.thread_id,'usage':assessment.usage,'input_pins':pins,
                        'artifact_sha256':sha(attempt/'artifact.json'),'adjudication_of_preserved_candidate':True}])
                    row.update(status='supported',attempt_directory=str(reviewed),candidate_sha256=digest(candidate),
                        review_sha256=digest(review),original_attempt=str(old),closure=str(folder/'closure.json'),
                        artifact_pins={n:sha(reviewed/n) for n in ['card.json','source_registry.json','extraction.json','review.json','graph.json','stages.json']})
                else:
                    # This fresh complete review changes no old record and clears no blocking Unknown.
                    row.update(status=outcome,review=str(attempt/'artifact.json'),review_sha256=sha(attempt/'artifact.json'),
                        candidate_sha256=digest(candidate),original_attempt=str(old),closure=str(folder/'closure.json'))
                row.pop('active_role',None)
            except Exception as exc:
                row.update(status='failed',reason=f'{type(exc).__name__}: {exc}')
                if 'attempt' in locals() and (attempt/'events.jsonl').exists():
                    row['failure_diagnostic']=classify_archived_terminal_failure(attempt)
            save()
    ids=[c['method_id'] for c in config['cases']]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate adjudication method')
    await asyncio.gather(*(one(c) for c in config['cases']));return state


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    for name in ('run','recover','adjudicate'):
        q=s.add_parser(name);q.add_argument('manifest',type=Path);q.add_argument('--output',type=Path,required=True)
        q.add_argument('--workers',type=int,default=25)
        if name=='run':q.add_argument('--max-revisions',type=int,default=2)
    a=p.parse_args()
    result=asyncio.run(recover(a.manifest,a.output,workers=a.workers) if a.command=='recover' else
        adjudicate(a.manifest,a.output,workers=a.workers) if a.command=='adjudicate' else
        run_prepared(a.manifest,a.output,workers=a.workers,max_revisions=a.max_revisions))
    print(json.dumps(result.get('counts',result),indent=2))


if __name__=='__main__':main()

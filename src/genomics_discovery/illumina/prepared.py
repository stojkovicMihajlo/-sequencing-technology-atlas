"""Continue pinned registered workflows through the existing extraction/review gates."""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter
import copy
import hashlib
import os
from pathlib import Path

from genomics_discovery.execution.codex_worker import CodexWorker, classify_archived_terminal_failure
from genomics_discovery.illumina.extraction import (
    extract_registered_workflow, read, write, sha, revalidate_completed, verify_registry_files)


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


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    for name in ('run','recover'):
        q=s.add_parser(name);q.add_argument('manifest',type=Path);q.add_argument('--output',type=Path,required=True)
        q.add_argument('--workers',type=int,default=25)
        if name=='run':q.add_argument('--max-revisions',type=int,default=2)
    a=p.parse_args()
    result=asyncio.run(recover(a.manifest,a.output,workers=a.workers) if a.command=='recover' else
        run_prepared(a.manifest,a.output,workers=a.workers,max_revisions=a.max_revisions))
    import json
    print(json.dumps(result.get('counts',result),indent=2))


if __name__=='__main__':main()

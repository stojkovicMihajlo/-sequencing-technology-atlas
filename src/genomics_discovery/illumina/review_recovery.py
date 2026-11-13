"""Bounded, checkpointed research-review return to existing source tools and review."""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

from genomics_discovery.execution.batch.storage import controller_lock, read, write, sha
from genomics_discovery.illumina.extraction import extract_registered_workflow, revalidate_completed, verify_registry_files
from genomics_discovery.illumina.models import digest, parse_extraction, parse_review, validate_review, review_outcome, review_reasons
from genomics_discovery.protocols.extraction.field_review import _resolve
from genomics_discovery.illumina import extraction as extraction_code
from genomics_discovery.sources.acquisition.failures import host_storage_failure


def tool_inventory():
    """A repaired host implementation/environment is a recorded new route input."""
    files={'review_recovery':Path(__file__),'extractor':Path(extraction_code.__file__)}
    root=Path(__file__).resolve().parents[3]
    files.update(registration=root/'experiments/card_methods_full_20261112/located_documents.py',
                 legacy_word=root/'src/genomics_discovery/sources/acquisition/legacy_word.py')
    for name in ('antiword','pdftotext'):
        executable=shutil.which(name)
        if executable:files[name]=Path(executable)
    if os.environ.get('ANTIWORDHOME'):
        files.update({f'antiword_asset:{name}':Path(os.environ['ANTIWORDHOME'])/name for name in ('UTF-8.txt','fontnames')})
    return {name:{'path':str(p.resolve()),'sha256':sha(p)} for name,p in files.items() if p.is_file()}


def tool_versions():
    return {name:ref['sha256'] for name,ref in tool_inventory().items()}


def logical_inputs(value):
    if isinstance(value,dict):
        return {k:logical_inputs(v) for k,v in value.items() if not (k=='path' and 'sha256' in value)}
    if isinstance(value,list):return [logical_inputs(v) for v in value]
    return value


def identifiers(value):
    return sorted({str(v).strip().casefold().removeprefix('https://doi.org/').removeprefix('http://doi.org/')
                   for v in (value if isinstance(value,list) else [value]) if v is not None})


def source_identity(source):
    """Paths, registration aliases and timestamps do not create new evidence."""
    kind=source.get('kind')
    text=source.get('text','')
    if text.startswith('[Rendition provenance, not publication content]\n'):text=text.partition('\n\n')[2]
    content=(' '.join(text.split()) if kind=='text' else source.get('sha256'))
    return digest({'kind':kind,'content':content,'level':source.get('content_level'),
                   'pmid':identifiers(source.get('pmid')),'doi':identifiers(source.get('doi'))})


def registered_custody(source):
    """Require host registration's original-to-readable-rendition byte chain."""
    path=source.get('path');value=source.get('sha256');pins=source.get('custody_files',[])
    if not path or not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}',value) or not pins:
        raise ValueError('New recovery evidence lacks registered readable/original custody')
    verify_registry_files({'new':source})
    if source.get('kind')=='text':
        raw=Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=value or raw.decode('utf-8')!=source.get('text'):
            raise ValueError('Registered reading bytes differ from supplied text')
        hashes={p['sha256'] for p in pins if p.get('path')!=path}
        bound=False
        for pin in pins:
            if not str(pin['path']).endswith('.json'):continue
            raw=Path(pin['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=pin['sha256']:raise ValueError('Registration custody changed')
            record=json.loads(raw)
            if isinstance(record,dict) and record.get('text_sha256')==value and record.get('original_sha256') in hashes:
                bound=True;break
        if not bound:raise ValueError('New recovery evidence lacks a bound original/conversion record')
    elif source.get('kind')=='image':
        bound=False
        for pin in pins:
            if not str(pin['path']).endswith('.json'):continue
            raw=Path(pin['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=pin['sha256']:raise ValueError('Visual custody changed')
            record=json.loads(raw)
            if isinstance(record,dict) and record.get('image_sha256')==value and record.get('parent_source_sha256')==source.get('parent_source_sha256'):
                bound=True;break
        if not bound:raise ValueError('Additional visual lacks bound rendering/asset provenance')


def distinct_sources(registry,candidates):
    identities={source_identity(s) for s in registry.values()};result={}
    for sid,source in candidates.items():
        if source.get('kind')=='text':
            if source.get('content_level')!='fulltext':continue
        elif source.get('kind')=='image':
            parent={**registry,**candidates}.get(source.get('parent_source_id'),{})
            parent_hash=source.get('parent_source_sha256')
            if (source.get('image_role')!='primary_paper_figure' or parent.get('kind')!='text'
                    or parent.get('content_level')!='fulltext'
                    or not isinstance(parent_hash,str) or not re.fullmatch(r'[0-9a-f]{64}',parent_hash)
                    or parent_hash!=parent.get('sha256')):
                raise ValueError('Additional visual evidence needs a bound registered primary parent')
        else:continue
        identity=source_identity(source)
        if identity in identities:continue
        registered_custody(source)
        if sid in registry:raise ValueError('Recovery cannot replace an existing source ID')
        result[sid]=source;identities.add(identity)
    return result


def research_request(directory):
    directory=Path(directory);candidate=parse_extraction((directory/'extraction.json').read_bytes())
    review=parse_review((directory/'review.json').read_bytes());registry=read(directory/'source_registry.json')
    card=read(directory/'card.json');verify_registry_files(registry)
    if errors:=validate_review(review,candidate,registry):raise ValueError('; '.join(errors))
    if review_outcome(review,candidate,registry,source_mode=card.get('source_mode','diagram'))!='research_needed':
        raise ValueError('Recovery requires an actual completed research review')
    questions=review_reasons(review,candidate)
    fingerprint=digest({'method_id':candidate.record_id,'candidate':digest(candidate),
        'questions':sorted(questions,key=lambda q:(q['target'],q['question'])),
        'sources':sorted({source_identity(s) for s in registry.values()})})
    return {'method_id':candidate.record_id,'fingerprint':fingerprint,'candidate_sha256':digest(candidate),
        'review_raw_sha256':sha(directory/'review.json'),'questions':questions,
        'source_state_sha256':digest(sorted({source_identity(s) for s in registry.values()})),
        'original_directory':str(directory)},card,registry,candidate


def _pinned(ref):
    path=Path(ref['path'])
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('Recovery inventory pin changed')
    value=json.loads(raw)
    return _resolve(value,ref['pointer']) if ref.get('pointer') else value


async def cached_sources(request,registry,policy,output):
    """Read registered inventory first; optionally replay exact receipt-bound originals."""
    candidates={};holds=[]
    for ref in policy.get('cache_registries',[]):
        ref=dict(ref)
        if ref.get('pointer'):
            ref['pointer']=ref['pointer'].replace('{method_id}',request['method_id'].replace('~','~0').replace('/','~1'))
        candidates.update(_pinned(ref))
    if policy.get('cached_documents'):
        from genomics_discovery.sources.acquisition.models import Document
        from experiments.card_methods_full_20261112.located_documents import register
        from genomics_discovery.sources.acquisition.downloads import Download,public_url
        from urllib.error import URLError
        for index,item in enumerate(policy['cached_documents']):
            original=Path(item['original']['path'])
            raw=original.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=item['original']['sha256']:raise ValueError('Cached original changed')
            document=Document.model_validate(_pinned(item['document']))
            receipt=_pinned(item['receipt'])
            if receipt['sha256']!=item['original']['sha256']:raise ValueError('Cached original/receipt disagree')
            responses={url:Download(raw,receipt['final_url'],receipt.get('content_type',''))
                for url in {receipt['requested_url'],receipt['final_url']}}
            if document.url not in responses:raise ValueError('Document URL is not the pinned cached receipt URL')
            for response in item.get('cached_responses',[]):
                body=Path(response['path']).read_bytes()
                if hashlib.sha256(body).hexdigest()!=response['sha256']:raise ValueError('Cached response changed')
                for url in {response['requested_url'],response['final_url']}:
                    responses[url]=Download(body,response['final_url'],response.get('content_type',''))
            def cached_only(url):
                public_url(url)
                if url not in responses:raise ValueError('Cache-only registration lacks pinned response: '+url)
                return responses[url]
            try:
                source=register(document,item['paper'],Path(output)/f'document_{index:03d}',
                    downloader=cached_only,parent_sources=registry)
                sid=item['source_id'];candidates[sid]=source
            except (ValueError,URLError) as exc:
                if host_storage_failure(exc):raise
                holds.append({'stage':'cached_registration','exception_type':type(exc).__name__,
                              'reason':str(exc),'source_availability':'not_inferred_from_host_failure'})
    return {'sources':candidates,'holds':holds}


async def investigate_request(request,registry,policy,output):
    """Existing one-publication finder/registration, only for a declared new route."""
    if not policy.get('new_route'):return {'sources':{},'holds':[{'reason':'No unattempted route declared'}]}
    from experiments.card_methods_full_20261112.investigate_holds import run
    from genomics_discovery.execution.codex_worker import classify_archived_terminal_failure
    output=Path(output);context=output/'inputs'
    if policy.get('case'):case=copy.deepcopy(policy['case'])
    else:
        catalog=_pinned(policy['case_catalog'])
        matches=[c for c in catalog['cases'] if c['selection']['method_id']==request['method_id']]
        if len(matches)!=1:raise ValueError('Recovery needs one curated case identity')
        case=copy.deepcopy(matches[0])
    if case['selection']['method_id']!=request['method_id']:raise ValueError('Recovery case changed method identity')
    case['audit']={'research_requests':request['questions']}
    case['hold']={'failure_category':'evidence_request_unresolved','reason':'New completed review requests specific evidence.'}
    write(context/'recovery_02/registered_additions.json',{request['method_id']:registry})
    write(context/'recovery_02/methods'/request['method_id']/'RECOVERY.json',{
        'request':request,'new_route':policy['new_route'],'prior_routes':policy.get('prior_routes',[])})
    route={**policy['new_route'],'paper_id':case['paper']['paper_id']}
    snapshot={'source_release':str(context),'cases':[case],'observed_source_leads':[route]}
    snapshot_path=output/'snapshot.json';write(snapshot_path,snapshot)
    destination=output/'investigation'
    await run(snapshot_path,Path(policy['library']),Path(policy['vault']),destination,workers=1)
    state=read(destination/'state.json')['publications'][case['paper']['paper_id']]
    if state['status']=='investigation_failed':
        diagnostic=classify_archived_terminal_failure(destination/'publications'/case['paper']['paper_id']/'finder')
        return {'sources':{},'failure':{'stage':state.get('failed_stage','source_investigation'),'exception_type':state.get('exception_type'),
            'reason':state.get('reason'),'diagnostic':diagnostic,
            'failure_category':state.get('failure_category'),
            'category':'provider_policy_block' if diagnostic['category']=='provider_biological_risk_block' else 'source_execution_failure'}}
    sources=read(destination/'registered_additions.json').get(request['method_id'],{})
    return {'sources':sources,'holds':state.get('documents',[])}


async def recover_review(row,directory,output,worker,policy,*,cache_lookup=cached_sources,
                         investigator=investigate_request,dispatcher=None):
    """Persist one bounded method-run episode across descendants and resumption.

    The caller reserves its workflow slot through all awaits. Completed child
    chains are replayed; an interrupted native dispatch is never blindly repeated.
    """
    if row.get('status')!='research_needed':return row
    limit=policy.get('max_rounds',1)
    if type(limit)!=int or not 0<=limit<=2:raise ValueError('Use 0-2 bounded evidence recovery rounds')
    if not limit:return row
    dispatcher=dispatcher or extract_registered_workflow
    output=Path(output);directory=Path(directory)
    row=copy.deepcopy(row);row.setdefault('attempt_directory',str(directory))
    first,_,_,_=research_request(directory);method=first['method_id']
    tag=row.get('recovery_episode')
    marker=directory/'recovery_episode.json'
    if tag is None and marker.exists():tag=read(marker)
    requested_store=(Path(policy['state_directory'])/digest(method) if policy.get('state_directory') else None)
    store=(Path(tag['store']) if tag else requested_store or output).resolve()
    if requested_store is not None and tag and requested_store.resolve()!=store.resolve():
        raise ValueError('Cannot silently relocate an inherited recovery episode')
    if store==directory or directory in store.parents:raise ValueError('Keep recovery outside the original attempt')
    versions=tool_versions()
    inputs=logical_inputs({**{k:policy.get(k) for k in ('strategy','new_route','cache_registries','cached_documents')},
        'tools':{**versions,**policy.get('tool_versions',{})},
        'model':getattr(worker,'model',None),'effort':getattr(worker,'effort',None)})
    initial_id=digest({'request':first['fingerprint'],'initial_route_inputs':inputs})
    result=copy.deepcopy(row)
    with controller_lock(store):
        path=store/'recovery.json'
        journal=read(path) if path.exists() else {'method_id':method,'episodes':{},'fingerprints':{},'finder_results':{}}
        if journal['method_id']!=method:raise ValueError('Recovery store belongs to another method')
        inherited=tag['id'] if tag else journal['fingerprints'].get(first['fingerprint'])
        if tag and inherited not in journal['episodes']:raise ValueError('Inherited recovery episode is unavailable')
        previous=journal['episodes'].get(inherited,{})
        # Descendants keep the original consumed bound. The original reviewed
        # input may start a separately recorded episode for a changed route.
        is_descendant=(previous and first['fingerprint']!=previous['initial_request']['fingerprint'])
        episode_id=inherited if is_descendant else initial_id
        episode=journal['episodes'].setdefault(episode_id,{'limit':limit,'used':0,'initial_request':first,
            'current_directory':str(directory),'latest_result':copy.deepcopy(row),'requests':{},'finished':False})
        if episode['limit']!=limit:raise ValueError('Resumption cannot change an episode recovery bound')
        def save():write(path,journal)
        pending=[e for e in episode['requests'].values() if e['status']!='complete']
        if pending:
            return {**episode['latest_result'],'recovery':{'status':'interrupted_no_duplicate',
                'next_action':'Inspect the preserved incomplete step/archive; no model or download repeated.',
                'request':pending[0]['request']['fingerprint']}}
        if episode['finished']:
            result=copy.deepcopy(episode['latest_result'])
            if result.get('artifact_pins') and result.get('status') in {'supported','research_needed','needs_revision'}:revalidate_completed(result)
            result['recovery_reused']=True;return result
        current=Path(episode['current_directory']);result=copy.deepcopy(episode['latest_result'])
        while episode['used']<episode['limit']:
            request,card,registry,candidate=research_request(current);fingerprint=request['fingerprint']
            journal['fingerprints'][fingerprint]=episode_id
            current_policy=dict(policy)
            if current_policy.get('strategy')=='new_review_request' and not current_policy.get('new_route'):
                current_policy['new_route']={'strategy':'specific_unseen_review_request','request_fingerprint':fingerprint,
                    'description':'Inspect inventory/cache first; investigate this exact new request without repeating exhausted routes.'}
            settings=logical_inputs({'route':current_policy.get('new_route'),'cache':policy.get('cache_registries',[]),
                'documents':policy.get('cached_documents',[]),'tools':{**versions,**policy.get('tool_versions',{})},
                'model':getattr(worker,'model',None),'effort':getattr(worker,'effort',None)})
            route=digest(settings);key=fingerprint+':'+route
            entry=episode['requests'].get(key)
            if entry:
                if entry['status']!='complete':
                    return {**result,'recovery':{'status':'interrupted_no_duplicate','request':fingerprint,
                        'next_action':'Inspect preserved dispatch/archive; no model or download repeated.'}}
                result=copy.deepcopy(entry['result'])
                if result.get('artifact_pins'):revalidate_completed(result)
                if result.get('status')=='research_needed' and result.get('recovery',{}).get('status')=='new_sources_reviewed':
                    current=Path(result['attempt_directory']);continue
                episode.update(finished=True,latest_result=copy.deepcopy(result));save()
                result['recovery_reused']=True;return result
            episode['used']+=1
            folder=output/f"round_{episode['used']:02d}_{fingerprint[:10]}_{route[:8]}"
            write(folder/'request.json',request);write(folder/'tool_provenance.json',tool_inventory())
            entry={'status':'running','request':request,'route':route,'directory':str(folder)}
            episode['requests'][key]=entry;save();stage='cache_lookup'
            try:
                cached=await cache_lookup(request,registry,current_policy,folder/'cache')
                write(folder/'cache_result.json',cached);new=distinct_sources(registry,cached.get('sources',{}))
                if not new and current_policy.get('new_route'):
                    stage='source_investigation'
                    search_key=digest({'request':fingerprint,'route':logical_inputs(current_policy['new_route'])})
                    found=journal['finder_results'].get(search_key)
                    if found is None:
                        found=await investigator(request,registry,current_policy,folder/'source')
                        journal['finder_results'][search_key]=found;save()
                    write(folder/'source_result.json',found)
                    if found.get('failure'):
                        failure=found['failure'];result.update(status='provider_policy_block' if failure.get('category')=='provider_policy_block' else 'failed',
                            prior_scientific_outcome='research_needed',failure_category=failure.get('failure_category') or failure.get('category'),recovery_failure=failure)
                    else:new=distinct_sources(registry,found.get('sources',{}))
                if result['status']=='research_needed' and new:
                    stage='author_and_fresh_review';enlarged={**registry,**new};verify_registry_files(enlarged)
                    updated_card=copy.deepcopy(card)
                    from experiments.diagram_paper_pilot_20261106.run import dependency_reference
                    for sid,source in new.items():
                        if source.get('kind')=='text' and source.get('document_role')!='supplement':
                            if not any(ref.get('source_id')==sid for ref in updated_card.get('references',[])):
                                updated_card.setdefault('references',[]).append(dependency_reference(method,sid,source))
                    write(folder/'registered_sources.json',enlarged);write(folder/'prepared_card.json',updated_card)
                    child=folder/'workflow';entry['dispatch_directory']=str(child);save()
                    images=[(sid,Path(s['path'])) for sid,s in enlarged.items() if s.get('kind')=='image']
                    result=await dispatcher(updated_card,enlarged,images,child,worker,
                        max_revisions=policy.get('max_revisions',2),representation=candidate.format_version)
                    result['attempt_directory']=str(child);current=child
                    result['recovery']={'status':'new_sources_reviewed','request':fingerprint,'added_source_ids':sorted(new)}
                    result['recovery_episode']={'id':episode_id,'store':str(store),'limit':episode['limit']}
                    write(child/'recovery_episode.json',result['recovery_episode']);write(folder/'workflow_result.json',result)
                    if result['status']=='research_needed':
                        descendant,_,_,_=research_request(child);journal['fingerprints'][descendant['fingerprint']]=episode_id
                elif result['status']=='research_needed':
                    result['recovery']={'status':'held_no_new_evidence','request':fingerprint,'questions':request['questions'],
                        'next_action':'A concrete new source/registration route is required; unchanged failed searches do not repeat.'}
            except Exception as exc:
                storage=host_storage_failure(exc)
                result.update(status='failed',prior_scientific_outcome='research_needed',failure_category='host_storage_failure' if storage else getattr(exc,'category','software_error'),
                    recovery_failure={'stage':stage,'exception_type':type(exc).__name__,'reason':str(exc)})
                if storage:result['recovery_failure']['host_failure']=storage
            result['recovery_episode']={'id':episode_id,'store':str(store),'limit':episode['limit']}
            entry.update(status='complete',result=copy.deepcopy(result))
            episode.update(latest_result=copy.deepcopy(result),current_directory=str(current));save()
            if result['status']!='research_needed' or result.get('recovery',{}).get('status')!='new_sources_reviewed':break
        episode.update(finished=True,latest_result=copy.deepcopy(result));save()
        return result


async def run_completed(manifest,policy_path,output,*,workers=1,worker=None):
    """Return actual completed research checkpoints directly to bounded recovery."""
    if not os.environ.get('SLURM_JOB_ID'):raise ValueError('Completed-review recovery requires Qiu Slurm')
    if type(workers)!=int or not 1<=workers<=50:raise ValueError('Use 1-50 explicit native workflow slots')
    raw=Path(manifest).read_bytes();config=json.loads(raw)
    policy_raw=Path(policy_path).read_bytes();base_policy=json.loads(policy_raw)
    if not config.get('purpose'):raise ValueError('Declare the recovery purpose before execution')
    cases=config['checkpoints'];ids=[c['method_id'] for c in cases]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate completed method checkpoint')
    if any(Path(mid).name!=mid or mid in {'.','..'} for mid in ids):raise ValueError('Use simple method IDs')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    state={'purpose':config['purpose'],'native_slot_limit':workers,'cards':{},
        'manifest_sha256':hashlib.sha256(raw).hexdigest(),'policy_sha256':hashlib.sha256(policy_raw).hexdigest()}
    from genomics_discovery.execution.codex_worker import CodexWorker
    worker=worker or CodexWorker(model='gpt-6.1-sol',effort='xhigh',timeout_seconds=None,
        web_search=False,network_access=False,pin_shell_environment=True)
    slots=asyncio.Semaphore(workers)
    def save():
        state['counts']=dict(Counter(r['status'] for r in state['cards'].values()));write(output/'state.json',state)
    async def one(case):
        mid=case['method_id'];row=state['cards'][mid]={'record_id':mid,'status':'pending'};save()
        async with slots:
            try:
                prior=_pinned(case['checkpoint']);directory=Path(prior['attempt_directory'])
                revalidate_completed(prior)
                if read(directory/'card.json')['record_id']!=mid:raise ValueError('Completed checkpoint changed method identity')
                row.update(prior);row['activity']={'status':'running','phase':'evidence_recovery'};save()
                policy={**base_policy,**config.get('recovery',{}),**case.get('recovery',{})}
                if row['status']=='research_needed':
                    row.update(await recover_review(row,directory,output/'methods'/mid,worker,policy))
                else:row['recovery_skipped']='Completed outcome is not a research request'
                row['activity']={'status':'complete','phase':'completed_recovery'}
            except Exception as exc:
                storage=host_storage_failure(exc)
                row.update(status='failed',failure_category='host_storage_failure' if storage else getattr(exc,'category','software_error'),
                    recovery_failure={'stage':'completed_review_handoff','exception_type':type(exc).__name__,'reason':str(exc)})
            save()
    await asyncio.gather(*(one(case) for case in cases));return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--policy',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=1)
    args=parser.parse_args()
    result=asyncio.run(run_completed(args.manifest,args.policy,args.output,workers=args.workers))
    print(json.dumps(result['counts']))


if __name__=='__main__':main()

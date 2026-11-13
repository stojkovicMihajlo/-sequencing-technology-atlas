"""Stream first-pass screening candidates into existing public-paper acquisition.

Publication identity and source roles are retained; no paper becomes an atlas
method automatically. Existing full-source cache is checked before retrieval.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import sys
import shutil
from urllib.parse import quote,urlencode,urlsplit

from experiments.method_intake_audit_20261113 import bulk_screen
from experiments.method_intake_audit_20261113 import live_pilot
from genomics_discovery.illumina.sources import acquire_pmc_fulltext
from genomics_discovery.illumina.sources import fetch,https_url
from genomics_discovery.sources.acquisition.models import Identity
from genomics_discovery.sources.acquisition.pdf_registration import verify_pdf_text
from genomics_discovery.sources.acquisition.runtime import _identity,_normal,_doi
from genomics_discovery.sources.acquisition.html_identity import assess_primary_html
from scripts.illumina.technology_sources.article_identity import xml_identity
from scripts.illumina.technology_sources.article_identity import normalized_title
from scripts.illumina.technology_sources.recover_selected import acquire_one

SOURCE_RECOVERY_ERRORS=(ValueError,OSError,RuntimeError,subprocess.SubprocessError)


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def identity_check(path,metadata):
    path=Path(path);expected={'pmid':metadata['pmid'],'title':metadata['title'],'metadata':metadata}
    if path.suffix.lower() in {'.xml','.nxml'}:
        checked=xml_identity(path.read_bytes(),expected)
        if checked['status']!='bibliographic_identity_corresponds' or not checked['has_body']:
            raise ValueError('Own article identifiers/title/body do not match')
        return checked
    doi=next(iter(metadata.get('identifiers',{}).get('doi',[])),None)
    if path.suffix.lower()=='.pdf':
        front=subprocess.run(['pdftotext','-f','1','-l','2',str(path),'-'],capture_output=True,check=True,timeout=60).stdout.decode(errors='replace')
        return verify_pdf_text(front,{'pmid':metadata['pmid'],'doi':doi,'title':metadata['title']})
    if path.suffix.lower() in {'.html','.htm'}:
        raw=path.read_bytes();expected=Identity(pmid=metadata['pmid'],doi=doi,title=metadata['title'])
        return assess_primary_html(raw,_identity(raw,'html'),expected,expected,'https://cached-source.invalid',normal=_normal,doi=_doi)
    raise ValueError('Unsupported original article format')


def cache_index(collection,expected_sha256):
    collection=Path(collection).resolve()
    if sha(collection)!=expected_sha256:raise ValueError('Full source-vault collection changed')
    data=json.loads(collection.read_text());index={}
    for paper in data['papers'].values():
        pmid=str(paper.get('pmid') or paper.get('metadata',{}).get('pmid') or '')
        if pmid:index.setdefault(pmid,[]).extend({**f,'absolute_path':str(collection.parent/f['path'])} for f in paper.get('files',[]) if f.get('kind')=='article')
    return index,len(data['papers'])


def planned_cohort_ids(cohorts):
    """Exclude the entire pinned prior roster, including queued/failed/no-abstract."""
    ids=set()
    for cohort in cohorts:
        for item,is_overlay in [(cohort['pool'],False),*[(o,True) for o in cohort.get('overlays',[])]]:
            path=Path(item['path'])
            if sha(path)!=item['sha256']:raise ValueError('Prior planned-cohort input changed')
            data=json.loads(path.read_text());records=data['metadata_overlay'] if is_overlay else data
            if any(record.get('pmid')!=pmid for pmid,record in records.items()):raise ValueError('Prior cohort own-PMID/key mismatch')
            ids.update(records)
    return ids


def acquire_publication(metadata,directory,cached=(),*,pmc_acquirer=acquire_pmc_fulltext,selected_acquirer=acquire_one):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False);attempts=[]
    for record in cached:
        path=Path(record['absolute_path'])
        try:
            if sha(path)!=record['sha256']:raise ValueError('Cached source byte hash differs')
            checked=identity_check(path,metadata)
            return {'pmid':metadata['pmid'],'status':'cached_original_verified','document_role':'primary_article',
                    'atlas_source_role':'pending_method_or_protocol_association','files':[{'path':str(path),'sha256':sha(path),'kind':'article'}],
                    'identity_check':checked,'cache_holds':attempts,'scientific_applicability':'not_reviewed'}
        except Exception as exc:attempts.append({'stage':'cached_original','path':str(path),'error':str(exc)})
    pmc=directory/'pmc';pmc.mkdir();result=pmc_acquirer(metadata,pmc);attempts.append({'stage':'existing_pmc_acquirer','result':result})
    if result.get('status')=='fulltext_acquired':
        path=pmc/result['path']
        try:
            checked=identity_check(path,metadata)
            return {'pmid':metadata['pmid'],'status':'acquired_original_verified','document_role':'primary_article',
                    'atlas_source_role':'pending_method_or_protocol_association','files':[{'path':str(pmc/result[k]),'sha256':sha(pmc/result[k]),'kind':kind} for k,kind in [('path','article'),('text_path','reading-text'),('blocks_path','blocks')]],
                    'identity_check':checked,'attempts':attempts,'scientific_applicability':'not_reviewed'}
        except Exception as exc:attempts.append({'stage':'pmc_identity','error':str(exc)})
    fallback=directory/'public_selected';fallback.mkdir()
    paper={'id':'pmid-'+metadata['pmid'],'pmid':metadata['pmid'],'title':metadata['title'],'metadata':metadata}
    _,files,receipts=selected_acquirer(fallback,paper)
    bulk_screen.write(directory/'PUBLIC_RETRIEVAL_RECEIPTS.json',receipts)
    attempts.append({'stage':'existing_selected_acquirer','receipt_path':str(directory/'PUBLIC_RETRIEVAL_RECEIPTS.json')})
    for path,kind in files:
        if kind!='article':continue
        try:
            checked=identity_check(path,metadata)
            return {'pmid':metadata['pmid'],'status':'acquired_original_verified','document_role':'primary_article',
                    'atlas_source_role':'pending_method_or_protocol_association','files':[{'path':str(p),'sha256':sha(p),'kind':k} for p,k in files],
                    'identity_check':checked,'attempts':attempts,'scientific_applicability':'not_reviewed'}
        except Exception as exc:attempts.append({'stage':'selected_identity','path':str(path),'error':str(exc)})
    return {'pmid':metadata['pmid'],'status':'public_source_or_identity_hold','files':[],
            'attempts':attempts,'document_role':'publication_candidate','scientific_applicability':'not_reviewed'}


async def acquire_validated_patch(patch,papers,output,collection_pin):
    """Publication-only handoff from a separately validated correction patch."""
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    index,total=cache_index(collection_pin['path'],collection_pin['sha256']);by_id={p['pmid']:p for p in papers};results={}
    for row in patch['papers']:
        if row['route']!='acquisition_review_candidate':continue
        pmid=row['pmid']
        result=await asyncio.to_thread(acquire_publication,by_id[pmid],output/('pmid-'+pmid),index.get(pmid,[]))
        results[pmid]={**result,'screening':row}
        bulk_screen.write(output/'ACQUISITION_PATCH.json',{'papers':results,'counts':dict(Counter(r['status'] for r in results.values())),
            'cache_publication_records_checked':total,'method_admissions':0,'boundary':'Publication source handoff only; no model calls or method-node admission.'})
    return dict(Counter(r['status'] for r in results.values()))


def unpaywall_leads(paper,directory,contact,*,fetcher=fetch,existing_response=None):
    """Adapt the existing approved DOI lookup; leads are not acquired sources."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    doi=next(iter(paper.get('doi',[])),None)
    if not doi:return {'status':'no_assigned_doi','candidates':[]}
    base='https://api.unpaywall.org/v2/'+quote(doi,safe='/');path=directory/'original.json'
    if existing_response:
        cached=Path(existing_response['path'])
        if sha(cached)!=existing_response['sha256']:raise ValueError('Prior OA metadata response changed')
        shutil.copyfile(cached,path)
        receipt={'retrieval_mode':'reuse_prior_approved_response','original_response':existing_response,'path':str(path),'sha256':sha(path)}
    else:
        if not contact:raise ValueError('Fresh OA lookup requires the approved project contact environment variable')
        receipt=fetcher(base+'?'+urlencode({'email':contact}),path,timeout=40)
    receipt['request_url']=base+'?email=<approved-project-contact>';receipt['final_url']=receipt['request_url']
    result=json.loads(path.read_text())
    if not isinstance(result.get('doi'),str) or _doi(result['doi'])!=_doi(doi):raise ValueError('Unpaywall own DOI differs from frozen assigned primary')
    title_matches=normalized_title(result.get('title'))==normalized_title(paper['title'])
    candidates=[]
    for location in result.get('oa_locations',[]):
        if location.get('version') not in {'acceptedVersion','publishedVersion'}:continue
        for key in ('url_for_pdf','url_for_landing_page'):
            if not location.get(key):continue
            url=https_url(location[key]);host=urlsplit(url).hostname or ''
            if host.endswith('ncbi.nlm.nih.gov') or host.endswith('europepmc.org'):continue
            item={'url':url,'format':'pdf' if key=='url_for_pdf' else 'html','version':location['version'],'host_type':location.get('host_type')}
            if item not in candidates:candidates.append(item)
    record={'status':'metadata_identity_matches' if title_matches else 'doi_matches_title_variant_unaccepted',
            'receipt':receipt,'response_sha256':sha(path),'returned_title':result.get('title'),'candidates':candidates,
            'limits':'Location leads only. The frozen primary title/IDs remain unchanged and must pass original-byte registration; no submitted preprint substitution.'}
    bulk_screen.write(directory/'LEADS.json',record);return record


def recover_assigned_primaries(config_path,output,*,registrar=None):
    """Zero-model cache-first recovery for exact assigned primary publications."""
    if registrar is None:
        from experiments.card_methods_full_20261112.located_documents import register
        registrar=register
    from genomics_discovery.sources.acquisition.models import Document
    config=bulk_screen.read(config_path);output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    snapshot=Path(config['snapshot']['path'])
    if sha(snapshot)!=config['snapshot']['sha256']:raise ValueError('Assigned-primary snapshot changed')
    cases={c['selection']['method_id']:c for c in bulk_screen.read(snapshot)['cases']}
    target_ids=[t['method_id'] for t in config['targets']]
    if len(target_ids)!=len(set(target_ids)):raise ValueError('Duplicate assigned method target')
    for target in config['targets']:
        if cases[target['method_id']]['paper']['paper_id']!='pmid-'+target['pmid']:
            raise ValueError('Method/assigned-primary identity changed')
    contact=os.environ.get(config.get('approved_contact_env','ATLAS_PUBLIC_ACCESS_CONTACT'))
    if contact and hashlib.sha256(contact.encode()).hexdigest()!=config['approved_contact_sha256']:
        raise ValueError('Project contact differs from the recorded approval')
    for receipt in [*config.get('prior_source_attempts',[]),*([config['prior_execution_receipt']] if config.get('prior_execution_receipt') else [])]:
        if sha(receipt['path'])!=receipt['sha256']:raise ValueError('Prior recovery receipt changed')
    bulk_screen.write(output/'RESOLVED_CONFIG.json',config)
    index,total=cache_index(config['cache_collection']['path'],config['cache_collection']['sha256'])
    bulk_root=Path(config['bulk_output']);bulk_path=bulk_root/'ACQUISITION.json'
    bulk_bytes=bulk_path.read_bytes();frozen_bulk=output/'BULK_CACHE_CHECKPOINT.json';frozen_bulk.write_bytes(bulk_bytes)
    bulk_screen.write(output/'BULK_CACHE_LOOKUP_RECEIPT.json',{'source_path':str(bulk_path),'captured_path':str(frozen_bulk),
        'raw_sha256':hashlib.sha256(bulk_bytes).hexdigest(),'bytes':len(bulk_bytes),
        'basis':'One read supplies both frozen bytes and lookup; live cache can continue changing.'})
    bulk_records=json.loads(bulk_bytes)['papers']
    state={};overrides={};provider_stopped=False;denied_hosts=set(config.get('blocked_hosts',[]))
    def checkpoint():
        bulk_screen.write(output/'OUTCOMES.json',{'papers':state,'counts':dict(Counter(r['status'] for r in state.values())),
                          'expected_targets':len(config['targets']),'cache_records_checked':total,'model_calls':0})
        bulk_screen.write(output/'PRIMARY_OVERRIDES.json',overrides)
        cached_pending={mid:r for mid,r in state.items() if r['status']=='verified_cached_pending_registration'}
        bulk_screen.write(output/'READY_HANDOFF.json',{'ready_methods':list(overrides),'primary_overrides':str(output/'PRIMARY_OVERRIDES.json'),'cached_pending_registration':cached_pending,
                          'snapshot':config['snapshot'],'model_calls':0,'fresh_review_required':True})
    for target in config['targets']:
        mid=target['method_id'];paper=cases[mid]['paper'];pmid=paper['paper_id'].removeprefix('pmid-')
        if pmid!=target['pmid']:raise ValueError('Method/assigned-primary identity changed')
        record={'method_id':mid,'paper_id':paper['paper_id'],'status':'checking_cache','cache_holds':[],'attempts':[],
                'prior_source_attempts':config.get('prior_source_attempts',[])};state[mid]=record
        metadata={'pmid':pmid,'title':paper['title'],'identifiers':{'doi':paper.get('doi',[])}}
        cached=list(index.get(pmid,[]));existing=bulk_records.get(pmid,{})
        if existing.get('publication_record'):
            p=Path(existing['publication_record'])
            if sha(p)==existing['sha256']:cached.extend({'absolute_path':f['path'],'sha256':f['sha256']} for f in bulk_screen.read(p).get('files',[]) if f.get('kind')=='article')
        for original in cached:
            try:
                path=Path(original['absolute_path'])
                if sha(path)!=original['sha256']:raise ValueError('Cached original bytes changed')
                checked=identity_check(path,metadata);record.update(status='verified_cached_pending_registration',original={'path':str(path),'sha256':sha(path)},identity_check=checked,
                    next_action='register_cached_original',limits='Original identity verified; readable source registration/custody still required before extraction readiness.')
                break
            except SOURCE_RECOVERY_ERRORS as exc:record['cache_holds'].append({'path':original['absolute_path'],'error':str(exc)})
            except Exception as exc:
                record.update(status='recovery_execution_failed',failed_stage='cached_identity',error=repr(exc));checkpoint();raise
        if record['status']=='verified_cached_pending_registration':
            checkpoint();continue
        if provider_stopped:record['status']='provider_metadata_hold';checkpoint();continue
        try:
            leads=unpaywall_leads(paper,output/'metadata'/paper['paper_id'],contact,existing_response=config.get('oa_response_cache',{}).get(pmid))
            record['oa_metadata']=leads
            extra=[{'url':u,'format':'pdf','version':'unverified_public_author_copy','host_type':'author_or_repository'} for u in target.get('candidate_pdf_urls',[])]
            attempted_urls=set()
            for number,lead in enumerate(leads['candidates']+extra):
                if lead['format']!='pdf':continue
                host=urlsplit(lead['url']).hostname
                if lead['url'] in attempted_urls:continue
                attempted_urls.add(lead['url'])
                if host in denied_hosts:
                    record['attempts'].append({'url':lead['url'],'status':'provider_access_challenge_not_repeated'});continue
                if https_url(lead['url']) in {https_url(u) for u in target.get('previously_failed_urls',[])}:
                    record['attempts'].append({'url':lead['url'],'status':'unchanged_failed_route_skipped'});continue
                document=Document(id='primary-'+pmid+'-'+str(number),url=lead['url'],role='primary_paper',format='pdf',
                    identity=Identity(pmid=pmid,doi=next(iter(paper.get('doi',[])),None),title=paper['title']),
                    rationale='Approved public OA location; frozen assigned primary unchanged; original bytes still require exact identity checks.')
                try:
                    source=registrar(document,paper,output/'documents'/(paper['paper_id']+'-'+str(number)))
                    source['metadata_declared_source_version']=lead['version'];overrides[mid]={paper['paper_id']:source}
                    record.update(status='registered_primary_ready',registered_source=source);break
                except SOURCE_RECOVERY_ERRORS as exc:
                    record['attempts'].append({'url':lead['url'],'status':'retrieval_or_identity_hold','error':str(exc)})
                    if any(code in str(exc).lower() for code in ('403','401','429','access challenge','captcha','anubis')):denied_hosts.add(host)
            if record['status']!='registered_primary_ready':record['status']='public_primary_still_held'
        except SOURCE_RECOVERY_ERRORS as exc:
            record.update(status='public_primary_still_held',error=str(exc))
            if any(code in str(exc) for code in ('403','429','401')):provider_stopped=True
        except Exception as exc:
            record.update(status='recovery_execution_failed',failed_stage='oa_metadata_or_registration',error=repr(exc));checkpoint();raise
        checkpoint();time.sleep(1)
    result={'counts':dict(Counter(r['status'] for r in state.values())),
            'ready_methods':list(overrides),
            'model_calls':0,'fresh_review_required':True}
    bulk_screen.write(output/'RESULTS.json',result);return result


async def run(config_path,output):
    config=json.loads(Path(config_path).read_text());output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    if not 1<=config['agents']<=config['native_ceiling']<=50:raise ValueError('Invalid configured native workflow allocation')
    bulk_screen.write(output/'RESOLVED_CONFIG.json',config)
    records=bulk_screen.read(config['pool']['path'])
    for overlay in config['overlays']:records.update(bulk_screen.read(overlay['path'])['metadata_overlay'])
    excluded=planned_cohort_ids(config.get('exclude_planned_cohorts',[]))
    index,total_cache=cache_index(config['cache_collection']['path'],config['cache_collection']['sha256'])
    bulk_screen.write(output/'CACHE_INDEX.json',{'collection':config['cache_collection'],'entire_publication_records_checked':total_cache,
                     'article_records_by_pmid':index,'limits':'Registered records; bytes and bibliographic identity checked individually before reuse.'})
    acquire_root=output/'publications';acquire_root.mkdir();queue=asyncio.Queue();states={};seen=set();doi_owner={}
    last_checkpoint=0.0
    def checkpoint(force=False):
        nonlocal last_checkpoint
        if not force and time.monotonic()-last_checkpoint<10:return
        last_checkpoint=time.monotonic()
        bulk_screen.write(output/'ACQUISITION.json',{'papers':states,'counts':dict(Counter(r['status'] for r in states.values())),
          'method_admissions':0,'cache_publication_records_checked':total_cache})
    async def acquire_worker():
        while True:
            item=await queue.get()
            if item is None:queue.task_done();return
            pmid,row=item
            try:
                states[pmid]={'pmid':pmid,'status':'acquiring','screening':row};checkpoint()
                result=await asyncio.to_thread(acquire_publication,records[pmid],acquire_root/('pmid-'+pmid),index.get(pmid,[]))
                result={**result,'screening':row,'own_dois':records[pmid].get('identifiers',{}).get('doi',[]),
                        'metadata_sha256':bulk_screen.digest(records[pmid])}
                record_path=acquire_root/('pmid-'+pmid)/'PUBLICATION_RESULT.json';bulk_screen.write(record_path,result)
                states[pmid]={'pmid':pmid,'status':result['status'],'publication_record':str(record_path),'sha256':sha(record_path),
                              'classification':row['classification'],'publication_role':row.get('publication_role')}
            except Exception as exc:states[pmid]={'pmid':pmid,'status':'acquisition_failed','error':str(exc),'screening':row}
            finally:checkpoint();queue.task_done()
    workers=[asyncio.create_task(acquire_worker()) for _ in range(config['http_workers'])]
    async def handoff(rows):
        for row in rows:
            if row['route']!='acquisition_review_candidate' or row['pmid'] in seen:continue
            pmid=row['pmid'];seen.add(pmid);dois=[_doi(d) for d in records[pmid].get('identifiers',{}).get('doi',[])]
            conflicts=[doi_owner[d] for d in dois if d in doi_owner and doi_owner[d]!=pmid]
            if conflicts:
                states[pmid]={'pmid':pmid,'status':'duplicate_doi_identity_hold','other_pmids':conflicts,'screening':row};checkpoint();continue
            for d in dois:doi_owner[d]=pmid
            states[pmid]={'pmid':pmid,'status':'queued','screening':row};checkpoint();await queue.put((pmid,row))
    worker=live_pilot.AbstractReadOnlyWorker(executable=str(Path(os.environ['ATLAS_CODEX_BIN_DIR'])/'codex'),model='gpt-6.1-sol',effort='xhigh',
               timeout_seconds=1800,web_search=False,network_access=False,pin_shell_environment=True)
    screening=await bulk_screen.screen(config['pool']['path'],output/'screening',worker,pool_sha256=config['pool']['sha256'],
         overlays=[(o['path'],o['sha256']) for o in config['overlays']],agents=config['agents'],batch_size=config['batch_size'],
         max_prompt_bytes=config['max_prompt_bytes'],runtime_manifest=os.path.expandvars(config['runtime_manifest']),on_batch=handoff,exclude_pmids=excluded)
    await queue.join()
    for _ in workers:await queue.put(None)
    await asyncio.gather(*workers);checkpoint(force=True)
    result={'screening_counts':screening['counts'],'acquisition_counts':dict(Counter(r['status'] for r in states.values())),
            'native_allocation':config['agents'],'overall_native_ceiling':config['native_ceiling'],'method_admissions':0,
            'limits':'First-pass screening and publication retrieval only. Primary/protocol/support method associations remain reviewable; negatives and missing abstracts stay inspectable.'}
    bulk_screen.write(output/'RESULTS.json',result);return result


def main():
    if len(sys.argv)>1 and sys.argv[1]=='recover-primaries':
        parser=argparse.ArgumentParser(description='Zero-model exact assigned-primary recovery')
        parser.add_argument('--config',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args(sys.argv[2:])
        if not os.environ.get('SLURM_JOB_ID'):raise SystemExit('Recovery belongs in Qiu Slurm')
        print(json.dumps(recover_assigned_primaries(args.config,args.output),indent=2));return
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID') or os.environ.get('GROUP_SCRATCH')!='/scratch/groups/xiaojie':raise SystemExit('Run in Qiu Slurm')
    if os.environ.get('ATLAS_CODEX_ACCOUNT_LABEL')!='atlas-agent-runs':raise SystemExit('Use the project account helper')
    if os.environ.get('CODEX_HOME')!=str(Path(os.environ['GROUP_HOME'])/'mstojkov/private/codex-atlas-runs') or any(k in os.environ for k in ('OPENAI_API_KEY','CODEX_API_KEY')):
        raise SystemExit('Require the project browser login without API-key environment overrides')
    print(json.dumps(asyncio.run(run(args.config,args.output)),indent=2))


if __name__=='__main__':main()

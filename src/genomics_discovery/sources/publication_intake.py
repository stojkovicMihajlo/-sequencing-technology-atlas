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

from experiments.method_intake_audit_20261113 import bulk_screen
from experiments.method_intake_audit_20261113 import live_pilot
from genomics_discovery.illumina.sources import acquire_pmc_fulltext
from genomics_discovery.sources.acquisition.models import Identity
from genomics_discovery.sources.acquisition.pdf_registration import verify_pdf_text
from genomics_discovery.sources.acquisition.runtime import _identity,_normal,_doi
from genomics_discovery.sources.acquisition.html_identity import assess_primary_html
from scripts.illumina.technology_sources.article_identity import xml_identity
from scripts.illumina.technology_sources.recover_selected import acquire_one


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
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID') or os.environ.get('GROUP_SCRATCH')!='/scratch/groups/xiaojie':raise SystemExit('Run in Qiu Slurm')
    if os.environ.get('ATLAS_CODEX_ACCOUNT_LABEL')!='atlas-agent-runs':raise SystemExit('Use the project account helper')
    if os.environ.get('CODEX_HOME')!=str(Path(os.environ['GROUP_HOME'])/'mstojkov/private/codex-atlas-runs') or any(k in os.environ for k in ('OPENAI_API_KEY','CODEX_API_KEY')):
        raise SystemExit('Require the project browser login without API-key environment overrides')
    print(json.dumps(asyncio.run(run(args.config,args.output)),indent=2))


if __name__=='__main__':main()

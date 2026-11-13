"""Source-only metadata refresh over the pinned existing discovery pool.

Uses existing rate-guarded fetch/parse_pubmed; never downloads fulltext or runs
models. Writes only new receipts/metadata overlays, never the original 104MB pool.
"""
import argparse
import collections
from datetime import date,timedelta
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode

from genomics_discovery.illumina.sources import fetch,parse_pubmed


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    with Path(path).open('x') as handle:json.dump(value,handle,indent=2);handle.write('\n')
def canonical_doi(value):return re.sub(r'^https?://(?:dx\.)?doi\.org/','',value or '',flags=re.I).strip().casefold()


def query_identity(request):
    raw=json.dumps({k:request[k] for k in ('source_id','query','from_date','through_date')},sort_keys=True)
    label=re.sub(r'[^a-z0-9]+','-',request['source_id'].casefold()).strip('-')[:50]
    return label+'-'+hashlib.sha256(raw.encode()).hexdigest()[:20]


def metadata_batch(pmids,output,tag,*,fetcher=fetch):
    raw=output/(tag+'.xml')
    url='https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?'+urlencode({'db':'pubmed','id':','.join(pmids),'retmode':'xml','tool':'sequencing_technology_atlas'})
    receipt=fetcher(url,raw,timeout=75)
    if sha(raw)!=receipt['sha256']:raise ValueError('Raw metadata receipt hash mismatch')
    records=parse_pubmed(raw.read_bytes())
    if set(records)-set(pmids):raise ValueError('Metadata includes an unexpected own PMID')
    for pmid,record in records.items():
        if record['pmid']!=pmid:raise ValueError('Own PMID/key conflict')
        record.update(metadata_receipt=receipt,screening_status='not_yet_screened',technology_identity='not_assigned_by_search')
    write(output/(tag+'.receipt.json'),{'requested_pmids':pmids,'retrieved_pmids':sorted(records),
           'missing_pmids':sorted(set(pmids)-set(records)),'receipt':receipt})
    return records


def search(request,output,*,probe=False,fetcher=fetch):
    ident=query_identity(request);start=date.fromisoformat(request['from_date']);stop=date.fromisoformat(request['through_date'])
    query='('+request['query']+') AND ("'+start.strftime('%Y/%m/%d')+'"[Date - Publication] : "'+stop.strftime('%Y/%m/%d')+'"[Date - Publication])'
    path=output/(ident+'.json')
    receipt=fetcher('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?'+urlencode({
        'db':'pubmed','term':query,'retmode':'json','retmax':20 if probe else 9999,'sort':'pub_date','tool':'sequencing_technology_atlas'}),path,timeout=60)
    result=json.loads(path.read_text())['esearchresult'];ids=result.get('idlist',[]);count=int(result['count'])
    record={'request':request,'query_id':ident,'exact_query':query,'reported_count':count,'returned_count':len(ids),
            'pmids':ids,'translation':result.get('querytranslation'),'complete':count==len(ids),'probe':probe,'receipt':receipt}
    if not probe and count>len(ids) and start<stop:
        middle=start+(stop-start)//2
        left={**request,'through_date':middle.isoformat()};right={**request,'from_date':(middle+timedelta(days=1)).isoformat()}
        children=search(left,output,fetcher=fetcher)+search(right,output,fetcher=fetcher)
        record['split_into']=[r['query_id'] for r in children]
        return [record,*children]
    return [record]


def prepare_requests(plan,old_plan,old_query_results,*,probe=False):
    requests=list(plan['supplement_queries'])
    for q in old_plan['queries']:
        requests.append({'source_id':'delta-'+q['id'],'query':q['query'],
                         'from_date':'2026-11-06','through_date':'2026-11-13','kind':'date_delta'})
    for q in old_query_results:
        if 'error' not in q:continue
        # Use the exact failed source query before its old date clause. A new
        # hashed namespace avoids repeating the old TARGET-seq slug collision.
        term=q['query'].rsplit(' AND ("',1)[0]
        requests.append({'source_id':'repair-'+q['id'],'query':term,
                         'from_date':'2017-01-01','through_date':'2026-11-13','kind':'failed_query_repair'})
    if probe:
        # Metadata samples plus query counts only; not a complete search claim.
        return requests
    return requests


def refresh(inputs_path,plan_path,output,*,phase='probe',prior_overlay=None,fetcher=fetch):
    inputs=json.loads(Path(inputs_path).read_text());plan=json.loads(Path(plan_path).read_text())
    pool=Path(inputs['pool']['path'])
    if sha(pool)!=inputs['pool']['sha256']:raise ValueError('Existing discovery pool changed')
    base=json.loads(pool.read_text());old=pool.parent
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    (output/'raw_queries').mkdir();(output/'raw_metadata').mkdir()
    overlay={}
    if prior_overlay:
        prior=json.loads(Path(prior_overlay).read_text())
        if prior['base_pool']['sha256']!=inputs['pool']['sha256']:raise ValueError('Prior overlay belongs to another pool')
        for pmid,record in prior['metadata_overlay'].items():
            receipt=record['metadata_receipt'];path=Path(receipt['path'])
            if not path.is_file() or sha(path)!=receipt['sha256']:raise ValueError('Prior metadata original changed')
            overlay[pmid]=record
    missing=[p for p in inputs['missing_metadata_pmids'] if p not in base and p not in overlay]
    failures=[]
    # The historic 150-ID request received HTTP400. Deliberately use fresh
    # thirty-ID requests, preserving every result rather than retrying unchanged.
    for offset in range(0,len(missing),30):
        batch=missing[offset:offset+30];tag=f'repair-{offset//30+1:03d}'
        try:overlay.update(metadata_batch(batch,output/'raw_metadata',tag,fetcher=fetcher))
        except Exception as exc:failures.append({'stage':'metadata_repair','pmids':batch,'error':str(exc)})
    queries=[]
    for key in ('existing_search_plan','existing_query_results'):
        if sha(inputs[key]['path'])!=inputs[key]['sha256']:raise ValueError('Historic discovery input changed: '+key)
    old_plan=json.loads(Path(inputs['existing_search_plan']['path']).read_text())
    old_queries=json.loads(Path(inputs['existing_query_results']['path']).read_text())
    old_membership=collections.defaultdict(set)
    for q in old_queries:
        for pmid in q.get('pmids',[]):old_membership[pmid].add('historical:'+q['id'])
    for request in prepare_requests(plan,old_plan,old_queries):
        try:queries.extend(search(request,output/'raw_queries',probe=phase=='probe',fetcher=fetcher))
        except Exception as exc:failures.append({'stage':'search','request':request,'error':str(exc)})
    membership=collections.defaultdict(set)
    for q in queries:
        for pmid in q['pmids']:membership[pmid].add(q['query_id'])
    # Acquire four positive papers solely for query-recall diagnostics; direct
    # control retrieval is kept separate and never counts as a search hit.
    controls=plan['query_recall_positive_pmids'];needed=sorted((set(membership)|set(controls))-set(base)-set(overlay),key=int)
    for offset in range(0,len(needed),100):
        batch=needed[offset:offset+100];tag=f'new-{offset//100+1:04d}'
        try:overlay.update(metadata_batch(batch,output/'raw_metadata',tag,fetcher=fetcher))
        except Exception as exc:failures.append({'stage':'new_metadata','pmids':batch,'error':str(exc)})
    for pmid,record in overlay.items():record['discovered_by_queries']=sorted(set(record.get('discovered_by_queries',[]))|membership.get(pmid,set())|old_membership.get(pmid,set()))
    merged={**base,**overlay};doi_groups=collections.defaultdict(list)
    for pmid,record in merged.items():
        for value in record.get('identifiers',{}).get('doi',[]):doi_groups[canonical_doi(value)].append(pmid)
    duplicate_dois={d:sorted(set(v)) for d,v in doi_groups.items() if len(set(v))>1}
    cached={p['pmid']:p for p in inputs.get('cached_pool_articles',[])}
    queue=[{'publication_id':'pmid-'+pmid,'pmid':pmid,'own_dois':record.get('identifiers',{}).get('doi',[]),
            'metadata_locator':'base_pool' if pmid in base and pmid not in overlay else 'metadata_overlay',
            'abstract_status':'available' if any(a.get('text') for a in record.get('abstract',[])) else 'not_in_returned_metadata',
            'existing_article_records':cached.get(pmid,{}).get('article_files',[]),
            'acquisition_status':'reuse_cached_identity_review_needed' if pmid in cached else 'not_yet_acquired',
            'screening_status':'not_yet_screened','fulltext_download_authorized_by_this_job':False} for pmid,record in sorted(merged.items(),key=lambda x:int(x[0]))]
    write(output/'PAPER_SOURCE_QUEUE.json',{'base_pool':inputs['pool'],'papers':queue,'duplicate_doi_identity_holds':duplicate_dois,
          'boundary':'One row per own PMID; DOI collisions are explicit holds, not silent merges. Fulltext records require identity/hash reuse checks. No fulltext retrieval or model call.'})
    summary={'format_version':'discovery_metadata_overlay/1','phase':phase,'base_pool':inputs['pool'],
             'base_records':len(base),'metadata_overlay':overlay,'metadata_overlay_records':len(overlay),
             'combined_records':len(merged),'combined_with_abstract':sum(any(a.get('text') for a in r.get('abstract',[])) for r in merged.values()),
             'original_missing_metadata_still_missing':sorted(set(inputs['missing_metadata_pmids'])-set(merged)),
             'queries':queries,'failures':failures,'query_recall':[
                 {'pmid':pmid,'in_original_pool':pmid in base,'in_original_query_results':pmid in old_membership,
                  'in_new_search_results':pmid in membership,
                  'metadata_available':pmid in merged,'interpretation':'Probe results sample latest20 IDs; a sampled absence is not a recall miss.' if phase=='probe' else 'Full recorded-query membership; direct control fetch does not count.'} for pmid in controls],
             'fulltext_requests':0,'model_calls':0,'methods_admitted':0,
             'boundary':'Immutable overlay; probe mode deliberately samples search results and cannot establish complete query coverage.'}
    write(output/'OVERLAY.json',summary)
    return {k:summary[k] for k in ('phase','base_records','metadata_overlay_records','combined_records','combined_with_abstract','original_missing_metadata_still_missing','fulltext_requests','model_calls')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--phase',choices=['probe','expand'],default='probe')
    parser.add_argument('--prior-overlay',type=Path)
    args=parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):raise SystemExit('Source acquisition belongs in a Qiu Slurm allocation')
    print(json.dumps(refresh(args.inputs,args.plan,args.output,phase=args.phase,prior_overlay=args.prior_overlay),indent=2))

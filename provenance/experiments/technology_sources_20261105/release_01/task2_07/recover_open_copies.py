"""Find additional public copies of the still-missing assigned publications.

One unauthenticated Semantic Scholar metadata batch, plus recorded direct leads.
Metadata does not admit a source; the frozen PDF identity/body checks do that.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from urllib.parse import urlencode

from scripts.illumina.technology_sources.article_identity import normalized_title
from scripts.illumina.technology_sources.inventory import read, sha
from scripts.illumina.technology_sources.materialize import write
from scripts.illumina.technology_sources.review_papers import prepare


def metadata_matches(record, paper):
    ids = record.get('externalIds') or {}
    pmid = str(ids.get('PubMed') or '')
    doi = str(ids.get('DOI') or '').casefold()
    expected = {d.casefold() for d in paper.get('metadata', {}).get('identifiers', {}).get('doi', [])}
    title = paper.get('metadata', {}).get('title') or paper.get('title') or ''
    if pmid and pmid != str(paper.get('pmid')):
        return False
    if doi and expected and doi not in expected:
        return False
    return (bool((pmid and pmid == str(paper.get('pmid'))) or (doi and doi in expected)) and
            bool(title) and normalized_title(record.get('title')) == normalized_title(title))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ['source-root', 'output', 'decisions', 'bindings', 'targets', 'direct-leads',
              'records', 'recovery-module']:
        p.add_argument('--' + n, required=True, type=Path)
    a = p.parse_args(); a.records.mkdir(exist_ok=False)
    prepare(a.source_root, a.output, read(a.decisions), read(a.bindings))
    state = read(a.output / 'collection.json')
    rows = read(a.targets)['targets']
    targets = [r for r in rows if r['status'] != 'source_identity_hold' and
               state['papers'][r['paper_id']]['status'] != 'fulltext_acquired']
    if len(targets) > 100 or len({r['paper_id'] for r in targets}) != len(targets):
        raise ValueError('Expected a bounded, unique assigned-publication list')
    area = a.output / 'acquisition_open_copies_07'; area.mkdir(exist_ok=False)
    previous = read(a.source_root / 'REPOSITORY_RECOVERY_RECEIPTS.json')
    attempted = {x.get('url') for rs in previous.values() for x in rs if x.get('url')}
    request, response = area/'metadata-request.json', area/'metadata-response.json'
    write(request, {'ids': ['PMID:' + state['papers'][r['paper_id']]['pmid'] for r in targets]})
    url = 'https://api.semanticscholar.org/graph/v1/paper/batch?' + urlencode({
        'fields': 'title,externalIds,openAccessPdf,isOpenAccess'})
    query = {'url': url, 'request_sha256': sha(request), 'retries': 0}
    try:
        result = subprocess.run(['curl','--silent','--show-error','--location','--proto','=https',
            '--proto-redir','=https','--max-redirs','5','--max-time','55','--request','POST',
            '--header','Content-Type: application/json','--data-binary','@'+str(request),
            '--output',str(response),'--write-out','%{http_code}',url],
            check=True,capture_output=True,timeout=65)
        query['http_status'] = int(result.stdout)
        query['response_sha256'] = sha(response)
        if query['http_status'] != 200:
            raise ValueError('Metadata endpoint returned HTTP'+str(query['http_status'])+'; no retries')
        records = read(response)
        if not isinstance(records,list) or len(records)!=len(targets):
            raise ValueError('Metadata batch response cardinality differs from requested publications')
    except Exception as exc:
        records = [None]*len(targets);query['error']=str(exc)
    write(a.records/'METADATA_QUERY_RECEIPT.json',query)
    spec=importlib.util.spec_from_file_location('frozen_pdf_recovery',a.recovery_module)
    recovery=importlib.util.module_from_spec(spec);spec.loader.exec_module(recovery)
    direct=read(a.direct_leads);receipts={}
    for row,record in zip(targets,records):
        pid=row['paper_id'];paper=state['papers'][pid]
        folder=area/pid;folder.mkdir()
        candidates=list(direct.get(pid,[]));attempts=[]
        if record:
            match=metadata_matches(record,paper)
            attempts.append({'stage':'semantic_scholar_metadata','identity_matches':match,
                             'record':record,'admitted':False})
            pdf=record.get('openAccessPdf') or {}
            if match and pdf.get('url'):
                candidates.append({'url':pdf['url'],'discovery':'Semantic Scholar openAccessPdf; metadata identity checked'})
        for n,candidate in enumerate(candidates):
            if paper['status']=='fulltext_acquired':break
            if candidate['url'] in attempted:
                attempts.append({'stage':'unchanged_url_skipped','url':candidate['url'],'admitted':False});continue
            attempt=recovery.recover_pdf(a.output,state,paper,candidate['url'],folder,n)
            attempt['discovery']=candidate;attempts.append(attempt)
            if attempt['admitted'] and candidate.get('repository_record'):
                source=folder/('candidate-'+str(n)+'.pdf')
                expected=candidate['repository_record']['checkSum']
                if expected['checkSumAlgorithm']!='MD5' or hashlib.md5(source.read_bytes()).hexdigest()!=expected['value']:
                    raise ValueError('Repository bitstream checksum differs; do not publish')
        receipts[pid]=attempts or [{'stage':'no_new_public_copy_returned','admitted':False}]
        write(a.output/'REPOSITORY_RECOVERY_RECEIPTS.json',receipts)
        write(a.output/'collection.json',state)
        print(pid,paper['status'],flush=True)
    write(a.records/'RECOVERY_SUMMARY.json',{'targets':len(targets),
        'new_papers_acquired':sum(any(x.get('admitted') for x in rs) for rs in receipts.values()),
        'metadata_query':query,'new_model_calls':0,'task3_started':False})


if __name__=='__main__':main()

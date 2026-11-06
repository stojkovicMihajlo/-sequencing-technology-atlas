"""Look up remaining assigned DOIs through the public Unpaywall API.

This writes candidate URLs, not acquired publications. The existing frozen PDF
checks subsequently validate the files. Submitted preprints are not substitutes.
"""
import argparse
import csv
import importlib.util
from pathlib import Path
import time
from urllib.parse import quote, urlencode

from genomics_discovery.illumina.sources import fetch, https_url
from scripts.illumina.technology_sources.inventory import read, sha
from scripts.illumina.technology_sources.materialize import write


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['source-root','previous-view','output','match-module']:
        p.add_argument('--'+n,required=True,type=Path)
    p.add_argument('--email',required=True)
    a=p.parse_args();a.output.mkdir(exist_ok=False)
    state=read(a.source_root/'collection.json')
    rows=list(csv.DictReader((a.previous_view/'MANUAL_PAPER_RECOVERY.csv').open()))
    if len(rows)>100 or len({r['paper_id'] for r in rows})!=len(rows):
        raise ValueError('Expected a bounded unique assigned-publication list')
    spec=importlib.util.spec_from_file_location('frozen_metadata_match',a.match_module)
    match=importlib.util.module_from_spec(spec);spec.loader.exec_module(match)
    previous=read(a.source_root/'REPOSITORY_RECOVERY_RECEIPTS.json')
    seen={https_url(r['url']) for rs in previous.values() for r in rs if r.get('url')}
    records=[];stop=False
    for row in rows:
        pid=row['paper_id'];paper=state['papers'][pid];row['public_pdf_candidates']=[]
        dois=paper.get('metadata',{}).get('identifiers',{}).get('doi',[])
        record={'paper_id':pid,'candidate_urls':[],'source_admitted':False}
        if not dois or stop:
            record['status']='no_assigned_doi' if not dois else 'provider_unavailable_no_repeated_requests'
            records.append(record);continue
        base='https://api.unpaywall.org/v2/'+quote(dois[0],safe='/')
        destination=a.output/(pid+'.json')
        try:
            receipt=fetch(base+'?'+urlencode({'email':a.email}),destination,timeout=40)
            receipt['request_url']=base+'?email=<project-contact>'
            result=read(destination)
            own={'title':result.get('title'),'externalIds':{'DOI':result.get('doi')}}
            if not match.metadata_matches(own,paper):
                raise ValueError('Unpaywall own DOI/title differ from assigned publication')
            record.update(status='metadata_identity_matches',receipt=receipt,
                          response_sha256=sha(destination),is_oa=result.get('is_oa'))
            for location in result.get('oa_locations',[]):
                url=location.get('url_for_pdf')
                if url:url=https_url(url)
                if not url or url in seen or location.get('version')=='submittedVersion':continue
                # PMC systematic retrieval must use its supported APIs; direct
                # website PDF links are not a substitute for the failed API route.
                if 'ncbi.nlm.nih.gov' in url or 'europepmc.org/articles/' in url:continue
                if url not in row['public_pdf_candidates']:row['public_pdf_candidates'].append(url)
                record['candidate_urls'].append({'url':url,'version':location.get('version'),
                                                 'location':location})
            print(pid,'new_public_pdf_candidates',len(row['public_pdf_candidates']),flush=True)
        except Exception as exc:
            record.update(status='metadata_not_available',error=str(exc))
            # Do not hammer an unavailable or rate-limited provider.
            if any(s in str(exc) for s in ['403','429','401']):stop=True
        records.append(record)
        write(a.output/'DISCOVERY_RECEIPTS.json',records)
        time.sleep(1)  # Provider request cadence, not scheduler runtime padding.
    write(a.output/'DISCOVERY_RECEIPTS.json',records)
    write(a.output/'remaining_papers.json',{'targets':rows,'scope':'Only still-missing assigned Illumina publications.'})
    write(a.output/'no_repeated_pmc_routes.json',{'records':[]})
    write(a.output/'SUMMARY.json',{'assigned_publications':len(rows),
        'new_pdf_candidate_urls':sum(len(r['public_pdf_candidates']) for r in rows),
        'publications_with_new_candidates':sum(bool(r['public_pdf_candidates']) for r in rows),
        'metadata_provider_stopped':stop,'new_model_calls':0,'task3_started':False})


if __name__=='__main__':main()

"""Collect public copies of assigned papers; preserve every attempted URL.

OpenAlex supplies candidate locations, not evidence of article identity. PDF
title and DOI must be present in its front matter before it is admitted.
"""
import argparse
from html import unescape
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

from genomics_discovery.illumina.sources import fetch
from scripts.illumina.technology_sources.article_identity import normalized_title
from scripts.illumina.technology_sources.inventory import read, sha
from scripts.illumina.technology_sources.materialize import write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--targets', type=Path, required=True)
    p.add_argument('--holds', type=Path, required=True)
    p.add_argument('--additional-urls', type=Path, required=True)
    p.add_argument('--attempt-name', default='acquisition_repositories',
                   help='New acquisition subdirectory; use a fresh name when the source vault retains older attempts.')
    a = p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', a.attempt_name):
        p.error('--attempt-name must be one safe directory name')
    state = read(a.root/'collection.json')
    held = {h['paper_id'] for h in read(a.holds)['holds']}
    additions = read(a.additional_urls)
    targets = read(a.targets)
    directory = a.root/a.attempt_name
    directory.mkdir(exist_ok=False)
    receipts = {}
    for n, pid in enumerate(targets, 1):
        if pid not in state['papers'] or pid in held:
            continue
        paper = state['papers'][pid]
        if paper['status'] == 'fulltext_acquired':
            continue
        meta = paper.get('metadata', {})
        title = meta.get('title') or paper.get('title') or ''
        extra = additions.get(pid, [])
        dois = meta.get('identifiers', {}).get('doi', [])
        if isinstance(extra, dict) and not dois:
            dois = [extra['doi']] if extra.get('doi') else []
        area = directory/pid
        area.mkdir()
        attempts = []
        urls = list(extra.get('urls', [])) if isinstance(extra, dict) else list(extra)
        if dois:
            try:
                crossref = area/'crossref.json'
                fetch('https://api.crossref.org/works/' + quote(dois[0], safe=''), crossref, timeout=35)
                record = read(crossref)['message']
                if record.get('DOI','').lower()!=dois[0].lower() or not any(
                    normalized_title(t)==normalized_title(title) for t in record.get('title',[])):
                    raise ValueError('Crossref own title/DOI differ')
                urls.extend(link['URL'] for link in record.get('link',[])
                            if link.get('content-type')=='application/pdf')
                attempts.append({'stage':'publisher_deposited_crossref_links','url_count':len(urls)})
            except Exception as exc:
                attempts.append({'stage':'publisher_deposited_crossref_links','error':str(exc)})
            try:
                api = area/'openalex.json'
                receipt = fetch('https://api.openalex.org/works/https://doi.org/' +
                    quote(dois[0], safe='/') + '?select=id,title,doi,open_access,locations', api, timeout=35)
                record = read(api)
                if normalized_title(record['title']) != normalized_title(title):
                    raise ValueError('OpenAlex title differs; candidate locations not admitted')
                for location in record.get('locations', []):
                    if location.get('pdf_url'):
                        urls.append(location['pdf_url'])
                    elif location.get('is_oa') and location.get('landing_page_url'):
                        landing = location['landing_page_url']
                        if '/pmc' in landing.lower() or 'pmc.ncbi.nlm.nih.gov' in landing:
                            continue  # PMC systematic retrieval uses its official APIs only.
                        try:
                            dest = area/('landing-'+str(len(attempts))+'.html')
                            fetch(landing, dest, timeout=30)
                            html = dest.read_text(errors='replace')
                            for tag in re.findall(r'<meta\s+[^>]*>', html, re.I):
                                attrs = dict(re.findall(r'([\w:-]+)\s*=\s*["\x27](.*?)["\x27]', tag))
                                if attrs.get('name','').lower() == 'citation_pdf_url':
                                    urls.append(unescape(attrs.get('content','')))
                            attempts.append({'stage':'repository_landing','url':landing})
                        except Exception as exc:
                            attempts.append({'stage':'repository_landing','url':landing,'error':str(exc)})
                attempts.append({'stage':'openalex_locations','receipt':receipt})
            except Exception as exc:
                attempts.append({'stage':'openalex_locations','error':str(exc)})
        acquired = False
        for i, url in enumerate(dict.fromkeys(urls)):
            try:
                pdf = area/('candidate-'+str(i)+'.pdf')
                receipt = fetch(url, pdf, timeout=45)
                if not pdf.read_bytes().startswith(b'%PDF-'):
                    raise ValueError('Candidate is not a PDF')
                front = subprocess.run(['pdftotext','-f','1','-l','2',str(pdf),'-'],
                    check=True,capture_output=True,timeout=30).stdout.decode(errors='replace')
                if not title or normalized_title(title) not in normalized_title(front):
                    raise ValueError('Assigned title absent from PDF front matter')
                if dois and not any(d.lower() in front.lower() for d in dois):
                    raise ValueError('Assigned DOI absent from PDF front matter; manual identity review required')
                text = area/'reading-text.txt'
                subprocess.run(['pdftotext',str(pdf),str(text)],check=True,capture_output=True,timeout=45)
                if len(text.read_text()) < 2000:
                    raise ValueError('No substantial article text')
                for path, kind in [(pdf,'article'),(text,'reading-text')]:
                    item = {'path':str(path.relative_to(a.root)),'sha256':sha(path),'kind':kind}
                    paper['files'].append(item)
                    state['files'][item['path']] = {**item,'bytes':path.stat().st_size,
                        'origins':[{'paper_id':pid,'receipt':receipt,'identity_check':'Assigned title and DOI in PDF front matter'}]}
                paper['status'] = 'fulltext_acquired'
                if isinstance(extra, dict) and extra.get('doi'):
                    paper.setdefault('metadata', {}).setdefault('identifiers', {})['doi'] = [extra['doi']]
                    paper['identifier_confirmation'] = {'source':extra.get('identity_source'),
                        'check':'Assigned title and DOI confirmed in downloaded PDF front matter'}
                attempts.append({'stage':'article_pdf','url':url,'admitted':True,'receipt':receipt})
                acquired = True
                break
            except Exception as exc:
                attempts.append({'stage':'article_pdf','url':url,'admitted':False,'error':str(exc)})
        paper['repository_recovery_status'] = 'acquired' if acquired else 'not_recovered_from_recorded_public_candidates'
        receipts[pid] = attempts
        write(a.root/'shared/papers'/pid/'paper.json',paper)
        write(a.root/'collection.json',state)
        write(a.root/'REPOSITORY_RECOVERY_RECEIPTS.json',receipts)
        print(f'Assigned repository recovery {n}/{len(targets)}: {pid} {paper["repository_recovery_status"]}',flush=True)


if __name__ == '__main__':
    main()

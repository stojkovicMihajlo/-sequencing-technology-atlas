"""Acquire only assigned method papers through public metadata and article APIs.

Bibliographic identity and an article body are required before an XML/PDF is
presented as an acquired paper. An inaccessible source remains a citation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from html import unescape
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from genomics_discovery.illumina.sources import fetch, jats_text
from .article_identity import normalized_title, xml_identity
from .inventory import sha
from .materialize import write


def acquire_one(root, paper):
    pid, pmid = paper['id'], paper.get('pmid')
    folder = root / 'acquisition_selected' / pid
    folder.mkdir(parents=True, exist_ok=False)
    receipts, candidates = [], []
    if not pmid:
        return pid, [], [{'status': 'no_pmid_for_metadata_route'}]
    meta = paper.get('metadata', {})
    expected_dois = meta.get('identifiers', {}).get('doi', [])
    try:
        path = folder / 'europepmc-metadata.json'
        receipt = fetch('https://www.ebi.ac.uk/europepmc/webservices/rest/search?' + urlencode({
            'query': 'EXT_ID:' + pmid + ' AND SRC:MED', 'format': 'json', 'resultType': 'core'}), path, timeout=35)
        results = json.loads(path.read_text()).get('resultList', {}).get('result', [])
        record = next((r for r in results if str(r.get('id')) == pmid and r.get('source') == 'MED'), None)
        if record is None:
            raise ValueError('Expected publication not returned')
        if normalized_title(record['title']) != normalized_title(meta.get('title') or paper.get('title') or ''):
            raise ValueError('Metadata title disagreement; no automatic reassignment')
        receipts.append({'stage': 'europepmc_metadata', **receipt})
        pmcid = record.get('pmcid')
        if pmcid:
            candidates.append(('xml', 'https://www.ebi.ac.uk/europepmc/webservices/rest/' + pmcid + '/fullTextXML'))
        for r in record.get('fullTextUrlList', {}).get('fullTextUrl', []):
            if str(r.get('documentStyle', '')).lower() == 'pdf' and str(r.get('availability', '')).lower() in {'open access', 'free'}:
                candidates.append(('pdf', r['url']))
    except Exception as exc:
        receipts.append({'stage': 'europepmc_metadata', 'error': str(exc)})
    if expected_dois:
        doi = expected_dois[0]
        try:
            path = folder / 'publisher.html'
            receipt = fetch('https://doi.org/' + doi, path, timeout=35)
            html = path.read_text(errors='replace')
            matches = re.findall(r'<meta\s+[^>]*>', html, re.I)
            attributes = {}
            for tag in matches:
                pairs = dict(re.findall(r'([\w:-]+)\s*=\s*[\"\x27](.*?)[\"\x27]', tag, re.S))
                name = pairs.get('name') or pairs.get('property')
                if name:
                    attributes[name.lower()] = unescape(pairs.get('content', ''))
            if attributes.get('citation_doi', '').lower() == doi.lower() and attributes.get('citation_pdf_url', '').startswith('https://'):
                candidates.append(('pdf', attributes['citation_pdf_url']))
            receipts.append({'stage': 'publisher_landing_page', **receipt,
                             'article_identity_confirmed': attributes.get('citation_doi', '').lower() == doi.lower()})
        except Exception as exc:
            receipts.append({'stage': 'publisher_landing_page', 'error': str(exc)})
    for n, (kind, url) in enumerate(dict.fromkeys(candidates)):
        try:
            path = folder / ('candidate-' + str(n) + '.' + kind)
            receipt = fetch(url, path, timeout=40)
            raw = path.read_bytes()
            if kind == 'xml':
                check = xml_identity(raw, paper)
                if check['status'] != 'bibliographic_identity_corresponds' or not check['has_body']:
                    receipts.append({'stage': 'article_candidate', **receipt, 'identity': check, 'admitted': False})
                    continue
                text, blocks = jats_text(raw)
                tp, bp = folder / 'reading.txt', folder / 'blocks.json'
                tp.write_text(text)
                write(bp, {'article_sha256': sha(path), 'blocks': blocks})
                files = [(path, 'article'), (tp, 'reading-text'), (bp, 'blocks')]
            else:
                if not raw.startswith(b'%PDF-') or not shutil.which('pdftotext'):
                    raise ValueError('Not a PDF, or no PDF reader')
                front = subprocess.run(['pdftotext', '-f', '1', '-l', '2', str(path), '-'], check=True, capture_output=True, timeout=30).stdout.decode(errors='replace')
                expected = meta.get('title') or paper.get('title') or ''
                if not expected or normalized_title(expected) not in normalized_title(front):
                    receipts.append({'stage': 'article_candidate', **receipt, 'admitted': False, 'reason': 'PDF title not confirmed'})
                    continue
                tp = folder / 'reading.txt'
                subprocess.run(['pdftotext', str(path), str(tp)], check=True, capture_output=True, timeout=50)
                files = [(path, 'article'), (tp, 'reading-text')]
            receipts.append({'stage': 'article_candidate', **receipt, 'admitted': True,
                             'validation': 'Own article identifiers/title/body for XML; first-two-page title for PDF. No scientific content review.'})
            return pid, files, receipts
        except Exception as exc:
            receipts.append({'stage': 'article_candidate', 'url': url, 'error': str(exc), 'admitted': False})
    return pid, [], receipts


def recover_selected(root, holds):
    root = Path(root)
    state = json.loads((root / 'collection.json').read_text())
    held_pids = {h['paper_id'] for h in holds.get('holds', [])}
    targets = [p for pid, p in state['papers'].items() if p.get('status') != 'fulltext_acquired' and pid not in held_pids]
    receipts = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for n, (pid, files, attempts) in enumerate(pool.map(lambda p: acquire_one(root, p), targets), 1):
            paper = state['papers'][pid]
            for path, kind in files:
                target = root / 'shared/papers' / pid / ('selected-recovered-' + kind + path.suffix)
                target.write_bytes(path.read_bytes())
                item = {'path': str(target.relative_to(root)), 'sha256': sha(target), 'kind': kind}
                paper['files'].append(item)
                state['files'][item['path']] = {**item, 'bytes': target.stat().st_size, 'origins': [{'paper_id': pid, 'basis': 'public_method_paper_recovery'}]}
            if files:
                paper['status'] = 'fulltext_acquired'
            paper['selected_recovery_status'] = 'acquired' if files else 'unavailable_through_attempted_public_routes'
            receipts[pid] = attempts
            write(root / 'shared/papers' / pid / 'paper.json', paper)
            write(root / 'collection.json', state)
            write(root / 'SELECTED_RECOVERY_RECEIPTS.json', receipts)
            print(f'Selected method paper {n}/{len(targets)}: {pid} {paper["selected_recovery_status"]}', flush=True)
    return receipts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--holds', required=True, type=Path)
    a = p.parse_args()
    recover_selected(a.root, json.loads(a.holds.read_text()))


if __name__ == '__main__':
    main()

"""Recover assigned publications from NCBI's current public article dataset.

Reference: https://pmc-oa-opendata.s3.amazonaws.com/README.txt (5 Nov 2026).
No credentials, speculative paper assignments, legacy FTP packages or model calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs
import xml.etree.ElementTree as ET

from genomics_discovery.illumina.sources import fetch, jats_text
from .article_identity import normalized_title, xml_identity
from .inventory import read, sha
from .materialize import write


BASE = 'https://pmc-oa-opendata.s3.amazonaws.com/'


def metadata_matches(record, paper):
    expected = paper.get('metadata', {})
    title = expected.get('title') or paper.get('title') or ''
    if not title or normalized_title(record.get('title', '')) != normalized_title(title):
        raise ValueError('Cloud article title does not match assigned publication')
    if str(record.get('pmid')) != str(paper['pmid']):
        raise ValueError('Cloud article own PMID does not match assigned publication')
    dois = {d.lower() for d in expected.get('identifiers', {}).get('doi', [])}
    if dois and str(record.get('doi', '')).lower() not in dois:
        raise ValueError('Cloud article DOI does not match assigned publication')
    if record.get('is_retracted') == 'yes':
        raise ValueError('Retracted article requires explicit source review')


def download(url, path):
    if not url.startswith(BASE):
        raise ValueError('Expected an official NCBI article-dataset URL')
    receipt = fetch(url, path, timeout=45)
    expected = parse_qs(urlsplit(url).query).get('md5', [])
    if expected and hashlib.md5(path.read_bytes()).hexdigest() != expected[0]:
        raise ValueError('NCBI object MD5 differs from metadata')
    return receipt


def recover(root, holds):
    root = Path(root)
    state = read(root / 'collection.json')
    held = {h['paper_id'] for h in holds.get('holds', [])}
    targets = {pid: p for pid, p in state['papers'].items()
               if p['status'] != 'fulltext_acquired' and pid not in held and p.get('pmid')}
    area = root / 'acquisition_cloud'
    area.mkdir(exist_ok=False)
    ids = sorted({p['pmid'] for p in targets.values()})
    if len(ids) > 200:
        raise ValueError('This bounded assigned-paper recovery accepts at most200publications')
    response = area / 'id-converter.json'
    fetch('https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/?' + urlencode({
        'ids': ','.join(ids), 'idtype': 'pmid', 'format': 'json', 'versions': 'yes',
        'tool': 'sequencing-technology-atlas'}), response, timeout=45)
    mapping = {str(r.get('requested-id')): r for r in read(response).get('records', [])}
    receipts = {}
    for number, (pid, paper) in enumerate(targets.items(), 1):
        attempts, acquired = [], False
        folder = area / pid
        folder.mkdir()
        try:
            pmcid = mapping.get(paper['pmid'], {}).get('pmcid')
            if not pmcid:
                raise ValueError('NCBI ID converter returned no PMC article for this assigned PMID')
            listing = folder / 'versions.xml'
            download(BASE + '?' + urlencode({'list-type': '2', 'prefix': pmcid + '.',
                                            'delimiter': '/'}), listing)
            tree = ET.fromstring(listing.read_bytes())
            prefixes = [e.text for e in tree.findall('{*}CommonPrefixes/{*}Prefix')]
            for prefix in prefixes:
                try:
                    version = prefix.rstrip('/')
                    metadata_path = folder / (version + '.json')
                    download(BASE + prefix + version + '.json', metadata_path)
                    metadata = read(metadata_path)
                    metadata_matches(metadata, paper)
                    xml = folder / (version + '.xml')
                    article_receipt = download(metadata['xml_url'], xml)
                    identity = xml_identity(xml.read_bytes(), paper)
                    if identity['status'] != 'bibliographic_identity_corresponds' or not identity['has_body']:
                        raise ValueError('Article body/title/own identifiers fail identity check: ' + str(identity))
                    text, blocks = jats_text(xml.read_bytes())
                    if len(text) < 1000:
                        raise ValueError('No substantive full article text')
                    text_path = folder / (version + '.txt')
                    text_path.write_text(text)
                    block_path = folder / (version + '-blocks.json')
                    write(block_path, {'article_sha256': sha(xml), 'blocks': blocks})
                    files = [(xml, 'article'), (text_path, 'reading-text'),
                             (block_path, 'blocks'), (metadata_path, 'article-dataset-metadata')]
                    # Retain dataset version/type rather than choosing the highest
                    # number as if it were the latest scientific method version.
                    paper['dataset_version'] = metadata
                    pdf_url = metadata.get('pdf_url')
                    if pdf_url:
                        try:
                            pdf = folder / (version + '.pdf')
                            download(pdf_url, pdf)
                            if not pdf.read_bytes().startswith(b'%PDF-'):
                                raise ValueError('Dataset PDF is not a PDF')
                            files.append((pdf, 'article'))
                        except Exception as exc:
                            attempts.append({'stage': 'optional_pdf', 'error': str(exc)})
                    for source, kind in files:
                        item = {'path': str(source.relative_to(root)), 'sha256': sha(source), 'kind': kind}
                        paper['files'].append(item)
                        state['files'][item['path']] = {**item, 'bytes': source.stat().st_size,
                            'origins': [{'paper_id': pid, 'article_dataset_receipt': article_receipt}]}
                    paper['status'] = 'fulltext_acquired'
                    attempts.append({'status': 'acquired', 'version': version, 'identity': identity,
                                     'license_code': metadata.get('license_code'),
                                     'media_urls_retained_in_metadata': metadata.get('media_urls', [])})
                    acquired = True
                    break
                except Exception as exc:
                    attempts.append({'version': prefix, 'error': str(exc), 'admitted': False})
            if not prefixes:
                attempts.append({'status': 'no_distributable_article_version', 'pmcid': pmcid})
        except Exception as exc:
            attempts.append({'status': 'not_acquired', 'error': str(exc)})
        paper['cloud_recovery_status'] = 'acquired' if acquired else 'unavailable_through_attempted_ncbi_cloud_route'
        write(root / 'shared/papers' / pid / 'paper.json', paper)
        receipts[pid] = attempts
        write(root / 'collection.json', state)
        write(root / 'CLOUD_RECOVERY_RECEIPTS.json', receipts)
        print(f'Assigned paper {number}/{len(targets)}: {pid} {paper["cloud_recovery_status"]}', flush=True)
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--holds', type=Path, required=True)
    args = parser.parse_args()
    recover(args.root, read(args.holds))


if __name__ == '__main__':
    main()

"""Recover only assigned missing publications into a fresh source snapshot.

Reuse the frozen source-library implementation. No model calls or new methods.
"""
import argparse
from pathlib import Path
import re
import subprocess
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from genomics_discovery.illumina.sources import fetch, jats_text
from scripts.illumina.technology_sources.article_identity import normalized_title, xml_identity
from scripts.illumina.technology_sources.inventory import read, sha
from scripts.illumina.technology_sources.materialize import write
from scripts.illumina.technology_sources.review_papers import prepare


def register(root, state, paper, files, receipt):
    for path, kind in files:
        item = {'path': str(path.relative_to(root)), 'kind': kind, 'sha256': sha(path)}
        paper['files'].append(item)
        state['files'][item['path']] = {**item, 'bytes': path.stat().st_size,
                                       'origins': [{'paper_id': paper['id'], 'receipt': receipt}]}
    paper['status'] = 'fulltext_acquired'
    write(root / 'shared/papers' / paper['id'] / 'paper.json', paper)


def recover_xml(root, state, paper, pmcid, folder):
    url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?' + urlencode({
        'db': 'pmc', 'id': pmcid.removeprefix('PMC'), 'retmode': 'xml'})
    attempt = {'stage': 'ncbi_pmc_eutils', 'url': url, 'admitted': False}
    try:
        raw = folder / 'api-response.xml'
        receipt = fetch(url, raw, timeout=45)
        attempt['response'] = receipt
        tree = ET.fromstring(raw.read_bytes())
        articles = [tree] if tree.tag.rsplit('}', 1)[-1] == 'article' else [
            n for n in tree if n.tag.rsplit('}', 1)[-1] == 'article']
        if len(articles) != 1:
            raise ValueError('Expected one full article; metadata/error responses are not full text')
        article = folder / 'article.xml'
        article.write_bytes(ET.tostring(articles[0], encoding='utf-8', xml_declaration=True))
        identity = xml_identity(article.read_bytes(), paper)
        if identity['status'] != 'bibliographic_identity_corresponds' or not identity['has_body']:
            raise ValueError('Own title/identifiers/body fail identity check: ' + str(identity))
        text, blocks = jats_text(article.read_bytes())
        if len(text) < 2000:
            raise ValueError('No substantial article text')
        tp, bp = folder / 'reading-text.txt', folder / 'blocks.json'
        tp.write_text(text)
        write(bp, {'article_sha256': sha(article), 'blocks': blocks})
        register(root, state, paper, [(raw, 'original-api-response'), (article, 'article'),
                                      (tp, 'reading-text'), (bp, 'blocks')], receipt)
        attempt.update(admitted=True, identity=identity)
    except Exception as exc:
        attempt['error'] = str(exc)
    return attempt


def recover_pdf(root, state, paper, url, folder, number):
    attempt = {'stage': 'article_pdf', 'url': url, 'admitted': False}
    try:
        pdf = folder / ('candidate-' + str(number) + '.pdf')
        receipt = fetch(url, pdf, timeout=55)
        attempt['response'] = receipt
        if not pdf.read_bytes().startswith(b'%PDF-'):
            raise ValueError('Response is not a PDF')
        front = subprocess.run(['pdftotext', '-f', '1', '-l', '2', str(pdf), '-'],
            check=True, capture_output=True, timeout=40).stdout.decode(errors='replace')
        title = paper.get('metadata', {}).get('title') or paper.get('title') or ''
        dois = paper.get('metadata', {}).get('identifiers', {}).get('doi', [])
        if not title or normalized_title(title) not in normalized_title(front):
            raise ValueError('Assigned title absent from PDF front matter; retain for identity review')
        if not dois or not any(d.casefold() in front.casefold() for d in dois):
            raise ValueError('Assigned DOI absent from PDF front matter; retain for identity review')
        text = folder / ('candidate-' + str(number) + '.txt')
        subprocess.run(['pdftotext', str(pdf), str(text)], check=True, capture_output=True, timeout=50)
        if len(text.read_text()) < 2000:
            raise ValueError('No substantial article text')
        register(root, state, paper, [(pdf, 'article'), (text, 'reading-text')], receipt)
        attempt.update(admitted=True, identity='Assigned title and DOI in first two pages; substantial article text')
    except Exception as exc:
        attempt['error'] = str(exc)
    return attempt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ['source-root', 'output', 'decisions', 'bindings', 'targets', 'prior-mapping', 'records']:
        p.add_argument('--' + n, required=True, type=Path)
    a = p.parse_args()
    a.records.mkdir(exist_ok=False)
    prepare(a.source_root, a.output, read(a.decisions), read(a.bindings))
    state = read(a.output / 'collection.json')
    targets = read(a.targets)['targets']
    if len({r['paper_id'] for r in targets}) != len(targets) or len(targets) > 100:
        raise ValueError('Expected a bounded unique assigned-publication list')
    if any(r['paper_id'] not in state['papers'] for r in targets):
        raise ValueError('Target publication is not in the assigned source collection')
    area = a.output / 'acquisition_remaining_06'
    area.mkdir(exist_ok=False)
    mapping = {str(r.get('requested-id')): r for r in read(a.prior_mapping)['records']}
    previous = read(a.source_root / 'REPOSITORY_RECOVERY_RECEIPTS.json')
    attempted_urls = {x.get('url') for attempts in previous.values() for x in attempts if x.get('url')}
    receipts, tested = {}, set()
    pilots = ['pmid-22143764', 'pmid-23345452', 'pmid-24248345']
    pilot_admissions = 0
    ordered = sorted(targets, key=lambda r: (r['paper_id'] not in pilots, r['paper_id']))
    for row in ordered:
        pid = row['paper_id']; paper = state['papers'][pid]
        if row['status'] == 'source_identity_hold':
            receipts[pid] = [{'stage': 'source_identity_hold', 'admitted': False,
                              'reason': 'Known wrong cached article remains excluded; no silent replacement'}]
            continue
        folder = area / pid; folder.mkdir()
        attempts = []
        pmcid = mapping.get(str(paper.get('pmid')), {}).get('pmcid', '')
        if re.fullmatch(r'PMC\d+', pmcid) and (pid in pilots or pilot_admissions):
            result = recover_xml(a.output, state, paper, pmcid, folder)
            attempts.append(result); tested.add(pid)
            if pid in pilots: pilot_admissions += result['admitted']
        for number, url in enumerate(row['public_pdf_candidates']):
            if paper['status'] == 'fulltext_acquired': break
            if url in attempted_urls:
                attempts.append({'stage': 'unchanged_url_skipped', 'url': url, 'admitted': False})
                continue
            attempts.append(recover_pdf(a.output, state, paper, url, folder, number))
        receipts[pid] = attempts or [{'stage': 'no_new_route', 'admitted': False}]
        write(a.output / 'collection.json', state)
        write(a.output / 'REPOSITORY_RECOVERY_RECEIPTS.json', receipts)
        print(pid, paper['status'], flush=True)
    write(a.output / 'REPOSITORY_RECOVERY_RECEIPTS.json', receipts)
    write(a.records / 'RECOVERY_SUMMARY.json', {'targets': len(targets),
        'new_papers_acquired': sum(any(x.get('admitted') for x in rs) for rs in receipts.values()),
        'api_pilot_admissions': pilot_admissions, 'api_publications_tested': sorted(tested),
        'input_collection_sha256': sha(a.source_root / 'collection.json'),
        'target_list_sha256': sha(a.targets), 'no_model_calls': True, 'no_workflow_extraction': True})


if __name__ == '__main__':
    main()

"""Recover missing listed papers, recording access failures without inventing text."""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from genomics_discovery.illumina.sources import acquire_pmc_fulltext, fetch, jats_text, parse_pubmed
from .inventory import sha
from .materialize import write


def recover(root, state):
    root = Path(root)
    target_ids = {p['paper_id'] for s in state['sources'].values() for p in s['papers'] if p['role'] == 'references'}
    targets = [state['papers'][p] for p in sorted(target_ids)
               if state['papers'][p]['status'] not in ('fulltext_acquired',) and state['papers'][p].get('pmid')]
    out = root / 'acquisition'
    out.mkdir(exist_ok=True)
    receipts = []
    for i, paper in enumerate(targets):
        pmid = paper['pmid']
        directory = root / 'shared/papers' / paper['id']
        publication = paper['metadata']
        if publication.get('identity_status') != 'pubmed_record_retrieved':
            url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?' + urlencode({'db': 'pubmed', 'id': pmid, 'retmode': 'xml'})
            path = out / ('metadata-' + pmid + '.xml')
            try:
                receipt = fetch(url, path, timeout=40)
                publication = parse_pubmed(path.read_bytes()).get(pmid, {})
                receipts.append({'pmid': pmid, 'stage': 'metadata', **receipt})
                paper['metadata'] = publication
            except Exception as exc:
                receipts.append({'pmid': pmid, 'stage': 'metadata', 'error': str(exc)})
            time.sleep(.4)
        if publication.get('identity_status') != 'pubmed_record_retrieved':
            paper['retrieval_status'] = 'metadata_not_resolved'
            continue
        # Reuse the existing identity-validating PMC acquisition implementation.
        attempt = acquire_pmc_fulltext(publication, out)
        receipts.append({'pmid': pmid, 'stage': 'pmc_fulltext', **attempt})
        files = []
        if attempt.get('status') == 'fulltext_acquired':
            files = [(out / attempt['path'], 'article'), (out / attempt['text_path'], 'reading-text'),
                     (out / attempt['blocks_path'], 'blocks')]
        else:
            # An independent public endpoint can have a fulltext rendition even
            # when OAI cannot distribute it. Require the article's own PMID.
            pmcs = publication.get('identifiers', {}).get('pmc', [])
            if pmcs:
                path = out / ('europepmc-' + pmid + '.xml')
                url = 'https://www.ebi.ac.uk/europepmc/webservices/rest/' + pmcs[0] + '/fullTextXML'
                try:
                    receipt = fetch(url, path, timeout=40)
                    article = ET.fromstring(path.read_bytes())
                    own = {e.attrib.get('pub-id-type'): (e.text or '').strip()
                           for e in article.findall('./front/article-meta/article-id')}
                    if article.tag != 'article' or own.get('pmid') != pmid or article.find('body') is None:
                        raise ValueError('Fulltext identity/body validation failed')
                    text, blocks = jats_text(path.read_bytes())
                    tp, bp = out / ('europepmc-' + pmid + '.txt'), out / ('europepmc-' + pmid + '-blocks.json')
                    tp.write_text(text)
                    write(bp, {'article_sha256': sha(path), 'blocks': blocks})
                    files = [(path, 'article'), (tp, 'reading-text'), (bp, 'blocks')]
                    receipts.append({'pmid': pmid, 'stage': 'europepmc_fulltext', **receipt})
                except Exception as exc:
                    receipts.append({'pmid': pmid, 'stage': 'europepmc_fulltext', 'error': str(exc)})
        for path, kind in files:
            destination = directory / (kind + '-recovered' + path.suffix)
            destination.write_bytes(path.read_bytes())
            paper['files'].append({'path': str(destination.relative_to(root)), 'sha256': sha(destination), 'kind': kind})
        paper['retrieval_status'] = 'recovered' if files else attempt.get('status', 'unavailable')
        if files:
            paper['status'] = 'fulltext_acquired'
        write(out / 'receipts.json', receipts)
        write(root / 'collection.json', state)
        print(f'Paper recovery {i + 1}/{len(targets)}: {pmid} {paper["retrieval_status"]}', flush=True)
    write(out / 'receipts.json', receipts)
    return receipts

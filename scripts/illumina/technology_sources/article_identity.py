"""Check cached article identity; matching an ID alone is insufficient.

This is a bibliographic check, not scientific review. A title discrepancy is held
for inspection, including harmless title variants. Original bytes stay untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from html import unescape
import json
from pathlib import Path
import re
import shutil
import subprocess
import unicodedata
import xml.etree.ElementTree as ET

from .inventory import sha
from .materialize import write


def normalized_title(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', unescape(value or '')).casefold() if c.isalnum())


def xml_identity(raw, expected):
    root = ET.fromstring(raw)
    for e in root.iter():
        e.tag = e.tag.rsplit('}', 1)[-1]
    if root.tag != 'article':
        raise ValueError('Expected the original article subtree')
    meta = root.find('front/article-meta')
    if meta is None:
        raise ValueError('Article lacks its own front matter')
    ids = {e.get('pub-id-type'): ''.join(e.itertext()).strip() for e in meta.findall('article-id')}
    title_node = meta.find('title-group/article-title')
    title = ''.join(title_node.itertext()) if title_node is not None else ''
    known_title = expected.get('metadata', {}).get('title') or expected.get('title')
    expected_pmid = str(expected.get('pmid') or '')
    expected_dois = expected.get('metadata', {}).get('identifiers', {}).get('doi', [])
    disagreements = []
    if ids.get('pmid') and expected_pmid and ids['pmid'] != expected_pmid:
        disagreements.append('pmid_disagreement')
    if ids.get('doi') and expected_dois and ids['doi'].casefold() not in [d.casefold() for d in expected_dois]:
        disagreements.append('doi_disagreement')
    if not known_title or not title:
        disagreements.append('title_not_available_for_comparison')
    elif normalized_title(title) != normalized_title(known_title):
        disagreements.append('title_disagreement')
    id_match = bool((expected_pmid and ids.get('pmid') == expected_pmid) or
                    (ids.get('doi') and ids['doi'].casefold() in [d.casefold() for d in expected_dois]))
    return {'status': 'bibliographic_identity_corresponds' if id_match and not disagreements
            else 'requires_identity_review', 'article_title': title, 'expected_title': known_title,
            'article_identifiers': ids, 'disagreements': disagreements,
            'has_body': root.find('body') is not None,
            'scientific_content_reviewed': False}


def audit_collection(root):
    root = Path(root)
    state = json.loads((root / 'collection.json').read_text())
    rows = []
    for pid, paper in sorted(state['papers'].items()):
        articles = [f for f in paper['files'] if f['kind'] == 'article']
        if not articles:
            rows.append({'paper_id': pid, 'status': 'no_original_article', 'scientific_content_reviewed': False})
        for f in articles:
            path = root / f['path']
            result = {'paper_id': pid, 'file': f, 'scientific_content_reviewed': False}
            try:
                if sha(path) != f['sha256']:
                    raise ValueError('Stored file hash differs')
                if path.suffix.lower() == '.xml':
                    result.update(xml_identity(path.read_bytes(), paper))
                elif path.suffix.lower() == '.pdf':
                    tool = shutil.which('pdftotext')
                    if not tool:
                        result.update(status='requires_identity_review', reason='PDF front-page reader unavailable')
                    else:
                        text = subprocess.run([tool, '-f', '1', '-l', '2', str(path), '-'],
                            check=True, capture_output=True, timeout=45).stdout.decode('utf-8', errors='replace')
                        expected = paper.get('metadata', {}).get('title') or paper.get('title') or ''
                        result.update(status='pdf_title_corresponds_identity_review_pending'
                            if expected and normalized_title(expected) in normalized_title(text)
                            else 'requires_identity_review', expected_title=expected,
                            inspected_pages=[1, 2], front_matter_text=text[:12000])
                else:
                    result.update(status='requires_identity_review', reason='Unsupported article format')
            except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError) as exc:
                result.update(status='identity_check_failed', error=str(exc))
            rows.append(result)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    rows = audit_collection(a.root)
    a.output.mkdir(parents=True, exist_ok=False)
    write(a.output / 'ARTICLE_IDENTITY_CHECKS.json', rows)
    summary = {'article_or_missing_records': len(rows), 'counts': dict(Counter(r['status'] for r in rows)),
               'scientific_content_reviews_completed_by_this_check': 0, 'extraction_authorized': False}
    write(a.output / 'SUMMARY.json', summary)
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()

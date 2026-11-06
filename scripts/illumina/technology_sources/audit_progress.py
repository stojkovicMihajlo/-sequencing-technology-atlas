"""Publish an honest, complete paper-review register from recorded inspections.

This renders review records; it does not read papers or decide scientific validity.
Include the original related/search bibliography, not only selected method papers.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path


def build_register(collection, acquired_checks, reviews, aliases, article_checks=None, method_assignments=None):
    papers = dict(collection['papers'])
    acquired = {p['paper_id']: p for p in acquired_checks}
    inspected = {p['paper_id']: p for p in reviews}
    if len(inspected) != len(reviews):
        raise ValueError('Duplicate paper inspection records')
    for pid, p in acquired.items():
        papers.setdefault(pid, {'id': pid, 'title': p['title'], 'files': p['files']})
    if method_assignments is not None and set(method_assignments) - set(papers):
        raise ValueError('Prepare new source papers before applying updated assignments')
    for old, target in aliases.items():
        if old not in papers or target not in papers or target in aliases:
            raise ValueError('Invalid or chained publication alias')
        a, b = papers[old], papers[target]
        hashes = lambda p: {f['sha256'] for f in p.get('files', []) if f['kind'] == 'reading-text'}
        if not a.get('url') or a.get('url') != b.get('url') or not (hashes(a) & hashes(b)):
            raise ValueError('Alias lacks identical source URL and reading-text bytes')
    occurrences = defaultdict(list)
    identity_checks = defaultdict(list)
    for check in article_checks or []:
        identity_checks[aliases.get(check['paper_id'], check['paper_id'])].append(
            {k: v for k, v in check.items() if k != 'front_matter_text'})
    for sid, source in collection['sources'].items():
        for p in source['papers']:
            pid = aliases.get(p['paper_id'], p['paper_id'])
            occurrences[pid].append({'source_id': sid, 'record_id': p['paper_id'], 'source_role': p['role']})
    rows = []
    for pid, p in sorted(papers.items()):
        if pid in aliases:
            continue
        review = inspected.get(pid, {})
        if review.get('review') == 'abstract_checked':
            expected = hashlib.sha256(review['abstract'].encode()).hexdigest()
            if not review['abstract'] or review.get('abstract_sha256') != expected:
                raise ValueError('Reviewed abstract differs: ' + pid)
        fresh = acquired.get(pid, {})
        methods = (method_assignments.get(pid, []) if method_assignments is not None
                   else fresh.get('methods', []))
        files = fresh.get('files', p.get('files', []))
        for f in review.get('inspected_files', []):
            if not any(v['sha256'] == f['sha256'] for v in files):
                raise ValueError('Inspected file absent from paper record: ' + pid)
        rows.append({
            'paper_id': pid,
            'original_record_ids': sorted([pid] + [a for a, t in aliases.items() if t == pid]),
            'title': fresh.get('title') or p.get('title') or p.get('metadata', {}).get('title'),
            'scope': 'selected_method_reference' if methods else 'unassigned_source_bibliography',
            'methods': methods,
            'source_occurrences': occurrences[pid],
            'availability': fresh.get('availability', p.get('status', 'unknown')),
            'files': files,
            'review_status': review.get('review', 'not_yet_reviewed'),
            'sections_inspected': review.get('sections_inspected', []),
            'finding': review.get('finding', ''),
            'abstract_sha256': review.get('abstract_sha256'),
            'inspected_files': review.get('inspected_files', []),
            'external_checks': review.get('external_checks', []),
            'bibliographic_review': review.get('bibliographic_review', 'pending'),
            'article_identity_checks': identity_checks[pid],
            'fulltext_content_review': review.get('fulltext_content_review', 'pending'),
            'extraction_authorized': False,
        })
    if set(inspected) - {r['paper_id'] for r in rows}:
        raise ValueError('Inspection outside recorded paper inventory')
    summary = {
        'original_paper_records': len(collection['papers']),
        'added_paper_records': len(set(acquired) - set(collection['papers'])),
        'duplicate_records_consolidated': len(aliases),
        'paper_rows': len(rows),
        'scope': dict(Counter(r['scope'] for r in rows)),
        'review_status': dict(Counter(r['review_status'] for r in rows)),
        'bibliographic_review': dict(Counter(r['bibliographic_review'] for r in rows)),
        'fulltext_content_review': dict(Counter(r['fulltext_content_review'] for r in rows)),
        'extraction_authorized': False,
        'meaning': 'An abstract or selected-passage check is not a complete paper or protocol review.',
    }
    return rows, summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ('collection', 'acquired-checks', 'reviews', 'aliases', 'output'):
        p.add_argument('--' + arg, required=True, type=Path)
    p.add_argument('--article-checks', type=Path)
    p.add_argument('--decisions', type=Path)
    p.add_argument('--bindings', type=Path)
    a = p.parse_args()
    read = lambda path: json.loads(path.read_text())
    if bool(a.decisions) != bool(a.bindings):
        p.error('--decisions and --bindings must be supplied together')
    collection = read(a.collection)
    assignments = None
    if a.decisions:
        from .curation import compile_inventory
        curated = compile_inventory(collection['inventory'], read(a.decisions), read(a.bindings), collection['papers'])
        assignments = defaultdict(list)
        for mid, method in curated['methods'].items():
            for reference in method['method_references']:
                pid = reference.get('paper_id')
                if pid and mid not in assignments[pid]:
                    assignments[pid].append(mid)
    rows, summary = build_register(collection, read(a.acquired_checks), read(a.reviews), read(a.aliases),
                                   read(a.article_checks) if a.article_checks else None, assignments)
    a.output.mkdir(parents=True, exist_ok=True)
    for filename, data in [('ALL_PAPERS.json', rows), ('AUDIT_PROGRESS.json', summary)]:
        (a.output / filename).write_text(json.dumps(data, indent=2) + '\n')
    keys = ['paper_id', 'title', 'scope', 'methods', 'availability', 'review_status',
            'sections_inspected', 'finding', 'bibliographic_review', 'fulltext_content_review', 'extraction_authorized']
    with (a.output / 'ALL_PAPERS.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=keys, lineterminator='\n')
        writer.writeheader()
        for r in rows:
            writer.writerow({k: '; '.join(r[k]) if isinstance(r[k], list) else r[k] for k in keys})
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()

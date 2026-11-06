"""Prepare every assigned paper for individual review, without marking it reviewed.

Build a new source vault using links to preserved bytes, recover unavailable
assigned papers there, and write a paper-by-paper checklist and reading index.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, Counter
import csv
import json
from pathlib import Path

from .curation import compile_inventory
from .inventory import read, sha
from .materialize import link, write


def prepare(source_root, output, decisions, bindings, recover_missing=False, include_source_bibliography=False):
    source_root, output = Path(source_root).resolve(), Path(output).resolve()
    state = read(source_root / 'collection.json')
    state['upstream_source_vault'] = str(source_root)
    state['upstream_collection_sha256'] = sha(source_root / 'collection.json')
    curated = compile_inventory(state['inventory'], decisions, bindings, state['papers'])
    uses = defaultdict(list)
    for mid, method in curated['methods'].items():
        for b in method['method_references']:
            if b.get('paper_id'):
                uses[b['paper_id']].append({'method_id': mid, 'method_name': method['name'],
                    'reference_id': b['reference_id'], 'role': b['role'],
                    'identity_check': b['identity_check'], 'citation': b.get('citation'),
                    'title': b.get('resolved_title'), 'pmid': b.get('pmid')})
    output.mkdir(parents=True, exist_ok=False)
    for name in ('files', 'depictions', 'documents'):
        if (source_root / 'shared' / name).exists():
            link(source_root / 'shared' / name, output / 'shared' / name)
    paper_ids = set(uses) | (set(state['papers']) if include_source_bibliography else set())
    # A selected-only bundle must not retain file claims for unrelated paper
    # directories that it does not materialize. Raw source bibliographies stay
    # in inventory/sources; they do not become acquisition targets.
    state['papers'] = {pid: p for pid, p in state['papers'].items() if pid in paper_ids}
    state['files'] = {key: f for key, f in state['files'].items()
        if not f['path'].startswith('shared/papers/') or f['path'].split('/')[2] in paper_ids}
    for pid in sorted(paper_ids):
        associations = uses.get(pid, [])
        first = associations[0] if associations else {}
        p = state['papers'].setdefault(pid, {'id': pid, 'pmid': first.get('pmid'),
            'title': first.get('title'), 'url': 'https://pubmed.ncbi.nlm.nih.gov/' + str(first.get('pmid')) + '/',
            'status': 'metadata_only', 'files': [], 'metadata': {}, 'source_ids': []})
        folder = output / 'shared/papers' / pid
        folder.mkdir(parents=True)
        for f in p['files']:
            if sha(source_root / f['path']) != f['sha256']:
                raise ValueError('Paper file differs from recorded bytes: ' + f['path'])
            link(source_root / f['path'], output / f['path'])
        write(folder / 'paper.json', p)
    write(output / 'REVIEW_SCOPE.json', {'paper_ids': sorted(paper_ids), 'associations': uses,
          'include_source_bibliography': include_source_bibliography,
          'scope': 'Assigned method papers and, when requested, every unassigned related/search source paper. Acquisition does not assign a paper to a method.'})
    if recover_missing:
        from .recover import recover
        original_sources = state['sources']
        # The recovery function accepts an acquisition target list through this
        # field. Restore real source provenance immediately afterwards.
        state['sources'] = {'review_targets': {'papers': [{'paper_id': pid, 'role': 'references'} for pid in sorted(paper_ids)]}}
        recover(output, state)
        state['sources'] = original_sources
    checks = []
    dossiers = []
    for pid in sorted(paper_ids):
        associations = uses.get(pid, [])
        p = state['papers'][pid]
        write(output / 'shared/papers' / pid / 'paper.json', p)
        meta = p.get('metadata', {})
        title = p.get('title') or meta.get('title') or (associations[0]['title'] if associations else None)
        abstract = '\n'.join(x.get('text', '') for x in meta.get('abstract', []))
        row = {'paper_id': pid, 'title': title, 'methods': sorted({a['method_id'] for a in associations}),
               'availability': p['status'], 'files': p['files'], 'abstract': abstract,
               'bibliographic_association': 'catalogue_reference_assignment_reviewed' if associations else 'unassigned_source_bibliography',
               'paper_content_review': 'not_yet_reviewed', 'sections_inspected': [],
               'findings': [], 'associations': associations}
        checks.append(row)
        dossiers.append({'paper_id': pid, 'title': title, 'methods': row['methods'],
                         'availability': p['status'], 'abstract': abstract})
        for f in p['files']:
            path = output / f['path']
            if sha(path) != f['sha256']:
                raise ValueError('Acquired paper hash mismatch: ' + f['path'])
            state['files'].setdefault(f['path'], {**f, 'bytes': path.stat().st_size,
                'origins': [{'paper_id': pid, 'basis': 'identity_checked_acquisition'}]})
    write(output / 'collection.json', state)
    write(output / 'PAPER_CHECKS.json', checks)
    write(output / 'PAPER_READING_INDEX.json', dossiers)
    write(output / 'PAPER_REVIEW_SUMMARY.json', {'assigned_papers': len(uses), 'papers_in_review_scope': len(paper_ids),
        'availability': dict(Counter(r['availability'] for r in checks)),
        'content_reviews_completed': 0, 'extraction_authorized': False})
    with (output / 'PAPER_CHECKLIST.csv').open('w') as f:
        keys = ['paper_id', 'title', 'methods', 'availability', 'bibliographic_association', 'paper_content_review', 'sections_inspected']
        writer = csv.DictWriter(f, fieldnames=keys, lineterminator='\n')
        writer.writeheader()
        for r in checks:
            writer.writerow({k: '; '.join(r[k]) if isinstance(r[k], list) else r[k] for k in keys})
    return checks


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--decisions', required=True, type=Path)
    p.add_argument('--bindings', required=True, type=Path)
    p.add_argument('--recover', action='store_true')
    p.add_argument('--include-source-bibliography', action='store_true')
    args = p.parse_args()
    checks = prepare(args.source_root, args.output, read(args.decisions), read(args.bindings), args.recover, args.include_source_bibliography)
    print(json.dumps({'papers': len(checks), 'availability': dict(Counter(x['availability'] for x in checks))}), flush=True)


if __name__ == '__main__':
    main()

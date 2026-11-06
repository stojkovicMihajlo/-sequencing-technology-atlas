"""Account for catalogue methods, aliases and card/poster differences.

These are comparisons of source records. Different image bytes or reference lists
do not, by themselves, establish a difference between experimental procedures.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import json
from pathlib import Path

from .materialize import write


def table(path, rows, fields):
    with Path(path).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def build_report(inventory, curated, sources=None):
    sources = sources or {}
    owners = defaultdict(list)
    for mid, method in curated['methods'].items():
        for rid in method['source_records']:
            owners[rid].append(mid)
    occurrence_rows, methods, coverage = [], {}, Counter()
    for mid, method in sorted(curated['methods'].items()):
        depictions = []
        for rid in method['source_records']:
            occurrence = inventory['occurrences'][rid]
            kind = ('website_card' if occurrence['batch'] in {'catalogue', 'initial_two'}
                    else 'poster_row' if occurrence['batch'] == 'posters' else 'footer_panel')
            refs = occurrence['card'].get('references', [])
            depiction = {'source_id': rid, 'kind': kind, 'printed_title': occurrence['title'],
                'printed_method_names': occurrence['card'].get('source_method_labels') or [],
                'url': occurrence['card'].get('source_page_url'),
                'poster': (occurrence.get('poster_row') or {}).get('resource_kind'),
                'classifications': occurrence.get('classifications', []),
                'shared_with_method_ids': sorted(set(owners[rid]) - {mid}),
                'listed_references': [{'reference_id': r.get('reference_id'), 'pmid': r.get('pmid'),
                    'role': r.get('role'), 'printed_citation': r.get('source_reference', {}).get('text'),
                    'url': r.get('source_reference', {}).get('url')} for r in refs if r.get('role') == 'references'],
                'diagrams': sources.get(rid, {}).get('diagrams', [])}
            depictions.append(depiction)
            occurrence_rows.append({'method_id': mid, 'source_id': rid, 'source_kind': kind,
                'printed_title': depiction['printed_title'], 'url': depiction['url'],
                'shared_with': '; '.join(depiction['shared_with_method_ids'])})
        cards = [d['source_id'] for d in depictions if d['kind'] == 'website_card']
        posters = [d['source_id'] for d in depictions if d['kind'] == 'poster_row']
        source_coverage = 'card_and_poster' if cards and posters else 'card_only' if cards else 'poster_only' if posters else 'footer_only'
        if method['entry_type'] == 'named_method':
            coverage[source_coverage] += 1
        differences = []
        if len({d['printed_title'] for d in depictions}) > 1:
            differences.append('different_printed_titles; see literal titles and identity decisions')
        if any(d['shared_with_method_ids'] for d in depictions):
            differences.append('grouped_depiction; drawing is shared with separately indexed methods')
        reference_sets = {tuple(sorted(r['pmid'] or r['url'] or r['reference_id'] for r in d['listed_references'])) for d in depictions}
        if len(reference_sets) > 1:
            differences.append('different_source_reference_lists; not automatically different method papers')
        hashes = {f['sha256'] for d in depictions for f in d['diagrams']}
        if len(hashes) > 1:
            differences.append('different_image_files; experimental differences have not been inferred from hashes')
        methods[mid] = {'id': mid, 'name': method['name'], 'entry_type': method['entry_type'],
            'aliases': method['aliases'], 'source_coverage': source_coverage,
            'website_card_ids': cards, 'poster_row_ids': posters,
            'identity_decisions': method['identity_decisions'], 'recorded_source_differences': differences,
            'depictions': depictions, 'protocol_difference_review': 'not_part_of_source_curation'}
    counts = {**curated['counts'], 'named_method_source_coverage': dict(coverage),
        'source_occurrences_by_kind': dict(Counter('website_card' if o['batch'] in {'catalogue', 'initial_two'} else 'poster_row' if o['batch'] == 'posters' else 'footer_panel' for o in inventory['occurrences'].values()))}
    return {'counts': counts, 'methods': methods, 'source_memberships': occurrence_rows}


def publish_report(output, inventory, curated, sources):
    output = Path(output)
    report = build_report(inventory, curated, sources)
    write(output / 'SOURCE_RECONCILIATION.json', report)
    table(output / 'SOURCE_OCCURRENCES.csv', report['source_memberships'],
          ['method_id', 'source_id', 'source_kind', 'printed_title', 'url', 'shared_with'])
    rows = [{'id': mid, 'name': m['name'], 'entry_type': m['entry_type'],
             'coverage': m['source_coverage'], 'aliases': '; '.join(m['aliases']),
             'cards': '; '.join(m['website_card_ids']), 'poster_rows': '; '.join(m['poster_row_ids']),
             'differences': '; '.join(m['recorded_source_differences'])} for mid, m in report['methods'].items()]
    table(output / 'METHOD_SOURCES.csv', rows, ['id', 'name', 'entry_type', 'coverage', 'aliases', 'cards', 'poster_rows', 'differences'])
    aliases = [r for r in curated['folder_decisions'] if r['action'] in {'merge', 'split'}]
    table(output / 'ALIASES_AND_SPLITS.csv', [{**r, 'source_ids': '; '.join(r['source_ids'])} for r in aliases],
          ['previous_id', 'canonical_id', 'action', 'entry_type', 'reason', 'source_ids'])
    counts = report['counts']
    (output / 'COUNTS.txt').write_text('ILLUMINA SOURCE INVENTORY\n\n' + json.dumps(counts, indent=2) +
        '\n\nCounting unit: one explicitly scoped named method/implementation, not a diagram,\n'
        'paper, independent invention, or every commercial kit. Versions remain separate\n'
        'when the source identifies a distinct implementation. All original source\n'
        'memberships are accounted for in SOURCE_OCCURRENCES.csv. Shared rows explain\n'
        'why the source counts cannot be added to obtain a method count.\n')
    for mid, item in report['methods'].items():
        write(output / curated['methods'][mid]['directory'] / 'source_comparison.json', item)
    return report

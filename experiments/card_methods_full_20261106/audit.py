"""Turn all recorded nonacceptance outcomes into an evidence-bound repair ledger."""
import argparse
from collections import Counter
import csv
from pathlib import Path

from genomics_discovery.illumina.extraction import read, write, sha


def actions(row, review=None):
    checks = (review or {}).get('checks', [])
    revise = [c['target'] for c in checks if c['action'] == 'revise']
    research = [c['target'] for c in checks if c['action'] == 'research']
    return {'revise_targets': revise, 'research_targets': research,
        'needs_correction': bool(revise),
        'needs_source_investigation': bool(research) or row['status'] == 'source_recovery_needed',
        'meaning': 'Flags can coexist. A revise verdict does not cancel an evidence-recovery request.'}


def build(inputs, output):
    document = read(inputs); rows = []
    for item in document['methods']:
        row, mid = item['outcome'], item['method_id']
        followup = actions(row, item.get('review.json'))
        provider = any('flagged for possible biological risk' in f.get('last_events', '')
            for f in item.get('native_failures', []))
        bugs = []
        if followup['needs_correction'] and followup['needs_source_investigation']:
            bugs.append('mixed_repair_and_research_hidden_by_single_status')
        papers = item.get('source_package.json', {}).get('method_papers', [])
        if row['status'] == 'source_recovery_needed':
            for paper in papers:
                if sum(f['kind'] == 'reading-text' for f in paper['files']) > 1:
                    bugs.append('adapter_rejects_multiple_renditions')
                if paper['availability'] == 'fulltext_acquired' and paper['paper_id'].startswith('external-'):
                    bugs.append('adapter_requires_pmid_despite_curated_doi')
            if 'catalogue prose' in row.get('reason', ''):
                bugs.append('empty_original_card_description_is_treated_as_total_source_failure')
        rows.append({'method_id': mid, 'status': row['status'], **followup,
            'provider_blocked': provider, 'automatic_model_retry': not provider,
            'harness_findings': sorted(set(bugs)),
            'review_actions': dict(Counter(c['action'] for c in item.get('review.json', {}).get('checks', []))),
            'source_reason': row.get('reason', '') if row['status'] == 'source_recovery_needed' else '',
            'attempt_directory': row.get('attempt_directory'),
            'evidence': {'audit_input': str(inputs), 'sha256': sha(inputs), 'method_id': mid}})
    output.mkdir(parents=True, exist_ok=False)
    report = {'input_counts': document['counts'], 'unfinished_methods': len(rows),
        'counts': {'mixed_revision_and_research': sum(r['needs_correction'] and r['needs_source_investigation'] for r in rows),
            'provider_blocked': sum(r['provider_blocked'] for r in rows),
            'harness_findings': dict(Counter(b for r in rows for b in r['harness_findings']))},
        'methods': rows,
        'boundary': 'Routing and recorded-evidence audit. Does not certify semantic correctness of reviewer judgments.'}
    write(output / 'REPAIR_LEDGER.json', report)
    with (output / 'REPAIR_LEDGER.csv').open('w', newline='') as stream:
        fields = ['method_id', 'status', 'needs_correction', 'needs_source_investigation', 'provider_blocked', 'harness_findings']
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        writer.writerows({k: ';'.join(r[k]) if isinstance(r[k], list) else r[k] for k in fields} for r in rows)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('inputs', type=Path); p.add_argument('output', type=Path)
    a = p.parse_args(); result = build(a.inputs, a.output); print(result['counts'])

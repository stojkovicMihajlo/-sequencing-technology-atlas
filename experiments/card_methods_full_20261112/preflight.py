"""Audit archived outcomes before dispatch; never infer scientific acceptance."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(previous, output):
    state = read(previous)
    rows, holds = [], {}
    for mid, outcome in state['cards'].items():
        row = {'method': mid, 'previous_status': outcome['status'], 'revision_requests': [],
               'research_requests': [], 'source_reason': '', 'failure_evidence': []}
        directory = Path(outcome['attempt_directory']) if outcome.get('attempt_directory') else None
        if directory and (directory / 'review.json').is_file():
            review_path = directory / 'review.json'
            review = read(review_path)
            row['review_source'] = {'path': str(review_path), 'sha256': sha(review_path)}
            for check in review['checks']:
                if check['action'] in {'revise', 'research'}:
                    key = 'revision_requests' if check['action'] == 'revise' else 'research_requests'
                    row[key].append({'target': check['target'], 'reason': check['rationale']})
        if outcome['status'] == 'source_recovery_needed':
            row['source_reason'] = outcome.get('reason', '')
        if outcome['status'] == 'failed' and directory:
            for stage in read(directory / 'stages.json'):
                events = Path(stage['path']) / 'events.jsonl'
                if not events.is_file():
                    continue
                # Inspect actual error events, never arbitrary text quoted in a tool output.
                errors = []
                for line in events.read_text().splitlines():
                    event = json.loads(line)
                    if event.get('type') == 'error':
                        errors.append(str(event.get('message', '')))
                    elif event.get('type') == 'turn.failed':
                        errors.append(str(event.get('error', {}).get('message', '')))
                evidence = {'path': str(events), 'sha256': sha(events), 'messages': errors}
                row['failure_evidence'].append(evidence)
                if any('flagged for possible biological risk' in message for message in errors):
                    holds[mid] = {'failure_category': 'provider_policy_block',
                        'reason': 'An archived provider safety block requires intervention; no automatic retry or prompt rephrasing.',
                        'evidence': evidence}
        row['mixed_revision_and_research'] = bool(row['revision_requests'] and row['research_requests'])
        row['next_action'] = ('intervention_required' if mid in holds else
            'source_preflight_then_fresh_author_and_review')
        rows.append(row)
    report = {'previous_state': str(previous), 'previous_state_sha256': sha(previous),
              'counts': dict(Counter(r['previous_status'] for r in rows)),
              'mixed_revision_and_research': sum(r['mixed_revision_and_research'] for r in rows),
              'methods': rows, 'new_scientific_judgments': False}
    output.mkdir(parents=True, exist_ok=False)
    for name, value in [('previous_outcomes.json', report), ('intervention_holds.json', holds)]:
        (output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    with (output / 'previous_outcomes.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['method', 'previous_status', 'revision_requests', 'research_requests', 'next_action', 'source_reason'])
        for row in rows:
            writer.writerow([row['method'], row['previous_status'], len(row['revision_requests']),
                             len(row['research_requests']), row['next_action'], row['source_reason']])
    return {'counts': report['counts'], 'intervention_holds': list(holds),
            'mixed_revision_and_research': report['mixed_revision_and_research']}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--previous', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(audit(args.previous, args.output)))

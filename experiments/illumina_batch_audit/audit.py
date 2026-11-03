"""Read-only census of a completed Illumina run; execute sustained audits in Slurm.

Uses the selected runtime's validators. Flags are inspection leads, not scientific
error judgments. Original artifacts and source files are never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re

from genomics_discovery.illumina.extraction import paper_coverage, requires_paper_matches
from genomics_discovery.illumina.models import (
    DiagramExtraction, DiagramReview, digest, review_outcome,
    validate_extraction, validate_review,
)


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def evidence_items(value):
    if isinstance(value, dict):
        if {'source_id', 'basis', 'support'} <= value.keys():
            yield value
        else:
            for child in value.values():
                yield from evidence_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from evidence_items(child)


def tool_statistics(attempt):
    counts = Counter()
    commands = []
    errors = []
    output_chars = 0
    events = attempt / 'events.jsonl'
    if events.exists():
        with events.open() as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except ValueError:
                    counts['unparsed_event_lines'] += 1
                    continue
                if event.get('type') in {'error', 'turn.failed'}:
                    errors.append(event.get('message') or event.get('error'))
                item = event.get('item', {})
                if event.get('type') == 'item.completed' and item.get('type') == 'command_execution':
                    command = item.get('command', '')
                    commands.append(command)
                    output_chars += len(item.get('aggregated_output', ''))
                    if 'illumina.extraction source' in command:
                        counts['source_helper'] += 1
                    if 'illumina.extraction check' in command:
                        counts['self_check'] += 1
                    if 'source_registry.json' in command and 'illumina.extraction source' not in command:
                        counts['direct_registry_command'] += 1
                    if item.get('exit_code') not in (0, None):
                        counts['nonzero_tool_exit'] += 1
    return {'command_count': len(commands), 'output_characters': output_chars,
            'tool_counts': dict(counts), 'errors': errors,
            'commands': commands}


def audit_record(batch, ident, row, *, source_hashes):
    directory = Path(row['attempt_directory'])
    registry = read(directory / 'source_registry.json', {})
    card = read(directory / 'card.json', {})
    stages = read(directory / 'stages.json', [])
    result = {'batch': batch, 'record_id': ident, 'title': row.get('title'),
              'status': row['status'], 'reason': row.get('reason'),
              'research_questions': row.get('research_questions', []),
              'attempt_directory': str(directory), 'flags': [], 'errors': [],
              'requires_paper_matches': requires_paper_matches(card),
              'source_levels': dict(Counter(s.get('content_level') for s in registry.values())),
              'sources': {key: {k: v for k, v in s.items() if k in
                  {'kind', 'content_level', 'identity_status', 'pmid', 'title', 'url', 'association_status'}}
                  for key, s in registry.items()},
              'source_characters': sum(len(s.get('text', '')) for s in registry.values()),
              'stages': [], 'card': {k: v for k, v in card.items() if k in
                  {'record_id', 'source_title', 'recovered_title', 'source_page_url',
                   'source_category_path', 'verified_method_names', 'references',
                   'recovered_references', 'source_method_labels', 'scope_kind'}}}
    for name, expected in row.get('artifact_pins', {}).items():
        path = directory / name
        if not path.exists() or sha(path) != expected:
            result['errors'].append('completed_artifact_pin:' + name)
    for source in registry.values():
        pins = source.get('custody_files', [])
        if source.get('path') and source.get('sha256'):
            pins = [*pins, {'path': source['path'], 'sha256': source['sha256']}]
        for pin in pins:
            path, expected = pin['path'], pin['sha256']
            if path not in source_hashes:
                file = Path(path)
                source_hashes[path] = sha(file) if file.is_file() else None
            if source_hashes[path] != expected:
                result['errors'].append('source_pin:' + path)
    for stage in stages:
        attempt = Path(stage['path'])
        stats = tool_statistics(attempt)
        record = {**stage, 'native_result': read(attempt / 'result.json'), **stats}
        if stage.get('status') == 'failed':
            record['artifact_exists'] = (attempt / 'artifact.json').exists()
            record['final_tail'] = (attempt / 'final.txt').read_text()[-2500:] if (attempt / 'final.txt').exists() else None
            record['files'] = sorted(p.name for p in attempt.iterdir() if p.is_file())
            record['failed_artifact'] = read(attempt / 'artifact.json')
            artifact = record['failed_artifact'] or {}
            record['catalogue_only_matches'] = [p for p in artifact.get('paper_matches', [])
                if registry.get(p['source_id'], {}).get('content_level') == 'catalogue']
        result['stages'].append(record)
    candidate_data = read(directory / 'extraction.json')
    result['candidate_origin'] = 'extraction.json' if candidate_data else None
    if candidate_data is None:
        for stage in reversed(stages):
            if stage['role'] == 'author':
                candidate_data = read(Path(stage['path']) / 'artifact.json')
                if candidate_data:
                    result['candidate_origin'] = stage['path'] + '/artifact.json'
                    break
    review_data = read(directory / 'review.json')
    result['candidate'] = candidate_data
    result['review'] = review_data
    if candidate_data is None:
        result['flags'].append('no_authored_candidate')
        return result
    try:
        candidate = DiagramExtraction.model_validate(candidate_data)
        errors = validate_extraction(candidate, registry, expected_record_id=ident,
            require_paper_matches=requires_paper_matches(card)) + paper_coverage(candidate, card)
        result['validation_errors'] = errors
        result['sizes'] = {k: len(candidate_data[k]) for k in
            ('states', 'operations', 'flows', 'paper_matches', 'corrections', 'unknowns')}
        result['paper_assessments'] = dict(Counter(p.assessment for p in candidate.paper_matches))
        result['blocking_unknowns'] = [u.model_dump(mode='json') for u in candidate.unknowns if u.blocking]
        result['unresolved_methods'] = [s.method for s in candidate.method_scopes if s.status != 'resolved']
        result['methods_without_nonbackground_paper'] = [m for m in candidate.method_names if not any(
            m in p.methods and p.relationship not in {'background', 'unresolved'}
            for p in candidate.paper_matches)] if candidate.paper_matches else []
        levels = Counter(registry[e['source_id']].get('content_level')
                         for e in evidence_items(candidate_data) if e['source_id'] in registry)
        result['evidence_levels'] = dict(levels)
        result['weak_paper_sources'] = [p.model_dump(mode='json') for p in candidate.paper_matches
            if registry.get(p.source_id, {}).get('content_level') == 'catalogue']
        primary = {'abstract', 'fulltext'}
        result['operations_without_primary_support'] = [o.id for o in candidate.operations
            if not any(registry.get(e.source_id, {}).get('content_level') in primary for e in o.evidence)]
        if len(candidate.method_names) > 1:
            result['flags'].append('grouped_method_names')
        if not levels.get('fulltext'):
            result['flags'].append('no_fulltext_evidence')
        if result['operations_without_primary_support']:
            result['flags'].append('some_operations_have_no_primary_support')
        if review_data:
            review = DiagramReview.model_validate(review_data)
            result['review_validation_errors'] = validate_review(review, candidate, registry)
            result['review_actions'] = dict(Counter(c.action for c in review.checks))
            result['reviewed_outcome'] = review_outcome(review, candidate, registry)
            if result['reviewed_outcome'] != row['status']:
                result['errors'].append('checkpoint_outcome_differs')
            if row['status'] == 'research_needed' and not row.get('reason'):
                result['flags'].append('unexplained_host_hold')
            graph = read(directory / 'graph.json')
            if graph:
                if graph['workflow'] != candidate_data or graph['review'] != review_data:
                    result['errors'].append('graph_embedded_records_differ')
                if graph['candidate_sha256'] != digest(candidate) or graph['review_sha256'] != digest(review):
                    result['errors'].append('graph_hash_binding_differs')
        if row['status'] == 'supported':
            result['errors'].extend(errors)
            result['errors'].extend(result.get('review_validation_errors', []))
    except Exception as exc:
        result['validation_exception'] = f'{type(exc).__name__}: {exc}'
        if row['status'] == 'supported':
            result['errors'].append(result['validation_exception'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source_hashes = {}
    counts, flags = Counter(), Counter()
    summary_rows = []
    methods = defaultdict(list)
    with gzip.open(args.output / 'records.jsonl.gz', 'wt') as handle:
        for batch in ('initial_two', 'catalogue', 'posters', 'footer_panels'):
            state = read(args.run_root / 'extractions_03' / batch / 'state.json')
            for ident, row in state['cards'].items():
                record = audit_record(batch, ident, row, source_hashes=source_hashes)
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')
                counts[record['status']] += 1
                flags.update(record['flags'])
                summary_rows.append({k: v for k, v in record.items() if k not in
                    {'candidate', 'review', 'sources', 'stages', 'card'}})
                candidate = record.get('candidate') or {}
                for name in candidate.get('method_names', []):
                    key = re.sub(r'[^a-z0-9]', '', name.casefold())
                    methods[key].append({'record_id': ident, 'name': name,
                        'status': row['status'], 'sizes': record.get('sizes')})
    result = {'format_version': 'illumina_run_audit/1.0',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'run_root': str(args.run_root), 'counts': dict(counts), 'flag_counts': dict(flags),
        'unique_custody_files_hashed': len(source_hashes),
        'accepted_integrity_errors': [r for r in summary_rows if r['status'] == 'supported' and r['errors']],
        'records': summary_rows,
        'repeated_name_candidates': {k: v for k, v in methods.items() if len(v) > 1},
        'limits': 'Mechanical census and risk flags; no claim of independent scientific review of every accepted fact. Repeated names are comparison leads, not canonical identity.'}
    (args.output / 'audit.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in
        {'records', 'repeated_name_candidates', 'accepted_integrity_errors'}}))
    print('accepted_integrity_error_records', len(result['accepted_integrity_errors']))


if __name__ == '__main__':
    main()

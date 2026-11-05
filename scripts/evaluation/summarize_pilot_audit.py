"""Summarize a pinned pilot audit snapshot; never change scientific verdicts.

All review/evaluation observations retain an original path, hash and JSON pointer.
Counts describe model records, not independently established assay correctness.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re


def summarize(path):
    raw = path.read_bytes()
    snapshot = json.loads(gzip.decompress(raw))
    records = snapshot['records']

    def rows(pattern):
        return [(k, r) for k, r in records.items() if re.fullmatch(pattern, k)]

    def pointer(record, pointer=''):
        return {k: record[k] for k in ('path', 'sha256')} | {'json_pointer': pointer}

    def finding_view(finding):
        # The exact evidence remains at the enclosing finding's recorded JSON
        # pointer. Avoid copying the same quotations across hundreds of checks.
        return {k: v for k, v in finding.items() if k != 'evidence'} | {
            'evidence_count': len(finding.get('evidence', []))}

    extraction_state = records['extractions/state.json']['data']['cards']
    history = records['history/state.json']['data']
    index = records['history/methods.json']['data']['methods']
    evaluations = records['evaluation/state.json']['data']['methods']
    coverage, extraction_findings, citation_findings, comparison_findings = [], [], [], []
    for mid, status in extraction_state.items():
        prefix = f'extractions/cards/{mid}/attempt_001/'
        review_rows = rows(re.escape(prefix) + r'attempts/[^/]+/artifact.json')
        checks, nonaccept = 0, 0
        for _, record in review_rows:
            data = record['data']
            if not isinstance(data, dict) or 'checks' not in data:
                continue
            checks += len(data['checks'])
            for i, finding in enumerate(data['checks']):
                if finding['action'] != 'accept':
                    nonaccept += 1
                    extraction_findings.append({'method': mid, 'finding': finding_view(finding),
                        'record': pointer(record, f'/checks/{i}')})
        candidate = records.get(prefix + 'extraction.json')
        coverage.append({'method': mid, 'extraction_status': status['status'],
            'extraction_record': pointer(candidate) if candidate else None,
            'review_checks_all_attempts': checks, 'nonaccept_checks_all_attempts': nonaccept,
            'history_readiness': index[mid]['status'], 'history_hold': index[mid].get('hold_reason'),
            'citation_run': history['citation_runs'].get(mid, {'status': 'not_launched'}),
            'evaluation': evaluations[mid],
            'audit_boundary': 'All stage outcomes and flagged findings inspected; not a fresh full-paper expert validation.'})

    relation_counts, decision_counts, citation_holds = Counter(), Counter(), Counter()
    omission_runs = omission_items = non_background_omission_holds = 0
    omission_hold_types, additional_omission_hold_flags = Counter(), Counter()
    for key, record in rows(r'history/citations/[^/]+/review.json'):
        mid = key.split('/')[2]
        review = record['data']
        decision_counts.update(f['decision'] for f in review['findings'])
        omission_runs += bool(review['omitted_candidates'])
        omission_items += len(review['omitted_candidates'])
        for i, finding in enumerate(review['findings']):
            if finding['decision'] != 'accept':
                citation_findings.append({'method': mid, 'finding': finding_view(finding),
                    'record': pointer(record, f'/findings/{i}')})
        for i, omission in enumerate(review['omitted_candidates']):
            citation_findings.append({'method': mid, 'omission': omission,
                'record': pointer(record, f'/omitted_candidates/{i}')})
    for _, record in rows(r'history/citations/[^/]+/analysis.json'):
        relation_counts.update(c['relation'] for c in record['data']['claims'])
    for _, record in rows(r'history/citations/[^/]+/graph.json'):
        for item in record['data']['withheld']:
            citation_holds[item['reason']] += 1
            if item['reason'] == 'review_found_omissions':
                claim = item['claim']
                omission_hold_types[claim['relation']] += 1
                non_background_omission_holds += claim['relation'] not in {'background', 'no_established_relation'}
                # These are overlapping diagnostic flags, not a counterfactual
                # acceptance decision: evidence and review must still be checked.
                if not claim.get('predecessor_evidence'):
                    additional_omission_hold_flags['missing_predecessor_evidence'] += 1
                if not claim.get('citation_context'):
                    additional_omission_hold_flags['missing_citation_context'] += 1
                if (claim['relation'] in {'method_development', 'component_adoption'}
                        and claim.get('claim_scope') != 'seed_workflow'):
                    additional_omission_hold_flags['historical_type_outside_seed_workflow'] += 1

    sources_changed, comparisons = Counter(), Counter()
    shared_omission_observations = 0
    for key, record in rows(r'evaluation/methods/[^/]+/result.json'):
        data = record['data']
        sources_changed[data['source_condition']] += 1
        comparisons[data['model_assessed_outcome']] += 1
        for i, finding in enumerate(data['assessment']['findings']):
            no_anchors = not finding['a_targets'] and not finding['b_targets']
            shared_omission_observations += no_anchors
            if finding['comparison'] != 'equivalent' or no_anchors:
                comparison_findings.append({'method': key.split('/')[2],
                    'finding': finding_view(finding), 'record': pointer(record, f'/assessment/findings/{i}'),
                    'adjudication': 'Model observation; scope and source entailment must be checked before changing the graph.'})

    level_counts, source_errors, duplicate_candidates = Counter(), Counter(), {}
    for key, registry in snapshot['source_inventories'].items():
        if key.startswith('history/citations/'):
            for sid, source in registry['entries'].items():
                if sid in {'seed', 'catalogue', 'bibliography', 'seed_extraction'}:
                    continue
                level_counts[source.get('content_level', 'missing')] += 1
                rendered = json.dumps(source)
                for code in ('400', '403', '404', '429'):
                    if 'HTTP ' + code in rendered or 'error: ' + code in rendered:
                        source_errors[code] += 1
        if key.startswith('extractions/'):
            counts = Counter((s.get('pmid'), s.get('doi'), s.get('title'), s['text_sha256'])
                for s in registry['entries'].values()
                if s.get('text_characters', 0) and (s.get('pmid') or s.get('doi')))
            duplicate_candidates[key.split('/')[2]] = sum(n - 1 for n in counts.values() if n > 1)

    stage_usage = {}
    for stage, prefix in [('extraction', 'extractions/'), ('history', 'history/')]:
        attempts = [r['data'] for key, r in records.items()
                    if key.startswith(prefix) and '/attempts/' in key and key.endswith('/result.json')]
        stage_usage[stage] = {'attempts': len(attempts),
            'summed_worker_hours': sum(r.get('elapsed_seconds', 0) for r in attempts) / 3600,
            'usage': {k: sum(r.get('usage', {}).get(k, 0) for r in attempts)
                for k in ['input_tokens', 'cached_input_tokens', 'output_tokens']}}
    return {'format_version': 'pilot_deep_audit/1', 'snapshot_sha256': hashlib.sha256(raw).hexdigest(),
        'pilot': snapshot['pilot'], 'missing_required_records': snapshot['missing_required'],
        'pilot_result': records['RESULT.json']['data'], 'coverage': coverage,
        'counts': {'citation_claims_by_type': dict(relation_counts),
            'citation_final_decisions': dict(decision_counts), 'citation_holds': dict(citation_holds),
            'citation_runs_with_omissions': omission_runs, 'omission_items': omission_items,
            'non_background_claims_assigned_global_omission_hold': non_background_omission_holds,
            'global_omission_holds_by_relation': dict(omission_hold_types),
            'additional_flags_on_globally_held_claims_not_complete_eligibility_check': dict(additional_omission_hold_flags),
            'comparison_outcomes': dict(comparisons), 'comparison_source_conditions': dict(sources_changed),
            'comparison_findings_with_both_target_lists_empty': shared_omission_observations,
            'citation_source_registrations_by_content_level': dict(level_counts),
            'registrations_recording_http_errors_not_unique_requests': dict(source_errors),
            'same_title_identifier_text_duplicate_candidates_by_method': duplicate_candidates,
            'stage_usage': stage_usage},
        'extraction_nonaccept_findings': extraction_findings,
        'citation_nonaccept_and_omission_findings': citation_findings,
        'comparison_findings_requiring_followup': comparison_findings,
        'boundary': 'Inspection of archived model results and code, not an independent completeness or factual-accuracy score. Repeated cached input tokens are not unique documents or a billing estimate. Duplicate candidates still require custody/identity checks.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.snapshot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'methods': len(result['coverage']),
                      'counts': result['counts']}, indent=2))

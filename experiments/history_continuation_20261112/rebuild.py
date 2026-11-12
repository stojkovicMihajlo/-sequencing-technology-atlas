"""Recompile saved reviewed histories before one isolated failed-seed retry.

This helper makes no model calls, edits no scientific record and adds no edge.
"""
import argparse
from pathlib import Path

from genomics_discovery.illumina.extraction import read, write, sha
from genomics_discovery.illumina.history import build_history_index
from genomics_discovery.illumina.history_method_identity import connectivity_report
from genomics_discovery.illumina.history_view import build as build_history_view
from genomics_discovery.execution.codex_worker import classify_archived_terminal_failure


def rebuild(plan, output):
    source = Path(plan['source_run'])
    original_manifest = source / 'history_inputs.json'
    original_registry = source / 'method_registry.json'
    if sha(original_manifest) != plan['history_inputs_sha256']:
        raise ValueError('Original reviewed-history manifest changed')
    if sha(original_registry) != plan['method_registry_sha256']:
        raise ValueError('Original method registry changed')
    document = read(original_manifest)
    if len(document['overlays']) != plan['completed_histories']:
        raise ValueError('Unexpected number of original history overlays')
    spec = document.get('method_registry')
    if not spec or (original_manifest.parent / spec['path']).resolve() != original_registry.resolve() or spec['sha256'] != plan['method_registry_sha256']:
        raise ValueError('History manifest does not bind the expected method registry')
    state = read(source / 'state.json')
    completed = [mid for mid, row in state['citation_runs'].items() if row['status'] == 'completed']
    if len(completed) != plan['completed_histories'] or plan['retry_record_id'] in completed:
        raise ValueError('Continuation must exclude every successful history from retry')
    output.mkdir(parents=True, exist_ok=False)
    # Compiler reopens all graph/source pins and reviewed method identity data.
    # Read the original manifest at its original location so relative references
    # retain their meaning. The copy below is an audit artifact, not a new input.
    index = build_history_index(original_manifest)
    write(output / 'history_index.json', index)
    connectivity = connectivity_report(index, completed)
    connectivity['selected_but_not_completed'] = sorted(set(state['seeds']) - set(completed))
    write(output / 'CONNECTIVITY.json', connectivity)
    write(output / 'ATLAS_ADDITION_QUEUE.json', {
        'history_index_sha256': sha(output / 'history_index.json'),
        'entries': connectivity['atlas_addition_queue']})
    view = build_history_view(output / 'history_index.json', output / 'view')
    receipt = {'source_manifest': {'path': str(original_manifest), 'sha256': sha(original_manifest)},
               'method_registry': {'path': str(original_registry), 'sha256': sha(original_registry)},
               'reviewed_overlays_recompiled': len(document['overlays']),
               'completed_methods': sorted(completed), 'view': view,
               'new_model_calls': 0, 'scientific_records_changed': False,
               'boundary': 'Existing reviewed graph compilation; no new scientific acceptance.'}
    write(output / 'REBUILD_RECEIPT.json', receipt)
    return receipt


def validate_retry(plan):
    """The one retry is tied to an inspected execution failure, never a policy stop."""
    source = Path(plan['source_run'])
    mid = plan['retry_record_id']
    row = read(source / 'state.json')['citation_runs'][mid]
    if row['status'] != 'failed':
        raise ValueError('Retry seed is not the recorded failed history')
    attempt = Path(plan['failure_evidence']['attempt'])
    for filename, expected in plan['failure_evidence']['pins'].items():
        if sha(attempt / filename) != expected:
            raise ValueError('Inspected native failure bytes changed: ' + filename)
    diagnosis = classify_archived_terminal_failure(attempt)
    if diagnosis['category'] != 'unknown' or diagnosis['basis'] != 'terminal_error_record':
        raise ValueError('Inspected execution-failure retry does not cover this failure category')
    if read(attempt / 'result.json')['exit_code'] == 0:
        raise ValueError('Cannot retry a successful native process')
    return {'method': mid, 'diagnosis': diagnosis, 'maximum_fresh_method_attempts': 1,
            'scientific_scope_changed': False,
            'boundary': 'One user-authorized fresh session after archived 403/Bad Request failure; another failure stops.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plan = read(args.plan)
    receipt = rebuild(plan, args.output)
    write(args.output / 'RETRY_ELIGIBILITY.json', validate_retry(plan))
    print(receipt)

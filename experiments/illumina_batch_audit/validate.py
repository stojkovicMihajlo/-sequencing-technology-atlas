"""Bounded forward validation of audit-derived changes, with original sources.

This experiment does not overwrite or admit anything to the first-pass atlas.
The selected examples are runtime configuration, not branches in extraction code.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from genomics_discovery.execution.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.illumina.extraction import run, write
from genomics_discovery.illumina.graph import build_graph


async def execute(root, plan):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Live validation must execute in Slurm')
    probe = root / 'structured_response_probe'
    probe.mkdir(exist_ok=False)
    schema = probe / 'schema.json'
    write(schema, {'type': 'object', 'properties': {
        'record_id': {'type': 'string'}, 'ready': {'type': 'boolean'}},
        'required': ['record_id', 'ready'], 'additionalProperties': False})
    worker = CodexWorker(model='gpt-6.1-sol', effort='xhigh', timeout_seconds=None,
                         web_search=False, network_access=False, pin_shell_environment=True)
    result = await worker.run(
        'Return record_id="structured_delivery_probe" and ready=true as the final JSON response. '
        'Do not run commands or edit files. This is a transport check, not a scientific task.',
        probe, response_schema_path=schema)
    assessment = assess_archived_attempt(probe)
    valid = bool(assessment.complete and not result.error and result.exit_code == 0)
    try:
        final = json.loads((probe / 'final.txt').read_text())
        valid &= final == {'record_id': 'structured_delivery_probe', 'ready': True}
    except (OSError, ValueError):
        valid = False
    write(root / 'structured_response_result.json', {
        'supported': valid, 'exit_code': result.exit_code, 'error': result.error,
        'scope': 'Simple schema final-delivery probe only; full extraction schema not yet migrated.'})

    async def batch(config):
        sources = Path(config['sources'])
        destination = root / config['name']
        state = await run(sources, destination, workers=config['workers'],
                          records=config['records'], max_revisions=1)
        graph = build_graph(destination, sources.parent / 'inventory.json')
        write(destination / 'atlas.json', graph)
        publication_errors = [k for k, row in state['cards'].items()
            if row['status'] == 'supported' and graph['cards'][k].get('admission') != 'supported']
        return {'name': config['name'], 'counts': state['counts'],
                'publication_errors': publication_errors,
                'records': {k: {key: row.get(key) for key in
                    ('status', 'reason', 'research_questions', 'failure_category', 'attempt_directory')}
                    for k, row in state['cards'].items()}}

    results = await asyncio.gather(*(batch(config) for config in plan['batches']))
    write(root / 'validation.json', {'format_version': 'illumina_audit_validation/1.0',
        'model': 'gpt-6.1-sol', 'effort': 'xhigh', 'structured_delivery_probe': valid,
        'batches': results, 'boundary': 'Fresh forward tests; no first-pass outputs modified or canonically admitted.'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    plan = json.loads(args.plan.read_text())
    write(args.output / 'plan.json', plan)
    asyncio.run(execute(args.output, plan))

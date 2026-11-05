"""Re-extract selected method units against exactly the same registered evidence.

The source run supplies immutable method-level bundles, not previous scientific
answers. Prior candidates enter only the subsequent blinded A/B evaluation.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import os
from pathlib import Path

from genomics_discovery.illumina.extraction import read, sha, write, now
from experiments.pilot50_20261104.run import extract, compare


def select_inputs(source_run, records, reason):
    source_run = Path(source_run).resolve()
    if not records or len(records) != len(set(records)):
        raise ValueError('Select distinct, explicit method IDs')
    if not reason.strip():
        raise ValueError('Record why these completed methods are being repeated')
    path = source_run / 'sources/manifest.json'
    manifest = read(path)
    by_id = {b['record_id']: b for b in manifest['bundles']}
    if len(by_id) != len(manifest['bundles']) or set(records) - by_id.keys():
        raise ValueError('Ambiguous or unknown source method IDs')
    state_path = source_run / 'extractions/state.json'
    state = read(state_path)
    if not state.get('finished_at'):
        raise ValueError('The source extraction batch is still active')
    baselines = {}
    for ident in records:
        row = state['cards'][ident]
        if not row.get('artifact_pins'):
            raise ValueError('Selected previous method lacks a pinned comparison artifact')
        baselines[ident] = {'attempt_directory': row['attempt_directory'],
            'artifact_pins': copy.deepcopy(row['artifact_pins']), 'declared_status': row['status'],
            'baseline_state_path': str(state_path), 'baseline_state_sha256': sha(state_path)}
    selection = {'method_ids': list(records), 'reason': reason,
        'source_run': str(source_run), 'source_manifest_sha256': sha(path),
        'source_extraction_state_sha256': sha(state_path),
        'evidence_policy': 'Exact selected bundles; no new acquisition or prior extraction used as evidence'}
    return selection, [copy.deepcopy(by_id[r]) for r in records], baselines


async def execute(source_run, records, output, reason, *, workers=4):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Native validation requires Sherlock Slurm')
    selection, bundles, baselines = select_inputs(source_run, records, reason)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    selection_path = output / 'selection.json'
    write(selection_path, selection)
    manifest = {'bundles': bundles, 'selection_sha256': sha(selection_path)}
    write(output / 'source_manifest.json', manifest)
    state = {'status': 'extracting', 'started_at': now(),
        'slurm_job_id': os.environ['SLURM_JOB_ID'], 'method_ids': records,
        'source_manifest_sha256': sha(output / 'source_manifest.json')}
    write(output / 'state.json', state)
    try:
        state['extraction'] = await extract(manifest, output / 'extractions', workers=workers)
        state['status'] = 'evaluating'
        write(output / 'state.json', state)
        current = read(output / 'extractions/state.json')['cards']
        rows = []
        for ident in records:
            row = current[ident]
            candidate = ({'attempt_directory': row['attempt_directory'],
                'artifact_pins': row['artifact_pins'], 'declared_status': row['status']}
                if row.get('artifact_pins') else None)
            card = read(next(b['card'] for b in bundles if b['record_id'] == ident))
            rows.append({'method_id': ident, 'scope_target': card.get('implementation_scope'),
                'identity_match': {'status': 'pending',
                    'rationale': 'Same assigned method and exact input bundle; evaluator must verify output scope'},
                'baseline': baselines[ident], 'candidate': candidate})
        state['evaluation'] = await compare({'format_version': 'atlas_pilot_comparison/1',
            'methods': rows}, output / 'evaluation', workers=workers)
        state.update(status='completed', finished_at=now(),
            boundary='Matched-source model comparison; independent source adjudication still required.')
        write(output / 'RESULT.json', state)
        return state
    except BaseException as exc:
        state.update(status='failed', reason=f'{type(exc).__name__}: {exc}', finished_at=now())
        raise
    finally:
        write(output / 'state.json', state)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-run', type=Path, required=True)
    p.add_argument('--record', action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--workers', type=int, default=4)
    a = p.parse_args()
    asyncio.run(execute(a.source_run, a.record, a.output, a.reason, workers=a.workers))

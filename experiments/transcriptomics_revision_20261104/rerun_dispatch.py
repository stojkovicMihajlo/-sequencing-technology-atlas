"""Rerun explicitly selected completed work units through existing extraction gates.

Reopens the original reviewed identity decisions and source pins. Prior extraction
artifacts justify selection, but are not supplied as scientific evidence.
"""
import argparse
import asyncio
import os
from pathlib import Path

from experiments.transcriptomics_revision_20261104 import atlas_batch, global_plan
from experiments.transcriptomics_revision_20261104.extract_plan import select_bundles
from genomics_discovery.illumina.extraction import read, sha, write


def selected_sources(parent, selection):
    index = parent / 'global_inputs/index.json'
    decisions = global_plan.admit_global(index, read(parent / 'component_runs.json'))
    if decisions != read(parent / 'dispatch_decisions.json'):
        raise ValueError('Parent identity decisions differ from fresh custody replay')
    directory = parent / 'dispatch_sources'
    worklist = read(directory / 'worklist.json')
    expected = {'format_version': 'atlas_admitted_worklist/1',
                'global_index_sha256': sha(index),
                'dispatch_decisions_sha256': atlas_batch.digest(decisions),
                'units': [item['unit'] for item in decisions['admitted_units']]}
    if worklist != expected:
        raise ValueError('Parent worklist differs from reviewed identity decisions')
    manifest = read(directory / 'workflow_bundles/manifest.json')
    if manifest['worklist_sha256'] != atlas_batch.digest(worklist):
        raise ValueError('Source bundles do not bind the reviewed worklist')
    if not selection or any(not isinstance(reason, str) or not reason.strip() for reason in selection.values()):
        raise ValueError('Every selected method needs a nonempty rerun reason')
    bundles = select_bundles(worklist, manifest['bundles'], list(selection))
    prior = read(parent / 'extractions/state.json')['cards']
    receipts = {}
    for bundle in bundles:
        ident = bundle['unit_id']
        row = prior[ident]
        if row['status'] not in {'supported', 'needs_revision', 'research_needed'}:
            raise ValueError('Rerun requires a completed reviewed extraction, not active work')
        pins = row.get('artifact_pins', {})
        if not {'extraction.json', 'review.json', 'stages.json'} <= set(pins):
            raise ValueError('Previous reviewed extraction lacks artifact pins')
        attempt = Path(row['attempt_directory'])
        if any(Path(name).name != name or sha(attempt / name) != pin for name, pin in pins.items()):
            raise ValueError('Previous reviewed extraction artifacts changed')
        receipts[ident] = {'reason': selection[ident], 'previous_status': row['status'],
                           'previous_attempt': str(attempt), 'artifact_pins': pins}
    subset = {**worklist, 'units': [u for u in worklist['units'] if u['unit_id'] in selection]}
    return subset, bundles, {'parent': str(parent), 'parent_worklist_sha256': atlas_batch.digest(worklist),
                             'selected': receipts, 'prior_extractions_used_as_evidence': False}


async def execute(parent, selection, output, workers=2):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Native reruns require a Sherlock Slurm allocation')
    worklist, bundles, receipt = selected_sources(parent, selection)
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'selection_receipt.json', receipt)
    return await atlas_batch.extract_dispatch(worklist, bundles, output / 'extractions', workers=workers)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('parent', type=Path)
    parser.add_argument('selection', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = asyncio.run(execute(args.parent.resolve(), read(args.selection), args.output.resolve()))
    print(result)
    raise SystemExit(0 if result['passed'] else 1)

"""One bounded source investigation per publication, then isolated fresh extraction.

The live full batch is read only. Retrieval never assigns scientific acceptance.
"""
import argparse
import asyncio
from pathlib import Path
import os

from genomics_discovery.illumina.extraction import read, write, sha, verify_registry_files
from experiments.card_methods_full_20261112.investigate_holds import run as investigate
from experiments.diagram_paper_pilot_20261106 import run as extractor


def retry_methods(cases, additions, overrides):
    """New registered evidence is required; finder prose is not primary evidence."""
    eligible, held = [], {}
    for case in cases:
        mid = case['selection']['method_id']
        if case['hold']['failure_category'] == 'provider_policy_block':
            held[mid] = 'Provider-blocked; no retry.'
        elif case['hold']['failure_category'] == 'evidence_recovery_failed' and not overrides.get(mid):
            held[mid] = 'Assigned primary paper still unavailable.'
        elif not additions.get(mid) and not overrides.get(mid):
            held[mid] = 'No new registered evidence; source findings remain research leads.'
        else:
            verify_registry_files(additions.get(mid, {}))
            for source in overrides.get(mid, {}).values():
                verify_registry_files({'primary': source})
            eligible.append(case['selection'])
    return eligible, held


async def execute(args):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Run on Qiu Slurm')
    args.output.mkdir(parents=True, exist_ok=False)
    initial = read(args.current / 'state.json')
    snapshot = read(args.snapshot)
    cases = [c for c in snapshot['cases'] if c['hold']['failure_category'] != 'provider_policy_block']
    expected = set(read(args.methods))
    actual = {c['selection']['method_id'] for c in cases}
    if len(cases) != len(actual) or actual != expected:
        raise ValueError('Recovery scope changed or contains duplicate method identities')
    if any(initial['cards'].get(mid, {}).get('status') != 'source_recovery_needed' for mid in actual):
        raise ValueError('A selected held method is no longer held; do not duplicate live work')
    snapshot = {**snapshot, 'cases': cases}
    if args.hints:
        snapshot['observed_source_leads'] = [{k:v for k,v in lead.items() if k != 'finding_summary'}
            for lead in read(args.hints)['documents']]
    selected_snapshot = args.output / 'held_cases.json'
    write(selected_snapshot, snapshot)
    state = {'source_job': os.environ['SLURM_JOB_ID'], 'model': 'gpt-6.1-sol', 'effort': 'xhigh',
             'methods': sorted(actual), 'publications': len({c['paper']['paper_id'] for c in cases}),
             'source_workers': args.workers, 'current_state_sha256': sha(args.current / 'state.json'),
             'retries': {}, 'scientific_acceptance': 'Only independent extraction/review can admit a workflow.'}
    write(args.output / 'state.json', state)

    async def retry_epirad():
        mid = 'epiradseq'
        old = initial['cards'].get(mid, {})
        if old.get('status') != 'failed' or "unhashable type: 'list'" not in old.get('reason', ''):
            raise ValueError('EpiRAD retry requires the inspected pre-model metadata-key failure')
        parent = Path(snapshot['source_release'])
        selected = read(args.current.parent / 'selected.json')
        methods = [m for m in selected['methods'] if m['method_id'] == mid]
        if len(methods) != 1:
            raise ValueError('Missing unique EpiRAD method identity')
        folder = args.output / 'epirad_retry';folder.mkdir()
        write(folder / 'selected.json', {'methods': methods,
            'reason_for_fresh_extraction': 'Verified pre-model metadata-key crash repaired; scientific instructions and registered evidence unchanged.'})
        original_additions = read(parent / 'recovery_02/registered_additions.json')
        original_overrides = read(parent / 'recovery_02/primary_overrides.json')
        write(folder / 'additions.json', {mid: original_additions.get(mid, {})})
        state['epirad_retry'] = {'status': 'running', 'path': str(folder / 'extraction')}
        write(args.output / 'state.json', state)
        result = await extractor.execute(args.library, args.vault, folder / 'selected.json',
            folder / 'extraction', 1, additions_path=folder / 'additions.json',
            isolate_source_failures=True, max_revisions=2, stop_on_first_issue=False,
            primary_overrides={mid: original_overrides.get(mid, {})})
        state['epirad_retry'].update(status='completed', result=result)
        write(args.output / 'state.json', state)

    async def extract_group(pid, group, additions, overrides, finding):
        eligible, held = retry_methods(group, additions, overrides)
        receipt = {'status': 'held' if not eligible else 'running', 'held': held,
                   'eligible_methods': [m['method_id'] for m in eligible]}
        state['retries'][pid] = receipt
        write(args.output / 'state.json', state)
        if not eligible:
            return receipt
        folder = args.output / 'retries' / pid
        folder.mkdir(parents=True)
        selected = {'format_version': 'curated_method_pilot/1',
                    'reason_for_fresh_extraction': 'User-authorized isolated retry with new identity-checked evidence; original outcomes remain unchanged.',
                    'methods': eligible}
        write(folder / 'selected.json', selected)
        # Each publication worker advances into one extraction at a time. This
        # keeps native source/author/reviewer concurrency within the same cap.
        write(folder / 'additions.json', {m['method_id']: additions.get(m['method_id'], {}) for m in eligible})
        result = await extractor.execute(args.library, args.vault, folder / 'selected.json',
            folder / 'extraction', 1, additions_path=folder / 'additions.json',
            isolate_source_failures=True, max_revisions=2, stop_on_first_issue=False,
            primary_overrides={m['method_id']: overrides.get(m['method_id'], {}) for m in eligible})
        receipt.update(status='completed', result=result, extraction=str(folder / 'extraction'))
        write(args.output / 'state.json', state)
        return receipt

    async def epi_isolated():
        try:
            await retry_epirad()
        except Exception as exc:
            state['epirad_retry'] = {'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'}
            write(args.output / 'state.json', state)
    await asyncio.gather(epi_isolated(), investigate(selected_snapshot, args.library, args.vault, args.output / 'source_work',
                      workers=args.workers, selected=actual, on_recovered=extract_group))
    state['status'] = 'finished';write(args.output / 'state.json', state)


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('snapshot','methods','current','library','vault','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--workers',type=int,default=47)
    p.add_argument('--hints',type=Path)
    asyncio.run(execute(p.parse_args()))

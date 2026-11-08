"""Dispatch one prespecified trial; the existing production adapter runs its stages."""
import argparse
import asyncio
from pathlib import Path

from experiments.readability_audit_20261108.rerun import execute
from genomics_discovery.illumina.extraction import read, write


def trial_plan(plan, trial_id):
    trials = {row['id']: row for row in plan['trials']}
    if len(trials) != len(plan['trials']):
        raise ValueError('Duplicate trial identities')
    trial = trials[trial_id]
    return {**plan['source_plan'], 'trial': trial,
            'author_information_effect_contract': trial['contract'],
            'comparison': plan['comparison'], 'interpretation': plan['interpretation']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--trial', required=True)
    args = parser.parse_args()
    selected = trial_plan(read(args.plan), args.trial)
    plan_path = args.root / ('plan_' + args.trial + '.json')
    if plan_path.exists() or (args.root / args.trial).exists():
        raise ValueError('Trial already exists; preserve it, do not overwrite/retry silently')
    write(plan_path, selected)
    result = asyncio.run(execute(plan_path, args.root / args.trial, workers=1))
    raise SystemExit(2 if result['counts'].get('failed') else 0)

"""Schedule the reviewed website-card method inventory through the tested extractor.

No identity inference, scientific prompt changes, historical-edge generation or
automatic missing-document acquisition occurs in this full-batch adapter.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import os
from collections import defaultdict
from pathlib import Path

from experiments.diagram_paper_pilot_20261106 import run as engine


def build_selection(library, scope_csv, pilot_selection):
    """Deduplicate existing method IDs; never split names or schedule depictions."""
    with scope_csv.open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    grouped = defaultdict(list)
    for row in rows:
        if row['entry_type'] == 'named_method':
            grouped[row['method_id']].append(row)
    overrides = {r['method_id']: r['primary_paper_id']
                 for r in engine.read(pilot_selection)['methods']}
    methods = []
    for ident, occurrences in sorted(grouped.items()):
        folders = {r['directory'] for r in occurrences}
        names = {r['method'] for r in occurrences}
        if len(folders) != 1 or len(names) != 1:
            raise ValueError('Contradictory curated method identity: ' + ident)
        folder = Path(next(iter(folders)))
        if folder.is_absolute() or '..' in folder.parts:
            raise ValueError('Unsafe curated method folder: ' + ident)
        package_path = library / folder / 'source_package.json'
        package = engine.read(package_path) if package_path.exists() else {}
        if package and package['method_id'] != ident:
            raise ValueError('Scope/package identity disagreement: ' + ident)
        papers = package.get('method_papers', [])
        available = [p['paper_id'] for p in papers if p['availability'] == 'fulltext_acquired']
        assigned = [p['paper_id'] for p in papers]
        primary = overrides.get(ident) or next(iter(available or assigned), '')
        methods.append({'method_id': ident, 'name': next(iter(names)),
            'primary_paper_id': primary, 'assigned_paper_ids': assigned,
            'primary_selection_basis': 'unchanged pilot assignment' if ident in overrides else
                'first acquired explicitly assigned method paper in curated package order; '
                'otherwise first assigned unavailable paper; no historical priority claim',
            'canonical_directory': str(folder),
            'card_ids': sorted({r['card_id'] for r in occurrences})})
    selection = {'format_version': 'curated_method_pilot/1',
        'reason_for_fresh_extraction': 'User authorized all distinct website-card methods after '
            'the ten-method pilot. Preserve unchanged supported workflows and reviewed source '
            'holds through exact reuse gates; extract remaining methods independently with '
            'the same diagram-granularity and primary-confirmation instructions.',
        'scope_sha256': engine.sha(scope_csv), 'pilot_assignment_sha256': engine.sha(pilot_selection),
        'source_card_count': len({r['card_id'] for r in rows}),
        'resource_assignments': [{k: r[k] for k in ('card_id', 'method_id', 'method', 'entry_type')}
                                 for r in rows if r['entry_type'] != 'named_method'],
        'methods': methods}
    engine.validate_selection(selection)
    return selection


def combine_prior_runs(runs, output):
    """Index completed attempts without changing their files, hashes or verdicts."""
    cards, sources = {}, []
    for root in runs:
        path = root / 'state.json'
        state = engine.read(path)
        sources.append({'path': str(path), 'sha256': engine.sha(path)})
        cards.update(copy.deepcopy(state['cards']))
    output.mkdir(parents=True, exist_ok=False)
    engine.write(output / 'state.json', {'cards': cards, 'source_states': sources,
        'meaning': 'Later explicitly selected run replaces an earlier row; all attempts remain at original paths.'})


async def execute(library, vault, scope_csv, pilot_selection, prior_runs, additions, output, workers,
                  *, max_revisions=2, fresh=False, intervention_holds=None, primary_overrides=None):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Full extraction preparation and execution require a Sherlock Slurm allocation')
    output.mkdir(parents=True, exist_ok=False)
    selection = build_selection(library, scope_csv, pilot_selection)
    if fresh:
        selection['reason_for_fresh_extraction'] = (
            'User requests all distinct website-card methods with the updated explanation '
            'contract and automatic correction followed by independent review. '
            'Previous artifacts remain unchanged; none is reused as a new extraction.')
    if len(selection['methods']) != 174 or selection['source_card_count'] != 154:
        raise ValueError('Current reviewed scope must be 174 distinct named methods from154 cards')
    selection_path = output / 'selected.json'
    engine.write(selection_path, selection)
    prior_index = output / 'prior_index'
    combine_prior_runs(prior_runs, prior_index)
    report = await engine.execute(library, vault, selection_path, output / 'extraction', workers,
        additions_path=additions, resume_authors=None if fresh else prior_index, stop_on_first_issue=False,
        isolate_source_failures=True, reuse_reviewed_holds=not fresh, max_revisions=max_revisions,
        intervention_holds=intervention_holds, primary_overrides=primary_overrides)
    state = engine.read(output / 'extraction/state.json')
    engine.write(output / 'ISSUES.json', {'methods': [
        {'method_id': ident, **{k: row[k] for k in ('status', 'failure_category', 'reason',
            'research_questions', 'attempt_directory') if k in row}}
        for ident, row in state['cards'].items() if row['status'] != 'supported'],
        'automatic_retrieval': False, 'source': 'Recorded source preparation and native review outcomes, not new model judgments.'})
    engine.write(output / 'RESULT.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('library', 'vault', 'scope', 'pilot-selection', 'additions', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--prior-run', action='append', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=10)
    parser.add_argument('--max-revisions', type=int, choices=range(3), default=2)
    parser.add_argument('--fresh', action='store_true', help='Record prior outcomes but do not reuse model outputs.')
    parser.add_argument('--intervention-holds', type=Path)
    parser.add_argument('--primary-overrides', type=Path)
    args = parser.parse_args()
    result = asyncio.run(execute(args.library, args.vault, args.scope, args.pilot_selection,
        args.prior_run, args.additions, args.output, args.workers,
        max_revisions=args.max_revisions, fresh=args.fresh,
        intervention_holds=engine.read(args.intervention_holds) if args.intervention_holds else None,
        primary_overrides=engine.read(args.primary_overrides) if args.primary_overrides else None))
    raise SystemExit(engine.completion_exit_code(result))

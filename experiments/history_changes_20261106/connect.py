"""Finish citation-nominated workflow connections without rerunning extraction."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

from genomics_discovery.illumina.extraction import read, sha, write
from genomics_discovery.illumina.history import build_history_index, technology_history
from genomics_discovery.illumina.history_bindings import load_reviewed_binding
from genomics_discovery.illumina.relationships import HISTORICAL_RELATIONS, is_assay_history


def binding_candidates(index, citation_runs):
    """Only accepted historical claims nominate work; identity still needs review."""
    by_pmid, plans, unresolved = {}, {}, []
    for method in index['methods'].values():
        for paper in method['papers']:
            if paper.get('pmid'):
                by_pmid.setdefault(str(paper['pmid']), []).append((method, paper))
    for later_id, run in citation_runs.items():
        root = Path(run)
        if read(root / 'state.json').get('status') != 'completed':
            continue
        overlay_path = root / 'graph.json'; graph = read(overlay_path)
        sources = read(root / 'inputs/sources.json')
        later = index['methods'][later_id]
        for item in graph['links']:
            claim = item['claim']
            if not is_assay_history({'relation': claim['relation'], 'claim': claim}) or claim.get('claim_scope') != 'seed_workflow':
                continue
            publication = sources[claim['reference_id']]
            original = publication.get('original') or publication.get('provenance', {}).get('original', {})
            text_sha = hashlib.sha256(publication['text'].encode()).hexdigest()
            # Names suggested by the citation author are retrieval hints, not a
            # verified method-identity registry. Never let them hide a matching
            # publication or select one implementation from an ambiguous set.
            candidates, checks = {}, []
            publication_matches = by_pmid.get(str(publication.get('pmid') or ''), [])
            for method, paper in publication_matches:
                check = {'record_id': method['record_id'], 'source_id': paper['source_id'],
                    'different_method': method['record_id'] != later_id,
                    'pair_eligible': bool(paper.get('pair_eligible')),
                    'same_article_bytes': paper.get('article_sha256') == original.get('sha256') and bool(original.get('sha256')),
                    'same_readable_text': paper.get('text_sha256') == text_sha}
                if check not in checks:
                    checks.append(check)
                if all(check[k] for k in ('different_method', 'pair_eligible', 'same_article_bytes', 'same_readable_text')):
                    candidates.setdefault(method['record_id'], (method, paper))
            diagnostics = {'publication_candidates': checks,
                'author_suggested_methods': claim.get('catalogue_candidates', []),
                'name_hints_used_as_identity_filter': False}
            if len(candidates) != 1:
                eligible = [c for c in checks if c['different_method'] and c['pair_eligible']]
                reason = ('ambiguous_method_assignment_requires_resolution' if candidates else
                    'cited_publication_identifier_missing' if not publication.get('pmid') else
                    'no_accepted_workflow_for_cited_publication' if not checks else
                    'same_method_requires_version_resolution' if all(not c['different_method'] for c in checks) else
                    'cited_publication_not_pair_eligible' if not eligible else
                    'registered_source_version_mismatch')
                unresolved.append({'later': later_id, 'claim_id': claim['id'],
                    'reason': reason,
                    'pmid': publication.get('pmid'), 'candidate_methods': sorted(candidates),
                    **diagnostics,
                    'boundary': 'Keep the reviewed paper contribution; do not invent or choose a workflow by name.'})
                continue
            earlier, paper = next(iter(candidates.values()))
            plan = {'left': {'batch': earlier['batch'], 'record_id': earlier['record_id'], 'primary_paper': paper['source_id']},
                'right': {'batch': later['batch'], 'record_id': later_id, 'primary_paper': later['selected_primary']['source_id']},
                'citation': {'run': str(root), 'claim_id': claim['id']}}
            ident = hashlib.sha256(json.dumps([sha(overlay_path), claim['id'], earlier['record_id'],
                earlier['extraction_sha256'], later_id, later['extraction_sha256']]).encode()).hexdigest()[:24]
            plans[ident] = {'id': ident, 'plan': plan,
                **diagnostics,
                'basis': 'Accepted citation claim plus exact article/text candidate; no workflow correspondence accepted yet.'}
    return {'plans': list(plans.values()), 'unbound_contributions': unresolved}


async def execute(args):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Native workflow connection checks require Sherlock Slurm')
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    history = args.history_run.resolve(); index = read(history / 'methods.json')
    current = read(history / 'state.json')
    citation_runs = {mid: row['path'] for mid,row in current['citation_runs'].items()
        if row['status'] == 'completed' and read(Path(row['path']) / 'state.json')['status'] == 'completed'}
    selection = binding_candidates(index, citation_runs)
    write(output / 'selection.json', selection)
    state = {'status': 'binding_workflows', 'job_id': os.environ['SLURM_JOB_ID'],
        'history_run': str(history), 'citation_runs': citation_runs, 'binding_runs': {},
        'model': 'gpt-6.1-sol', 'effort': 'xhigh'}
    write(output / 'state.json', state)
    semaphore = asyncio.Semaphore(args.workers)

    async def one(row):
        async with semaphore:
            ident = row['id']; plan = output / 'plans' / (ident + '.json')
            write(plan, row['plan']); destination = output / 'bindings' / ident
            item = {'status': 'running', 'path': str(destination), 'plan': str(plan)}
            state['binding_runs'][ident] = item; write(output / 'state.json', state)
            command = [sys.executable, str(args.runtime / 'experiments/citation_lineage_pilot/pair_run.py'),
                'run', '--plan', str(plan), '--output', str(destination)]
            try:
                with (output / (ident + '.log')).open('wb') as stream:
                    process = await asyncio.create_subprocess_exec(*command, stdout=stream, stderr=asyncio.subprocess.STDOUT)
                    code = await process.wait()
                native = read(destination / 'state.json') if (destination / 'state.json').exists() else {}
                item.update(status='completed' if code == 0 and native.get('status') == 'completed' else 'failed',
                    exit_code=code, reason=native.get('error'), native_state=native.get('status'))
            except Exception as exc:
                item.update(status='failed', reason=f'{type(exc).__name__}: {exc}')
            write(output / 'state.json', state)

    await asyncio.gather(*(one(row) for row in selection['plans']))
    overlays = []
    for mid, run in citation_runs.items():
        root = Path(run); binding = read(root / 'inputs/seed_binding.json')
        overlays.append({'path': str(root / 'graph.json'), 'sha256': sha(root / 'graph.json'),
            'seed_extraction': str(Path(binding['parent_attempt']) / 'extraction.json'),
            'source_registry': {'path': str(root / 'inputs/sources.json'), 'sha256': sha(root / 'inputs/sources.json')}})
    for row in state['binding_runs'].values():
        if row['status'] != 'completed': continue
        root = Path(row['path']); load_reviewed_binding(root / 'graph.json', sha(root / 'graph.json'))
        overlays.append({'path': str(root / 'graph.json'), 'sha256': sha(root / 'graph.json'),
            'source_registry': {'path': str(root / 'inputs/sources.json'), 'sha256': sha(root / 'inputs/sources.json')}})
    # Include already reviewed neighborhoods without scheduling them again.
    # Reopen their pins; paths in old records themselves remain unchanged.
    loaded = {entry['sha256'] for entry in overlays}
    for previous in getattr(args, 'prior_manifests', []):
        previous = Path(previous).resolve()
        build_history_index(previous)
        for entry in read(previous)['overlays']:
            if entry['sha256'] in loaded: continue
            registered = dict(entry)
            for key in ('path', 'seed_extraction'):
                if key in registered: registered[key] = str((previous.parent / registered[key]).resolve())
            if 'source_registry' in registered:
                spec = registered['source_registry']
                registered['source_registry'] = {**spec, 'path': str((previous.parent / spec['path']).resolve())}
            overlays.append(registered); loaded.add(entry['sha256'])
    manifest = output / 'history_inputs.json'
    write(manifest, {'format_version': 'illumina_history_inputs/1', 'overlays': overlays})
    graph = build_history_index(manifest)
    write(output / 'history_index.json', graph)
    for mid in citation_runs:
        write(output / 'queries' / (mid + '.json'), technology_history(graph, mid, max_depth=2))
    state.update(status='completed_with_issues' if any(r['status'] == 'failed' for r in state['binding_runs'].values()) else 'completed',
        indexed_relationships=len(graph['edges']),
        reviewed_workflow_connections=sum('source_citation' in e for e in graph['edges']),
        unbound_contributions=len(selection['unbound_contributions']))
    write(output / 'state.json', state); write(output / 'RESULT.json', state)
    return state


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history-run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--runtime', type=Path, required=True)
    p.add_argument('--workers', type=int, choices=range(1, 5), default=2)
    p.add_argument('--prior-manifest', dest='prior_manifests', type=Path, action='append', default=[])
    result = asyncio.run(execute(p.parse_args()))
    raise SystemExit(2 if result['status'] == 'completed_with_issues' else 0)

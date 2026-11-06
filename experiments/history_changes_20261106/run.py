"""Pilot explicit changes from full papers over already accepted extractions."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from genomics_discovery.illumina.extraction import read, sha, write
from genomics_discovery.illumina.history_inputs import resolve_article, snapshot_accepted
from experiments.diagram_paper_pilot_20261106.run import checked_files, inside
from experiments.pilot50_20261104.history_stage import prepare_index, pair_candidates


def reference_cache(snapshot, library, vault, output):
    """Reuse registered original full texts; cached evidence is still unreviewed."""
    from importlib.util import spec_from_file_location, module_from_spec
    scripts = Path(__file__).resolve().parents[1] / 'citation_lineage_pilot'
    sys.path.insert(0, str(scripts))
    spec = spec_from_file_location('history_citation_runner', scripts / 'run.py')
    runner = module_from_spec(spec); spec.loader.exec_module(runner)
    papers, skipped, duplicates = {}, [], []

    def admit(source, origin):
        pmid = str(source.get('pmid') or '')
        if not pmid.isdecimal() or source.get('document_role') == 'supplement':
            return
        try:
            source = {**source, 'original': resolve_article(source)}
            runner.validate_cached_reference(pmid, source)
        except Exception as exc:
            skipped.append({'origin': origin, 'pmid': pmid, 'reason': str(exc)})
            return
        if pmid in papers:
            duplicates.append({'pmid': pmid, 'origin': origin,
                'same_article_and_text': (source['original']['sha256'] == papers[pmid]['original']['sha256']
                                          and source['text'] == papers[pmid]['text'])})
            return
        papers[pmid] = {**source, 'cache_origin': origin}

    for row in read(snapshot / 'state.json')['cards'].values():
        for sid, source in read(Path(row['attempt_directory']) / 'source_registry.json').items():
            if source.get('content_level') == 'fulltext':
                admit(source, str(row['attempt_directory']) + ':' + sid)
    inventory_path = library / 'canonical_inventory.json'
    inventory = read(inventory_path)
    for method in inventory['methods'].values():
        for reference in method.get('method_references', []):
            pmid = str(reference.get('pmid') or '')
            if pmid in papers or reference.get('availability') != 'fulltext_acquired' or reference.get('held_files'):
                continue
            files = reference.get('cached_files', [])
            texts = [f for f in files if f['kind'] == 'reading-text']
            originals = [f for f in files if f['kind'] == 'article']
            if len(texts) != 1 or len(originals) != 1:
                continue
            try:
                pins = checked_files(vault, files)
                text_path = inside(vault, texts[0]['path'])
                admit({'kind': 'text', 'content_level': 'fulltext', 'pmid': pmid,
                    'title': reference.get('resolved_title') or reference.get('citation'),
                    'url': reference.get('url'), 'path': str(text_path), 'sha256': texts[0]['sha256'],
                    'text': text_path.read_text(), 'original': {
                        'path': str(inside(vault, originals[0]['path'])), 'sha256': originals[0]['sha256']},
                    'custody_files': [*pins, {'path': str(inventory_path), 'sha256': sha(inventory_path)}]},
                    'curated method reference: ' + method['id'])
            except Exception as exc:
                skipped.append({'pmid': pmid, 'reason': str(exc)})
    write(output, {'format_version': 'atlas_reference_cache/1', 'papers': papers})
    write(output.with_name('reference_cache_receipt.json'), {
        'publications': len(papers), 'skipped': skipped, 'duplicate_registrations': duplicates,
        'precedence': 'Existing admitted-workflow source first, then first verified curated publication assignment. Different renditions remain recorded, not merged.',
        'boundary': 'Publication lookup only; no method-version identity, historical adoption or scientific acceptance is established.'})
    return output


async def execute(args):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('History pilot requires a Sherlock Slurm allocation')
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    snapshot = output / 'accepted_snapshot'
    snapshot_result = snapshot_accepted(args.batch, snapshot)
    cards = {c['record_id']: c for c in read(snapshot / 'inventory.json')['cards']}
    selection = {mid: 'paper:' + cards[mid]['curation_binding']['primary_paper_id'].removeprefix('pmid-')
                 for mid in args.seed if mid in cards}
    write(output / 'primary_selection.json', selection)
    index = prepare_index(snapshot, output / 'primary_selection.json')
    write(output / 'methods.json', index)
    cache = reference_cache(snapshot, args.library, args.vault, output / 'reference_cache.json')
    state = {'status': 'citation_research', 'job_id': os.environ['SLURM_JOB_ID'],
        'seeds': args.seed, 'citation_runs': {}, 'snapshot': snapshot_result, 'workers': args.workers,
        'analysis_format': 'citation_analysis/4', 'model': 'gpt-6.1-sol', 'effort': 'xhigh'}
    write(output / 'state.json', state)
    semaphore = asyncio.Semaphore(args.workers)

    async def one(mid):
        async with semaphore:
            method = index['methods'].get(mid)
            if not method or method['status'] != 'ready':
                state['citation_runs'][mid] = {'status': 'source_hold', 'reason': method.get('hold_reason') if method else 'Extraction not yet accepted'}
                write(output / 'state.json', state); return
            destination = output / 'citations' / mid
            paper = method['selected_primary']
            command = [sys.executable, str(args.runtime / 'experiments/citation_lineage_pilot/run.py'), 'run',
                '--batch', str(snapshot), '--inventory', str(args.batch / 'inventory.json'), '--record', mid,
                '--paper', paper['source_id'], '--article', paper['article'], '--source-cache', str(cache),
                '--output', str(destination)]
            row = {'status': 'running', 'path': str(destination), 'command': command}
            state['citation_runs'][mid] = row; write(output / 'state.json', state)
            log = output / (mid + '.log')
            try:
                with log.open('wb') as stream:
                    process = await asyncio.create_subprocess_exec(*command, stdout=stream, stderr=asyncio.subprocess.STDOUT)
                    code = await process.wait()
                native = read(destination / 'state.json') if (destination / 'state.json').exists() else {}
                row.update(status='completed' if code == 0 and native.get('status') == 'completed' else 'failed',
                           exit_code=code, native_state=native.get('status'), reason=native.get('error'), log=str(log))
            except Exception as exc:
                row.update(status='failed', reason=f'{type(exc).__name__}: {exc}', log=str(log))
            write(output / 'state.json', state)

    await asyncio.gather(*(one(mid) for mid in args.seed))
    completed = {mid: row['path'] for mid, row in state['citation_runs'].items() if row['status'] == 'completed'}
    write(output / 'pair_candidates.json', pair_candidates(index, completed))
    overlays = []
    for mid, path in completed.items():
        root = Path(path); binding = read(root / 'inputs/seed_binding.json')
        overlays.append({'path': str(root / 'graph.json'), 'sha256': sha(root / 'graph.json'),
            'seed_extraction': str(Path(binding['parent_attempt']) / 'extraction.json'),
            'source_registry': {'path': str(root / 'inputs/sources.json'), 'sha256': sha(root / 'inputs/sources.json')}})
    write(output / 'history_inputs.json', {'format_version': 'illumina_history_inputs/1', 'overlays': overlays})
    state.update(status='completed_with_issues' if any(r['status'] != 'completed'
                 for r in state['citation_runs'].values()) else 'completed', completed_citation_runs=len(completed))
    write(output / 'state.json', state)
    write(output / 'RESULT.json', state)
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('batch', 'library', 'vault', 'runtime', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--seed', action='append', required=True)
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    if len(set(args.seed)) != len(args.seed) or not 1 <= args.workers <= 4:
        parser.error('Distinct explicit seeds and one to four history workers required')
    result = asyncio.run(execute(args))
    raise SystemExit(2 if any(r['status'] == 'failed' for r in result['citation_runs'].values()) else 0)

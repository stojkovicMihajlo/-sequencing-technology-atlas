"""Matched paper-only extraction with/without complete worked output examples."""
import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import time

from genomics_discovery.execution.codex_worker import CodexWorker
from genomics_discovery.illumina.extraction import read, sha, write, extract_registered_workflow
from genomics_discovery.illumina.graph import _load_card, protocol_text
from .sources import prepare


def accepted_examples(batch, methods):
    state = read(batch / 'state.json'); examples, receipts = [], []
    for method in methods:
        row = state['cards'][method]
        if row['status'] != 'supported':
            raise ValueError('Worked example is not an accepted workflow: ' + method)
        root = Path(row['attempt_directory'])
        _load_card(row, read(root / 'card.json'))
        examples.append(read(root / 'extraction.json'))
        receipts.append({'record_id': method, 'source_attempt': str(root),
            'extraction_sha256': row['artifact_pins']['extraction.json'],
            'review_sha256': row['artifact_pins']['review.json']})
    return examples, receipts


async def execute(args):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('New native extraction experiments require Qiu Sherlock Slurm')
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=False)
    began = time.monotonic(); target = read(args.target)
    state = {'status': 'preparing_sources', 'job_id': os.environ['SLURM_JOB_ID'],
        'target': target, 'model': 'gpt-6.1-sol', 'effort': 'xhigh', 'conditions': {}}
    write(root / 'state.json', state)
    try:
        registry = prepare(args.target, root / 'sources')
        examples, receipts = accepted_examples(args.batch, target['example_methods'])
    except Exception as exc:
        state.update(status='failed_preflight', error=f'{type(exc).__name__}: {exc}')
        write(root / 'state.json', state)
        raise
    write(root / 'example_provenance.json', receipts)
    write(root / 'examples.json', examples)
    # The source descriptor is identical in both conditions. No condition or
    # example facts enter this descriptor, the source registry, or review context.
    card = {'record_id': target['record_id'], 'source_title': target['title'], 'source_mode': 'paper',
        'verified_method_names': target['method_names'], 'implementation_scope': target['scope'],
        'require_paper_matches': True, 'references': [{'reference_id': 'primary', 'role': 'references',
            'pmid': target['pmid'], 'url': target['article_url'], 'citation': target['paper_title']}],
        'source_bindings': [{'role': 'primary_paper', 'source_id': 'paper:' + target['pmid'], 'version': target['version']}]}
    write(root / 'method.json', card)
    state.update(status='extracting', source_registry_sha256=sha(root / 'sources/source_registry.json'),
        examples_sha256=sha(root / 'examples.json'),
        condition_map={'condition_a': 'no worked examples', 'condition_b': 'two complete accepted output examples'})
    write(root / 'state.json', state)

    async def one(name, worked):
        destination = root / name
        row = {'status': 'running', 'attempt_directory': str(destination), 'example_count': len(worked)}
        state['conditions'][name] = row; write(root / 'state.json', state)
        started = time.monotonic()
        worker = CodexWorker(model=state['model'], effort=state['effort'], timeout_seconds=None,
            web_search=False, network_access=False, pin_shell_environment=True)
        try:
            result = await extract_registered_workflow(card, registry, [], destination, worker,
                max_revisions=0, author_examples=worked)
            row.update(result)
            if result['status'] == 'supported':
                _load_card(row, card)
            graph = read(destination / 'graph.json')
            (destination / 'workflow.txt').write_text(protocol_text(graph, sources=registry))
            judged = read(destination / 'review.json')
            row.update(review_actions=dict(Counter(c['action'] for c in judged['checks'])),
                nonaccept_checks=[c for c in judged['checks'] if c['action'] != 'accept'],
                source_registry_sha256=sha(destination / 'source_registry.json'),
                stages=[{k: s[k] for k in ('role', 'elapsed_seconds', 'usage') if k in s}
                    for s in read(destination / 'stages.json')])
        except Exception as exc:
            row.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        row['elapsed_seconds'] = time.monotonic() - started
        write(root / 'state.json', state)

    await asyncio.gather(one('condition_a', ()), one('condition_b', examples))
    if any(row.get('source_registry_sha256') != state['source_registry_sha256']
           for row in state['conditions'].values() if row['status'] != 'failed'):
        raise ValueError('Conditions did not use identical target source bytes')
    state.update(status='completed_with_failures' if any(r['status'] == 'failed' for r in state['conditions'].values())
        else 'completed', elapsed_seconds=time.monotonic() - began,
        boundary='One paired transfer pilot; independent model review, not expert ground truth or general proof of few-shot improvement. Operation counts are descriptive, not a score.')
    write(root / 'state.json', state); write(root / 'RESULT.json', state)
    return state


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target', type=Path, required=True)
    p.add_argument('--batch', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    result = asyncio.run(execute(p.parse_args()))
    raise SystemExit(2 if result['status'] == 'completed_with_failures' else 0)

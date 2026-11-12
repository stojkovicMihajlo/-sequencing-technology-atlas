"""Prepare a source-ready method history cohort; never calls a model.

Run in Qiu Slurm. The input is the current accepted extraction batch, not an
older display export. Citation counts guide sampling; they are not history edges.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import copy
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from genomics_discovery.illumina.extraction import read, sha, write
from genomics_discovery.illumina.history_inputs import snapshot_accepted
from experiments.pilot50_20261104.history_stage import prepare_index


PILOT_METHODS = frozenset({'atac-seq', 'chipmentation', 'bischip-seq', 'dr-seq'})
PROVIDER_EXCLUSIONS = frozenset({'clickseq', 'maine-seq', 'mnase-seq', 'cirseq', 'shape-map'})
EXCLUSIONS = PILOT_METHODS | PROVIDER_EXCLUSIONS


class SelectionNotReady(ValueError):
    pass


def expanded_method_inventory(inventory, canonical, *, canonical_sha256, assigned_methods=()):
    """Expose curated named methods as metadata, without adding model seeds.

    Existing extraction cards keep their source fields. No label similarity or
    shared-paper rule merges canonical IDs. Only method-labelled reference lists
    and explicit assigned paper IDs supply method PMIDs, never dependency lists.
    """
    result = copy.deepcopy(inventory)
    cards = result['cards']
    if len({c['record_id'] for c in cards}) != len(cards):
        raise ValueError('Duplicate canonical IDs in original method inventory')
    by_id = {c['record_id']: c for c in cards}
    assignments = {m['method_id']: m for m in assigned_methods}
    methods = {}
    for key, method in canonical['methods'].items():
        if method.get('entry_type') != 'named_method':
            continue
        if method['id'] != key or method.get('identity_status') != 'catalogue_identity_reviewed':
            raise ValueError('Named method lacks the declared reviewed catalogue identity: ' + key)
        methods[key] = method

    def pmids(mid, card=None):
        ids = set()
        assignment = assignments.get(mid, {})
        paper_ids = [*assignment.get('assigned_paper_ids', []), assignment.get('primary_paper_id'),
                     (card or {}).get('curation_binding', {}).get('primary_paper_id')]
        for paper in paper_ids:
            if isinstance(paper, str) and paper.startswith('pmid-') and paper[5:].isdecimal():
                ids.add(paper[5:])
        for ref in methods.get(mid, {}).get('method_references', []):
            value = str(ref.get('pmid') or '')
            if value.isdecimal() and ':dependency:' not in str(ref.get('reference_id', '')):
                ids.add(value)
        return sorted(ids)

    original_count = len(cards)
    for mid, card in by_id.items():
        if 'method_pmids' not in card:
            card['method_pmids'] = pmids(mid, card)
    added = []
    for mid, method in sorted(methods.items()):
        if mid in by_id:
            continue
        description = method.get('description') or {}
        text = description.get('text', '') if isinstance(description, dict) else description
        references = [{key: ref[key] for key in ['reference_id','label','citation','pmid','doi','url','role','association_basis','identity_check'] if key in ref}
                      for ref in method.get('method_references', [])
                      if ':dependency:' not in str(ref.get('reference_id', ''))]
        card = {'record_id': mid, 'source_title': method['name'],
            'verified_method_names': list(dict.fromkeys([method['name'], *method.get('aliases', [])])),
            'description_verbatim': text, 'description_source': copy.deepcopy(description),
            'source_page_url': description.get('url') if isinstance(description, dict) else None,
            'references': references, 'method_pmids': pmids(mid),
            'identity_status': method['identity_status'], 'identity_decisions': copy.deepcopy(method.get('identity_decisions', [])),
            'classification_metadata': copy.deepcopy(method.get('category_assignments', [])),
            'curation_provenance': {'canonical_inventory_sha256': canonical_sha256, 'canonical_method_id': mid},
            'catalogue_only': True}
        cards.append(card)
        added.append(mid)
    result['method_catalogue_provenance'] = {
        'canonical_inventory_sha256': canonical_sha256,
        'original_inventory_methods': original_count, 'curated_named_methods': len(methods),
        'added_catalogue_only_methods': added, 'total_method_entries': len(cards),
        'boundary': 'Known named-method lookup only; additional catalogue entries are not accepted workflows or new investigation seeds.'}
    return result


def category(card):
    """Keep the curated folder classification; do not classify scientific prose."""
    if isinstance(card.get('category'), list) and card['category'] and all(isinstance(x, str) for x in card['category']):
        return tuple(card['category'])
    package = Path(card.get('curation_binding', {}).get('package', ''))
    if 'methods' in package.parts:
        position = max(i for i, part in enumerate(package.parts) if part == 'methods')
        path = package.parts[position + 1:-2]
        if path:
            return tuple(path)
    paths = sorted({tuple(x['path']) for x in card.get('source_category_path', []) if x.get('path')})
    return tuple(' / '.join(x) for x in paths) or ('Unclassified metadata',)


def eligible_methods(index, cards):
    ready, excluded = {}, {}
    for mid, method in sorted(index['methods'].items()):
        if mid in EXCLUSIONS:
            excluded[mid] = 'already_running_history_pilot' if mid in PILOT_METHODS else 'explicit_provider_exclusion'
            continue
        if mid not in cards or not cards[mid].get('curation_binding', {}).get('primary_paper_id'):
            excluded[mid] = 'no_declared_curated_primary'
            continue
        paper = method.get('selected_primary') or {}
        if method.get('status') != 'ready' or paper.get('format') != 'JATS' or not paper.get('seed_eligible'):
            excluded[mid] = method.get('hold_reason') or 'not_source_ready_registered_JATS_primary'
            continue
        ready[mid] = method
    return ready, excluded


def choose_methods(ready, cards, *, count=50, citation_hints=None):
    """Deterministic leaf-category balance, with citation-ranked bucket order."""
    if not 1 <= count <= 50:
        raise ValueError('Select between 1 and 50 methods')
    if len(ready) < count:
        raise SelectionNotReady(f'Only {len(ready)} source-ready methods; {count} required. No model calls made.')
    hints = citation_hints or {}
    buckets = defaultdict(list)
    for mid in ready:
        buckets[category(cards[mid])].append(mid)
    queues = [deque(sorted(ids, key=lambda mid: (-len(hints.get(mid, [])), mid)))
              for _, ids in sorted(buckets.items())]
    selected = []
    while len(selected) < count:
        for queue in queues:
            if queue:
                selected.append(queue.popleft())
                if len(selected) == count:
                    break
    return selected


def citation_hints(ready):
    """Read reference identifiers only; no adoption or lineage inference."""
    available = {str(m['selected_primary'].get('pmid') or '') for m in ready.values()} - {''}
    hints = {}
    for mid, method in ready.items():
        paper = method['selected_primary']
        tree = ET.parse(paper['article'])
        cited = {''.join(element.itertext()).strip()
                 for reference in tree.iter() if reference.tag.rsplit('}', 1)[-1] == 'ref'
                 for element in reference.iter()
                 if element.tag.rsplit('}', 1)[-1] == 'pub-id' and element.get('pub-id-type') == 'pmid'}
        hints[mid] = sorted((cited & available) - {str(paper.get('pmid') or '')})
    return hints


def prepare(batch, output, *, library, count=50, expected_source_job='52055656'):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Source readiness preparation requires a Sherlock Slurm allocation')
    batch, output = Path(batch).resolve(), Path(output).resolve()
    source_state = read(batch / 'state.json')
    if str(source_state.get('slurm_job_id')) != str(expected_source_job):
        raise ValueError('Input is not the declared current extraction job')
    output.mkdir(parents=True, exist_ok=False)
    receipt = {'status': 'checking', 'source_batch': str(batch), 'source_job': str(expected_source_job),
               'required_methods': count, 'model_calls': 0,
               'boundary': 'Source readiness and selection hints only; no historical claims or agent execution.'}
    write(output/'PREFLIGHT.json', receipt)
    try:
        # Keep the complete canonical inventory separately from accepted-only
        # workflow lookup, so a known unextracted donor can keep its method ID.
        original_inventory = output/'original_method_inventory.json'
        original_inventory.write_bytes((batch/'inventory.json').read_bytes())
        canonical_path = Path(library)/'canonical_inventory.json'
        canonical = read(canonical_path)
        assignments_path = batch.parent/'selected.json'
        assignments = read(assignments_path).get('methods', []) if assignments_path.exists() else []
        expanded = expanded_method_inventory(read(original_inventory), canonical,
            canonical_sha256=sha(canonical_path), assigned_methods=assignments)
        inventory_path = output/'method_inventory.json'
        write(inventory_path, expanded)
        receipt.update(method_inventory=str(inventory_path), method_inventory_sha256=sha(inventory_path),
            original_method_inventory_sha256=sha(original_inventory),
            canonical_inventory_sha256=sha(canonical_path),
            assigned_method_selection_sha256=sha(assignments_path) if assignments_path.exists() else None,
            method_catalogue=expanded['method_catalogue_provenance'])
        snapshot = output/'accepted_snapshot'
        receipt['snapshot'] = snapshot_accepted(batch, snapshot)
        inventory = read(snapshot/'inventory.json')['cards']
        if len({c['record_id'] for c in inventory}) != len(inventory):
            raise ValueError('Duplicate canonical methods in snapshot inventory')
        cards = {c['record_id']: c for c in inventory}
        primary = {mid: 'paper:' + c['curation_binding']['primary_paper_id'].removeprefix('pmid-')
                   for mid, c in cards.items() if c.get('curation_binding', {}).get('primary_paper_id')}
        write(output/'primary_selection.json', primary)
        index = prepare_index(snapshot, output/'primary_selection.json')
        write(output/'methods.json', index)
        ready, excluded = eligible_methods(index, cards)
        receipt.update(source_ready_methods=len(ready), excluded=excluded, selected_methods=[])
        write(output/'PREFLIGHT.json', receipt)
        if len(ready) < count:
            raise SelectionNotReady(f'Only {len(ready)} source-ready methods; {count} required. No model calls made.')
        hints = citation_hints(ready)
        write(output/'selection_hints.json', {'meaning': 'References to other ready publications, for sampling only; not ancestry.',
                                            'cited_ready_pmids': hints})
        selected = choose_methods(ready, cards, count=count, citation_hints=hints)
        selection = {'format_version': 'citation_history_selection/1', 'seed_methods': selected,
            'review_existing': [], 'prior_manifests': [],
            'selection_reason': 'Exactly source-ready accepted methods from job52055656, excluding the running four-method pilot and named provider-blocked methods. Round-robin curated leaf categories; references to ready publications only rank sampling candidates and do not establish history edges.'}
        write(output/'selection.json', selection)
        receipt.update(status='ready', selected_methods=selected, selection_sha256=sha(output/'selection.json'),
            categories=dict(Counter(' / '.join(category(cards[mid])) for mid in selected)),
            primary_bindings={mid: {k: ready[mid]['selected_primary'].get(k) for k in
                ['source_id','pmid','article_sha256','text_sha256','format']} for mid in selected})
    except Exception as exc:
        receipt.update(status='blocked', error=f'{type(exc).__name__}: {exc}')
        write(output/'PREFLIGHT.json', receipt)
        raise
    write(output/'PREFLIGHT.json', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--count', type=int, choices=range(1, 51), default=50)
    parser.add_argument('--expected-source-job', default='52055656')
    args = parser.parse_args()
    try:
        result = prepare(args.batch, args.output, library=args.library, count=args.count, expected_source_job=args.expected_source_job)
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc), 'model_calls': 0}))
        raise SystemExit(2)
    print(json.dumps({k: result[k] for k in ['status','source_ready_methods','selected_methods','categories','model_calls']}))

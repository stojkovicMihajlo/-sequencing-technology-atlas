"""Bind history research to registered articles and immutable accepted workflows.

This module recovers existing custody declarations, not new scientific facts.
It never edits the extraction batch or its original source registrations.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import xml.etree.ElementTree as ET

from .extraction import checkpoint, now, read, sha, verify_registry_files, write
from .graph import build_graph, load_reviewed_workflow


def is_html_article(raw):
    return re.match(br'\s*(?:<!doctype\s+html|<html\b)',raw,re.I) is not None


def html_article_text(source, raw):
    """Reopen a registered HTML rendition; never regenerate old source text."""
    from genomics_discovery.sources.cached_documents import ArticleHTML
    parser=ArticleHTML();parser.feed(raw.decode('utf-8'));parser.close()
    if not source.get('pmid') or {p.strip() for p in parser.metadata.get('citation_pmid',[])}!={str(source['pmid'])}:
        raise ValueError('Registered HTML original disagrees with its declared publication PMID')
    path=Path(source.get('path',''))
    if (not source.get('sha256') or not path.is_file() or sha(path)!=source['sha256']
            or path.read_text()!=source.get('text') or not source.get('text','').strip()):
        raise ValueError('HTML citation tracing requires the exact pinned registered text rendition')
    return source['text']


def resolve_article(source):
    """Resolve a declared original, never a filename/title similarity candidate."""
    if source.get('content_level') != 'fulltext' or source.get('document_role') == 'supplement':
        raise ValueError('Require a full-text primary article, not metadata or a supplement')
    verify_registry_files({'source': source})
    original = source.get('original')
    if original and original.get('path'):
        original = copy.deepcopy(original)
    else:
        pmid = str(source.get('pmid') or '')
        if not pmid.isdecimal():
            raise ValueError('Curated original recovery requires a declared publication PMID')
        pins = source.get('custody_files', [])
        declared = set()
        for pin in pins:
            package_path = Path(pin['path'])
            if package_path.name != 'source_package.json':
                continue
            # All pins were verified above before interpreting the declaration.
            package = read(package_path)
            for paper in package.get('method_papers', []):
                if paper['paper_id'] == 'pmid-' + pmid:
                    declared.update(f['sha256'] for f in paper['files'] if f['kind'] == 'article')
        if len(declared) != 1:
            raise ValueError('Require one unambiguous article declared for this PMID in pinned source packages')
        expected = next(iter(declared))
        matches = [p for p in pins if p['sha256'] == expected]
        if not matches:
            raise ValueError('Declared article has no pinned original file')
        original = {**matches[0], 'binding_basis': 'exact publication assignment and article hash in pinned source package'}
    path = Path(original.get('path', ''))
    if not path.is_file() or sha(path) != original.get('sha256'):
        raise ValueError('Primary paper original differs from its registered hash or is missing')
    raw = path.read_bytes()
    if not raw.startswith(b'%PDF-'):
        try:
            tree = ET.fromstring(raw)
        except ET.ParseError:
            tree = None
        if tree is not None and tree.tag.rsplit('}', 1)[-1] == 'article':
            front = next((e for e in tree if e.tag.rsplit('}', 1)[-1] == 'front'), None)
            meta = next((e for e in front if e.tag.rsplit('}', 1)[-1] == 'article-meta'), None) if front is not None else None
            own_ids = {''.join(e.itertext()).strip() for e in meta
                       if e.tag.rsplit('}', 1)[-1] == 'article-id' and e.get('pub-id-type') == 'pmid'} if meta is not None else set()
            if own_ids and source.get('pmid') and own_ids != {str(source['pmid'])}:
                raise ValueError('Registered original article PMID disagrees with its own front matter')
    return original


def snapshot_accepted(batch, output, record_ids=()):
    """Freeze admitted rows while another extraction batch continues to change."""
    batch, output = Path(batch).resolve(), Path(output).resolve()
    if output == batch or output.is_relative_to(batch):
        raise ValueError('History snapshot must be outside the active extraction batch')
    state_bytes = (batch / 'state.json').read_bytes()
    state, inventory = json.loads(state_bytes), read(batch / 'inventory.json')
    if sha(batch / 'inventory.json') != state['policy']['inventory_sha256']:
        raise ValueError('Source inventory changed')
    requested = set(record_ids) or set(state['cards'])
    if requested - state['cards'].keys():
        raise ValueError('Requested snapshot method absent from source batch')
    kept, cards, excluded = {}, [], {}
    for card in inventory['cards']:
        ident = card['record_id']
        if ident not in requested:
            continue
        row = state['cards'][ident]
        if row['status'] != 'supported':
            excluded[ident] = {'reason': 'not_supported', 'source_status': row['status']}
            continue
        try:
            load_reviewed_workflow(row, card)
        except Exception as exc:
            excluded[ident] = {'reason': 'admission_reopening_failed', 'error': f'{type(exc).__name__}: {exc}'}
            continue
        cards.append(copy.deepcopy(card))
        kept[ident] = copy.deepcopy(row)
    if not kept:
        raise ValueError('No supported workflow passed snapshot admission')
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'inventory.json', {'cards': cards})
    write(output / 'selection.json', {'record_ids': sorted(kept), 'purpose': 'Immutable accepted-workflow history input'})
    policy = {**copy.deepcopy(state['policy']), 'record_ids': sorted(kept),
              'inventory_sha256': sha(output / 'inventory.json'),
              'selection_sha256': sha(output / 'selection.json')}
    receipt = {'source_batch': str(batch), 'source_state_sha256_at_snapshot': hashlib.sha256(state_bytes).hexdigest(),
               'source_inventory_sha256': sha(batch / 'inventory.json'), 'created_at': now(),
               'excluded': excluded, 'original_attempts_copied_or_modified': False}
    frozen = {'policy': policy, 'cards': kept, 'snapshot_provenance': receipt}
    checkpoint(output, frozen, cards)
    graph = build_graph(output, output / 'inventory.json')
    if any(c.get('admission') != 'supported' for c in graph['cards'].values()):
        raise ValueError('Snapshot graph failed fresh publication checks')
    write(output / 'atlas.json', graph)
    write(output / 'snapshot_receipt.json', receipt)
    return {'snapshot': str(output), 'accepted_methods': sorted(kept), 'excluded': excluded}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--record', action='append', default=[])
    args = parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        raise SystemExit('Snapshot admission checks run in a Sherlock Slurm allocation')
    print(json.dumps(snapshot_accepted(args.batch, args.output, args.record), indent=2))

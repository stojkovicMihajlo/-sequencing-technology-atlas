"""Organize original Illumina names/sources; never use extracted graphs as evidence.

A technology directory can contain several implementations. Source membership is
not permission to combine their operations or an admission of historical ancestry.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path


def read(path):
    path = Path(path)
    if path.suffix == '.gz':
        with gzip.open(path, 'rt') as stream:
            return json.load(stream)
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def key(name):
    table = str.maketrans({"’": "'", "′": "'", "‘": "'", "‐": "-", "‑": "-", "–": "-", "—": "-", "−": "-"})
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", name).translate(table)).strip().casefold()


def slug(name):
    text = key(name).replace("'", "-prime-").replace("&", "-and-").replace("ψ", "psi-symbol")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def catalogue_group(method):
    """Group literal catalogue placements, not inferred measurement capabilities.

    A DNA poster is not the Genome category: it also contains epigenetic assays.
    Similarly, repeated appearance in posters is not evidence of multi-omics.
    Poster-only entries retain their original sections pending category curation.
    """
    domains = sorted({c['path'][0] for c in method.get('classifications', [])
                      if c.get('path') and c['path'][0] in
                      ('Genome', 'Transcriptome', 'Epigenome')})
    if len(domains) > 1:
        return ['Combined catalogue categories', ' + '.join(domains)]
    if domains:
        return domains
    return ['Category review pending']


def taxonomy(paths):
    """Recover original category paths from the saved Illumina CMS page tree."""
    result = defaultdict(list)
    for path in paths:
        root = read(path)
        domain = root['jcr:content']['jcr:title']
        for section_id, section in root.items():
            if not isinstance(section, dict) or section_id == 'jcr:content':
                continue
            title = section.get('jcr:content', {}).get('jcr:title')
            if not title:
                continue
            for card_id, card in section.items():
                if not isinstance(card, dict) or card_id == 'jcr:content' or card.get('jcr:primaryType') != 'cq:Page':
                    continue
                result[card_id].append({'path': [domain, title], 'source': str(path),
                    'locator': f'/{section_id}/{card_id}', 'basis': 'original_illumina_taxonomy'})
    return result


SECTION_NAMES = {
    'rna_poster': {'RNA Transcription', 'RNA-Protein Interactions', 'RNA Low-Level Detection', 'RNA Modifications', 'RNA Structure'},
    'dna_poster': {'DNA Rearrangements and Markers', 'DNA Low-Level Detection', 'Epigenetics', 'DNA-Protein Interactions', 'Protein-Protein Interactions'},
    'single_cell_poster': {'RNA Low-Level Detection', 'DNA Low-Level Detection', 'Integrated Techniques'},
}


def poster_sections(index, rows):
    """Read printed section headings; preserve cross-column continuations.

Only the three inspected poster layouts use this three-column rule. It is not a
general PDF classifier. Every recovered label retains its exact word IDs.
"""
    resources = {r['resource_id']: r['resource_kind'] for r in rows}
    sections = {}
    for rid, kind in resources.items():
        page = read(Path(index) / rid / 'page_0001.json')
        lines = defaultdict(list)
        for w in page['words']:
            x, y, x1, y1 = w['bbox']
            if 22 < y1 - y < 60:
                lines[(int(x // (page['width'] / 3)), round(y / 5) * 5)].append(w)
        headings = []
        for (column, y), words in lines.items():
            words = sorted(words, key=lambda w: w['bbox'][0])
            label = ' '.join(w['text'] for w in words)
            if label in SECTION_NAMES[kind]:
                headings.append({'label': label, 'column': column, 'y': y,
                    'word_ids': [w['word_id'] for w in words]})
        if {r['label'] for r in headings} != SECTION_NAMES[kind]:
            raise ValueError('Poster headings differ from inspected layout: ' + rid)
        sections[rid] = (page, headings)
    result = {}
    for row in rows:
        page, headings = sections[row['resource_id']]
        x, y, *_ = row['exact_label_bboxes'][0]
        column = int(x // (page['width'] / 3))
        candidates = [h for h in headings if (h['column'], h['y']) <= (column, y)]
        if not candidates:
            raise ValueError('No preceding printed section: ' + row['row_id'])
        h = max(candidates, key=lambda h: (h['column'], h['y']))
        result[row['row_id']] = {'path': [row['resource_kind'], h['label']],
            'source': row['source_url'], 'locator': h['word_ids'],
            'basis': 'printed_poster_heading; column_continuation'}
    return result


def build(archive, source_root, decisions):
    with gzip.open(archive, 'rt') as f:
        records = [json.loads(line) for line in f if line.strip()]
    if len({r['record_id'] for r in records}) != len(records):
        raise ValueError('Duplicate source occurrence')
    source_root = Path(source_root)
    taxpaths = [source_root / 'sources/live_cms_snapshot' / ('taxonomy-' + n + '.json')
                for n in ('rna', 'dna', 'epigenome')]
    categories = taxonomy(taxpaths)
    overlay_path = source_root / 'poster_visual_review_01/inventory_overlay_proposals.json'
    overlay = read(overlay_path)
    section_index = poster_sections(source_root / 'sources/poster_index_01', overlay['rows'])
    row_index = {r['row_id']: r for r in overlay['rows']}
    row_for_record = {'poster_' + hashlib.sha256(r['row_id'].encode()).hexdigest()[:24]: r['row_id']
                      for r in overlay['rows']}
    aliases = {key(r['alias']): r for r in decisions['name_bindings']}
    scoped_names = {(r['record_id'], key(r['source_name'])): r for r in decisions.get('scoped_names', [])}
    methods, occurrences = {}, {}
    for record in records:
        rid, card = record['record_id'], record['card']
        title = card.get('source_title') or record['title']
        members = card.get('source_method_labels') or [s.strip() for s in title.split('/') if s.strip()]
        # Literal labels are an organizational index. Variants and grouped source
        # applicability remain explicit; no assay equivalence is asserted here.
        named = []
        cats = list(categories.get(rid, []))
        if card.get('source_category_path') and record['batch'] in ('catalogue', 'initial_two'):
            cats.append({'path': card['source_category_path'], 'source': card.get('source_page_url'),
                         'basis': 'archived_illumina_category'})
        poster_row = row_for_record.get(rid) or next((ref.get('source_reference', {}).get('source_row_id')
            for ref in card.get('references', []) if ref.get('source_reference', {}).get('source_row_id')), None)
        if poster_row in section_index:
            cats.append(section_index[poster_row])
        elif record['batch'] not in ('catalogue', 'initial_two'):
            cats.append({'path': card.get('source_category_path', []),
                'source': card.get('source_page_url'), 'basis': 'resource_category; section_unresolved'})
        for name in members:
            decision = scoped_names.get((rid, key(name))) or aliases.get(key(name))
            canonical = decision['name'] if decision else name
            ident = slug(canonical)
            if not ident:
                raise ValueError('Empty technology name: ' + name)
            if ident in methods and key(methods[ident]['name']) != key(canonical):
                raise ValueError('Directory-name collision: ' + canonical)
            m = methods.setdefault(ident, {'id': ident, 'name': canonical, 'source_names': [],
                'source_records': [], 'classifications': [], 'name_decisions': [],
                'kind': 'supporting_resource' if record['batch'] == 'footer_panels' else 'technology',
                'workflow_identity': 'source_collection; implementations_not_merged',
                'extraction_ready': False})
            if name not in m['source_names']:
                m['source_names'].append(name)
            if rid not in m['source_records']:
                m['source_records'].append(rid)
            for c in cats:
                if c not in m['classifications']:
                    m['classifications'].append(c)
            if decision and decision not in m['name_decisions']:
                m['name_decisions'].append(decision)
            if record['batch'] != 'footer_panels':
                m['kind'] = 'technology'
            named.append(ident)
        occurrences[rid] = {'record_id': rid, 'title': title, 'members': sorted(set(named)),
            'batch': record['batch'], 'card': card,
            'registry': str(Path(record['attempt_directory']) / 'source_registry.json'),
            'poster_row': row_index.get(poster_row),
            'classifications': cats,
            'shared_source': len(set(named)) > 1}
        cms = source_root / 'sources/live_cards' / (rid + '.json')
        if cms.is_file():
            occurrences[rid]['original_cms'] = {'path': str(cms), 'sha256': sha(cms), 'content': read(cms)}
    possible = defaultdict(list)
    for mid, method in methods.items():
        possible[re.sub(r'[^a-z0-9]', '', key(method['name']))].append(mid)
    unresolved = [v for v in possible.values() if len(v) > 1]
    return {'format_version': 'technology_source_collection/1',
        'inputs': [{'path': str(Path(p).resolve()), 'sha256': sha(p)} for p in [archive, overlay_path, *taxpaths]],
        'methods': methods, 'occurrences': occurrences, 'possible_name_collisions': unresolved,
        'policy': {'no_model_outputs_used': True, 'no_extraction_dispatched': True,
            'grouped_sources_do_not_imply_identical_workflows': True,
            'paper_listing_is_not_origin_or_ancestry_verification': True},
        'counts': {'source_occurrences': len(occurrences),
            'technology_directories': sum(m['kind'] == 'technology' for m in methods.values()),
            'supporting_resource_directories': sum(m['kind'] == 'supporting_resource' for m in methods.values()),
            'scientifically_verified_distinct_technology_count': None}}

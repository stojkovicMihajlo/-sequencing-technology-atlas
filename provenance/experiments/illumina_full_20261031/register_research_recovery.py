"""Register explicit source-backed recovery leads into a new source snapshot.

This imports documents and bibliographic leads. It does not assign scientific
method identity, edit literal references, or create development relationships.
Run inside the allocation; use a new output path for each registration.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from collections import defaultdict, deque

sys.path.insert(0, str(Path(os.environ['ATLAS_REPOSITORY']) / 'scripts'))
from illumina_merge_sources import SnapshotFiles, verify_cached_identity
from illumina_cached_sources import identities, xml_article
from genomics_discovery.curation.illumina_batch import card_sources
from genomics_discovery.curation.illumina_sources import parse_pubmed


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def verify_fulltext_identity(pmid, full, publication):
    """Respect each producer's declared reading-block contract, without coercion."""
    if full.get('format') != 'JATS':
        return verify_cached_identity(pmid, full, publication)
    _, own_ids = xml_article(Path(full['path']).read_bytes())
    observed, expected = identities(own_ids), identities(publication)
    if (observed['pmid'] and observed['pmid'] != {pmid}) or (
            observed['doi'] and expected['doi'] and observed['doi'].isdisjoint(expected['doi'])):
        raise ValueError('JATS own article identity disagrees with its PubMed record')
    if not (observed['pmid'] == {pmid} or observed['doi'] & expected['doi']):
        raise ValueError('JATS identity cannot be established')
    blocks = read(full['blocks_path'])
    key = {'jats_reading_blocks/1.0': 'article_sha256',
           'cached_reading_blocks/1.0': 'original_sha256'}.get(blocks.get('format_version'))
    if not key or blocks.get('source_id') != 'paper:' + pmid or blocks.get(key) != full['sha256']:
        raise ValueError('Reading blocks do not bind the declared original article')


def register(source_path, overlay_path, output, mappings, candidates_path=None, interleave=False):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Source registration belongs in a Slurm allocation')
    source_path, overlay_path, output = map(lambda p: Path(p).resolve(), (source_path, overlay_path, output))
    parent, inventory = read(source_path), read(source_path.parent / 'inventory.json')
    if sha(source_path.parent / 'inventory.json') != parent['inventory_sha256']:
        raise ValueError('Parent inventory changed')
    overlay = read(overlay_path)
    output.mkdir(parents=True, exist_ok=False)
    files = SnapshotFiles(output, mappings, [])
    state = files.records(copy.deepcopy(parent), source_path.parent)
    recovered = {key: files.records(overlay.get(key, {}), overlay_path.parent) for key in
                 ('additional_publications', 'additional_fulltexts', 'additional_external_sources')}
    leads = read(candidates_path) if candidates_path else {}
    if candidates_path:
        imported = {key: files.records(leads.get(key, {}), Path(candidates_path).resolve().parent)
                    for key in ('publications', 'fulltexts')}
    else:
        imported = {'publications': {}, 'fulltexts': {}}
    metadata_records = {}
    for publications, fulltexts in ((imported['publications'], imported['fulltexts']),
                                    (recovered['additional_publications'], recovered['additional_fulltexts'])):
        for pmid, publication in publications.items():
            if publication.get('pmid') != pmid or not pmid.isdecimal():
                raise ValueError('Recovery publication does not bind its own PMID')
            prior = state.setdefault('publications', {}).get(pmid)
            if publication.get('identity_status') == 'pubmed_record_retrieved':
                source = publication.get('metadata_source')
                if source and source not in metadata_records:
                    metadata_records[source] = parse_pubmed(Path(source).read_bytes())
                own = metadata_records.get(source, {}).get(pmid)
                if not own or own.get('title') != publication.get('title'):
                    raise ValueError('PubMed metadata source does not contain its declared record')
            elif prior is None:
                raise ValueError('New publication identity is unverified')
            if prior and prior.get('title') and publication.get('title') != prior['title']:
                raise ValueError('One PMID has inconsistent publication titles')
            state['publications'].setdefault(pmid, publication)
        for pmid, full in fulltexts.items():
            if pmid not in state['publications']:
                raise ValueError('Fulltext has no corresponding publication identity')
            if state.setdefault('fulltexts', {}).get(pmid, {}).get('status') != 'fulltext_acquired':
                if full.get('status') == 'fulltext_acquired':
                    verify_fulltext_identity(pmid, full, state['publications'][pmid])
                state['fulltexts'][pmid] = full
    cards = {c['record_id']: c for c in inventory['cards']}
    rows = {c['source_row_id']: c for c in cards.values() if c.get('source_row_id')}
    provenance = {'path': str(overlay_path), 'sha256': sha(overlay_path),
                  'association_status': 'source_backed_recovery_lead; independent_method_review_required'}

    def add_pmid(card, paper, basis, references=()):
        pmid = paper.get('pmid')
        if not pmid:
            return
        if pmid not in state['publications']:
            raise ValueError('Candidate recovery PMID has no verified metadata: ' + pmid)
        values = card.setdefault('candidate_publications', [])
        if any(p['pmid'] == pmid for p in values):
            return
        values.append({'pmid': pmid, 'candidate_basis': basis, 'reference_ids': list(references),
                       'association_status': 'unverified_candidate', 'recovery_source': provenance})

    # The export producer, rather than this importer, is responsible for
    # normalizing provider-specific payloads. Do not guess an identifier or
    # generate an unverified text rendition while attaching a source lead.
    external_by_doi = {}
    for document in recovered['additional_external_sources'].values():
        required = ('doi', 'title', 'identity_status', 'text_path', 'text_sha256')
        if any(not isinstance(document.get(k), str) or not document[k].strip() for k in required):
            raise ValueError('External recovery requires normalized identity and text fields')
        if document.get('content_level') not in {'metadata', 'abstract', 'fulltext'}:
            raise ValueError('External recovery requires an explicit content level')
        if sha(document['text_path']) != document['text_sha256']:
            raise ValueError('External recovery text custody mismatch')
        doi = document['doi'].casefold()
        if doi in external_by_doi and external_by_doi[doi] != document:
            raise ValueError('Conflicting external recovery records for one DOI')
        external_by_doi[doi] = document
    def add_external(card, paper, references=()):
        doi = str(paper.get('doi', '')).casefold()
        document = external_by_doi.get(doi)
        if not document:
            return
        refid = card['record_id'] + ':recovery-document:' + hashlib.sha256(doi.encode()).hexdigest()[:16]
        if any(r['reference_id'] == refid for r in card['references']):
            return
        card['references'].append({'reference_id': refid, 'role': 'candidate_publications', 'pmid': None,
            'source_reference': {'doi': document['doi'], 'text': document.get('title'),
                                 'association_status': 'unverified_candidate', 'reference_ids': list(references)}})
        document = copy.deepcopy(document)
        state.setdefault('external_publications', {})[refid] = {**document,
            'association_status': 'candidate_document; literal_poster_identity_not_assumed',
            'recovery_source': provenance}

    for resolution in overlay.get('citation_resolutions', []):
        cid = resolution['citation_id']
        for occurrence in resolution.get('occurrences', []):
            card = rows.get(occurrence['row_id'])
            if card is None:
                continue
            refs = [r['reference_id'] for r in card['references'] if r.get('source_reference', {}).get('citation_id') == cid]
            for paper in resolution.get('candidate_papers', []):
                add_pmid(card, paper, 'independent_bibliographic_reconciliation; literal_reference_unchanged', refs)
                add_external(card, paper, refs)
    for resolution in overlay.get('method_attribution_overlays', []):
        card = rows.get(resolution['row_id'])
        if card:
            add_pmid(card, resolution['proposed_method_paper'],
                     'primary_method_attribution_candidate; compare_against_preserved_literal_citation')
    for resolution in overlay.get('card_scope_overlays', []):
        card = cards.get(resolution['card_id'])
        if card:
            for source in resolution.get('primary_method_sources', []):
                add_pmid(card, source['paper'], 'primary_source_scope_research; no_assay_equivalence_assumed')
    # Literal reference dictionaries must be byte-for-byte unchanged for every
    # original reference. Only optional candidate leads can be appended.
    old_inventory = read(source_path.parent / 'inventory.json')
    for old in old_inventory['cards']:
        current = {r['reference_id']: r for r in cards[old['record_id']]['references']}
        if any(current[r['reference_id']] != r for r in old['references']):
            raise ValueError('A literal source reference was rewritten')
    if interleave:
        groups = defaultdict(deque)
        for card in inventory['cards']:
            groups[card.get('source_category', '')].append(card)
        ordered = []
        while any(groups.values()):
            for group in groups.values():
                if group:
                    ordered.append(group.popleft())
        inventory['cards'] = ordered
        inventory['processing_order'] = 'Deterministic round-robin by source category; per-source row order and Flow semantics preserved.'
    indexed = {p['pmid']: copy.deepcopy(p) for p in inventory.get('publications', [])}
    for card in inventory['cards']:
        for lead in [*card.get('references', []), *card.get('candidate_publications', [])]:
            pmid = lead.get('pmid')
            if not pmid:
                continue
            record = indexed.setdefault(pmid, {'pmid': pmid, 'source_id': 'paper:' + pmid,
                'card_ids': [], 'reference_ids': [], 'reference_roles': []})
            for field, value in [('card_ids', card['record_id']), ('reference_ids', lead.get('reference_id')),
                                 ('reference_roles', lead.get('role', 'candidate_publications'))]:
                if value and value not in record.setdefault(field, []):
                    record[field].append(value)
    inventory['publications'] = list(indexed.values())
    inventory.setdefault('statistics', {}).update(unique_pmids=len(indexed),
        reference_occurrences=sum(len(c.get('references', [])) for c in inventory['cards']))
    inventory['recovery_source'] = provenance
    state['parent_acquisition'] = {'path': str(source_path), 'sha256': sha(source_path)}
    state['independent_recovery_source'] = provenance
    write(output / 'inventory.json', inventory)
    state['inventory_sha256'] = sha(output / 'inventory.json')
    state['statistics'].update(inventory['statistics'])
    state['statistics']['publication_metadata_pool'] = len(state['publications'])
    state['statistics']['fulltexts_acquired'] = sum(v.get('status') == 'fulltext_acquired' for v in state['fulltexts'].values())
    write(output / 'sources.json', state)
    for card in inventory['cards']:
        registry, images = card_sources(card, state, output)
        if not images:
            raise ValueError('Registered card lost its source image')
    write(output / 'recovery_registration.json', {'scientific_review': False, 'parent_source_sha256': sha(source_path),
        'recovery_overlay_sha256': sha(overlay_path), 'inventory_sha256': state['inventory_sha256'],
        'sources_sha256': sha(output / 'sources.json'), 'imports': list(files.imports.values()),
        'counts': {'cards': len(cards), 'publications': len(state['publications']), 'fulltexts': state['statistics']['fulltexts_acquired']}})
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--overlay', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--candidates', type=Path)
    parser.add_argument('--path-map', action='append', default=[])
    parser.add_argument('--interleave-source-categories', action='store_true')
    args = parser.parse_args()
    mappings = [(Path(a), Path(b)) for a, b in (item.split('=', 1) for item in args.path_map)]
    result = register(args.sources, args.overlay, args.output, mappings, args.candidates, args.interleave_source_categories)
    print(json.dumps(result['statistics']))

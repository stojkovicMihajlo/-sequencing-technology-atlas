import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from scripts.illumina.technology_sources.curation import compile_inventory, fingerprint, source_identity_fingerprint
from scripts.illumina.technology_sources.audit_progress import build_register
from scripts.illumina.technology_sources.article_identity import xml_identity


ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / 'provenance/experiments/technology_sources_20261105/identity_review'


def test_review_register_does_not_drop_unassigned_papers_or_promote_downloads():
    paper = {'id': 'a', 'title': 'Assay paper', 'url': 'https://example.org/a',
             'status': 'fulltext_acquired', 'files': [{'kind': 'reading-text', 'sha256': 'abc'}]}
    other = {'id': 'b', 'title': 'Related paper', 'status': 'metadata_only', 'files': []}
    duplicate = {**paper, 'id': 'alias'}
    collection = {'papers': {'a': paper, 'b': other, 'alias': duplicate},
                  'sources': {'s': {'papers': [{'paper_id': 'alias', 'role': 'references'},
                                               {'paper_id': 'b', 'role': 'related_publications'}]}}}
    rows, summary = build_register(collection, [{'paper_id': 'a', 'title': paper['title'],
        'availability': 'fulltext_acquired', 'files': paper['files'], 'methods': ['assay']}], [], {'alias': 'a'})
    assert len(rows) == 2
    assert summary['duplicate_records_consolidated'] == 1
    assert rows[0]['source_occurrences'][0]['record_id'] == 'alias'
    assert rows[1]['scope'] == 'unassigned_source_bibliography'
    assert all(r['review_status'] == 'not_yet_reviewed' for r in rows)
    assert not summary['extraction_authorized']
    with pytest.raises(ValueError, match='identical source'):
        build_register(collection, [], [], {'b': 'a'})


def test_review_register_rejects_changed_reviewed_bytes():
    collection = {'papers': {'a': {'title': 'A', 'files': []}}, 'sources': {}}
    review = {'paper_id': 'a', 'review': 'abstract_checked', 'abstract': 'Original',
              'abstract_sha256': hashlib.sha256(b'Original').hexdigest()}
    build_register(collection, [], [review], {})
    review['abstract'] = 'Changed'
    with pytest.raises(ValueError, match='abstract differs'):
        build_register(collection, [], [review], {})


def test_current_reference_review_overrides_old_acquisition_assignments():
    collection = {'papers': {'a': {'title': 'A', 'files': []}}, 'sources': {}}
    acquired = [{'paper_id': 'a', 'title': 'A', 'files': [], 'methods': ['old_method']}]
    rows, _ = build_register(collection, acquired, [], {}, method_assignments={})
    assert rows[0]['methods'] == []
    assert rows[0]['scope'] == 'unassigned_source_bibliography'
    assert not rows[0]['extraction_authorized']
    with pytest.raises(ValueError, match='Prepare new source papers'):
        build_register(collection, acquired, [], {}, method_assignments={'missing': ['method']})


def test_article_identity_checks_title_even_when_embedded_identifiers_match():
    xml = b'''<article xmlns="urn:jats"><front><article-meta>
    <article-id pub-id-type="pmid">42</article-id><article-id pub-id-type="doi">10.1/a</article-id>
    <title-group><article-title>Later protocol</article-title></title-group>
    </article-meta></front><body/><back><ref-list><ref>
    <article-id pub-id-type="pmid">999</article-id></ref></ref-list></back></article>'''
    expected = {'pmid': '42', 'metadata': {'title': 'Original protocol', 'identifiers': {'doi': ['10.1/a']}}}
    r = xml_identity(xml, expected)
    assert r['status'] == 'requires_identity_review'
    assert r['disagreements'] == ['title_disagreement']
    assert r['article_identifiers']['pmid'] == '42'
    expected['metadata']['title'] = 'Later protocol.'
    r = xml_identity(xml, expected)
    assert r['status'] == 'bibliographic_identity_corresponds'
    assert not r['scientific_content_reviewed']


@pytest.fixture
def records():
    with gzip.open(REVIEW.parent / 'inventory.json.gz', 'rt') as f:
        inv = json.load(f)
    return inv, json.loads((REVIEW / 'decisions.json').read_text()), json.loads((REVIEW / 'reference_bindings.json').read_text())


def compile_records(records):
    return compile_inventory(*records, {})


def test_every_old_folder_and_source_survives(records):
    r = compile_records(records)
    assert {d['previous_id'] for d in r['folder_decisions']} == set(records[0]['methods'])
    assert {s for m in r['methods'].values() for s in m['source_records']} == set(records[0]['occurrences'])
    assert not r['extraction_authorized']


def test_aliases_combine_but_neighboring_assays_do_not(records):
    m = compile_records(records)['methods']
    assert {'art-seq', 'artseq', 'ribo-seq'} == set(m['ribo-seq']['previous_ids'])
    assert 'gti-seq' not in m['ribo-seq']['previous_ids']
    assert not any(b.get('pmid') in {'24203712', '22927429'} for b in m['ribo-seq']['method_references'])
    assert {'drop-chip', 'scchip-seq'} == set(m['drop-chip']['previous_ids'])
    assert {'nanogrid', 'snrs'} == set(m['snrs']['previous_ids'])
    assert {'bischip-seq', 'chip-bs-seq', 'cap-seq', 'capseq', 'psi-seq', 'psi-symbol-seq'} <= m.keys()


def test_homonym_split_also_splits_papers_and_categories(records):
    m = compile_records(records)['methods']
    assert 'nuc-seq' not in m
    assert {b['pmid'] for b in m['nuc-seq-dna']['method_references']} == {'25079324'}
    assert {b['pmid'] for b in m['nuc-seq-rna']['method_references']} == {'27471252'}
    assert '26890679' in {b['pmid'] for b in m['snrna-seq']['method_references']}
    assert not any(c['path'][0] == 'Genome' for c in m['nuc-seq-rna']['classifications'])


def test_wrong_source_citations_never_become_method_references(records):
    m = compile_records(records)['methods']
    for mid, wrong, right in [('os-seq', '22853953', '22020387'),
                              ('hits-clip', '17540862', '19536157'),
                              ('sci-dna-seq', '29545511', '28135258'),
                              ('mosaic-seq', '29474909', '28416141'),
                              ('scrna-seq', '25910207', '19349980'),
                              ('mira', '25798578', '20304072')]:
        actual = {b.get('pmid') for b in m[mid]['method_references']}
        assert wrong not in actual and right in actual
    for mid, expected, rejected in [('scatac-seq-cell-index', '25953818', '26083756'),
                                     ('scatac-seq-microfluidics', '26083756', '25953818')]:
        actual = {b.get('pmid') for b in m[mid]['method_references']}
        assert expected in actual and rejected not in actual


def test_missing_or_stale_reviews_fail_closed(records):
    inv, d, b = copy.deepcopy(records)
    d['decisions'].pop()
    b['decision_fingerprint'] = fingerprint(d)
    with pytest.raises(ValueError, match='Every original'):
        compile_inventory(inv, d, b, {})
    inv, d, b = copy.deepcopy(records)
    inv['occurrences']['atac-seq']['title'] = 'changed source'
    with pytest.raises(ValueError, match='snapshot'):
        compile_inventory(inv, d, b, {})
    inv, d, b = copy.deepcopy(records)
    d['decisions'][0]['reason'] += ' changed review'
    with pytest.raises(ValueError, match='different identity'):
        compile_inventory(inv, d, b, {})


def test_invalid_split_or_reference_is_rejected(records):
    inv, d, b = copy.deepcopy(records)
    split = next(x for x in d['decisions'] if x['action'] == 'split')
    split['assignments'][0]['source_ids'].pop()
    b['decision_fingerprint'] = fingerprint(d)
    with pytest.raises(ValueError, match='each original membership'):
        compile_inventory(inv, d, b, {})
    inv, d, b = copy.deepcopy(records)
    b['bindings'][0]['reference_id'] = 'fabricated'
    with pytest.raises(ValueError, match='Unknown source reference'):
        compile_inventory(inv, d, b, {})


def test_products_families_and_unresolved_labels_are_not_assay_counts(records):
    m = compile_records(records)['methods']
    assert m['3-seq']['entry_type'] == 'unresolved_source_label'
    assert m['rna-seq']['entry_type'] == 'family_overview'
    assert m['4c-seq']['entry_type'] == 'family_overview'
    assert m['truseq-rna-access']['entry_type'] == 'commercial_kit'
    assert set(m['truseq-rna-access']['previous_ids']) == {'truseq-rna-access', 'truseqtm-rna-exome'}
    assert 'truseqtm-nano' not in m


def test_availability_is_not_scientific_review(records):
    inv, d, b = records
    paper = {'pmid-24097267': {'pmid': '24097267', 'status': 'fulltext_acquired', 'files': []}}
    m = compile_inventory(inv, d, b, paper)['methods']['atac-seq']
    assert any(x['availability'] == 'fulltext_acquired' for x in m['method_references'])
    assert m['extraction_status'] == 'not_scheduled'
    assert 'not full-paper' in m['review_limits']


def test_published_folders_only_link_assigned_papers_and_original_bytes(tmp_path):
    from scripts.illumina.technology_sources.materialize import materialize
    from scripts.illumina.technology_sources.publish_curated import publish
    from scripts.illumina.technology_sources.review_papers import prepare
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({
        'card:shared': {'content_level': 'catalogue', 'text': 'A and B share a cartoon.', 'title': 'A / B'},
        'paper:1': {'pmid': '1', 'title': 'Paper A', 'content_level': 'abstract'},
        'paper:2': {'pmid': '2', 'title': 'Paper B', 'content_level': 'abstract'},
        'paper:3': {'pmid': '3', 'title': 'Related paper C', 'content_level': 'abstract'}}))
    inv = {'methods': {k: {'name': k, 'kind': 'technology', 'source_names': [k],
                           'source_records': ['shared']} for k in ('a', 'b')},
           'occurrences': {'shared': {'title': 'A / B', 'registry': str(registry), 'shared_source': True,
                'classifications': [], 'card': {'references': [
                    {'pmid': str(i), 'reference_id': 'ref'+str(i), 'role': 'references'} for i in (1, 2)]}}}}
    d = {'source_identity_fingerprint': source_identity_fingerprint(inv), 'decisions': [
        {'previous_id': k, 'canonical_id': k, 'action': 'retain', 'entry_type': 'named_method',
         'reason': 'Separate named assay.', 'reviewed_source_ids': ['shared']} for k in ('a', 'b')]}
    b = {'decision_fingerprint': fingerprint(d), 'bindings': [
        {'previous_id': k, 'source_id': 'shared', 'reference_id': 'ref'+str(i), 'paper_id': 'pmid-'+str(i),
         'pmid': str(i), 'role': 'method_reference', 'identity_check': 'checked', 'citation': 'Paper '+k,
         'association_basis': 'Own labelled citation.'} for i, k in enumerate(('a', 'b'), 1)],
         'excluded_from_method_references': []}
    vault = tmp_path / 'original'
    materialize(inv, vault, [])
    bundle = tmp_path / 'review_bundle'
    checks = prepare(vault, bundle, d, b)
    assert len(checks) == 2
    assert all(x['paper_content_review'] == 'not_yet_reviewed' for x in checks)
    all_checks = prepare(vault, tmp_path / 'all_paper_bundle', d, b, include_source_bibliography=True)
    assert len(all_checks) == 3
    related = next(x for x in all_checks if x['paper_id'] == 'pmid-3')
    assert related['methods'] == []
    assert related['bibliographic_association'] == 'unassigned_source_bibliography'
    result = publish(bundle, tmp_path / 'curated', d, b)
    assert result['validation']['passed']
    a = tmp_path / 'curated/methods/a'
    assert (a / 'papers/pmid-1/paper.json').is_file()
    assert not (a / 'papers/pmid-2').exists()
    assert len(json.loads((a / 'source_references.json').read_text())) == 2
    assert len(json.loads((a / 'references.json').read_text())) == 1
    assert (tmp_path / 'curated/CHECKLIST.csv').is_file()
    with pytest.raises(FileExistsError):
        publish(bundle, tmp_path / 'curated', d, b)

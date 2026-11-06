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


def test_source_report_accounts_for_shared_and_repeated_depictions(records):
    from scripts.illumina.technology_sources.source_report import build_report
    inventory, _, _ = records
    curated = compile_records(records)
    report = build_report(inventory, curated)
    counts = report['counts']
    assert counts['source_occurrences_by_kind'] == {'website_card': 154, 'poster_row': 285, 'footer_panel': 39}
    assert sum(counts['named_method_source_coverage'].values()) == counts['entries_by_type']['named_method']
    drop = report['methods']['drop-seq']
    assert len(drop['website_card_ids']) == 1 and len(drop['poster_row_ids']) == 2
    gt = report['methods']['g-and-t-seq']
    assert len(gt['website_card_ids']) == 1 and len(gt['poster_row_ids']) == 3
    assert any(d['shared_with_method_ids'] for d in report['methods']['chip-seq']['depictions'])
    assert not any('historical' in s for s in drop['recorded_source_differences'])


def test_poster_only_source_reviews_cover_methods_and_pin_their_papers(records):
    from scripts.illumina.technology_sources.source_report import validate_poster_only_reviews
    inventory, _, _ = records
    curated = compile_records(records)
    report_path = REVIEW.parent/'release_01/task2_03/POSTER_ONLY_REVIEW.json'
    reviews = json.loads(report_path.read_text())
    papers = {pid: {'metadata': {'abstract': []}} for m in curated['methods'].values()
              for r in m['method_references'] if (pid := r.get('paper_id'))}
    # The actual review records pin complete abstracts. A deliberately different
    # in-memory corpus must be rejected rather than inherit their approval.
    with pytest.raises(ValueError, match='Reviewed abstract changed'):
        validate_poster_only_reviews(inventory, curated, papers, reviews)
    test_reviews = copy.deepcopy(reviews)
    for r in test_reviews:
        r['paper_abstract_sha256'] = {pid: hashlib.sha256(b'[]').hexdigest() for pid in r['assigned_paper_ids']}
    assert len(validate_poster_only_reviews(inventory, curated, papers, test_reviews)) == 99
    with pytest.raises(ValueError, match='exactly once'):
        validate_poster_only_reviews(inventory, curated, papers, test_reviews[:-1])


def test_poster_row_windows_preserve_group_labels_without_neighbor_names(records):
    from scripts.illumina.technology_sources.poster_check import row_windows, overlap
    inventory, _, _ = records
    rows = row_windows(inventory)
    assert len(rows) == 285
    target = next(r for r in rows if r['row']['source_row_caption'] == 'cP-RNA-Seq')
    previous = next(r for r in rows if r['row']['source_row_caption'].startswith('3Seq /'))
    assert any(overlap(target['row']['prior_tight_region_proposal'], box) for box in previous['row']['exact_label_bboxes'])
    assert not any(overlap(target['pdf_point_box'], box) for box in previous['row']['exact_label_bboxes'])
    assert all(r['own_method_labels_contained'] and not r['neighbor_method_labels_intersect'] for r in rows)
    assert all(r['workflow_extent_review'] == 'pending_visual_review' for r in rows)


def test_diagram_rejections_require_exact_image_and_cannot_approve_workflows():
    from scripts.illumina.technology_sources.publish_curated import apply_diagram_rejections
    state = {'sources': {'s': {'diagrams': [{'sha256': 'original', 'quality': 'pending'}]}}}
    finding = {'source_id': 's', 'image_sha256': 'original',
        'status': 'rejected_as_complete_workflow_view', 'reviewer': 'Visual inspection',
        'finding': 'The reaction input label is clipped.'}
    review = {'poster_previews': {'reviewed_examples': [finding]}}
    bad = copy.deepcopy(review)
    bad['poster_previews']['reviewed_examples'].append({**finding, 'image_sha256': 'changed'})
    with pytest.raises(ValueError, match='does not match source and bytes'):
        apply_diagram_rejections(state, bad)
    assert state['sources']['s']['diagrams'][0]['quality'] == 'pending'
    bad = {'poster_previews': {'reviewed_examples': [{**finding, 'status': 'approved'}]}}
    with pytest.raises(ValueError, match='cannot approve a workflow'):
        apply_diagram_rejections(state, bad)
    apply_diagram_rejections(state, review)
    assert state['sources']['s']['diagrams'][0]['visual_review'] == finding
    assert state['sources']['s']['diagrams'][0]['quality'] == finding['status']


def test_category_layout_preserves_subclassifications_and_rejects_unplaced_methods(records):
    from scripts.illumina.technology_sources.classification import placements, method_directory
    compiled = compile_records(records)
    assignments = json.loads((REVIEW / 'category_assignments.json').read_text())
    for m in compiled['methods'].values():
        p = placements(m, assignments.get(m['id'], []))
        if m['entry_type'] == 'named_method':
            assert p and len(method_directory(m, p).parts) == 4
    drop = compiled['methods']['drop-seq']
    assert str(method_directory(drop, placements(drop, []))) == 'methods/Transcriptome/RNA Low-Level Detection/drop-seq'
    unplaced = {'id': 'x', 'classifications': [], 'entry_type': 'named_method'}
    with pytest.raises(ValueError, match='subclassification'):
        placements(unplaced, [])


def test_title_entities_do_not_create_false_publication_mismatches():
    from scripts.illumina.technology_sources.article_identity import normalized_title
    assert normalized_title('G&amp;T-seq') == normalized_title('G&T-seq')
    assert normalized_title('G&amp;T-seq') != normalized_title('DR-seq')


def test_cloud_recovery_rejects_wrong_own_identifiers_despite_matching_title():
    from scripts.illumina.technology_sources.pmc_cloud import metadata_matches
    paper = {'pmid': '12', 'title': 'The intended protocol',
             'metadata': {'identifiers': {'doi': ['10.1/intended']}}}
    record = {'pmid': 12, 'title': paper['title'], 'doi': '10.1/intended'}
    metadata_matches(record, paper)
    for changed, message in [({'pmid': 99}, 'own PMID'),
                             ({'doi': '10.1/other'}, 'DOI'),
                             ({'title': 'A different protocol'}, 'title'),
                             ({'is_retracted': 'yes'}, 'Retracted')]:
        with pytest.raises(ValueError, match=message):
            metadata_matches({**record, **changed}, paper)


def test_image_only_cms_block_is_not_an_assay_description():
    from scripts.illumina.technology_sources.descriptions import describe
    prose = 'This assay records RNA molecules and their cellular origins using molecular barcodes before pooling the captured material.'
    occurrence = {'card': {'source_page_url': 'https://example.org/assay'}, 'original_cms': {'content': {
        'image': {'text': '<p class="seq-method-image"><img src="/a-very-long-path-to-the-original-source-image-to-exceed-the-length-threshold.png"></p>'},
        'description': {'text': '<p>' + prose + '</p>'}}}}
    state = {'inventory': {'occurrences': {'s': occurrence}}, 'papers': {}}
    m = {'id': 'assay', 'source_records': ['s'], 'method_references': []}
    result = describe(state, m)
    assert result['text'] == prose and result['locator'] == '/description/text'
    occurrence['original_cms']['content'].pop('description')
    assert describe(state, m)['status'] == 'description_unavailable_in_collected_sources'


def test_guide_description_needs_scope_review_and_exact_source_support(tmp_path):
    from scripts.illumina.technology_sources.descriptions import describe, apply_description_reviews
    text = 'A DNA method called Nuc-seq.\n\nThe RNA method profiles individual nuclei.'
    path = tmp_path / 'guide-page.txt'; path.write_text(text)
    candidate = {'text': 'A DNA method called Nuc-seq.', 'source_kind': 'illumina_methods_guide_excerpt',
                 'locator': 'PDF page 1', 'source_pdf_sha256': 'pdf'}
    method = {'id': 'rna', 'source_records': [], 'method_references': [
        {'role': 'method_reference', 'paper_id': 'paper', 'reference_id': 'ref'}]}
    state = {'papers': {'paper': {'metadata': {'abstract': [{'text': 'RNA is sequenced from individual nuclei.'}]}}},
        'method_descriptions': {'rna': candidate}, 'method_guide_sources': {'rna': [
            {'pdf_page': 1, 'pdf_sha256': 'pdf', 'text_file': {'path': path.name,
             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}]}}
    assert describe(state, method)['source_kind'] == 'method_paper_abstract'
    review = {'candidate_text_sha256': hashlib.sha256(candidate['text'].encode()).hexdigest(),
              'action': 'replace_guide_excerpt', 'reviewer': 'Source review',
              'reason': 'The matching name describes a different material.',
              'text': 'The RNA method profiles individual nuclei.'}
    apply_description_reviews(tmp_path, state, {'rna': method}, {'rna': review})
    assert describe(state, method)['text'] == review['text']
    with pytest.raises(ValueError, match='absent from its registered guide page'):
        apply_description_reviews(tmp_path, state, {'rna': method}, {'rna': {**review, 'text': 'Invented chemistry.'}})
    path.write_text('Changed source bytes')
    with pytest.raises(ValueError, match='guide-page bytes changed'):
        apply_description_reviews(tmp_path, state, {'rna': method}, {'rna': review})


def test_cached_html_reuse_requires_own_identity_and_substantive_body(tmp_path):
    from scripts.illumina.technology_sources.assemble_sources import reuse_registered_html
    project = tmp_path / 'project'
    files = {}
    for pmid, own in [('1', '1'), ('2', '1')]:
        path = project / 'papers' / pmid / 'article.html'
        path.parent.mkdir(parents=True)
        path.write_text('<meta name="citation_pmid" content="' + own + '">'
            '<meta name="citation_title" content="Target ' + own + '">'
            '<nav>Navigation must not be the paper body</nav><section class="main-article-body"><p>' +
            ('Scientific article body. ' * 100) + '</p></section>')
        files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    index = project / 'receipts.json'; index.write_text(json.dumps({'files': files}))
    state = {'files': {}, 'papers': {'pmid-'+p: {'pmid': p, 'title': 'Target '+p,
        'metadata': {'title': 'Target '+p}, 'status': 'metadata_only', 'files': []} for p in ['1', '2']}}
    output = tmp_path / 'vault'
    rows = reuse_registered_html(output, state, index, project)
    assert [r['status'] for r in rows] == ['reused', 'not_admitted']
    assert state['papers']['pmid-1']['status'] == 'fulltext_acquired'
    assert state['papers']['pmid-2']['status'] == 'metadata_only'
    text = output / 'new_assets/cached_papers/1/reading-text.txt'
    assert 'Scientific article body' in text.read_text() and 'Navigation' not in text.read_text()


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
    image = tmp_path / 'shared-diagram.png'
    image.write_bytes(b'Original unmodified diagram bytes')
    image_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({
        'card:shared': {'content_level': 'catalogue', 'text': 'A and B share a cartoon.', 'title': 'A / B'},
        'diagram:shared': {'kind': 'image', 'path': str(image), 'sha256': image_hash},
        'paper:1': {'pmid': '1', 'title': 'Paper A', 'content_level': 'abstract'},
        'paper:2': {'pmid': '2', 'title': 'Paper B', 'content_level': 'abstract'},
        'paper:3': {'pmid': '3', 'title': 'Related paper C', 'content_level': 'abstract'}}))
    inv = {'methods': {k: {'name': k, 'kind': 'technology', 'source_names': [k],
                           'source_records': ['shared']} for k in ('a', 'b')},
           'occurrences': {'shared': {'title': 'A / B', 'registry': str(registry), 'shared_source': True, 'batch': 'catalogue',
                'classifications': [{'path': ['Genome', 'DNA Low-Level Detection']}], 'card': {'references': [
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
    finding = {'source_id': 'shared', 'image_sha256': image_hash,
        'status': 'rejected_as_complete_workflow_view', 'reviewer': 'Visual inspection',
        'finding': 'Input label is clipped.'}
    rejected = publish(bundle, tmp_path / 'quality_view', d, b,
                       source_quality_review={'poster_previews': {'reviewed_examples': [finding]}})
    for mid in ('a', 'b'):
        method = rejected['methods'][mid]
        directory = tmp_path / 'quality_view' / method['directory']
        assert method['diagram_quality_holds'] == [finding]
        assert not method['extraction_authorized']
        assert method['workflow_validation']['paper_confirmation_required']
        comparison = json.loads((directory / 'source_comparison.json').read_text())
        assert comparison['depictions'][0]['diagrams'][0]['visual_review'] == finding
        html = (directory / 'index.html').read_text()
        assert '<summary>Rejected preview: Input label is clipped.</summary>' in html
        assert 'paper or published protocol must confirm operations' in html
    # Publication overlays cannot silently rewrite the preserved source record.
    preserved = json.loads((bundle / 'collection.json').read_text())
    assert 'visual_review' not in preserved['sources']['shared']['diagrams'][0]
    # Source-vault preparation must retain derived non-paper assets too.
    asset = bundle / 'new_assets/guide_pages/figure.png'
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b'Derived full-page source preview')
    preserved['files']['derived'] = {'path': str(asset.relative_to(bundle)),
        'sha256': hashlib.sha256(asset.read_bytes()).hexdigest(), 'kind': 'image'}
    (bundle / 'collection.json').write_text(json.dumps(preserved))
    next_bundle = tmp_path / 'with_derived_asset'
    prepare(bundle, next_bundle, d, b)
    assert (next_bundle / 'new_assets/guide_pages/figure.png').read_bytes() == asset.read_bytes()
    a = tmp_path / 'curated' / result['methods']['a']['directory']
    assert (a / 'papers/pmid-1/paper.json').is_file()
    assert not (a / 'papers/pmid-2').exists()
    assert len(json.loads((a / 'source_references.json').read_text())) == 2
    assert len(json.loads((a / 'references.json').read_text())) == 1
    assert (tmp_path / 'curated/CHECKLIST.csv').is_file()
    nested = tmp_path / 'nested_selected'
    prepare(tmp_path / 'all_paper_bundle', nested, d, b)
    nested_state = json.loads((nested / 'collection.json').read_text())
    assert set(nested_state['papers']) == {'pmid-1', 'pmid-2'}
    assert publish(nested, tmp_path / 'nested_view', d, b)['validation']['passed']
    with pytest.raises(FileExistsError):
        publish(bundle, tmp_path / 'curated', d, b)
    # A paper with matching recorded hashes can still be the wrong article.
    # The method view must not expose quarantined originals through a directory link.
    state = json.loads((bundle / 'collection.json').read_text())
    article = bundle / 'shared/papers/pmid-1/wrong.xml'
    article.write_text('<article><body>Wrong paper</body></article>')
    f = {'path': str(article.relative_to(bundle)), 'sha256': hashlib.sha256(article.read_bytes()).hexdigest(), 'kind': 'article'}
    state['papers']['pmid-1'].update(files=[f], status='fulltext_acquired')
    state['files'][f['path']] = f
    (bundle / 'collection.json').write_text(json.dumps(state))
    held_view = tmp_path / 'held_view'
    result = publish(bundle, held_view, d, b, {'holds': [{'paper_id': 'pmid-1', 'files': [f], 'reason': 'Wrong article'}]},
                     {'a': [{'category': 'Transcriptome', 'subclassification': 'RNA Low-Level Detection', 'basis': 'Inspected source category'}]})
    paper = held_view / result['methods']['a']['directory'] / 'papers/pmid-1'
    assert not (paper / 'wrong.xml').exists()
    assert json.loads((paper / 'paper.json').read_text())['status'] == 'source_identity_hold'
    assert (held_view / 'methods/Transcriptome/RNA Low-Level Detection/a').resolve() == held_view / result['methods']['a']['directory']
    assert not (held_view / result['methods']['b']['directory'] / 'papers/pmid-1').exists()
    assert result['validation']['passed']

"""Launch/registration controls; fake transport, not scientific validation."""
import asyncio
import importlib.util
from pathlib import Path
import sys

import pytest

from genomics_discovery.illumina.extraction import card_sources, paper_instructions, read, sha, write

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts' / 'illumina'

def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module

launch = load_script('extract_all')
prepare = load_script('prepare_fresh_batch')

@pytest.fixture
def manifest(tmp_path):
    root = tmp_path / 'sources'
    batches, rows = {}, []
    for name, ids in [('catalogue', ['a', 'b', 'c']), ('posters', ['d']), ('footer_panels', ['e'])]:
        folder = root / name
        write(folder / 'inventory.json', {'cards': [{'record_id': i} for i in ids]})
        write(folder / 'sources.json', {'assets': []})
        batches[name] = {'sources': str(folder / 'sources.json'), 'sources_sha256': sha(folder / 'sources.json'),
                         'inventory_sha256': sha(folder / 'inventory.json')}
        rows += [{'record_id': i} for i in ids]
    write(root / 'manifest.json', {'prior_model_outputs_used': False, 'batches': batches, 'records': rows})
    return root / 'manifest.json'

@pytest.mark.parametrize('initial_status, expected_calls', [('supported', 4), ('research_needed', 1), ('failed', 1)])
def test_initial_gate_and_no_repeated_records(manifest, tmp_path, monkeypatch, initial_status, expected_calls):
    calls = []
    async def fake_run(source, output, *, records=(), **kw):
        ids = list(records) or [c['record_id'] for c in read(Path(source).parent / 'inventory.json')['cards']]
        calls.append(ids)
        status = initial_status if output.name == 'initial_two' else 'supported'
        return {'cards': {i: {'status': status} for i in ids}, 'counts': {status: len(ids)}}
    monkeypatch.setenv('SLURM_JOB_ID', 'test')
    monkeypatch.setattr(launch, 'run', fake_run)
    monkeypatch.setattr(launch, 'build_graph', lambda *args: {'cards': {
        i: {'admission': 'supported'} for i in 'abcde'}})
    result = asyncio.run(launch.execute(manifest, tmp_path / 'out', ['a', 'b']))
    assert len(calls) == expected_calls
    flattened = [i for call in calls for i in call]
    assert len(flattened) == len(set(flattened))
    if initial_status == 'supported':
        assert set(flattened) == set('abcde')
        assert result['counts'] == {'supported': 5}
    else:
        assert result['status'] == 'held_after_initial_two'
        assert flattened == ['a', 'b']


def test_changed_bundle_or_imported_result_is_rejected(manifest):
    original = read(manifest)
    changed = dict(original, prior_model_outputs_used=True)
    write(manifest, changed)
    with pytest.raises(ValueError, match='raw-source-only'):
        launch.validate_manifest(manifest)
    write(manifest, original)
    source = Path(original['batches']['catalogue']['sources'])
    write(source, {'assets': [], 'changed': True})
    with pytest.raises(ValueError, match='changed'):
        launch.validate_manifest(manifest)


def test_collection_sources_stay_secondary_and_hash_verified(tmp_path):
    paper = tmp_path / 'page.txt'; paper.write_text('A catalogue description.')
    pdf = tmp_path / 'source.pdf'; pdf.write_bytes(b'original bytes')
    document = {'source_id': 'collection:page1', 'content_level': 'catalogue', 'title': 'Collection page 1',
                'text_path': str(paper), 'text_sha256': sha(paper),
                'original_pdf': {'path': str(pdf), 'sha256': sha(pdf)},
                'association_status': 'candidate'}
    card = {'record_id': 'a', 'source_id': 'card:a', 'diagram_asset_ids': [], 'references': [],
            'catalogue_documents': [document]}
    sources, _ = card_sources(card, {'assets': []}, tmp_path)
    assert sources['collection:page1']['content_level'] == 'catalogue'
    assert sources['collection:page1']['association_status'] == 'candidate'
    pdf.write_bytes(b'changed original')
    with pytest.raises(ValueError, match='changed'):
        card_sources(card, {'assets': []}, tmp_path)


def test_unlisted_paper_does_not_become_mandatory():
    assert 'not itself a blocking' in paper_instructions({'references': []})
    assert 'Assess every listed' in paper_instructions({'require_paper_matches': True})


def test_no_fuzzy_label_equivalence():
    audits = {'rna': {'figures': [{'pdf_page': 2, 'actual_drawing_poster_row_ids': ['r1'],
                                  'card_label_candidates': ['cap-seq']} ]}}
    cards = [{'record_id': prepare.row_record_id('r1'), 'source_method_labels': ['CapSeq']}]
    leads, labels = prepare.collection_candidates(audits, cards)
    assert leads['cap-seq'] == {('rna', 2)}
    assert labels['CAP-seq'] == set()
    assert labels['CapSeq']


def test_historical_inventory_paths_are_not_rebased_into_current_snapshot(tmp_path):
    raw = tmp_path / 'raw'
    raw.mkdir()
    image = raw / 'diagram.png'
    image.write_bytes(b'original image')
    historical = {'path': 'older/location/superseded.png', 'sha256': 'a' * 64}
    inventory = {'catalogue': {'path': 'methods.jsonl', 'sha256': 'b' * 64},
                 'cards': [{'record_id': 'x', 'poster_scope': {'superseded_context_crop': historical}}]}
    write(raw / 'inventory.json', inventory)
    write(raw / 'sources.json', {'assets': [{'path': 'diagram.png', 'sha256': sha(image)}]})
    result, sources = prepare.load_source_bundle(raw, prepare.Custody())
    assert result['catalogue'] == inventory['catalogue']
    assert result['cards'][0]['poster_scope']['superseded_context_crop'] == historical
    assert sources['assets'][0]['path'] == str(image)
    assert read(raw / 'inventory.json') == inventory
    image.unlink()
    with pytest.raises(ValueError, match='materialized source file'):
        prepare.load_source_bundle(raw, prepare.Custody())


def test_changed_active_source_remains_blocking(tmp_path):
    image = tmp_path / 'diagram.png'; image.write_bytes(b'original')
    write(tmp_path / 'inventory.json', {'cards': []})
    write(tmp_path / 'sources.json', {'assets': [{'path': 'diagram.png', 'sha256': sha(image)}]})
    image.write_bytes(b'changed')
    with pytest.raises(ValueError, match='custody mismatch'):
        prepare.load_source_bundle(tmp_path, prepare.Custody())


@pytest.mark.parametrize('source_title,recovered_title', [(None, 'Recovered method'), ('', None)])
def test_complete_source_preparation_with_missing_title(tmp_path, monkeypatch, source_title, recovered_title):
    """Exercise registration to manifest across all three source classes, no models."""
    import subprocess
    source_root = tmp_path / 'raw'
    annotation = tmp_path / 'annotations'
    annotation.mkdir()
    pdf_assets = []
    for kind, aid in [('dna', 'asset_409e5c0403ff18301d168974'),
                      ('rna', 'asset_4a5d25b11e0887cf7958bda3')]:
        pdf = tmp_path / (kind + '.pdf'); pdf.write_bytes(kind.encode())
        pdf_assets.append({'asset_id': aid, 'path': str(pdf), 'sha256': sha(pdf), 'url': 'https://example.org/' + kind})
        write(annotation / (kind + '_collection.json'), {'figures': [
            {'page': 1, 'source_pdf_sha256': sha(pdf), 'poster_row_candidate': 'row:one'}]})
    image = tmp_path / 'figure.png'; image.write_bytes(b'pinned image')
    main_asset = {'asset_id': 'main', 'source_id': 'image:main', 'status': 'downloaded',
                  'path': str(image), 'sha256': sha(image), 'url': 'https://example.org/figure'}
    catalogue_card = {'record_id': 'blank', 'source_id': 'card:blank', 'source_title': source_title,
                      'recovered_title': recovered_title, 'diagram_asset_ids': ['main'], 'references': []}
    poster_card = {'record_id': prepare.row_record_id('row:one'), 'source_id': 'card:poster',
                   'source_title': 'Recovered method', 'source_method_labels': ['Recovered method'],
                   'diagram_asset_ids': ['main'], 'references': []}
    for name, card in [('sources_06', catalogue_card), ('poster_sources_06', poster_card)]:
        write(source_root / name / 'inventory.json', {'catalogue': {'path': 'historical.json', 'sha256': 'f' * 64},
                                                     'cards': [card]})
        write(source_root / name / 'sources.json', {'assets': [main_asset, *pdf_assets]})
    (annotation / 'panels').mkdir()
    footer = annotation / 'panels' / 'footer.png'; footer.write_bytes(b'footer image')
    write(annotation / 'footer_panels.json', {'rows': [{'row_id': 'row:footer',
        'image_provenance': {'crop_path': 'old/footer.png', 'crop_sha256': sha(footer)},
        'source_pdf_sha256': pdf_assets[0]['sha256'], 'source_url': 'https://example.org/footer',
        'diagram_region_proposal': [0, 0, 10, 10], 'source_row_caption': 'Readout',
        'members': [{'source_method_label': 'Readout'}], 'scope_note': 'Readout schematic',
        'panel_classification': 'readout'}]})
    monkeypatch.setenv('SLURM_JOB_ID', 'test')
    monkeypatch.setattr(prepare.subprocess, 'run', lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, stdout=b'Collection page text.\f'))
    result = prepare.prepare(source_root, annotation, tmp_path / 'prepared')
    assert result['source_occurrences'] == 3
    assert set(result['batches']) == {'catalogue', 'posters', 'footer_panels'}
    card = read(tmp_path / 'prepared/catalogue/inventory.json')['cards'][0]
    assert card['source_title'] == source_title  # Preserve the literal missing CMS value.
    assert len(card['catalogue_documents']) == (2 if recovered_title else 0)
    assert result['records'][0]['title'] == (recovered_title or 'blank')
    assert launch.validate_manifest(tmp_path / 'prepared/manifest.json')['source_occurrences'] == 3

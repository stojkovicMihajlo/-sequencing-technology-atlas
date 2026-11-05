"""Freeze completed relationship evidence without changing the running pilot."""
import copy
import json
from pathlib import Path

import pytest

from experiments.ideation_demo_20261105.snapshot import main, prepare_from_reference, snapshot
from genomics_discovery.ideation.run import prepare
from genomics_discovery.illumina.extraction import sha
from genomics_discovery.illumina.history import build_history_index
from tests.illumina.test_illumina_relationships import pair


def make_pilot(tmp_path):
    root = tmp_path/'pilot'
    (root/'extractions').mkdir(parents=True)
    (root/'history').mkdir()
    overlay = pair()
    graph = copy.deepcopy(overlay['source_graph'])
    (root/'extractions'/'atlas.json').write_text(json.dumps(graph))
    directory = root/'history'/'one'; directory.mkdir()
    (directory/'graph.json').write_text(json.dumps(overlay))
    (root/'history'/'state.json').write_text(json.dumps({'pair_runs': {
        'one': {'status':'completed', 'path': str(directory)},
        'two': {'status':'running', 'path': str(root/'history'/'two')},
    }}))
    return root, overlay


def add_pair_registry(root, overlay):
    """Real pair export, synthetic source metadata; no fabricated scientific review."""
    identifiers = set()

    def collect(value):
        if isinstance(value, dict):
            if 'source_id' in value and 'quote' in value:
                identifiers.add(value['source_id'])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for section in ('historical_links', 'correspondences', 'withheld'):
        for row in overlay.get(section, []):
            collect(row.get('claim', {}))
            collect(row.get('review', {}))
    registry = {sid: {'title': 'Synthetic metadata for namespace regression',
                      'content_level': 'excerpt'} for sid in identifiers}
    directory = root/'history/one/inputs'
    directory.mkdir()
    path = directory/'sources.json'
    path.write_text(json.dumps(registry))
    return path


def test_snapshot_only_admits_completed_exact_versions_and_preserves_sources(tmp_path):
    root, _ = make_pilot(tmp_path)
    original = (root/'extractions'/'atlas.json').read_bytes()
    output = tmp_path/'snapshot'
    result = snapshot(root, output)
    assert result['reviewed_history_overlays'] == 1
    assert result['not_loaded'] == [{'pair_id':'two','reason':'not_completed_at_snapshot'}]
    assert (root/'extractions'/'atlas.json').read_bytes() == original == (output/'atlas.json').read_bytes()
    assert build_history_index(output/'history_inputs.json')['edges']


def test_pair_registry_keeps_claim_scope_separate_from_embedded_workflow_scope(tmp_path):
    root, overlay = make_pilot(tmp_path)
    source = add_pair_registry(root, overlay)
    output = tmp_path/'snapshot'
    receipt = snapshot(root, output)
    assert receipt['history_preflight']['status'] == 'passed'
    assert receipt['history_preflight']['indexed_relationships'] > 0
    assert (output/'pairs/one/sources.json').read_bytes() == source.read_bytes()
    index = build_history_index(output/'history_inputs.json')
    sources = index['edges'][0]['artifact']['source_registry']['sources']
    assert sources and all(sid.startswith(('left:', 'right:')) for sid in sources)
    assert (output/'pairs/one/graph.json').read_bytes() == (root/'history/one/graph.json').read_bytes()


def test_snapshot_fails_preflight_for_missing_claim_source_before_native_calls(tmp_path):
    root, overlay = make_pilot(tmp_path)
    source = add_pair_registry(root, overlay)
    registry = json.loads(source.read_text())
    registry.pop(next(iter(registry)))
    source.write_text(json.dumps(registry))
    output = tmp_path/'snapshot'
    with pytest.raises(ValueError, match='Evidence source IDs absent'):
        snapshot(root, output)
    assert not (output/'receipt.json').exists()


def test_snapshot_excludes_pairs_requiring_finer_extraction(tmp_path):
    root, overlay = make_pilot(tmp_path)
    overlay['diagram_adequacy'][0]['verdict'] = 'needs_finer_extraction'
    (root/'history'/'one'/'graph.json').write_text(json.dumps(overlay))
    result = snapshot(root, tmp_path/'snapshot')
    assert result['reviewed_history_overlays'] == 0
    assert result['not_loaded'][0]['reason'] == 'needs_finer_extraction'


def test_snapshot_rejects_different_workflow_version(tmp_path):
    root, _ = make_pilot(tmp_path)
    path = root/'extractions'/'atlas.json'; graph = json.loads(path.read_text())
    next(iter(graph['cards'].values()))['artifact_pins']['extraction.json'] = '0'*64
    path.write_text(json.dumps(graph))
    with pytest.raises(ValueError, match='different workflow version'):
        snapshot(root, tmp_path/'snapshot')


@pytest.fixture
def reference_run(tmp_path):
    pilot, overlay = make_pilot(tmp_path)
    # The relationship fixture omits the atlas inventory convenience field.
    atlas_path = pilot/'extractions/atlas.json'
    atlas = json.loads(atlas_path.read_text())
    for record_id, card in atlas['cards'].items():
        card['operation_ids'] = [node['id'] for node in atlas['nodes']
                                 if node['record_id'] == record_id and node['node_type'] == 'operation']
    atlas_path.write_text(json.dumps(atlas))
    add_pair_registry(pilot, overlay)
    frozen = tmp_path/'frozen'
    snapshot(pilot, frozen)
    request = tmp_path/'question.json'
    request.write_text(json.dumps({'prompt': 'Propose a sequencing assay with a supported measurement improvement.',
                                   'constraints': ['Existing sequencing equipment']}))
    root = tmp_path/'reference_run'
    config = prepare(request, frozen/'atlas.json', root, history_manifest=frozen/'history_inputs.json')
    # Historical run config; exact input pins remain the authority, not latest
    # pilot completion or any old generated answers.
    config['format_version'] = 'ideation_run/1'
    request_path = Path(config['request_file'])
    request_path.write_text(json.dumps(json.loads(request_path.read_text()), separators=(',', ':')) + '\n')
    config['request_sha256'] = sha(request_path)
    (root/'config.json').write_text(json.dumps(config))
    (root/'old_candidate.json').write_text('{"old_generated_idea":"DO NOT COPY"}')
    (root/'old_review.json').write_text('{"review":"DO NOT COPY"}')
    (root/'sources.json').write_text('{"sources":{"prior_search":"DO NOT COPY"}}')
    return root, config, frozen, pilot


def test_reference_preparation_freezes_inputs_not_previous_run_memory(reference_run, tmp_path):
    root, previous, frozen, pilot = reference_run
    originals = {path: path.read_bytes() for base in (root, frozen, pilot) for path in base.rglob('*') if path.is_file()}
    # A refreshed pilot is irrelevant to a matched-input preparation.
    history = pilot/'history/state.json'
    history.write_text(json.dumps({'pair_runs': {'new': {'status': 'completed', 'path': '/nonexistent/new-pair'}}}))
    originals[history] = history.read_bytes()
    output = tmp_path/'matched'
    receipt = prepare_from_reference(root, output)
    current = json.loads((output/'config.json').read_text())
    assert current['format_version'] == receipt['prepared_run_format'] == 'ideation_run/2'
    assert receipt['reference_run_format'] == 'ideation_run/1'
    for key in ('atlas_sha256', 'request_sha256', 'history_sha256'):
        assert current[key] == previous[key] == receipt[key]
    assert current['history_manifest'] == previous['history_manifest']
    for file_key in ('atlas_file', 'request_file'):
        assert Path(current[file_key]).read_bytes() == Path(previous[file_key]).read_bytes()
    assert json.loads((output/'state.json').read_text()) == {'status': 'prepared', 'stages': [], 'failures': []}
    assert not (output/'old_candidate.json').exists()
    assert not (output/'old_review.json').exists()
    assert not (output/'sources.json').exists()
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert {path for path in root.rglob('*') if path.is_file()} == {path for path in originals if path.is_relative_to(root)}
    assert receipt['history_preflight']['status'] == 'passed'
    assert receipt['current_harness_sources']['run.py']
    assert 'model-service behavior are not frozen' in receipt['boundary']


@pytest.mark.parametrize('field', ['atlas_file', 'request_file', 'history_manifest'])
def test_reference_preparation_rejects_stale_top_level_pins_before_output(reference_run, tmp_path, field):
    root, previous, _, _ = reference_run
    path = Path(previous[field]); path.write_bytes(path.read_bytes() + b' ')
    output = tmp_path/'matched'
    with pytest.raises(ValueError, match='Reference input changed: ' + field):
        prepare_from_reference(root, output)
    assert not output.exists()


@pytest.mark.parametrize('artifact', ['graph.json', 'sources.json'])
def test_reference_preparation_verifies_linked_history_artifacts(reference_run, tmp_path, artifact):
    root, _, frozen, _ = reference_run
    path = frozen/'pairs/one'/artifact
    path.write_bytes(path.read_bytes() + b' ')
    output = tmp_path/'matched'
    with pytest.raises(ValueError, match='History input changed'):
        prepare_from_reference(root, output)
    assert not output.exists()


def test_reference_preparation_requires_fresh_output_outside_reference(reference_run, tmp_path):
    root, _, _, _ = reference_run
    for output in (root, root/'child'):
        with pytest.raises(ValueError, match='must not write inside'):
            prepare_from_reference(root, output)
    existing = tmp_path/'existing'; existing.mkdir()
    with pytest.raises(FileExistsError, match='fresh output'):
        prepare_from_reference(root, existing)


def test_reference_preparation_supports_explicit_no_history(reference_run, tmp_path):
    root, config, _, _ = reference_run
    config.update(history_manifest=None, history_sha256=None)
    (root/'config.json').write_text(json.dumps(config))
    receipt = prepare_from_reference(root, tmp_path/'matched')
    assert receipt['history_manifest'] is None and receipt['history_sha256'] is None
    assert receipt['history_preflight']['status'] == 'not_loaded'


def test_snapshot_cli_reference_run_and_pilot_are_mutually_exclusive(reference_run, tmp_path, capsys):
    root, _, _, pilot = reference_run
    output = tmp_path/'matched'
    main(['--reference-run', str(root), '--output', str(output)])
    assert json.loads(capsys.readouterr().out)['prepared_run_format'] == 'ideation_run/2'
    with pytest.raises(SystemExit) as error:
        main(['--reference-run', str(root), '--pilot', str(pilot), '--output', str(tmp_path/'bad')])
    assert error.value.code == 2
    assert not (tmp_path/'bad').exists()

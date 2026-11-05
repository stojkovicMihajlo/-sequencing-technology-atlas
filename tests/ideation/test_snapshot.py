"""Freeze completed relationship evidence without changing the running pilot."""
import copy
import json
from pathlib import Path

import pytest

from experiments.ideation_demo_20261105.snapshot import snapshot
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

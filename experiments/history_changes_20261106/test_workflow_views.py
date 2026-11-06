"""A directed history view must not silently join a different workflow version."""
import copy
import hashlib
import json

import pytest

from experiments.history_changes_20261106.workflow_views import follow


def fixture(tmp_path):
    paths = {}; nodes = {}
    for side in ['old', 'new']:
        document = {'record_id': side, 'states': [{'id': 's', 'properties': [{'value': side}]}],
                    'operations': [], 'flows': []}
        path = tmp_path / (side + '.json'); path.write_text(json.dumps(document))
        pin = hashlib.sha256(path.read_bytes()).hexdigest(); paths[pin] = path
        nodes[side] = {'kind': 'bound_workflow', 'record_id': side, 'extraction_sha256': pin}
    anchor = {'kind': 'state', 'record_id': 's', 'field': '/properties/0/value'}
    edge = {'id': 'history-edge', 'source': 'old', 'target': 'new', 'source_citation': {'claim_id': 'c'},
            'review': {'decision': 'accept'},
            'mapping_review': {'findings': [{'change_id': 'change', 'decision': 'accept'}]},
            'claim': {'change_records': [{'id': 'change', 'before': 'old', 'after': 'new',
                'workflow_locations': {'earlier_scope': 'bound_workflow',
                                       'earlier': [anchor], 'later': [anchor]}}]}}
    return {'nodes': nodes, 'edges': [edge]}, paths


def test_follow_preserves_direction_and_resolves_each_version(tmp_path):
    index, paths = fixture(tmp_path); original = copy.deepcopy(index)
    value = follow(index, 'history-edge', paths)
    assert value['earlier']['workflow']['record_id'] == 'old'
    assert value['later']['workflow']['record_id'] == 'new'
    assert value['changes'][0]['earlier'][0]['value'] == 'old'
    assert value['changes'][0]['later'][0]['value'] == 'new'
    assert index == original


def test_paper_only_relation_cannot_be_displayed_as_workflow_connection(tmp_path):
    index, paths = fixture(tmp_path); index['edges'][0].pop('source_citation')
    with pytest.raises(ValueError, match='no reviewed connection'):
        follow(index, 'history-edge', paths)


def test_named_workflow_with_different_bytes_is_rejected(tmp_path):
    index, paths = fixture(tmp_path)
    path = paths[index['nodes']['old']['extraction_sha256']]
    path.write_text(path.read_text() + '\n')
    with pytest.raises(ValueError, match='bytes differ'):
        follow(index, 'history-edge', paths)


def test_missing_mapping_review_cannot_pass_vacuously(tmp_path):
    index, paths = fixture(tmp_path); index['edges'][0]['mapping_review']['findings'] = []
    with pytest.raises(ValueError, match='Unaccepted workflow mapping'):
        follow(index, 'history-edge', paths)


def test_unlocated_earlier_record_is_not_invented(tmp_path):
    index, paths = fixture(tmp_path)
    index['edges'][0]['claim']['change_records'][0]['workflow_locations']['earlier_scope'] = 'paper_only'
    with pytest.raises(ValueError, match='not been located'):
        follow(index, 'history-edge', paths)


def test_local_claim_id_is_not_used_instead_of_exact_edge_id(tmp_path):
    index, paths = fixture(tmp_path)
    with pytest.raises(ValueError, match='exact history edge'):
        follow(index, 'c', paths)

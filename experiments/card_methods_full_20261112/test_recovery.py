import json
from pathlib import Path
import pytest

from experiments.card_methods_full_20261112 import recover as runner
from experiments.card_methods_full_20261112.preflight import audit


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.mark.parametrize('extra,held', [(False, True), (True, False)])
def test_evidence_request_is_not_retried_without_new_evidence(tmp_path, monkeypatch, extra, held):
    old = tmp_path / 'old'
    put(old / 'review.json', {'checks': [{'action': 'research', 'target': 'operation:x', 'rationale': 'Read supplement.'}]})
    state = tmp_path / 'state.json'
    put(state, {'cards': {'a': {'status': 'research_needed', 'attempt_directory': str(old)}}})
    audit_path = tmp_path / 'audit'; audit(state, audit_path)
    existing = tmp_path / 'existing.json'; put(existing, {})
    monkeypatch.setattr(runner, 'build_selection', lambda *_: {'methods': [
        {'method_id': 'a', 'primary_paper_id': 'pmid-1', 'canonical_directory': 'methods/a'}]})
    monkeypatch.setattr(runner, 'prepare_method', lambda *_args, **_kwargs: ({}, {}, []))
    # New evidence gets an actual pinned file. Recovery does not claim that it answers the question.
    paper = tmp_path / 'supplement.txt'; paper.write_text('Example supplemental procedure.')
    source = {'kind': 'text', 'content_level': 'fulltext', 'path': str(paper),
              'sha256': runner.sha(paper), 'text': paper.read_text()}
    monkeypatch.setattr(runner, 'recover_evidence', lambda *_args, **_kwargs: {'supplement:1': source} if extra else {})
    output = tmp_path / 'out'
    runner.recover(state, audit_path, tmp_path, tmp_path, Path('unused'), Path('unused'), existing, output, 'unused@example.org')
    assert ('a' in runner.read(output / 'holds.json')) == held
    assert runner.read(output / 'results.json')['a']['scientific_gap_resolved'] is False
    assert ('a' in runner.read(output / 'registered_additions.json')) == extra


def test_only_actual_provider_error_creates_policy_hold(tmp_path):
    state = tmp_path / 'state.json'; rows = {}
    for name, event in [('a', {'type': 'error', 'message': 'This content was flagged for possible biological risk.'}),
                        ('b', {'type': 'item.completed', 'text': 'flagged for possible biological risk'})]:
        attempt = tmp_path / name; stage = attempt / 'native'
        put(attempt / 'stages.json', [{'path': str(stage)}])
        put(stage / 'events.jsonl', event)
        rows[name] = {'status': 'failed', 'attempt_directory': str(attempt)}
    put(state, {'cards': rows})
    audit(state, tmp_path / 'audit')
    holds = runner.read(tmp_path / 'audit/intervention_holds.json')
    assert list(holds) == ['a']
    assert holds['a']['evidence']['sha256'] == runner.sha(tmp_path / 'a/native/events.jsonl')

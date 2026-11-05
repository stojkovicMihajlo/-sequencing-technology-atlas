import json
from pathlib import Path

import pytest

from experiments.extraction_validation_20261105.run import select_inputs


def source_run(tmp_path):
    (tmp_path/'sources').mkdir()
    (tmp_path/'extractions').mkdir()
    for path, value in [
        ('sources/manifest.json', {'bundles': [{'record_id': 'one', 'card': '/source/card.json'},
                                             {'record_id': 'two', 'card': '/source/other.json'}]}),
        ('extractions/state.json', {'finished_at': '2026-11-05T00:00:00Z', 'cards': {
            'one': {'status': 'research_needed', 'attempt_directory': '/source/attempt',
                    'artifact_pins': {'extraction.json': 'a'*64}}}})]:
        (tmp_path/path).write_text(json.dumps(value))
    return tmp_path


def test_exact_selected_bundles_and_terminal_holds_are_preserved(tmp_path):
    root=source_run(tmp_path)
    before={p:p.read_bytes() for p in root.glob('*/*.json')}
    selection,bundles,baselines=select_inputs(root,['one'],'Controlled source-matched validation')
    assert [b['record_id'] for b in bundles]==['one']
    assert baselines['one']['declared_status']=='research_needed'
    assert selection['method_ids']==['one']
    assert {p:p.read_bytes() for p in before}==before


@pytest.mark.parametrize('ids,reason', [([], 'repeat'), (['one','one'],'repeat'),
                                      (['absent'],'repeat'), (['one'],'')])
def test_ambiguous_or_unrecorded_repeat_is_rejected(tmp_path,ids,reason):
    with pytest.raises(ValueError): select_inputs(source_run(tmp_path),ids,reason)


def test_active_source_batch_cannot_be_reused(tmp_path):
    root=source_run(tmp_path)
    p=root/'extractions/state.json'
    d=json.loads(p.read_text());del d['finished_at'];p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='active'): select_inputs(root,['one'],'repeat')


def test_unpinned_baseline_is_not_reconstructed_from_its_json(tmp_path):
    root=source_run(tmp_path)
    p=root/'extractions/state.json'
    d=json.loads(p.read_text());d['cards']['one']['artifact_pins']={};p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='pinned'): select_inputs(root,['one'],'repeat')

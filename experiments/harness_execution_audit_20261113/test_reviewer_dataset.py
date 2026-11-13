"""Custody and blinding of observed-error fixtures; no semantic/model oracle."""
import json
from pathlib import Path

import pytest

from experiments.harness_execution_audit_20261113.pin_reviewer_regression import (
    prepare_diagnostic, raw_sha, value_sha,
)
from genomics_discovery.evaluation.grounding_diagnostic import Dataset, Labels, public_input, validate_labels, verify_dataset


def fixture(root):
    source = root/'paper.txt';source.write_text('Untreated input records background. Treated input records the label.\n\nRead both arms separately.')
    registry = root/'registry.json';registry.write_text('{}')
    def save(name, claim):
        path = root/name
        document = {'operations':[{'id':'minus','inputs':['a'],'outputs':['b'],
            'information_effect':claim,'evidence':[{'quote':'HIDDEN_CANDIDATE_QUOTE'}]},
            {'id':'plus','inputs':['c'],'outputs':['d'],'information_effect':'Records the label.'}],
            'states':[{'id':i,'label':i} for i in ['a','b','c','d','irrelevant']]}
        path.write_text(json.dumps(document))
        return {'path':str(path),'raw_sha256':raw_sha(path),
            'field_value_sha256':value_sha(claim),'expected_grounding_outcome':
                'contradicted' if name=='original.json' else 'supported',
            'context_value_sha256':{'/operations/0':value_sha(document['operations'][0])}}
    bad=save('original.json','Records the label.');good=save('corrected.json','Records background.')
    spec={'pairs':[{'id':'local_condition','method':'method','pointer':'/operations/0/information_effect',
        'context_pointers':['/operations/0','/states'],'source_id':'s',
        'registry':{'path':str(registry),'raw_sha256':raw_sha(registry)},
        'source':{'path':str(source),'sha256':raw_sha(source),'source_id':'s',
                  'title':'Paper','url':'https://example.org/paper'},
        'anchors':['Untreated input records background.'],'rationale':'HIDDEN_HOST_RATIONALE',
        'versions':{'bad':bad,'good':good}}],
        'same_wording_positive':{'parent_pair':'local_condition','path':bad['path'],
            'raw_sha256':bad['raw_sha256'],'field_pointer':'/operations/1/information_effect',
            'field_value_sha256':bad['field_value_sha256']}}
    path=root/'spec.json';path.write_text(json.dumps(spec))
    return path,source


def test_actual_claim_context_and_source_custody_without_host_labels(tmp_path):
    spec,_=fixture(tmp_path);out=tmp_path/'prepared'
    assert prepare_diagnostic(spec,out)['cases']==3
    data=Dataset.model_validate_json((out/'cases.json').read_bytes())
    labels=Labels.model_validate_json((out/'labels.json').read_bytes())
    assert verify_dataset(data);validate_labels(labels,data)
    public=json.dumps(public_input(data))
    assert 'HIDDEN_HOST' not in public and 'HIDDEN_CANDIDATE' not in public
    assert str(tmp_path) not in public and 'expected_grounding_outcome' not in public
    claims=[case.value['claim'] for case in data.cases]
    assert claims.count('Records the label.')==2
    for case in data.cases:
        states=case.value['context']['/states']
        assert len(states)==2 and all(s['id']!='irrelevant' for s in states)
    assert sorted(label.outcome for label in labels.cases)==['contradicted','supported','supported']
    with pytest.raises(FileExistsError):prepare_diagnostic(spec,out)
    assert prepare_diagnostic(spec,tmp_path/'selected',pair_ids=['local_condition'])['cases']==3
    with pytest.raises(ValueError,match='Unknown diagnostic pair'):
        prepare_diagnostic(spec,tmp_path/'unknown',pair_ids=['not_recorded'])


def test_changed_original_and_missing_anchor_are_not_silently_repaired(tmp_path):
    spec,source=fixture(tmp_path)
    source.write_text('Changed original')
    with pytest.raises(ValueError,match='Preserved input changed'):
        prepare_diagnostic(spec,tmp_path/'bad_original')
    doc=json.loads(spec.read_text());doc['pairs'][0]['source']['sha256']=raw_sha(source)
    spec.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='Decisive anchor'):
        prepare_diagnostic(spec,tmp_path/'bad_anchor')

"""Set containment finds witnessed subregions, never semantic equivalence."""
import json
import pytest
from test_workflow_transformations import cases as cases
from test_operation_registry import registry as registry,assignment,review,commit
from genomics_discovery.curation.operation_registry.regions import components_in_region
from genomics_discovery.curation.operation_registry.__main__ import main
from genomics_discovery.curation.workflow_extraction.models import digest


def admitted(cases,registry):
    a=assignment(cases,registry.snapshot())
    commit(registry,a,review(a,cases),*cases[:2])
    return registry.snapshot(),cases[2][0]


def test_larger_module_contains_a_smaller_reviewed_witness(cases,registry):
    snapshot,workflow=admitted(cases,registry)
    before=digest(snapshot)
    result=components_in_region(snapshot,workflow,module='unit')
    assert len(result['contained_components'])==1
    witness=result['contained_components'][0]
    assert set(witness['witness_operation_ids'])=={'insert','repair'}
    assert set(result['operation_ids'])=={'insert','repair','assay'}
    assert result['boundary_crossing_components']==[] and result['new_mapping_assertions']==0
    assert digest(snapshot)==before


def test_a_partial_witness_is_not_presented_as_a_contained_component(cases,registry):
    snapshot,workflow=admitted(cases,registry)
    result=components_in_region(snapshot,workflow,['insert'])
    assert not result['contained_components']
    assert result['boundary_crossing_components'][0]['outside_region']==['repair']


def test_identical_operation_names_in_changed_workflow_do_not_reuse_mapping(cases,registry):
    snapshot,workflow=admitted(cases,registry)
    changed=workflow.model_copy(update={'scope':'A different scientific scope'})
    result=components_in_region(snapshot,changed,module='unit')
    assert not result['contained_components'] and result['bound_assignment_count']==0


@pytest.mark.parametrize('operations,module',[([],None),(['insert'],'unit'),(['unknown'],None),(['insert','insert'],None),([], 'unknown')])
def test_ambiguous_or_invalid_region_is_rejected(cases,registry,operations,module):
    snapshot,workflow=admitted(cases,registry)
    with pytest.raises(ValueError):components_in_region(snapshot,workflow,operations,module=module)


def test_cli_returns_witness_bindings(cases,registry,tmp_path,monkeypatch,capsys):
    snapshot,workflow=admitted(cases,registry)
    sp=tmp_path/'snapshot.json';sp.write_text(json.dumps(snapshot))
    wp=tmp_path/'workflow.json';wp.write_text(workflow.model_dump_json())
    monkeypatch.setattr('sys.argv',['atlas-concepts','in-region',str(sp),str(wp),'--module','unit'])
    main();result=json.loads(capsys.readouterr().out)
    assert result['workflow_sha256']==digest(workflow)
    assert len(result['contained_components'][0]['mapping_review_sha256'])==64

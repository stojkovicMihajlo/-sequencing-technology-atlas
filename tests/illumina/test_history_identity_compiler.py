"""Exercise identity proof across overlay -> index -> canonical join.

These synthetic artifact fixtures test compilation/provenance, not assay truth.
Direct bind_reviewed_methods tests cannot detect an upstream node-copy loss.
"""
import hashlib
import json
from pathlib import Path

import pytest

from genomics_discovery.illumina.history import build_history_index
from genomics_discovery.illumina.history_method_identity import connectivity_report
from tests.illumina.test_illumina_history import current_citation_fixture


def write(path, value):
    path.write_text(json.dumps(value))
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def inputs(tmp_path, status='resolved', *, include_proof=True):
    graph,save=current_citation_fixture(tmp_path)
    item=graph['links'][0];contributor=graph['nodes'][1]
    if status=='resolved':
        match={'status':'resolved','record_id':'earlier','method_name':None,
               'rationale':'Earlier assay and the referenced implementation have been independently matched.',
               'evidence':item['claim']['predecessor_evidence']}
        basis={'record_id':'earlier','catalogue_entry_sha256':'scope-earlier'}
    else:
        match={'status':'not_in_atlas','record_id':None,'method_name':'Identified absent assay',
               'rationale':'The earlier paper identifies this method; no supplied atlas implementation matches.',
               'evidence':item['claim']['predecessor_evidence']}
        basis={'paper_pmid':'111','method_name':'Identified absent assay',
               'identity_status':'identified_unextracted_method'}
    item['review']['method_match']=match
    if include_proof:contributor['method_identity_basis']=basis
    source_registry={
        'seed':{'content_level':'fulltext','pmid':'222','title':'Later paper',
                'text':item['claim']['seed_evidence'][0]['quote']},
        'R1':{'content_level':'fulltext','pmid':'111','title':'Earlier paper',
              'text':item['claim']['predecessor_evidence'][0]['quote']}}
    sources=write(tmp_path/'sources.json',source_registry)
    earlier=write(tmp_path/'earlier.json',{'record_id':'earlier','operations':[]})
    registry={'format_version':'reviewed_history_method_registry/1','methods':{
        'earlier':{'name':'Earlier assay','method_pmids':['111'],
            'catalogue_entry_sha256':'scope-earlier','extraction':earlier}}}
    method_registry=write(tmp_path/'method_registry.json',registry)
    path=save();manifest=json.loads(path.read_text())
    manifest['overlays'][0]['source_registry']=sources
    manifest['method_registry']=method_registry
    path.write_text(json.dumps(manifest))
    return path,basis,earlier['sha256']


@pytest.mark.parametrize('status',['resolved','not_in_atlas'])
def test_compiler_preserves_reviewed_identity_basis_through_canonical_join(tmp_path,status):
    path,basis,earlier_pin=inputs(tmp_path,status)
    index=build_history_index(path)
    edge=index['edges'][0]
    assert not index['method_identity_issues']
    assert edge['source_contribution']['method_identity_basis']==basis
    assert not edge['source'].startswith('external:')
    if status=='resolved':
        assert edge['source']=='workflow:earlier:'+earlier_pin
        assert edge['method_identity_resolution']['status']=='independently_reviewed_method_identity'
    else:
        assert edge['source'].startswith('unextracted:')
        assert edge['method_identity_resolution']['status']=='identified_not_in_atlas'
    report=connectivity_report(index,['new_assay'])
    assert report['resolved_method_relationships']==1
    assert report['weak_component_sizes']==[2]
    assert len(report['atlas_addition_queue'])==(status=='not_in_atlas')


def test_reviewed_identity_without_host_proof_remains_unbound(tmp_path):
    path,_,_=inputs(tmp_path,include_proof=False)
    index=build_history_index(path)
    assert index['edges'][0]['source'].startswith('external:')
    assert len(index['method_identity_issues'])==1
    assert connectivity_report(index)['resolved_method_relationships']==0


def test_method_summary_survives_compilation_without_becoming_reviewed_novelty(tmp_path):
    path,_,_=inputs(tmp_path)
    manifest=json.loads(path.read_text())
    overlay_path=manifest['overlays'][0]['path']
    overlay=json.loads(Path(overlay_path).read_text())
    seed=next(node for node in overlay['nodes'] if node['id']=='seed')
    summary={'text':'The later assay changes the library route.',
             'status':'model-authored summary; not independently adjudicated structured novelty',
             'source':{'path':'analysis.json','field':'summary'}}
    seed['method_contribution_summary']=summary
    pin=write(Path(overlay_path),overlay)
    manifest['overlays'][0].update(pin)
    path.write_text(json.dumps(manifest))
    index=build_history_index(path)
    published=next(node for node in index['nodes'].values() if node.get('method_contribution_summaries'))
    assert published['method_contribution_summaries'][0]=={'artifact_sha256':pin['sha256'],**summary}

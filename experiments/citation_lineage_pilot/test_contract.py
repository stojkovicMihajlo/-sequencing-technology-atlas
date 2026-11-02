"""Negative controls for the new relation-publication boundary."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from models import Analysis, Claim, Evidence, Finding, Review, UncertaintyAssessment, graph_overlay, validate_analysis, validate_review
from run import bibliography, prepare_review_reuse
import json


SOURCES = {
    'seed': {'text': 'We compared the assay against method A.'},
    'R1': {'text': 'Method A prepares a sequencing library.'},
}


def case():
    claim = Claim(id='c1',reference_id='R1',contributor='method A',contributor_kind='assay',
        relation='comparison',seed_operation_ids=['o1'],earlier_demonstration='library preparation',
        seed_design_change='not established',limitation_addressed='not established',
        reported_gain='not established',tradeoffs_and_transfer_limits='Comparison does not establish descent.',
        seed_evidence=[Evidence(source_id='seed',quote=SOURCES['seed']['text'])],
        predecessor_evidence=[Evidence(source_id='R1',quote=SOURCES['R1']['text'])],
        catalogue_candidates=[],unresolved=[])
    analysis=Analysis(seed_method='seed method',claims=[claim],reference_dispositions={'R1':'comparison'},summary='test')
    review=Review(analysis_sha256='pin',findings=[Finding(claim_id='c1',decision='accept',rationale='comparison only',
        evidence=claim.seed_evidence,unresolved_assessments=[])],omitted_candidates=[],summary='test')
    return analysis,review


def test_comparison_does_not_become_descent():
    a,r=case()
    g=graph_overlay(a,r,SOURCES,'pin',{})
    assert g['links'][0]['kind']=='comparison'
    assert g['links'][0]['historical_development_edge'] is False


@pytest.mark.parametrize('decision',['revise','needs_evidence','reject'])
def test_unaccepted_claim_never_published(decision):
    a,r=case(); r.findings[0].decision=decision
    assert not graph_overlay(a,r,SOURCES,'pin',{})['links']


def test_missing_predecessor_evidence_withheld_even_after_positive_review():
    a,r=case(); a.claims[0].predecessor_evidence=[]
    assert graph_overlay(a,r,SOURCES,'pin',{})['withheld'][0]['reason']=='no_independent_predecessor_evidence'


def test_unassessed_uncertainty_prevents_publication():
    a,r=case(); a.claims[0].unresolved=['Which substrate?']
    with pytest.raises(ValueError,match='every unresolved issue'):
        graph_overlay(a,r,SOURCES,'pin',{})


def test_nonblocking_scope_is_retained_without_erasing_supported_relation():
    a,r=case(); a.claims[0].unresolved=['No exact catalogue entry; contributor is explicitly external.']
    r.findings[0].unresolved_assessments=[UncertaintyAssessment(issue_index=0,blocks_relation=False,
        rationale='This comparison does not assert catalogue identity or a merge.')]
    graph=graph_overlay(a,r,SOURCES,'pin',{})
    assert graph['links'][0]['claim']['unresolved']==a.claims[0].unresolved
    assert not graph['links'][0]['historical_development_edge']


def test_blocking_uncertainty_cannot_coexist_with_acceptance():
    a,r=case(); a.claims[0].unresolved=['Required substrate identity is unverified.']
    r.findings[0].unresolved_assessments=[UncertaintyAssessment(issue_index=0,blocks_relation=True,
        rationale='The proposed relation depends on this unverified substrate identity.')]
    with pytest.raises(ValueError,match='cannot have a blocking'):
        graph_overlay(a,r,SOURCES,'pin',{})
    r.findings[0].decision='needs_evidence'
    assert not graph_overlay(a,r,SOURCES,'pin',{})['links']


def test_duplicate_or_missing_uncertainty_reviews_are_rejected():
    a,r=case();a.claims[0].unresolved=['First issue','Second issue']
    assessment=UncertaintyAssessment(issue_index=0,blocks_relation=False,rationale='Outside the scoped relation as written.')
    r.findings[0].unresolved_assessments=[assessment,assessment]
    with pytest.raises(ValueError,match='every unresolved issue'):
        validate_review(r,a,'pin',SOURCES)


def test_omitted_candidate_still_blocks_publication():
    a,r=case();r.omitted_candidates=['R2']
    assert not graph_overlay(a,r,SOURCES,'pin',{})['links']


def test_changed_analysis_and_missing_review_rejected():
    a,r=case()
    with pytest.raises(ValueError,match='different analysis'):
        validate_review(r,a,'changed',SOURCES)
    r.findings=[]
    with pytest.raises(ValueError,match='every claim'):
        validate_review(r,a,'pin',SOURCES)


def test_fabricated_quote_operation_and_catalogue_identity_rejected():
    for field,value,pattern in [('seed_operation_ids',['made_up'],'operation'),
                                ('catalogue_candidates',['invented'],'Illumina')]:
        a,_=case(); setattr(a.claims[0],field,value)
        with pytest.raises(ValueError,match=pattern):
            validate_analysis(a,SOURCES,['R1'],['o1'],[])
    a,_=case(); a.claims[0].predecessor_evidence[0].quote='A claim absent from the cited paper.'
    with pytest.raises(ValueError,match='Quote absent'):
        validate_analysis(a,SOURCES,['R1'],['o1'],[])


def test_cross_reference_source_substitution_rejected():
    a,_=case(); a.claims[0].predecessor_evidence=a.claims[0].seed_evidence
    with pytest.raises(ValueError,match='Unregistered evidence'):
        validate_analysis(a,SOURCES,['R1'],['o1'],[])


def test_citation_context_preserves_multiple_refs_and_identifiers():
    raw=b'''<article><body><p>We adapted <xref ref-type="bibr" rid="R1 R2">1,2</xref>.</p></body>
    <back><ref-list><ref id="R1"><label>1</label><element-citation><article-title>Paper A</article-title>
    <pub-id pub-id-type="pmid">123</pub-id></element-citation></ref><ref id="R2"><label>2</label>
    <element-citation><article-title>Paper B</article-title><pub-id pub-id-type="doi">10.x/b</pub-id>
    </element-citation></ref></ref-list></back></article>'''
    refs=bibliography(raw)
    assert refs['R1']['identifiers']['pmid']=='123'
    assert refs['R1']['contexts']==refs['R2']['contexts']==['We adapted 1,2.']


def test_review_reuse_refuses_failed_run(tmp_path):
    previous=tmp_path/'failed';previous.mkdir()
    (previous/'state.json').write_text(json.dumps({'status':'failed','stages':[]}))
    with pytest.raises(ValueError,match='never a failed/provider-blocked'):
        prepare_review_reuse(previous,tmp_path/'new')
    assert not (tmp_path/'new').exists()

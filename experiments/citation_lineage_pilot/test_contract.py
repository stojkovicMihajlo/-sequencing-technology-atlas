"""Negative controls for the new relation-publication boundary."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from models import Analysis, Claim, Evidence, Finding, Review, UncertaintyAssessment, graph_overlay, validate_analysis, validate_review
from models import CurrentNominations, Nominations, validate_nominations
from run import bibliography, prepare_review_reuse
import json
import copy
from types import SimpleNamespace
from run import review_cycle


@pytest.mark.asyncio
@pytest.mark.parametrize('author_role', ['analyse', 'author'])
async def test_review_cycle_preserves_issues_for_fresh_reviewer_and_stops_at_cap(author_role):
    calls = []
    revisions = []

    async def stage(role, **context):
        calls.append((role, context))
        if role == author_role:
            return object(), Path(f'analysis_{len(calls)}.json')
        doc = SimpleNamespace(findings=[SimpleNamespace(decision='revise')], omitted_candidates=[])
        revisions.append(doc)
        return doc, Path(f'review_{len(calls)}.json')

    _, path, final_review = await review_cycle(stage, author_role)
    assert [role for role, _ in calls] == [author_role, 'review', author_role, 'review']
    assert calls[2][1]['analysis_path'] == Path('analysis_1.json')
    assert calls[2][1]['feedback'] == Path('review_2.json')
    assert calls[3][1] == {'analysis_path': Path('analysis_3.json'),
                           'feedback': Path('review_2.json'),
                           'previous_analysis_path': Path('analysis_1.json')}
    assert path == Path('analysis_3.json') and final_review is revisions[-1]
    # Still-revise is returned to normal withholding gates, never rewritten accept.
    assert final_review.findings[0].decision == 'revise'


@pytest.mark.asyncio
async def test_review_existing_does_not_research_again_when_review_accepts():
    calls = []
    analysis = object()

    async def stage(role, **context):
        calls.append((role, context))
        return SimpleNamespace(findings=[SimpleNamespace(decision='accept')], omitted_candidates=[]), Path('review.json')

    result, path, _ = await review_cycle(stage, 'analyse', (analysis, Path('original.json')))
    assert result is analysis and path == Path('original.json')
    assert calls == [('review', {'analysis_path': Path('original.json'),
                                 'feedback': None, 'previous_analysis_path': None})]


SOURCES = {
    'seed': {'text': 'We compared the assay against method A.', 'content_level': 'fulltext'},
    'R1': {'text': 'Method A prepares a sequencing library.', 'content_level': 'fulltext'},
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
    assert g['links'][0]['relationship_layer'] == 'comparison'


@pytest.mark.parametrize('level', ['metadata', 'catalogue', 'unavailable'])
def test_positive_review_cannot_promote_metadata_into_scientific_support(level):
    a, r = case(); sources = copy.deepcopy(SOURCES)
    sources['R1']['content_level'] = level
    with pytest.raises(ValueError, match='primary content'):
        graph_overlay(a, r, sources, 'pin', {})


def test_title_quote_in_fulltext_is_not_scientific_support():
    a, r = case(); sources = copy.deepcopy(SOURCES)
    sources['R1']['title'] = sources['R1']['text']
    with pytest.raises(ValueError, match='article title'):
        graph_overlay(a, r, sources, 'pin', {})


def test_fresh_nomination_accounts_for_all_references_without_fetching_all():
    n = CurrentNominations(references=[], selection_rationale='No methodological references in this fixture',
        unresolved_searches=[], excluded_references={'R1': 'Biological result only', 'R2': 'Application comparison outside this scope'})
    validate_nominations(n, SOURCES, {'R1': {}, 'R2': {}})
    n.excluded_references.pop('R2')
    with pytest.raises(ValueError, match='every bibliography'):
        validate_nominations(n, SOURCES, {'R1': {}, 'R2': {}})
    n.excluded_references['invented'] = 'Invented reference'
    with pytest.raises(ValueError, match='every bibliography'):
        validate_nominations(n, SOURCES, {'R1': {}, 'R2': {}})


def test_nomination_cannot_both_follow_and_exclude_or_give_blank_reason():
    n = CurrentNominations(references=[{'reference_id': 'R1', 'contribution': 'method',
        'reason_to_follow': 'Potential adopted component',
        'seed_evidence': [{'source_id': 'seed', 'quote': SOURCES['seed']['text']}]}],
        selection_rationale='Methodological lead', unresolved_searches=[], excluded_references={'R1': 'Background'})
    with pytest.raises(ValueError, match='every bibliography'):
        validate_nominations(n, SOURCES, {'R1': {}})
    n.excluded_references = {'R2': ' '}
    with pytest.raises(ValueError, match='context-specific'):
        validate_nominations(n, SOURCES, {'R1': {}, 'R2': {}})


def test_legacy_nomination_is_readable_without_implying_complete_accounting():
    raw = {'references': [], 'selection_rationale': 'Archived', 'unresolved_searches': []}
    assert Nominations.model_validate(raw).model_dump() == raw
    with pytest.raises(ValueError):
        CurrentNominations.model_validate(raw)


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


def current_case():
    from models import CurrentAnalysis
    a, r = case()
    raw = a.model_dump()
    raw['format_version'] = 'citation_analysis/3'
    raw['claims'][0].update(citation_context=SOURCES['seed']['text'], claim_scope='seed_workflow')
    a = CurrentAnalysis.model_validate(raw)
    sources = copy.deepcopy(SOURCES)
    sources['bibliography'] = {'content_level': 'seed_reference_contexts', 'text': json.dumps({
        'R1': {'contexts': [SOURCES['seed']['text']]},
        'R2': {'contexts': ['A different reference is discussed here.']}})}
    r.findings[0].evidence += a.claims[0].predecessor_evidence
    return a, r, sources


def test_method_development_cannot_declare_just_a_component_as_its_ancestor():
    a, r, sources = current_case()
    a.claims[0].relation = 'method_development'
    a.claims[0].contributor_kind = 'experimental_component'
    with pytest.raises(ValueError, match='earlier assay'):
        validate_analysis(a, sources, ['R1'], ['o1'], [])
    with pytest.raises(ValueError, match='earlier assay'):
        graph_overlay(a, r, sources, 'pin', {})


@pytest.mark.parametrize('field', ['contributor', 'earlier_demonstration', 'seed_design_change'])
def test_blank_history_descriptions_cannot_publish_even_after_acceptance(field):
    a, r, sources = current_case()
    a.claims[0].relation = 'component_adoption'
    setattr(a.claims[0], field, '  \n')
    with pytest.raises(ValueError, match='nonblank ' + field):
        graph_overlay(a, r, sources, 'pin', {})


def test_unchanged_adoption_without_reported_gain_remains_representable():
    # Synthetic source fixture: the software must not require a new mechanism or
    # a performance gain. This is not a model-based semantic classification test.
    a, r, sources = current_case()
    c = a.claims[0]
    sources['seed']['text'] = 'We used the adapter design from method A without modification for RNA capture.'
    sources['R1']['text'] = 'Method A demonstrated this adapter design for RNA capture.'
    sources['bibliography']['text'] = json.dumps({'R1': {'contexts': [sources['seed']['text']]}})
    c.citation_context = sources['seed']['text']
    c.relation = 'component_adoption'
    c.contributor_kind = 'experimental_component'
    c.contributor = 'adapter design from method A'
    c.earlier_demonstration = sources['R1']['text']
    c.seed_design_change = 'The adapter design is retained without modification and used in the RNA capture operation.'
    c.reported_gain = 'Not established; no performance comparison was reported.'
    c.seed_evidence = [Evidence(source_id='seed', quote=sources['seed']['text'])]
    c.predecessor_evidence = [Evidence(source_id='R1', quote=sources['R1']['text'])]
    r.findings[0].evidence = c.seed_evidence + c.predecessor_evidence
    validate_analysis(a, sources, ['R1'], ['o1'], [])
    g = graph_overlay(a, r, sources, 'pin', {})
    assert g['links'][0]['kind'] == 'component_adoption'
    assert g['links'][0]['claim']['reported_gain'] == c.reported_gain


def test_new_claim_requires_its_exact_citation_context():
    a, r, sources = current_case()
    validate_analysis(a, sources, ['R1'], ['o1'], [])
    a.claims[0].citation_context = 'A different reference is discussed here.'
    with pytest.raises(ValueError, match='exact reference'):
        validate_analysis(a, sources, ['R1'], ['o1'], [])
    with pytest.raises(ValueError, match='exact reference'):
        graph_overlay(a, r, sources, 'pin', {})


def test_no_citation_context_withholds_even_an_accepted_claim():
    a, r, sources = current_case(); a.claims[0].citation_context = ''
    g = graph_overlay(a, r, sources, 'pin', {})
    assert g['format_version'] == 'citation_lineage_pilot/3'
    assert not g['links']
    assert g['withheld'][0]['reason'] == 'no_located_citation_context'


@pytest.mark.parametrize('scope', ['supporting_experiment', 'unlocalized'])
def test_outside_adoption_cannot_become_assay_history(scope):
    a, r, sources = current_case()
    a.claims[0].relation = 'component_adoption'
    a.claims[0].claim_scope = scope
    a.claims[0].seed_operation_ids = []
    validate_analysis(a, sources, ['R1'], ['o1'], [])
    g = graph_overlay(a, r, sources, 'pin', {})
    assert not g['links']
    assert g['withheld'][0]['reason'] == 'not_part_of_seed_workflow'
    assert g['withheld'][0]['claim']['relation'] == 'component_adoption'


def test_claim_location_cannot_contradict_operation_binding():
    a, _, sources = current_case(); a.claims[0].seed_operation_ids = []
    with pytest.raises(ValueError, match='locate at least one'):
        validate_analysis(a, sources, ['R1'], ['o1'], [])
    a.claims[0].claim_scope = 'supporting_experiment'
    a.claims[0].seed_operation_ids = ['o1']
    with pytest.raises(ValueError, match='cannot borrow'):
        validate_analysis(a, sources, ['R1'], ['o1'], [])


def test_new_review_acceptance_requires_both_specific_papers():
    a, r, sources = current_case()
    r.findings[0].evidence = a.claims[0].seed_evidence
    with pytest.raises(ValueError, match='exact predecessor'):
        validate_review(r, a, 'pin', sources)
    r.findings[0].decision = 'needs_evidence'
    validate_review(r, a, 'pin', sources)


def test_archived_analysis_does_not_acquire_new_assurances():
    from models import parse_analysis, CurrentAnalysis
    a, r = case()
    assert type(parse_analysis(a.model_dump())) is Analysis
    current, _, _ = current_case()
    assert isinstance(parse_analysis(current.model_dump()), CurrentAnalysis)
    assert graph_overlay(a, r, SOURCES, 'pin', {})['format_version'] == 'citation_lineage_pilot/2'


def test_current_comparison_keeps_citation_context_without_becoming_history():
    a, r, sources = current_case()
    g = graph_overlay(a, r, sources, 'pin', {})
    assert g['links'][0]['relationship_layer'] == 'comparison'
    assert g['links'][0]['claim']['citation_context'] == SOURCES['seed']['text']

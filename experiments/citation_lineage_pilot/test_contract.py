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
import hashlib
from types import SimpleNamespace
from run import review_cycle


def test_reference_acquisition_reuses_verified_full_text_without_network(tmp_path, monkeypatch):
    import run as runner
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    original = tmp_path / 'primary.xml'
    original.write_text('<article><front><article-meta><article-id pub-id-type="pmid">123</article-id>'
                        '</article-meta></front><body><p>Exact registered passage.</p></body></article>')
    source = {'kind': 'text', 'pmid': '123', 'title': 'Original protocol',
              'content_level': 'fulltext', 'text': runner.jats_text(original.read_bytes())[0],
              'original': {'path': str(original), 'sha256': runner.sha(original)}}
    cache = tmp_path / 'cache.json'; runner.write(cache, {'format_version': 'atlas_reference_cache/1', 'papers': {'123': source}})
    runner.write(inputs/'reference_cache_receipt.json', {'path': str(cache), 'sha256': runner.sha(cache)})
    runner.write(inputs/'sources_initial.json', {'seed': SOURCES['seed']})
    runner.write(inputs/'bibliography.json', {'R1': {'identifiers': {'pmid': '123'}, 'citation': 'Original protocol'}})
    def no_network(*args, **kwargs):
        raise AssertionError('Exact cached source must not be downloaded again')
    monkeypatch.setattr(runner, 'fetch', no_network)
    monkeypatch.setattr(runner, 'acquire_pmc_fulltext', no_network)
    result = runner.acquire(inputs, SimpleNamespace(references=[SimpleNamespace(reference_id='R1')]))
    assert result['R1']['text'] == source['text']
    assert result['R1']['cache_provenance']['lookup'] == 'exact_pmid'
    assert result['R1']['bibliographic_title_agreement'] == 'review_required_against_seed_citation'


def test_newly_acquired_full_paper_has_reusable_original_and_reading_artifact_bindings(tmp_path, monkeypatch):
    import run as runner
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    runner.write(inputs / 'sources_initial.json', {'seed': SOURCES['seed']})
    runner.write(inputs / 'bibliography.json', {'R1': {'identifiers': {'pmid': '123'}, 'citation': 'Earlier paper'}})

    def fetch_metadata(url, path):
        path.write_text('<PubmedArticleSet/>')
        return {'path': str(path), 'sha256': runner.sha(path)}

    def acquire(publication, output):
        article = output / 'article.xml'
        article.write_text('<article><front><article-meta><article-id pub-id-type="pmid">123</article-id>'
            '</article-meta></front><body><p>Original experimental contribution.</p></body></article>')
        text = output / 'article.txt'; text.write_text(runner.jats_text(article.read_bytes())[0])
        return {'status': 'fulltext_acquired', 'path': article.name, 'sha256': runner.sha(article),
                'text_path': text.name, 'text_sha256': runner.sha(text)}

    monkeypatch.setattr(runner, 'fetch', fetch_metadata)
    monkeypatch.setattr(runner, 'parse_pubmed', lambda raw: {'123': {'pmid': '123', 'title': 'Earlier paper',
        'abstract': [], 'identifiers': {'pmc': ['PMC123']}}})
    monkeypatch.setattr(runner, 'acquire_pmc_fulltext', acquire)
    result = runner.acquire(inputs, SimpleNamespace(references=[SimpleNamespace(reference_id='R1')]))['R1']
    assert Path(result['original']['path']).is_file() and Path(result['path']).is_file()
    assert result['text'] == Path(result['path']).read_text()
    runner.validate_cached_reference('123', result)


def test_cache_embedded_text_cannot_differ_from_pinned_rendition(tmp_path):
    import run as runner
    original = tmp_path / 'article.xml'; original.write_text('<article><body><p>Actual source.</p></body></article>')
    text = tmp_path / 'article.txt'; text.write_text(runner.jats_text(original.read_bytes())[0])
    source = {'pmid': '123', 'content_level': 'fulltext', 'text': 'Invented source passage.',
              'path': str(text), 'sha256': runner.sha(text),
              'original': {'path': str(original), 'sha256': runner.sha(original)}}
    with pytest.raises(ValueError, match='registered rendition'):
        runner.validate_cached_reference('123', source)


def test_cache_original_pmid_is_not_taken_from_reference_list(tmp_path):
    import run as runner
    original = tmp_path / 'article.xml'
    original.write_text('<article><front><article-meta><article-id pub-id-type="pmid">456</article-id>'
                        '</article-meta></front><body><p>Actual source.</p></body>'
                        '<back><ref-list><ref><pub-id pub-id-type="pmid">123</pub-id></ref></ref-list></back></article>')
    source = {'pmid': '123', 'content_level': 'fulltext', 'text': runner.jats_text(original.read_bytes())[0],
              'original': {'path': str(original), 'sha256': runner.sha(original)}}
    with pytest.raises(ValueError, match='original article PMID'):
        runner.validate_cached_reference('123', source)


def test_cached_pdf_requires_registered_text_artifact(tmp_path):
    import run as runner
    original = tmp_path / 'article.pdf'; original.write_bytes(b'%PDF-1.7\nPublished PDF')
    source = {'pmid': '123', 'content_level': 'fulltext', 'text': 'Unbound OCR rendition.',
              'original': {'path': str(original), 'sha256': runner.sha(original)}}
    with pytest.raises(ValueError, match='pinned derived text'):
        runner.validate_cached_reference('123', source)


def test_reference_cache_id_and_level_are_checked_during_acquisition(tmp_path):
    import run as runner
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    cache = tmp_path / 'cache.json'
    runner.write(cache, {'format_version': 'atlas_reference_cache/1',
                        'papers': {'123': {'pmid': '456', 'content_level': 'fulltext'}}})
    runner.write(inputs / 'reference_cache_receipt.json', {'path': str(cache), 'sha256': runner.sha(cache)})
    runner.write(inputs / 'sources_initial.json', {})
    runner.write(inputs / 'bibliography.json', {})
    with pytest.raises(ValueError, match='exact publication IDs'):
        runner.acquire(inputs, SimpleNamespace(references=[]))


def test_reference_cache_cannot_change_after_registration(tmp_path):
    import run as runner
    inputs = tmp_path/'inputs'; inputs.mkdir()
    cache = tmp_path/'cache.json'; runner.write(cache, {'format_version': 'atlas_reference_cache/1', 'papers': {}})
    runner.write(inputs/'reference_cache_receipt.json', {'path': str(cache), 'sha256': runner.sha(cache)})
    runner.write(inputs/'sources_initial.json', {}); runner.write(inputs/'bibliography.json', {})
    cache.write_text('{}')
    with pytest.raises(ValueError, match='cache changed'):
        runner.acquire(inputs, SimpleNamespace(references=[]))


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


def test_full_reference_ledger_keeps_background_and_uninvestigated_leads_without_edges():
    from models import reference_disposition_ledger
    a,r=case();sources=copy.deepcopy(SOURCES)
    sources['seed']['text']+=' R3 is an unverified reagent lead.'
    bibliography={'R1':{'citation':'Method A','contexts':[SOURCES['seed']['text']]},
                  'R2':{'citation':'Biological result','contexts':['Background motivation.']},
                  'R3':{'citation':'Reagent lead','contexts':['R3 is an unverified reagent lead.']}}
    sources['bibliography']={'text':json.dumps(bibliography)}
    n=CurrentNominations(references=[
        {'reference_id':'R1','contribution':'method A','reason_to_follow':'Compare the implementation',
         'seed_evidence':[{'source_id':'seed','quote':SOURCES['seed']['text']}]},
        {'reference_id':'R3','contribution':'possible reagent','reason_to_follow':'Resolve the reagent lead',
         'seed_evidence':[{'source_id':'seed','quote':'R3 is an unverified reagent lead.'}]}],
        selection_rationale='Technical context',unresolved_searches=[],
        excluded_references={'R2':'Biological-result background in the seed context'})
    a.reference_dispositions['R3']='Held: earlier implementation evidence is unavailable'
    g=graph_overlay(a,r,sources,'pin',{},nominations=n)
    rows={x['reference_id']:x for x in g['reference_ledger']['references']}
    assert set(rows)==set(bibliography)
    assert rows['R1']['claims'][0]['relationship_layer']=='comparison'
    assert rows['R2']['claims']==[] and rows['R2']['selection']=='not_followed'
    assert rows['R2']['citation_contexts']==['Background motivation.']
    assert rows['R3']['analysis_disposition'].startswith('Held:') and not rows['R3']['claims']
    assert len(g['links'])==1 and not g['links'][0]['historical_development_edge']
    # Archived export calls do not acquire a new ledger or new ancestry assurances.
    assert 'reference_ledger' not in graph_overlay(a,r,sources,'pin',{})
    n.excluded_references={}
    with pytest.raises(ValueError,match='every bibliography'):
        reference_disposition_ledger(n,a,sources,g['links'],g['withheld'])


def test_selective_acquisition_and_own_context_grounding_are_self_contained(tmp_path,monkeypatch):
    """Check routing/binding, not whether a question is scientifically important."""
    import run as runner
    first='We adapt Adapter Alpha [R1] to attach library handles.'
    second='We use Index Beta [R3] to identify cell-origin libraries.'
    background='Population biology [R2] motivates the demonstration sample.'
    seed='\n\n'.join([first,second,background,'Our release procedure is a separate contribution.'])
    bibliography={
        'R1':{'citation':'Adapter Alpha','identifiers':{'pmid':'111101'},'contexts':[first]},
        'R2':{'citation':'Population biology','identifiers':{'pmid':'222202'},'contexts':[background]},
        'R3':{'citation':'Index Beta','identifiers':{'pmid':'333303'},'contexts':[second]}}
    sources={'seed':{'text':seed,'content_level':'fulltext'},
             'bibliography':{'text':json.dumps(bibliography),'content_level':'seed_reference_contexts'}}
    nomination=CurrentNominations(references=[
        {'reference_id':'R1','contribution':'Library-handle attachment',
         'reason_to_follow':'Which Alpha adapter arrangement is adopted? This determines the library-route comparison.',
         'seed_evidence':[{'source_id':'seed','quote':first}]},
        {'reference_id':'R3','contribution':'Cell-origin indexing',
         'reason_to_follow':'Which Beta indexing feature is used? This determines whether the cell-attribution design is inherited.',
         'seed_evidence':[{'source_id':'seed','quote':second}]}],
        selection_rationale='Map handle attachment, cell attribution and the seed own release design before selecting earlier-source questions.',
        unresolved_searches=['The seed release design has no assigned historical donor.'],
        excluded_references={'R2':'Biological demonstration context; no technical connection question.'})
    validate_nominations(nomination,sources,bibliography,selective=True)
    wrong=nomination.model_copy(deep=True)
    wrong.references[1].seed_evidence[0].quote=first  # Real seed text, wrong citation.
    with pytest.raises(ValueError,match='own seed citation context'):
        validate_nominations(wrong,sources,bibliography,selective=True)
    blank=nomination.model_copy(deep=True);blank.references[0].reason_to_follow=' '
    with pytest.raises(ValueError,match='source question'):
        validate_nominations(blank,sources,bibliography,selective=True)
    runner.write(tmp_path/'sources_initial.json',sources)
    runner.write(tmp_path/'bibliography.json',bibliography)
    metadata_ids=[];requested=[]
    def metadata(url,path):
        from urllib.parse import parse_qs,urlparse
        metadata_ids.extend(parse_qs(urlparse(url).query)['id'][0].split(','))
        path.write_text('<PubmedArticleSet/>')
        return {'path':str(path),'sha256':runner.sha(path)}
    monkeypatch.setattr(runner,'fetch',metadata)
    monkeypatch.setattr(runner,'parse_pubmed',lambda raw:{p:{'pmid':p,'title':'Synthetic source','abstract':[],'identifiers':{}} for p in ['111101','333303']})
    def acquire(publication,output):
        requested.append(publication['pmid']);return {'status':'unavailable'}
    monkeypatch.setattr(runner,'acquire_pmc_fulltext',acquire)
    result=runner.acquire_registered(tmp_path,nomination)
    assert set(metadata_ids)==set(requested)=={'111101','333303'}
    assert set(result)-set(sources)=={'R1','R3'}
    assert 'R2' in nomination.excluded_references and 'R2' not in result
    assert set(x.reference_id for x in nomination.references)|set(nomination.excluded_references)==set(bibliography)


def test_selective_followups_use_actual_seed_contexts_without_reading_all60_refs(tmp_path,monkeypatch):
    import run as runner
    root=Path(__file__).resolve().parents[2]
    bundle=root/'experiments/history_contract_audit_20261113/reader_inputs.json'
    receipt=root/'experiments/history_contract_audit_20261113/READER_INPUT_VERIFICATION.json'
    if not bundle.is_file():
        pytest.skip('Optional real-source audit: restore the private reader_inputs.json bundle to '
                    f'{bundle}; verify its SHA256 using {receipt} (source job52178921). '
                    'The raw publication bundle is intentionally not required or committed for ordinary tests.')
    raw_bundle=bundle.read_bytes()
    expected=json.loads(receipt.read_text())['reader_inputs_sha256']
    assert hashlib.sha256(raw_bundle).hexdigest()==expected,'Restore the exact private bundle identified by READER_INPUT_VERIFICATION.json'
    cached=json.loads(raw_bundle)
    scope=json.loads((root/'experiments/history_contract_audit_20261113/fixtures.json').read_text())['selective_followup']
    selected_total=metadata_total=0
    for method,row in cached['methods'].items():
        plan=scope[method];raw=copy.deepcopy(row['nominations'])
        raw['references']=[x for x in raw['references'] if x['reference_id'] in plan['questions']]
        for item in raw['references']:item['reason_to_follow']=plan['questions'][item['reference_id']]
        raw['excluded_references'].update(plan['deferred'])
        raw['selection_rationale']=plan['primary_contributions']
        raw['unresolved_searches']=[plan['unresolved']]
        nomination=CurrentNominations.model_validate(raw)
        bibliography=json.loads(row['sources']['bibliography']['text'])
        validate_nominations(nomination,row['sources'],bibliography,selective=True)
        assert set(x.reference_id for x in nomination.references)|set(nomination.excluded_references)==set(bibliography)
        # Existing acquisition must fetch ONLY the chosen questions, not all metadata.
        inputs=tmp_path/method;inputs.mkdir()
        runner.write(inputs/'sources_initial.json',row['sources'])
        runner.write(inputs/'bibliography.json',bibliography)
        requested=[];pmids={bibliography[x.reference_id]['identifiers']['pmid'] for x in nomination.references}
        def metadata(url,path):path.write_text('<PubmedArticleSet/>');return {'path':str(path),'sha256':runner.sha(path)}
        monkeypatch.setattr(runner,'fetch',metadata)
        monkeypatch.setattr(runner,'parse_pubmed',lambda raw:{p:{'pmid':p,'title':'Fixture','abstract':[],'identifiers':{}} for p in pmids})
        def acquire(publication,output):requested.append(publication['pmid']);return {'status':'unavailable'}
        monkeypatch.setattr(runner,'acquire_pmc_fulltext',acquire)
        result=runner.acquire_registered(inputs,nomination)
        assert set(requested)==pmids
        assert set(result)-set(row['sources'])==set(plan['questions'])
        selected_total+=len(nomination.references);metadata_total+=len(bibliography)
        bad=nomination.model_copy(deep=True);bad.references[0].reason_to_follow=' '
        with pytest.raises(ValueError,match='source question'):validate_nominations(bad,row['sources'],bibliography,selective=True)
    assert metadata_total==60 and selected_total==4
    # Four is this stated design scope, not a production count limit or gold ancestry list.


def test_real_source_selectivity_audit_skips_cleanly_without_private_bundle(tmp_path,monkeypatch):
    bundle=Path(__file__).resolve().parents[2]/'experiments/history_contract_audit_20261113/reader_inputs.json'
    original=Path.is_file
    monkeypatch.setattr(Path,'is_file',lambda path:False if path==bundle else original(path))
    with pytest.raises(pytest.skip.Exception,match='READER_INPUT_VERIFICATION.json') as skipped:
        test_selective_followups_use_actual_seed_contexts_without_reading_all60_refs(tmp_path,monkeypatch)
    assert str(bundle) in str(skipped.value)


def test_new_run_defaults_focused_archived_review_defaults_full(monkeypatch):
    import run as runner
    captured=[]
    async def capture(args):captured.append(args)
    monkeypatch.setattr(runner,'execute',capture)
    monkeypatch.setattr(sys,'argv',['run.py','run','--batch','b','--article','a','--inventory','i','--output','o','--record','r','--paper','p'])
    runner.main();assert captured[-1].reading_policy=='focused'
    monkeypatch.setattr(sys,'argv',['run.py','review-existing','--previous','p','--output','o'])
    runner.main();assert captured[-1].reading_policy=='full'
    pipeline=json.loads((Path(__file__).resolve().parents[2]/'pipeline.json').read_text())
    assert pipeline['actions']['evolution']['parameters']['reading_policy']=='focused'


def test_evolution_file_inputs_resolve_through_recorder_and_real_cli(tmp_path,monkeypatch):
    from genomics_discovery.execution import pipeline as recorder
    import run as runner
    root=Path(__file__).resolve().parents[2]
    actual=json.loads((root/'pipeline.json').read_text())
    project=tmp_path/'repo';project.mkdir()
    action=copy.deepcopy(actual['actions']['evolution'])
    mapping={'format_version':actual['format_version'],'stages':actual['stages'],
             'model':actual['model'],'effort':actual['effort'],'code_patterns':[],
             'actions':{'evolution':action}}
    for rel in ('experiments/citation_lineage_pilot/run.py',
                'experiments/history_contract_audit_20261113/focused_reading.py'):
        target=project/rel;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((root/rel).read_bytes())
    map_file=project/'pipeline.json';map_file.write_text(json.dumps(mapping))
    inputs={}
    for key,content in {'batch_state':'{"cards":{}}','article':'<article/>','inventory':'{}'}.items():
        path=tmp_path/(key+'.input');path.write_text(content);inputs[key]=str(path)
    plan=recorder.plan(map_file,'evolution',tmp_path/'recorded',inputs=inputs,
                       parameters={'record':'example','paper':'paper:123'})
    captured=[]
    async def capture(args):captured.append(args)
    monkeypatch.setattr(runner,'execute',capture)
    monkeypatch.setattr(sys,'argv',plan['command'][1:])
    runner.main()
    args=captured[0]
    assert args.batch is None and args.batch_state==Path(inputs['batch_state'])
    assert args.article==Path(inputs['article']) and args.inventory==Path(inputs['inventory'])
    assert args.reading_policy=='focused'
    assert set(plan['input_pins'])==set(inputs.values())
    assert not Path(plan['resolved_values']['output']).exists()
    assert '--sources' not in plan['command'] and '--manifest' not in plan['command'] and '--atlas' not in plan['command']
    # Legacy directory input remains supported; the two input forms cannot mix.
    monkeypatch.setattr(sys,'argv',['run.py','run','--batch','legacy','--article','a',
        '--inventory','i','--output','o','--record','r','--paper','p'])
    runner.main();assert captured[-1].batch==Path('legacy') and captured[-1].batch_state is None
    monkeypatch.setattr(sys,'argv',['run.py','run','--batch','legacy','--batch-state','state.json',
        '--article','a','--inventory','i','--output','o','--record','r','--paper','p'])
    with pytest.raises(SystemExit):runner.main()


def test_prepare_reads_direct_state_file_or_legacy_state_and_reaches_custody_gate(tmp_path,monkeypatch):
    import run as runner
    direct=tmp_path/'extraction_state.input';legacy=tmp_path/'legacy';legacy.mkdir()
    runner.write(direct,{'cards':{'seed':{'status':'supported','origin':'direct'}}})
    runner.write(legacy/'state.json',{'cards':{'seed':{'status':'supported','origin':'legacy'}}})
    seen=[]
    def custody(row):seen.append(row['origin']);raise RuntimeError('scientific custody gate reached')
    monkeypatch.setattr(runner,'revalidate_completed',custody)
    for kind in ('direct','legacy'):
        args=SimpleNamespace(output=tmp_path/('out_'+kind),record='seed',
            batch_state=direct if kind=='direct' else None,batch=legacy if kind=='legacy' else None)
        with pytest.raises(RuntimeError,match='custody gate reached'):runner.prepare(args)
    assert seen==['direct','legacy']


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


def review_only_completion_fixture(tmp_path, *, bad_provenance=False, missing_reference=False, include_stage=False,legacy_nomination=False):
    """Synthetic archives exercising the real transport, science and graph checks."""
    import run as runner
    base = tmp_path / 'research'; inputs = base / 'inputs'; inputs.mkdir(parents=True)
    sources=copy.deepcopy(SOURCES)
    if missing_reference:
        sources['R1'].update(content_level='abstract',pmid='123')
        refs={'R1':{'reference_id':'R1','identifiers':{'pmid':'123'},'contexts':[SOURCES['seed']['text']]}}
        sources['bibliography']={'content_level':'seed_reference_contexts','text':json.dumps(refs)}
        runner.write(inputs/'bibliography.json',refs)
        runner.write(inputs/'sources_initial.json',{'seed':sources['seed'],'bibliography':sources['bibliography']})
        (inputs/'seed.xml').write_text('<article><body><p>Unchanged seed.</p></body></article>')
    runner.write(inputs / 'sources.json', sources)
    runner.write(inputs / 'seed_extraction.json', {'states':[],'operations': [{'id': 'o1'}],'flows':[]})
    runner.write(inputs / 'catalogue.json', [])
    runner.write(inputs / 'seed_binding.json', {'record_id': 'synthetic-seed'})
    nominations = {'references': [{'reference_id': 'R1', 'contribution': 'method',
        'reason_to_follow': 'Resolve the comparison baseline',
        'seed_evidence': [{'source_id': 'seed', 'quote': SOURCES['seed']['text']}]}],
        'selection_rationale': 'Synthetic fixture', 'unresolved_searches': []}
    if missing_reference and not legacy_nomination:nominations.update(format_version='citation_nomination/2',excluded_references={})
    analysis, review = case()

    def stage(root, role, document, thread):
        attempt = root / 'native' / role; attempt.mkdir(parents=True)
        runner.write(attempt / 'artifact.json', document)
        usage = {'input_tokens': 1, 'output_tokens': 1}
        runner.write(attempt / 'result.json', {'exit_code': 0, 'error': None,
            'thread_id': thread, 'usage': usage, 'elapsed_seconds': 1})
        (attempt / 'events.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in [
            {'type': 'thread.started', 'thread_id': thread}, {'type': 'turn.started'},
            {'type': 'turn.completed', 'usage': usage}]))
        (attempt / 'final.txt').write_text('Synthetic completed transport fixture.')
        runner.write(attempt / 'image_delivery.json', [])
        runner.write(attempt / 'command.json', {'argv': ['codex', 'exec']})
        runner.write(attempt / 'input_pins.json', {str(p): runner.sha(p)
            for p in (root / 'inputs').rglob('*') if p.is_file()})
        return {'role': role, 'path': str(attempt), 'status': 'validated',
            'thread_id': thread, 'artifact_sha256': runner.sha(attempt / 'artifact.json')}

    rows = [stage(base, 'nominate', nominations, 'research-nominate'),
            stage(base, 'analyse', analysis.model_dump(mode='json'), 'research-analyse')]
    author = Path(rows[-1]['path']) / 'artifact.json'
    review.analysis_sha256 = runner.sha(author)
    rows.append(stage(base, 'review', review.model_dump(mode='json'), 'research-review'))
    runner.write(base / 'nominations.json', nominations)
    runner.write(base / 'analysis.json', analysis.model_dump(mode='json'))
    runner.write(base / 'review.json', review.model_dump(mode='json'))
    runner.write(base / 'graph.json', graph_overlay(analysis, review, sources,
        runner.sha(author), runner.read(inputs / 'seed_binding.json'), catalogue=[]))
    runner.write(base / 'state.json', {'status': 'completed', 'stages': rows})
    current = tmp_path / 'review-only'
    copied, prior_analysis = runner.prepare_review_reuse(base, current)
    provenance = runner.read(copied / 'review_reuse.json')
    if bad_provenance:
        provenance['source_registry_sha256'] = '0' * 64
        runner.write(copied / 'review_reuse.json', provenance)
    fresh = stage(current, 'review', review.model_dump(mode='json'), 'fresh-review')
    runner.write(current / 'analysis.json', runner.read(prior_analysis))
    runner.write(current / 'review.json', review.model_dump(mode='json'))
    runner.write(current / 'graph.json', runner.read(base / 'graph.json'))
    runner.write(current / 'state.json', {'status': 'completed', 'stages': [fresh],
        'review_reuse': provenance})
    return (base,current,stage) if include_stage else (base,current)


def test_review_only_completion_reopens_real_provenance_transport_and_graph_contract(tmp_path):
    import run as runner
    base, current = review_only_completion_fixture(tmp_path)
    before = (base / 'state.json').read_bytes()
    state, author = runner.validate_completed_citation(current)
    assert [row['role'] for row in state['stages']] == ['review']
    assert author == current / 'inputs/prior_analysis.json'
    assert (base / 'state.json').read_bytes() == before


def test_review_only_completion_rejects_recorded_but_false_prior_source_pin(tmp_path):
    import run as runner
    _, current = review_only_completion_fixture(tmp_path, bad_provenance=True)
    with pytest.raises(ValueError, match='does not match validated prior research'):
        runner.validate_completed_citation(current)


def source_recovery_fixture(tmp_path,*,legacy_nomination=False):
    import run as runner
    from change_models import ChangeAnalysis,CoverageMethodReview
    base,previous,stage=review_only_completion_fixture(tmp_path,missing_reference=True,include_stage=True,legacy_nomination=legacy_nomination)
    original=tmp_path/'recovered.xml';original.write_text('<article><front><article-meta>'
        '<article-id pub-id-type="pmid">123</article-id></article-meta></front><body><p>'
        +SOURCES['R1']['text']+' Full molecular implementation is now available.</p></body></article>')
    cached={'content_level':'fulltext','pmid':'123','text':runner.jats_text(original.read_bytes())[0],
        'original':{'path':str(original),'sha256':runner.sha(original)}}
    cache=tmp_path/'cache.json';runner.write(cache,{'format_version':'atlas_reference_cache/1','papers':{'123':cached}})
    current=tmp_path/'source-recovery';inputs=runner.prepare_source_recovery(previous,current,cache,
        'Resolve the nominated earlier library implementation from its newly available primary text.')
    evidence=[{'source_id':'seed','quote':SOURCES['seed']['text']},{'source_id':'R1','quote':SOURCES['R1']['text']}]
    analysis=ChangeAnalysis.model_validate({'format_version':'citation_analysis/4','seed_method':'Synthetic assay',
        'claims':[{'id':'c1','reference_id':'R1','contributor':'Earlier comparator','contributor_kind':'assay',
        'relation':'comparison','citation_context':SOURCES['seed']['text'],'claim_scope':'seed_workflow',
        'seed_operation_ids':['o1'],'change_records':[{'id':'d1','compared_feature':'Comparator library preparation',
        'before':'The earlier assay prepares a sequencing library.','after':'The seed compares this assay.',
        'retained':[],'workflow_locations':{'earlier_scope':'paper_only','earlier':[],
        'later':[{'kind':'operation','record_id':'o1','field':None}]},'evidence':{'before':[evidence[1]],'after':[evidence[0]],
        'retained':[],'inheritance':[]},'consequence_and_limits':{'effect_status':'not_established',
        'reported_effects':[],'limits':[],'limit_evidence':[],'unresolved':[]}}],
        'catalogue_candidates':[],'unresolved':[]}],'reference_dispositions':{'R1':'Comparison only'},'summary':'Synthetic comparison, no ancestry.'})
    author=stage(current,'analyse',analysis.model_dump(mode='json'),'source-recovery-analyse')
    author_path=Path(author['path'])/'artifact.json'
    review=CoverageMethodReview.model_validate({'format_version':'citation_change_review/3',
        'analysis_sha256':runner.sha(author_path),'findings':[{'claim_id':'c1','decision':'accept',
        'rationale':'Synthetic provenance contract fixture, not a scientific judgment.','evidence':evidence,
        'unresolved_assessments':[],'change_checks':[{'change_id':'d1','decision':'accept',
        'factual_assessment':'Fixture source quotation.','workflow_assessment':'Existing fixture operation.',
        'evidence_assessment':'Both fixture sources supplied.','consequence_assessment':'No gain asserted.',
        'evidence':evidence}],'method_match':{'status':'not_applicable','record_id':None,
        'rationale':'Comparison fixture has no resolved ancestral endpoint.','evidence':[]}}],
        'coverage_findings':[],'summary':'Synthetic contract fixture.'})
    reviewer=stage(current,'review',review.model_dump(mode='json'),'source-recovery-review')
    runner.write(current/'analysis.json',analysis.model_dump(mode='json'));runner.write(current/'review.json',review.model_dump(mode='json'))
    runner.write(current/'graph.json',graph_overlay(analysis,review,runner.read(inputs/'sources.json'),
        runner.sha(author_path),runner.read(inputs/'seed_binding.json'),catalogue=[],
        nominations=(None if legacy_nomination else CurrentNominations.model_validate(runner.read(current/'nominations.json')))))
    runner.write(current/'state.json',{'status':'completed','stages':[author,reviewer],
        'source_recovery':runner.read(inputs/'source_recovery.json')})
    return previous,current,cache


def test_source_recovery_reuses_real_validated_nomination_and_requires_fresh_analysis_review(tmp_path):
    import run as runner
    previous,current,_=source_recovery_fixture(tmp_path)
    prior=(previous/'state.json').read_bytes();nomination=(previous/'nominations.json').read_bytes()
    state,author=runner.validate_completed_citation(current)
    assert [r['role'] for r in state['stages']]==['analyse','review']
    assert author==current/'native/analyse/artifact.json'
    assert (current/'nominations.json').read_bytes()==nomination
    assert (previous/'state.json').read_bytes()==prior
    assert runner.read(current/'inputs/sources.json')['R1']['content_level']=='fulltext'
    assert runner.read(previous/'inputs/sources.json')['R1']['content_level']=='abstract'


def test_source_recovery_preserves_legacy_nomination_without_inventing_exclusion_accounting(tmp_path):
    import run as runner
    previous,current,_=source_recovery_fixture(tmp_path,legacy_nomination=True)
    runner.validate_completed_citation(current)
    assert (current/'nominations.json').read_bytes()==(previous/'nominations.json').read_bytes()
    assert 'excluded_references' not in runner.read(current/'nominations.json')
    assert 'reference_ledger' not in runner.read(current/'graph.json')


@pytest.mark.parametrize('defect',['seed','bibliography','catalogue','donor','provenance','missing_analysis'])
def test_source_recovery_rejects_changed_seed_citation_donor_or_asserted_provenance(tmp_path,defect):
    import run as runner
    previous,current,cache=source_recovery_fixture(tmp_path)
    if defect in {'seed','bibliography','catalogue'}:
        name={'seed':'seed.xml','bibliography':'bibliography.json','catalogue':'catalogue.json'}[defect]
        path=current/'inputs'/name;path.write_text(path.read_text()+' ')
    elif defect=='donor':
        value=runner.read(current/'inputs/sources.json');value['R1']['pmid']='999'
        runner.write(current/'inputs/sources.json',value)
        record=runner.read(current/'inputs/source_recovery.json');record['updated_source_registry_sha256']=runner.sha(current/'inputs/sources.json')
        runner.write(current/'inputs/source_recovery.json',record)
        state=runner.read(current/'state.json');state['source_recovery']=record;runner.write(current/'state.json',state)
    elif defect=='provenance':
        record=runner.read(current/'inputs/source_recovery.json');record['state_sha256']='0'*64
        runner.write(current/'inputs/source_recovery.json',record)
        state=runner.read(current/'state.json');state['source_recovery']=record;runner.write(current/'state.json',state)
    else:
        state=runner.read(current/'state.json');state['stages']=state['stages'][1:];runner.write(current/'state.json',state)
    # Repin the alleged new transport inputs: inherited proof must still reject.
    for row in runner.read(current/'state.json')['stages']:
        attempt=Path(row['path']);runner.write(attempt/'input_pins.json',{
            str(p):runner.sha(p) for p in (current/'inputs').rglob('*') if p.is_file()})
    with pytest.raises(ValueError,match='Source recovery|Source-recovery'):
        runner.validate_completed_citation(current)


def test_source_recovery_cannot_replace_an_already_available_fulltext_or_download(tmp_path,monkeypatch):
    import run as runner
    previous,current,cache=source_recovery_fixture(tmp_path)
    def no_network(*args,**kwargs):raise AssertionError('Recovery uses registered bytes only')
    monkeypatch.setattr(runner,'fetch',no_network);monkeypatch.setattr(runner,'acquire_pmc_fulltext',no_network)
    with pytest.raises(ValueError,match='no missing nominated'):
        runner.recovered_sources(current,cache)


def test_source_recovery_rejects_cache_that_labels_another_primary_as_the_nominated_donor(tmp_path):
    import run as runner
    previous,_,cache=source_recovery_fixture(tmp_path)
    value=runner.read(cache);source=value['papers']['123'];original=Path(source['original']['path'])
    original.write_bytes(original.read_bytes().replace(b'>123<',b'>999<'))
    source['original']['sha256']=runner.sha(original);source['text']=runner.jats_text(original.read_bytes())[0]
    runner.write(cache,value)
    with pytest.raises(ValueError,match='original article PMID'):
        runner.prepare_source_recovery(previous,tmp_path/'wrong-donor',cache,'Inspect the missing nominated source.')
    assert not (tmp_path/'wrong-donor').exists()


def test_source_recovery_pipeline_arguments_reach_real_parser_without_models(tmp_path,monkeypatch):
    import run as runner
    action=runner.read(Path(__file__).resolve().parents[2]/'pipeline.json')['actions']['evolution.recover-sources']
    values={'previous_state':str(tmp_path/'state.json'),'source_cache':str(tmp_path/'cache.json'),
        'reason':'Inspect the nominated implementation in the newly available primary.','reading_policy':'focused',
        'output':str(tmp_path/'new')}
    argv=['recover-sources',*[part.format_map(values) for part in action['arguments']]]
    captured=[]
    async def execute(args):captured.append(args)
    monkeypatch.setattr(runner,'execute',execute);monkeypatch.setattr(sys,'argv',['citation-run',*argv])
    runner.main()
    assert len(captured)==1 and captured[0].command=='recover-sources'
    assert captured[0].previous_state==Path(values['previous_state'])
    assert captured[0].source_cache==Path(values['source_cache']) and captured[0].reason==values['reason']

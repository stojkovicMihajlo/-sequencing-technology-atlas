"""Offline orchestrator contracts. Fake outputs are not scientific demonstrations."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from genomics_discovery.ideation.models import CandidateIdea, parse_candidate, RESEARCH_AREAS
from genomics_discovery.ideation.run import (
    CitationCheck, LiteratureReport, context_for, literature_reference_keys,
    merge_sources, pipeline, prepare, read, validate_artifact, validate_literature_report,
    validate_literature_execution, write,
    NativeStages, ReviewPanel, inherited_search_coverage, validate_review_search,
)
from genomics_discovery.illumina.models import digest
from tests.ideation.test_context import context
from tests.ideation.test_models import atlas_ref, candidate_data, review_data
from tests.ideation.test_research import research_candidate_data, research_questions


@pytest.fixture
def prepared(context, tmp_path, monkeypatch):
    request = tmp_path / "request.json"
    write(request, {"prompt": "Develop a sequencing assay to distinguish the desired RNA states.",
                    "constraints": ["Keep specimen identity linked to readout."]})
    directory = tmp_path / "run"
    prepare(request, context.path, directory)
    # Rendering has separate tests. This exercises the exact orchestrator handoff.
    monkeypatch.setitem(sys.modules, "genomics_discovery.ideation.report", SimpleNamespace(
        export_report=lambda output, config, files, panel: write(output / "report_handoff.json",
            {"candidate_files": files, "panel": panel, "atlas_sha256": config["atlas_sha256"]})))
    return directory


class FakeStages:
    def __init__(self, *, revise=False, fail_expert=None, stale_review=False, omit_response=False,
                 skip_review_target=False, retain_first=False, omit_final_resolution=False,
                 stale_literature=False, skip_literature_change=False, use_citation=False,
                 omit_citation_check=False, claim_unobserved_search=False):
        self.revise = revise
        self.fail_expert = fail_expert
        self.stale_review = stale_review
        self.omit_response = omit_response
        self.skip_review_target = skip_review_target
        self.retain_first = retain_first
        self.omit_final_resolution = omit_final_resolution
        self.stale_literature = stale_literature
        self.skip_literature_change = skip_literature_change
        self.use_citation = use_citation
        self.omit_citation_check = omit_citation_check
        self.claim_unobserved_search = claim_unobserved_search
        self.contracts = []

    async def __call__(self, contract, attempt):
        self.contracts.append(copy.deepcopy(contract))
        role = contract["stage"]
        if role == "plan":
            request = read(contract["request_file"])
            payload = {"original_request": request["prompt"], "constraints": request["constraints"],
                "interpreted_goal": "Seek distinguishable sequencing measurements.", "assumptions": [],
                "experts": [{"id": ident, "expertise": expertise, "question": "Investigate an assay route.",
                    "approach": approach, "why_distinct": "Distinct source of measurement signal.",
                    "research_questions": research_questions()}
                    for ident, expertise, approach in [("expert_a", "Molecular chemistry", "Covalent recording"),
                                                       ("expert_b", "Assay architecture", "Partitioned recording")]]}
        elif role == "propose":
            if contract["expert_id"] == self.fail_expert:
                raise ValueError("Fixture transport failure")
            payload = research_candidate_data()
            payload["id"] = contract["expert_id"]
            if self.use_citation:
                source = attempt/'retrieved.txt'
                source.write_text('The original assay uses a reverse-transcription primer.')
                captured = context_for(contract).add_source(source, url='https://example.org/paper',
                    title='Original assay', content_level='excerpt')['data']
                payload['assessments'][0]['literature_refs'] = [{'source_id': captured['source_id'],
                    'content_sha256': captured['content_sha256'], 'locator': 'Methods', 'quote': source.read_text()}]
        elif role == "literature":
            ideas = [parse_candidate(read(p)) for p in contract['candidate_files']]
            findings = []
            for idea in ideas:
                findings.append({'candidate_id': idea.id, 'candidate_sha256': '0'*64 if self.stale_literature else digest(idea),
                    'checks': [{'id': 'check'+str(n), 'target': target, 'claim': 'Unproven proposed measurement or change.',
                        'verdict': 'not_found', 'reason': 'No live research in this software fixture.'}
                        for n, target in enumerate(['candidate', *('research:'+area for area in RESEARCH_AREAS)] + ([] if self.skip_literature_change else ['change:'+c.id for c in idea.changes]))],
                    'closest_priors': [], 'coverage_limitations': ['No scientific verification in a mock.']})
            keys = set().union(*(literature_reference_keys(i.model_dump(mode='json')) for i in ideas))
            sources = read(contract['sources_file'])['sources']
            checks = [{'source_id': sid, 'content_sha256': pin, 'declared_url': sources[sid]['url'],
                'declared_title': sources[sid]['title'], 'access_status': 'unavailable', 'status': 'unverifiable',
                'reason': 'This fixture has not reopened the publication.'} for sid, pin in sorted(keys)]
            payload = {'candidate_findings': findings, 'citation_checks': [] if self.omit_citation_check else checks,
                'search_status': 'searched' if self.claim_unobserved_search else 'not_performed',
                'searches_performed': ['claimed search'] if self.claim_unobserved_search else [],
                'limitations': ['No external search executed in this software fixture.']}
        elif role == "review":
            reviews = []
            previous = read(contract["review_file"]) if contract.get("review_file") else None
            resolutions = []
            for path in contract["candidate_files"]:
                idea = parse_candidate(read(path))
                review = review_data(idea)
                if self.revise and contract["revision_round"] == 0 and idea.id == "expert_a":
                    review["disposition"] = "revise"
                    review["issues"] = [{"id": key, "target": target, "severity": "major",
                        "reason": "Fixture correction needed.", "next_test": "Inspect the relevant source."}
                        for key, target in [("issue_a", "operation:prepare"), ("issue_b", "candidate")]]
                if self.retain_first and idea.id == "expert_a":
                    review["disposition"] = "retain_for_testing"
                if self.stale_review:
                    review["candidate_sha256"] = "0" * 64
                if self.skip_review_target:
                    review["reviewed_targets"].pop()
                if previous:
                    old = next(r for r in previous["reviews"] if r["candidate_id"] == idea.id)
                    review["issues"] = copy.deepcopy(old["issues"])
                    resolutions.extend({"candidate_id": idea.id, "issue_id": issue["id"],
                        "status": "unresolved", "reason": "Still requires scientific evidence."}
                        for issue in old["issues"])
                reviews.append(review)
            payload = {"reviews": reviews, "comparison_summary": "Software fixture; no scientific claim.",
                       "searches_performed": [], "previous_issue_resolutions": resolutions}
            if resolutions and self.omit_final_resolution:
                payload["previous_issue_resolutions"].pop()
        else:
            payload = {"candidate": read(contract["previous_candidate_file"]),
                "responses": [{"issue_id": ident, "status": "unresolved", "explanation": "Requires evidence."}
                              for ident in ["issue_a", "issue_b"]]}
            payload["candidate"]["expected_benefit"] = "Revised untested prediction."
            if self.omit_response:
                payload["responses"].pop()
        write(attempt / "artifact.json", payload)
        return {"thread_id": "offline-fixture-" + str(len(self.contracts)), "usage": {},
                "web_search_event_observed": False, "elapsed_seconds": 0}


@pytest.mark.asyncio
async def test_independent_experts_then_joint_review_and_exact_handoff(prepared):
    stages = FakeStages()
    state = await pipeline(prepared, stages)
    assert state["status"] == "completed"
    proposals = [c for c in stages.contracts if c["stage"] == "propose"]
    assert len(proposals) == 2
    assert all(c["candidate_files"] == [] for c in proposals)
    assert len({c["research_plan_file"] for c in proposals}) == 1
    assert len({c["atlas_file"] for c in proposals}) == 1
    assert len({c["sources_file"] for c in proposals}) == 2
    review = next(c for c in stages.contracts if c["stage"] == "review")
    assert {read(p)["id"] for p in review["candidate_files"]} == {"expert_a", "expert_b"}
    assert state["final_dispositions"] == {"expert_a": "hold", "expert_b": "hold"}
    assert read(prepared / "report_handoff.json")["panel"]["reviews"]
    literature = next(c for c in stages.contracts if c['stage'] == 'literature')
    assert {read(p)['id'] for p in literature['candidate_files']} == {'expert_a', 'expert_b'}
    assert review['literature_report_file'] == literature['output_file']
    assert read(prepared/'literature_report.json')['search_status'] == 'not_performed'
    assert read(prepared/'literature_coverage.json')['candidates'][0]['unchecked_graph_targets']
    assert not (prepared / "atlas.json").exists()  # No publication into established graph.
    with pytest.raises(ValueError, match="already started"):
        await pipeline(prepared, stages)


@pytest.mark.asyncio
async def test_revision_sees_exact_questions_and_requires_fresh_review(prepared):
    stages = FakeStages(revise=True)
    state = await pipeline(prepared, stages)
    assert state["status"] == "completed"
    assert [c["stage"] for c in stages.contracts].count("review") == 2
    revision = next(c for c in stages.contracts if c["stage"] == "revise")
    issues = next(r for r in read(revision["review_file"])["reviews"] if r["candidate_id"] == "expert_a")["issues"]
    assert {i["id"] for i in issues} == {"issue_a", "issue_b"}
    assert {read(p)["id"] for p in revision["candidate_files"]} == {"expert_a", "expert_b"}
    assert revision["revision_round"] == 1
    final_review = [c for c in stages.contracts if c["stage"] == "review"][-1]
    revised = next(p for p in final_review["candidate_files"] if read(p)["id"] == "expert_a")
    assert Path(revised).name == "revised.json"
    assert read(revised)["expected_benefit"] == "Revised untested prediction."
    responses = read(Path(revised).parent / "issue_responses.json")
    assert {r["issue_id"] for r in responses} == {"issue_a", "issue_b"}
    assert state["final_dispositions"]["expert_a"] == "hold"
    assert len(read(prepared / "final_review.json")["previous_issue_resolutions"]) == 2


@pytest.mark.asyncio
async def test_failed_author_preserves_other_candidate_and_failure(prepared):
    stages = FakeStages(fail_expert="expert_a")
    state = await pipeline(prepared, stages)
    assert state["status"] == "completed_with_stage_failures"
    assert state["failures"][0]["stage"] == "02_expert_a"
    assert [read(p)["id"] for p in state["candidate_files"]] == ["expert_b"]
    assert state["final_dispositions"] == {"expert_b": "hold"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["stale_review", "skip_review_target"])
async def test_review_cannot_bind_wrong_bytes_or_skip_graph_targets(prepared, failure):
    with pytest.raises(ValueError):
        await pipeline(prepared, FakeStages(**{failure: True}))
    state = read(prepared / "state.json")
    assert state["status"] == "failed"
    assert state["failures"][-1]["stage"] == "04_shared_review"
    assert (prepared / "candidates/expert_a/initial.json").exists()
    assert not (prepared / "final_review.json").exists()


@pytest.mark.asyncio
async def test_revision_cannot_silently_omit_an_issue(prepared):
    stages = FakeStages(revise=True, omit_response=True)
    state = await pipeline(prepared, stages)
    assert state["status"] == "completed_with_stage_failures"
    assert any("every exact prior review issue" in f["error"] for f in state["failures"])
    assert not (prepared / "candidates/expert_a/revised.json").exists()
    assert state["final_dispositions"]["expert_a"] == "revise"


@pytest.mark.asyncio
async def test_final_reviewer_cannot_silently_drop_prior_issues(prepared):
    with pytest.raises(ValueError, match="every previous issue"):
        await pipeline(prepared, FakeStages(revise=True, omit_final_resolution=True))
    state = read(prepared / "state.json")
    assert state["status"] == "failed"
    assert state["failures"][-1]["stage"] == "06_final_review"


@pytest.mark.asyncio
async def test_review_dispositions_remain_separate_in_report(prepared):
    # Mechanical routing test only: this fake reviewer is not evidence of feasibility.
    state = await pipeline(prepared, FakeStages(retain_first=True))
    assert state["final_dispositions"] == {"expert_a": "retain_for_testing", "expert_b": "hold"}
    panel = read(prepared / "report_handoff.json")["panel"]
    assert {r["candidate_id"]: r["disposition"] for r in panel["reviews"]} == state["final_dispositions"]
    assert all(r["novelty_status"] == "search_incomplete" for r in panel["reviews"])


@pytest.mark.asyncio
async def test_prepared_input_mutation_rejected_before_any_agent(prepared):
    config = read(prepared / "config.json")
    Path(config["atlas_file"]).write_text("{}")
    stages = FakeStages()
    with pytest.raises(ValueError, match="Prepared input changed"):
        await pipeline(prepared, stages)
    assert not stages.contracts


@pytest.mark.asyncio
async def test_changed_history_overlay_is_rejected_before_any_agent(prepared):
    from experiments.ideation_demo_20261105.snapshot import snapshot
    from tests.ideation.test_snapshot import make_pilot, add_pair_registry
    from genomics_discovery.illumina.extraction import sha

    root, overlay = make_pilot(prepared.parent/'history_fixture')
    add_pair_registry(root, overlay)
    output = prepared.parent/'history_snapshot'
    snapshot(root, output)
    manifest = output/'history_inputs.json'
    config = read(prepared/'config.json')
    config.update(history_manifest=str(manifest), history_sha256=sha(manifest))
    write(prepared/'config.json', config)
    # Break nested bytes without changing the top-level manifest hash.
    (output/'pairs/one/sources.json').write_text('{}')
    stages = FakeStages()
    with pytest.raises(ValueError, match='History input changed'):
        await pipeline(prepared, stages)
    assert not stages.contracts


def test_source_merge_accepts_same_capture_with_distinct_custody_locations(tmp_path):
    paths = []
    for index in range(2):
        directory = tmp_path / str(index); directory.mkdir()
        source = directory / "text.txt"; source.write_text("Exact inspected passage.")
        import hashlib
        registry = directory / "sources.json"
        write(registry, {"format_version": "ideation_sources/1", "sources": {"lit": {
            "source_id": "lit", "url": "https://example.org/article", "title": "Paper",
            "content_level": "excerpt", "content_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "path": str(source), "captured_at": str(index)}}})
        paths.append(registry)
    merged = merge_sources(paths)
    assert len(merged["sources"]) == 1
    assert merged["sources"]["lit"]["additional_captures"][0]["path"].endswith("1/text.txt")
    altered = read(paths[1]); altered["sources"]["lit"]["content_sha256"] = "0" * 64
    write(paths[1], altered)
    with pytest.raises(ValueError):
        merge_sources(paths)


def test_identical_source_bytes_preserve_different_metadata_and_origin_receipts(tmp_path):
    import hashlib
    source = tmp_path/'text.txt'; source.write_text('Same exact scientific passage.')
    pin = hashlib.sha256(source.read_bytes()).hexdigest()
    first = {'source_id': 'lit', 'url': 'https://example.org/paper', 'title': 'Full publication title',
        'content_level': 'fulltext', 'content_sha256': pin, 'path': str(source), 'captured_at': 'first',
        'origin_receipts': [{'original_sha256': 'a'*64, 'original_path': 'first'}]}
    second = {**copy.deepcopy(first), 'title': 'Abbreviated title', 'content_level': 'excerpt', 'captured_at': 'second',
              'origin_receipts': [{'original_sha256': 'b'*64, 'original_path': 'second'}]}
    paths = [tmp_path/'first.json', tmp_path/'second.json']
    for path, entry in zip(paths, [first, second]):
        write(path, {'format_version': 'ideation_sources/1', 'sources': {'lit': entry}})
    original_files = [p.read_bytes() for p in paths]
    merged = merge_sources(paths)['sources']['lit']
    assert merged['title'] == first['title']  # Existing address retained, not canonicalized from model preference.
    assert {d['title'] for d in merged['capture_declarations']} == {first['title'], second['title']}
    assert {d['content_level'] for d in merged['capture_declarations']} == {'excerpt', 'fulltext'}
    assert merged['origin_receipts'] == first['origin_receipts'] + second['origin_receipts']
    assert merged['metadata_status'].startswith('differing_capture_declarations')
    assert [p.read_bytes() for p in paths] == original_files
    inherited = tmp_path/'inherited.json'
    write(inherited, {'format_version': 'ideation_sources/1', 'sources': {'lit': merged}})
    again = merge_sources([inherited, paths[1]])['sources']['lit']
    assert len(again['capture_declarations']) == 2
    assert len(again['origin_receipts']) == 2


def test_coordinator_cannot_rewrite_explicit_user_constraints(prepared):
    config = read(prepared / "config.json")
    output = prepared / "bad_plan.json"
    request = read(config["request_file"])
    write(output, {"original_request": request["prompt"], "constraints": [],
        "interpreted_goal": "Something", "assumptions": [], "experts": [
            {"id": ident, "expertise": "Expert", "question": "Question", "approach": "Approach", "why_distinct": "Reason",
             "research_questions": research_questions()}
            for ident in ("first", "second")]})
    with pytest.raises(ValueError, match="verbatim"):
        validate_artifact({**config, "stage": "plan"}, output)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['stale_literature', 'skip_literature_change', 'claim_unobserved_search'])
async def test_literature_stage_fails_closed_without_losing_proposals(prepared, failure):
    with pytest.raises(ValueError):
        await pipeline(prepared, FakeStages(**{failure: True}))
    state = read(prepared/'state.json')
    assert state['status'] == 'failed'
    assert state['failures'][-1]['stage'] == '03_literature'
    assert (prepared/'candidates/expert_a/initial.json').is_file()
    assert (prepared/'candidates/expert_b/initial.json').is_file()
    assert not any(row['role'] == 'review' for row in state['stages'])


@pytest.mark.asyncio
async def test_used_citations_receive_explicit_check_and_unverifiable_is_preserved(prepared):
    stages = FakeStages(use_citation=True)
    await pipeline(prepared, stages)
    report = read(prepared/'literature_report.json')
    assert len(report['citation_checks']) == 1  # Same captured source used by both candidates.
    assert report['citation_checks'][0]['status'] == 'unverifiable'
    assert read(prepared/'sources.json')['sources']
    review_contract = next(c for c in stages.contracts if c['stage'] == 'review')
    assert read(review_contract['literature_report_file'])['citation_checks']


@pytest.mark.asyncio
async def test_missing_citation_check_is_rejected(prepared):
    with pytest.raises(ValueError, match='every distinct literature reference'):
        await pipeline(prepared, FakeStages(use_citation=True, omit_citation_check=True))


def test_citation_verified_label_cannot_use_metadata_or_no_captured_verification():
    check = {'source_id': 'lit', 'content_sha256': 'a'*64, 'declared_url': 'https://example.org',
        'declared_title': 'Paper', 'checked_identity': 'Paper', 'verification_url': 'https://example.org',
        'access_status': 'metadata_only', 'status': 'verified_agent_review', 'reason': 'Fixture'}
    with pytest.raises(ValueError, match='accessed publication content'):
        CitationCheck.model_validate(check)
    check['access_status'] = 'fulltext'
    with pytest.raises(ValueError, match='captured verification passage'):
        CitationCheck.model_validate(check)


def test_citation_reopening_and_external_search_are_independent(tmp_path):
    import hashlib
    source = tmp_path/'paper.txt'; source.write_text('A captured publication passage.')
    pin = hashlib.sha256(source.read_bytes()).hexdigest()
    receipt = {'request_url': 'https://example.org/paper', 'final_url': 'https://example.org/paper',
        'http_status': 200, 'origin_status': 'host_fetched_public_url; scientific identity unverified',
        'original_path': str(source), 'original_sha256': pin, 'text_path': str(source), 'text_sha256': pin}
    sources = tmp_path/'sources.json'
    write(sources, {'format_version': 'ideation_sources/1', 'sources': {'lit': {
        'source_id': 'lit', 'content_sha256': pin, 'path': str(source), 'origin_receipts': [receipt]}}})
    check = {'source_id': 'lit', 'content_sha256': pin, 'declared_url': receipt['request_url'],
        'declared_title': 'Paper', 'checked_identity': 'Paper by Author', 'verification_url': receipt['final_url'],
        'access_status': 'fulltext', 'status': 'verified_agent_review', 'reason': 'Reopened publication.',
        'verification_evidence': [{'source_id': 'lit', 'content_sha256': pin, 'locator': 'Methods', 'quote': source.read_text()}]}
    idea = CandidateIdea.model_validate(candidate_data())
    report = LiteratureReport.model_validate({'candidate_findings': [{'candidate_id': idea.id,
        'candidate_sha256': digest(idea), 'checks': [{'id': 'c', 'target': 'candidate', 'claim': 'Proposal',
            'verdict': 'not_found', 'reason': 'No search.'}], 'closest_priors': [], 'coverage_limitations': ['No search.']}],
        'citation_checks': [check], 'search_status': 'not_performed', 'searches_performed': [],
        'limitations': ['Known citation reopened directly; no broader novelty search.']})
    validate_literature_execution(report, {'web_search_event_observed': False}, sources)
    imported = read(sources); imported['sources']['lit'].pop('origin_receipts'); write(sources, imported)
    with pytest.raises(ValueError, match='imported excerpts alone'):
        validate_literature_execution(report, {'web_search_event_observed': True}, sources)
    write(tmp_path/'origin_receipts.json', {'lit': [receipt]})
    validate_literature_execution(report, {'web_search_event_observed': False}, sources)
    source.write_text('Modified source')
    with pytest.raises(ValueError, match='matching host-captured'):
        validate_literature_execution(report, {'web_search_event_observed': False}, sources)


@pytest.mark.asyncio
async def test_literature_self_check_enforces_origin_before_submission(prepared):
    stages = FakeStages(use_citation=True)
    await pipeline(prepared, stages)
    contract = next(c for c in stages.contracts if c['stage'] == 'literature')
    artifact = Path(contract['output_file'])
    report = read(artifact)
    check = report['citation_checks'][0]
    registry = read(contract['sources_file'])
    source = registry['sources'][check['source_id']]
    check.update(status='verified_agent_review', access_status='abstract',
        checked_identity='Original assay', verification_url=source['url'],
        verification_evidence=[{'source_id': source['source_id'],
            'content_sha256': source['content_sha256'], 'locator': 'Captured passage',
            'quote': Path(source['path']).read_text()}])
    write(artifact, report)
    with pytest.raises(ValueError, match='matching host-captured'):
        validate_artifact(contract, artifact)
    # Offline fixture for the receipt contract; this performs no network request.
    source['origin_receipts'] = [{'request_url': source['url'], 'final_url': source['url'],
        'http_status': 200, 'origin_status': 'host_fetched_public_url; identity unverified',
        'original_path': source['path'], 'original_sha256': source['content_sha256'],
        'text_path': source['path'], 'text_sha256': source['content_sha256']}]
    write(contract['sources_file'], registry)
    assert validate_artifact(contract, artifact).citation_checks[0].status == 'verified_agent_review'


def searched_review_case(tmp_path, mutation=None):
    """An observed search on exact draft bytes; relevance is not asserted by this fixture."""
    original = CandidateIdea.model_validate(candidate_data())
    current_data = original.model_dump(mode='json')
    if mutation:
        mutation(current_data)
    current = CandidateIdea.model_validate(current_data)
    original_path, current_path = tmp_path/'draft.json', tmp_path/'current.json'
    write(original_path, original.model_dump(mode='json'))
    write(current_path, current.model_dump(mode='json'))
    report_path = tmp_path/'literature.json'
    write(report_path, {'candidate_findings': [{'candidate_id': original.id,
        'candidate_sha256': digest(original), 'checks': [{'id': 'scope', 'target': 'candidate',
            'claim': 'Compare the original design.', 'verdict': 'not_found',
            'reason': 'This is a software fixture.'}], 'closest_priors': [], 'coverage_limitations': []}],
        'citation_checks': [], 'search_status': 'searched', 'searches_performed': ['Fixture query'],
        'limitations': ['This does not assess scientific search adequacy.']})
    contract = {'candidate_files': [str(current_path)], 'literature_candidate_files': [str(original_path)],
        'literature_report_file': str(report_path), 'literature_search_observed': True}
    review = review_data(current)
    review.update(novelty_status='possible_distinction', nearest_prior=[{
        'title': 'Fixture prior', 'atlas_refs': [atlas_ref()], 'overlap': 'Existing component.',
        'residual_difference': 'Unproven transfer.'}])
    panel = ReviewPanel.model_validate({'reviews': [review], 'comparison_summary': 'Fixture.',
        'searches_performed': [], 'previous_issue_resolutions': []})
    return contract, panel


@pytest.mark.parametrize('mutation', [None, lambda d: d.update(title='A clearer display title')])
def test_inherited_search_eligible_for_unchanged_science(tmp_path, mutation):
    contract, panel = searched_review_case(tmp_path, mutation)
    coverage = validate_review_search(panel, contract, {'web_search_event_observed': False})
    assert coverage['idea1']['eligible']


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(expected_benefit='A different scientific benefit.'),
    lambda d: d['workflow']['operations'][0].update(description='A new molecular route.'),
    lambda d: d['assessments'][0].update(required_property='Newly required selectivity.'),
    lambda d: d['changes'][0].update(expected_gain='A new performance claim.'),
    lambda d: d['assessments'][0].update(atlas_refs=[atlas_ref()]),
])
def test_revision_cannot_borrow_draft_search_for_changed_claims(tmp_path, mutation):
    contract, panel = searched_review_case(tmp_path, mutation)
    assert not inherited_search_coverage(contract)['idea1']['eligible']
    with pytest.raises(ValueError, match='these scientific claims'):
        validate_review_search(panel, contract, {'web_search_event_observed': False})
    # Search execution permits a scoped verdict, not a guarantee of adequate search.
    validate_review_search(panel, contract, {'web_search_event_observed': True})
    incomplete = panel.model_dump(mode='json')
    incomplete['reviews'][0]['novelty_status'] = 'search_incomplete'
    validate_review_search(ReviewPanel.model_validate(incomplete), contract, {'web_search_event_observed': False})


def test_inherited_search_requires_observation_report_status_and_exact_binding(tmp_path):
    contract, panel = searched_review_case(tmp_path)
    contract['literature_search_observed'] = False
    with pytest.raises(ValueError, match='observed search'):
        validate_review_search(panel, contract, {})
    contract['literature_search_observed'] = True
    report = read(contract['literature_report_file'])
    report.update(search_status='not_performed', searches_performed=[])
    write(contract['literature_report_file'], report)
    assert not inherited_search_coverage(contract)['idea1']['eligible']
    report.update(search_status='searched', searches_performed=['Fixture'])
    report['candidate_findings'][0]['candidate_sha256'] = '0'*64
    write(contract['literature_report_file'], report)
    with pytest.raises(ValueError, match='bind its supplied candidate'):
        inherited_search_coverage(contract)


@pytest.mark.asyncio
@pytest.mark.parametrize('fresh_final_search', [False, True])
async def test_pipeline_applies_search_coverage_per_revised_candidate(prepared, fresh_final_search):
    class SearchedStages(FakeStages):
        async def __call__(self, contract, attempt):
            receipt = await super().__call__(contract, attempt)
            payload = read(attempt/'artifact.json')
            if contract['stage'] == 'literature':
                payload.update(search_status='searched', searches_performed=['Recorded fixture search'])
                receipt['web_search_event_observed'] = True
            if contract['stage'] == 'review' and contract['revision_round'] == 1:
                review = next(r for r in payload['reviews'] if r['candidate_id'] == 'expert_a')
                review.update(novelty_status='possible_distinction', nearest_prior=[{
                    'title': 'Existing fixture method', 'overlap': 'RNA copying.',
                    'residual_difference': 'The modified library block remains hypothetical.',
                    'atlas_refs': [{'record_id': 'method', 'extraction_sha256': 'e'*64,
                                   'node_id': 'illumina:method:operation:copy'}]}])
                receipt['web_search_event_observed'] = fresh_final_search
            write(attempt/'artifact.json', payload)
            return receipt

    stages = SearchedStages(revise=True)
    if fresh_final_search:
        state = await pipeline(prepared, stages)
        assert state['status'] == 'completed'
    else:
        with pytest.raises(ValueError, match='these scientific claims'):
            await pipeline(prepared, stages)
        assert read(prepared/'state.json')['status'] == 'failed'
        assert (prepared/'candidates/expert_a/revised.json').exists()
    final = [c for c in stages.contracts if c['stage'] == 'review'][-1]
    assert final['inherited_search_coverage']['expert_b']['eligible']
    assert not final['inherited_search_coverage']['expert_a']['eligible']


@pytest.mark.asyncio
async def test_final_review_cannot_silently_downgrade_unresolved_blocker(prepared):
    stages = FakeStages(revise=True)
    await pipeline(prepared, stages)
    contract = [c for c in stages.contracts if c['stage'] == 'review'][-1]
    previous = read(contract['review_file'])
    previous['reviews'][0]['issues'][0]['severity'] = 'blocking'
    write(contract['review_file'], previous)
    with pytest.raises(ValueError, match='unresolved blocking issue must remain blocking'):
        validate_artifact(contract, contract['output_file'])
    # Explicit reclassification with a reason is possible; semantic adequacy needs review.
    current = read(contract['output_file'])
    resolution = next(r for r in current['previous_issue_resolutions'] if r['issue_id'] == 'issue_a')
    resolution.update(status='resolved', reason='The prior blocking rationale conflated a specified transfer with an unsupported primitive; the scoped risk remains explicit.')
    write(contract['output_file'], current)
    validate_artifact(contract, contract['output_file'])


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_key', ['literature_candidate_files', 'previous_candidate_files',
                                      'issue_response_files', 'contract'])
async def test_native_review_detects_changes_to_auxiliary_inputs(tmp_path, monkeypatch, changed_key):
    import genomics_discovery.ideation.run as runner
    attempt = tmp_path/'attempt'; attempt.mkdir()
    contract = {'stage': 'review'}
    for key in ['request_file', 'atlas_file', 'schema_file', 'sources_file']:
        path = tmp_path/(key+'.json'); write(path, {'sources': {}})
        contract[key] = str(path)
    for key in ['candidate_files', 'literature_candidate_files', 'previous_candidate_files', 'issue_response_files']:
        path = tmp_path/(key+'.json'); write(path, {'fixture': key})
        contract[key] = [str(path)]
    write(attempt/'contract.json', contract)
    changed_path = attempt/'contract.json' if changed_key == 'contract' else Path(contract[changed_key][0])

    async def fake_run(prompt, directory):
        changed_path.write_text('{"unexpected_change": true}')
        return SimpleNamespace(error=None, exit_code=0, thread_id='fixture', usage={}, elapsed_seconds=0)

    native = NativeStages.__new__(NativeStages)
    native.worker = SimpleNamespace(run=fake_run)
    native.threads = set()
    monkeypatch.setattr(runner, 'assess_archived_attempt', lambda _: SimpleNamespace(
        complete=True, thread_id='fixture', usage={}))
    with pytest.raises(ValueError, match='registered stage input changed'):
        await native(contract, attempt)


@pytest.mark.asyncio
async def test_native_provider_block_is_visible_and_not_retried(tmp_path, monkeypatch):
    import genomics_discovery.ideation.run as runner
    attempt = tmp_path/'attempt'; attempt.mkdir()
    contract = {'stage': 'review'}
    for key in ('request_file', 'atlas_file', 'schema_file', 'sources_file'):
        path = tmp_path/(key+'.json'); write(path, {'sources': {}})
        contract[key] = str(path)
    write(attempt/'contract.json', contract)
    calls = []

    async def fake_run(prompt, directory):
        calls.append(prompt)
        (directory/'events.jsonl').write_text(json.dumps({'type': 'turn.failed',
            'error': {'message': 'This content was flagged for possible biological risk.'}})+'\n')
        return SimpleNamespace(error='Codex exited with code 1', exit_code=1)

    native = NativeStages.__new__(NativeStages)
    native.worker = SimpleNamespace(run=fake_run); native.threads = set()
    monkeypatch.setattr(runner, 'assess_archived_attempt', lambda _: SimpleNamespace(complete=False))
    with pytest.raises(ValueError, match='flagged for possible biological risk'):
        await native(contract, attempt)
    assert len(calls) == 1

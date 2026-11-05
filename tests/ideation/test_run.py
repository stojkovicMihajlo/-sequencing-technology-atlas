"""Offline orchestrator contracts. Fake outputs are not scientific demonstrations."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from genomics_discovery.ideation.models import CandidateIdea
from genomics_discovery.ideation.run import (
    CitationCheck, LiteratureReport, context_for, literature_reference_keys,
    merge_sources, pipeline, prepare, read, validate_artifact, validate_literature_report, write,
)
from genomics_discovery.illumina.models import digest
from tests.ideation.test_context import context
from tests.ideation.test_models import candidate_data, review_data


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
                    "approach": approach, "why_distinct": "Distinct source of measurement signal."}
                    for ident, expertise, approach in [("expert_a", "Molecular chemistry", "Covalent recording"),
                                                       ("expert_b", "Assay architecture", "Partitioned recording")]]}
        elif role == "propose":
            if contract["expert_id"] == self.fail_expert:
                raise ValueError("Fixture transport failure")
            payload = candidate_data()
            payload["id"] = contract["expert_id"]
            if self.use_citation:
                source = attempt/'retrieved.txt'
                source.write_text('The original assay uses a reverse-transcription primer.')
                captured = context_for(contract).add_source(source, url='https://example.org/paper',
                    title='Original assay', content_level='excerpt')['data']
                payload['assessments'][0]['literature_refs'] = [{'source_id': captured['source_id'],
                    'content_sha256': captured['content_sha256'], 'locator': 'Methods', 'quote': source.read_text()}]
        elif role == "literature":
            ideas = [CandidateIdea.model_validate(read(p)) for p in contract['candidate_files']]
            findings = []
            for idea in ideas:
                findings.append({'candidate_id': idea.id, 'candidate_sha256': '0'*64 if self.stale_literature else digest(idea),
                    'checks': [{'id': 'check'+str(n), 'target': target, 'claim': 'Unproven proposed measurement or change.',
                        'verdict': 'not_found', 'reason': 'No live research in this software fixture.'}
                        for n, target in enumerate(['candidate'] + ([] if self.skip_literature_change else ['change:'+c.id for c in idea.changes]))],
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
                idea = CandidateIdea.model_validate(read(path))
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


def test_coordinator_cannot_rewrite_explicit_user_constraints(prepared):
    config = read(prepared / "config.json")
    output = prepared / "bad_plan.json"
    request = read(config["request_file"])
    write(output, {"original_request": request["prompt"], "constraints": [],
        "interpreted_goal": "Something", "assumptions": [], "experts": [
            {"id": ident, "expertise": "Expert", "question": "Question", "approach": "Approach", "why_distinct": "Reason"}
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

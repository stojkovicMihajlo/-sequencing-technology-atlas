"""Stage prompts must not give non-authors an impossible author-only contract."""
import json

import pytest

from genomics_discovery.ideation.prompts import build_prompt
from genomics_discovery.ideation.run import SCHEMAS


@pytest.mark.parametrize("role", list(SCHEMAS))
def test_role_contract_matches_output_schema(role):
    context = {"stage": role, "schema_file": "schema.json", "output_file": "artifact.json"}
    if role in {"propose", "revise"}:
        context["expert_id"] = "author_a"
    prompt = build_prompt(role, context)
    is_author = role in {"propose", "revise"}
    assert ("AUTHOR OUTPUT CONTRACT" in prompt) == is_author
    assert ("Candidate id must equal expert_id" in prompt) == is_author
    assert ("The candidate uses proposed_assay/1.1" in prompt) == is_author
    assert "Your ROLE below defines your responsibility and output schema" in prompt
    assert "Never modify the input atlas" in prompt
    assert "Do not dump the full atlas" in prompt
    assert "send one nonempty final\nresponse sentence" in prompt
    assert "actual check result" in prompt
    assert "Do not write final.txt yourself" in prompt
    assert json.loads(prompt.split("\nCONTEXT\n", 1)[1]) == context


def test_coordinator_has_planning_done_condition_without_claiming_design_completion():
    prompt = build_prompt("plan", {})
    assert "Finish planning when" in prompt
    assert "Pass unresolved mechanisms" in prompt
    assert "write their final proposals" in prompt  # explicitly forbidden


def test_review_distinguishes_inherited_search_from_own_and_revised_claims():
    prompt = build_prompt("review", {"literature_search_observed": True})
    assert "either in this" in prompt
    assert "candidate versions they actually" in prompt
    assert "never list them as searches you performed" in prompt
    assert "inherited search receipt alone does not" in prompt
    assert "actual external web search in this review\nsession" not in prompt
    assert "inherited_search_coverage" in prompt
    assert "eligible=false" in prompt


def test_review_separates_testable_transfer_from_missing_essential_mechanism():
    prompt = build_prompt('review', {})
    assert 'Lack of a completed wet-lab test alone does not block' in prompt
    assert 'unsupported required NEW primitive' in prompt
    assert 'unexplained essential handoff' in prompt
    assert 'An unresolved blocking issue must remain blocking' in prompt


def test_research_findings_point_to_graph_without_repeating_the_graph():
    prompt = build_prompt('propose', {})
    assert 'do not repeat the workflow or assessment ledger as four long essays' in prompt
    assert 'preserve actual\ncomponent precedent, mark contextual transfers' in prompt


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="Unknown ideation role"):
        build_prompt("invented_role", {})


@pytest.mark.parametrize('role', ['plan', 'propose', 'peer', 'literature', 'specialist', 'review', 'revise'])
def test_team_roles_have_versioned_responsibilities_and_bounded_tool_navigation(role):
    context = {'format_version': 'ideation_run/3', 'expert_count': 4}
    prompt = build_prompt(role, context)
    assert 'history-list <record_id>' in prompt
    assert ('AUTHOR OUTPUT CONTRACT' in prompt) == (role in {'propose', 'revise'})
    assert 'Assign exactly two' not in prompt
    assert 'both candidate_files' not in prompt
    assert json.loads(prompt.split('\nCONTEXT\n', 1)[1]) == context
    if role == 'review':
        assert 'specialist_issue_resolutions' in prompt
        assert 'resolution_targets' in prompt
    if role == 'revise':
        assert 'peer_responses' in prompt

"""Peer feedback is exact-bound and answerable without forced all-pairs chatter."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from genomics_discovery.ideation.models import CandidateIdea
from genomics_discovery.ideation.team import (
    PeerReport, PeerResponse, validate_peer_report, validate_peer_responses,
)
from genomics_discovery.illumina.models import digest
from tests.ideation.test_models import candidate_data


def candidates():
    result = []
    for name in ("designer_a", "designer_b", "designer_c", "designer_d"):
        data = candidate_data()
        data["id"] = name
        result.append(CandidateIdea.model_validate(data))
    return result


def report_data(ideas, author=0, target=1):
    return {"author_id": ideas[author].id, "author_candidate_sha256": digest(ideas[author]),
        "comments": [{"id": "interface", "target_candidate_id": ideas[target].id,
            "target_candidate_sha256": digest(ideas[target]), "target": "operation:prepare",
            "kind": "question", "text": "Which source establishes the proposed substrate compatibility?"}]}


def response(author="designer_a", comment="interface", status="unresolved"):
    return PeerResponse(peer_author_id=author, comment_id=comment, status=status,
        explanation="The required property remains a question pending retrieved evidence.")


def test_four_designers_can_produce_sparse_feedback_without_all_pairs_requirement():
    ideas = candidates()
    report = PeerReport.model_validate(report_data(ideas))
    validate_peer_report(report, ideas, "designer_a")
    assert len(report.comments) == 1
    validate_peer_responses([response()], [report], "designer_b")
    validate_peer_responses([], [report], "designer_c")


def test_no_feedback_is_explicit_and_does_not_require_an_invented_comment():
    ideas = candidates()
    report = PeerReport(author_id=ideas[0].id, author_candidate_sha256=digest(ideas[0]),
        comments=(), no_feedback_reason="The inspected alternatives add no decision-relevant objection beyond the supplied literature report.")
    validate_peer_report(report, ideas, ideas[0].id)
    validate_peer_responses([], [report], ideas[1].id)


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_empty_peer_report_without_a_meaningful_explanation_is_rejected(reason):
    data = report_data(candidates())
    data.update(comments=[], no_feedback_reason=reason)
    with pytest.raises(ValidationError):
        PeerReport.model_validate(data)


def test_comment_and_no_feedback_claim_cannot_coexist():
    data = report_data(candidates())
    data["no_feedback_reason"] = "No useful comments."
    with pytest.raises(ValidationError, match="cannot claim no feedback"):
        PeerReport.model_validate(data)


@pytest.mark.parametrize("fault", ["wrong_author", "stale_author", "unknown_candidate",
    "self_critique", "stale_target", "unknown_target", "duplicate_candidates"])
def test_invalid_or_stale_peer_bindings_are_rejected(fault):
    ideas = candidates()
    data = report_data(ideas)
    if fault == "wrong_author":
        data["author_id"] = "designer_d"
    elif fault == "stale_author":
        data["author_candidate_sha256"] = "f"*64
    elif fault == "unknown_candidate":
        data["comments"][0]["target_candidate_id"] = "unknown"
    elif fault == "self_critique":
        data = report_data(ideas, target=0)
    elif fault == "stale_target":
        data["comments"][0]["target_candidate_sha256"] = "f"*64
    elif fault == "unknown_target":
        data["comments"][0]["target"] = "operation:invented"
    else:
        ideas.append(ideas[0])
    with pytest.raises(ValueError):
        validate_peer_report(PeerReport.model_validate(data), ideas, "designer_a")


def test_peer_comment_ids_are_local_to_one_report_not_global():
    ideas = candidates()
    reports = [PeerReport.model_validate(report_data(ideas, author=i, target=1)) for i in (0, 2)]
    for report in reports:
        validate_peer_report(report, ideas, report.author_id)
    validate_peer_responses([response(), response(author="designer_c")], reports, "designer_b")
    duplicate = report_data(ideas)
    duplicate["comments"].append(deepcopy(duplicate["comments"][0]))
    with pytest.raises(ValidationError, match="unique"):
        PeerReport.model_validate(duplicate)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "extra", "wrong_author", "duplicate_report"])
def test_author_cannot_ignore_or_fabricate_peer_feedback(fault):
    ideas = candidates()
    reports = [PeerReport.model_validate(report_data(ideas))]
    responses = [response()]
    if fault == "missing":
        responses = []
    elif fault == "duplicate":
        responses.append(response())
    elif fault == "extra":
        responses.append(response(comment="never_asked"))
    elif fault == "wrong_author":
        responses = [response(author="designer_c")]
    else:
        reports.append(reports[0])
    with pytest.raises(ValueError):
        validate_peer_responses(responses, reports, "designer_b")


@pytest.mark.parametrize("status", ["addressed", "unresolved", "disputed"])
def test_disagreement_and_unresolved_questions_are_valid_explicit_responses(status):
    ideas = candidates()
    report = PeerReport.model_validate(report_data(ideas))
    validate_peer_responses([response(status=status)], [report], "designer_b")


@pytest.mark.parametrize("explanation", ["", "   "])
def test_response_requires_a_nonempty_explanation(explanation):
    with pytest.raises(ValidationError):
        PeerResponse(peer_author_id="designer_a", comment_id="interface", status="addressed",
            explanation=explanation)


def test_changed_candidate_requires_new_peer_binding_not_silent_reuse():
    ideas = candidates()
    report = PeerReport.model_validate(report_data(ideas))
    altered = ideas[1].model_dump(mode="json")
    altered["expected_benefit"] = "Different requested gain."
    ideas[1] = CandidateIdea.model_validate(altered)
    with pytest.raises(ValueError, match="exact target candidate"):
        validate_peer_report(report, ideas, "designer_a")

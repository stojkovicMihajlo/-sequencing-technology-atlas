"""One bounded peer-critique round and explicit author responses.

Peer comments are suggestions or challenges, not changes to a candidate, votes,
or scientific acceptance. The caller owns round count, source validation and
any explicit closure of a dropped candidate.
"""
from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.illumina.models import Text, digest
from .models import AtlasReference, Hash, LiteratureReference, review_targets


class PeerComment(StrictModel):
    id: Text
    target_candidate_id: Text
    target_candidate_sha256: Hash
    target: Text
    kind: Literal["question", "suggestion", "risk", "overlap"]
    text: Text
    atlas_refs: tuple[AtlasReference, ...] = ()
    literature_refs: tuple[LiteratureReference, ...] = ()


class PeerReport(StrictModel):
    format_version: Literal["ideation_peer_report/1"] = "ideation_peer_report/1"
    author_id: Text
    author_candidate_sha256: Hash
    comments: tuple[PeerComment, ...]
    no_feedback_reason: Text | None = None

    @model_validator(mode="after")
    def explicit_feedback_or_abstention(self):
        if len({comment.id for comment in self.comments}) != len(self.comments):
            raise ValueError("Peer comment IDs must be unique within a report")
        if not self.comments and self.no_feedback_reason is None:
            raise ValueError("An empty peer report requires an explicit no-feedback reason")
        if self.comments and self.no_feedback_reason is not None:
            raise ValueError("A peer report with comments cannot claim no feedback")
        return self


class PeerResponse(StrictModel):
    peer_author_id: Text
    comment_id: Text
    status: Literal["addressed", "unresolved", "disputed"]
    explanation: Text


def validate_peer_report(report, candidates, author_id):
    """Bind the author and every comment to the original candidate snapshots."""
    candidates = tuple(candidates)
    ideas = {candidate.id: candidate for candidate in candidates}
    if not ideas or len(ideas) != len(candidates):
        raise ValueError("Peer review requires nonempty, distinct candidate IDs")
    if report.author_id != author_id or author_id not in ideas:
        raise ValueError("Peer report must match its assigned candidate author")
    if report.author_candidate_sha256 != digest(ideas[author_id]):
        raise ValueError("Peer report is not bound to the exact author candidate")
    if len({comment.id for comment in report.comments}) != len(report.comments):
        raise ValueError("Peer comment IDs must be unique within a report")
    if not report.comments and not report.no_feedback_reason:
        raise ValueError("An empty peer report requires an explicit no-feedback reason")
    for comment in report.comments:
        if comment.target_candidate_id == author_id:
            raise ValueError("A peer report cannot critique its author's own candidate")
        candidate = ideas.get(comment.target_candidate_id)
        if candidate is None:
            raise ValueError("Peer comment identifies an unknown candidate")
        if comment.target_candidate_sha256 != digest(candidate):
            raise ValueError("Peer comment is not bound to the exact target candidate")
        if comment.target not in review_targets(candidate):
            raise ValueError("Peer comment must identify an exact target in the referenced candidate")


def validate_peer_responses(responses, reports, candidate_id):
    """Require one explicit response to every addressed comment, not agreement.

    Reports must first be validated against their original candidate snapshots.
    This function checks response coverage; it does not establish that a claimed
    change was made or that a rebuttal is scientifically correct.
    """
    reports, responses = tuple(reports), tuple(responses)
    authors = [report.author_id for report in reports]
    if len(authors) != len(set(authors)):
        raise ValueError("Peer response inputs contain duplicate author reports")
    expected = []
    for report in reports:
        ids = [comment.id for comment in report.comments]
        if len(ids) != len(set(ids)):
            raise ValueError("Peer comment IDs must be unique within a report")
        expected.extend((report.author_id, comment.id) for comment in report.comments
                        if comment.target_candidate_id == candidate_id)
    actual = [(response.peer_author_id, response.comment_id) for response in responses]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError("Author must answer every peer comment addressed to this candidate exactly once")


PEER_PROMPT = """ROLE: ASSAY DESIGNER IN ONE PEER-CRITIQUE ROUND
Read your original mandate and candidate, then inspect the other candidate_files.
Use author_id (or expert_id when supplied) and the exact candidate_bindings; do not
guess candidate hashes. Respond through your scientific expertise with useful
questions, suggestions, risks or evidence-backed overlap observations addressed
to another design. Inspect the actual affected graph path and cite its exact
candidate target (State/property, Operation, Flow, change, research target or
candidate). Distinguish a factual source claim from a proposed transfer or test.

Prioritize feedback that could change a mechanism, identity carrier, interface,
readout interpretation, comparator or expected benefit. A suggestion to combine
parts must state the new interface or dependency it introduces. Register and cite
evidence for external factual claims; a possible problem or unanswered question
can remain explicitly uncertain. The atlas is incomplete and similarity is not
proof of prior-art overlap or compatibility. Do not propose a stronger unrecorded
version and review that instead of the supplied candidate.

You may comment on a subset of the other candidates. There is no all-pairs quota,
vote, ranking or forced consensus. Do not critique your own candidate in this
report, rewrite another graph, merge proposals, invent a conversation, or start
additional agents. If no decision-relevant feedback is justified, return an empty
comments tuple with a specific no_feedback_reason instead of filler comments.

Write PeerReport with your author_id and author_candidate_sha256. Each comment
has a locally unique id, target_candidate_id, target_candidate_sha256, target,
kind, text and optional exact evidence references. The runner gives comments to
their recipients for one author response; addressed means the author claims a
response, not that the peer or an independent reviewer has accepted the change.
"""

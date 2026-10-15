"""Replay connected views, validate claim bindings and retrieve facet descriptions."""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from genomics_discovery.curation.workflow_extraction.models import (
    Workflow,
    WorkflowReview,
    digest,
    validate_evidence,
)
from genomics_discovery.curation.workflow_extraction.sources import load_packet
from genomics_discovery.curation.workflow_extraction.transformations import cut_transformation

from .models import QUESTIONS, AlignmentBatch, Case, MatchingReview, ProfileBatch, Selection


@dataclass
class Context:
    parents: dict
    occurrences: dict
    unit_parents: dict
    files: set[Path]

    def cut(self, selection: Selection):
        row = self.parents[selection.parent_id]
        return cut_transformation(
            row["workflow"], row["packet"], selection.operation_ids, review=row["review"]
        )


def load_context(parents_path: Path, manifest_path: Path) -> Context:
    parents, files = {}, {parents_path.resolve(), manifest_path.resolve()}
    for key, row in json.loads(parents_path.read_text()).items():
        paths = {k: (parents_path.parent / v).resolve() if v else None for k, v in row.items()}
        if set(paths) - {"workflow", "packet", "review"} or not all(
            paths.get(k) for k in ("workflow", "packet")
        ):
            raise ValueError("Parent registry requires workflow, packet and optional review")
        workflow = Workflow.model_validate_json(paths["workflow"].read_bytes())
        packet = load_packet(paths["packet"])
        review = (
            WorkflowReview.model_validate_json(paths["review"].read_bytes())
            if (paths.get("review"))
            else None
        )
        parents[key] = {"workflow": workflow, "packet": packet, "review": review, "paths": paths}
        files.update(p for p in paths.values() if p)
        files.update(Path(s["original_path"]) for s in packet["sources"])
    context = Context(parents, {}, {}, files)
    for ident, row in json.loads(manifest_path.read_text()).items():
        paths = {k: (manifest_path.parent / v).resolve() if v else None for k, v in row.items()}
        matches = [
            k
            for k, p in parents.items()
            if all(
                paths.get(field) == p["paths"].get(field)
                for field in ("workflow", "packet", "review")
            )
        ]
        if len(matches) != 1:
            raise ValueError("Each occurrence requires one exact registered parent")
        raw = json.loads(paths["occurrence"].read_text())
        rebuilt = context.cut(
            Selection(parent_id=matches[0], operation_ids=[op["id"] for op in raw["operations"]])
        )
        if rebuilt.id != ident or rebuilt.model_dump(mode="json") != raw:
            raise ValueError("Occurrence differs from its frozen parent")
        context.occurrences[ident] = rebuilt
        context.unit_parents[ident] = matches[0]
        files.add(paths["occurrence"])
    if set(context.unit_parents.values()) != parents.keys():
        raise ValueError("Every comparison parent needs a source-bound seed occurrence")
    return context


def targets(cut) -> set[str]:
    return (
        {f"operation:{op.id}" for op in cut.operations}
        | {f"operation:{ref.operation_id}" for ref in cut.external_operations}
        | {f"state:{s.id}" for s in (*cut.inputs, *cut.outputs, *cut.internal_states)}
        | {f"flow:{f.id}" for f in cut.flows}
    )


def evidence_check(evidence, packet):
    errors = validate_evidence({"claims": [e.model_dump() for e in evidence]}, packet)
    if errors:
        raise ValueError("; ".join(errors))


def validate_profiles(batch: ProfileBatch, context: Context):
    ids = [p.occurrence_id for p in batch.profiles]
    if len(ids) != len(set(ids)) or set(ids) != context.occurrences.keys():
        raise ValueError("Profiles must cover every supplied occurrence exactly once")
    for profile in batch.profiles:
        cut = context.occurrences[profile.occurrence_id]
        if profile.occurrence_sha256 != digest(cut):
            raise ValueError("Stale profile occurrence")
        packet = context.parents[context.unit_parents[cut.id]]["packet"]
        for facet in profile.facets:
            if set(facet.targets) - targets(cut):
                raise ValueError("Profile targets outside its recorded cut/context")
            evidence_check(facet.evidence, packet)


def validate_alignments(batch: AlignmentBatch, cases: list[Case], context: Context):
    by_id = {c.id: c for c in cases}
    if (
        len(by_id) != len(cases)
        or len(batch.alignments) != len(by_id)
        or {a.case_id for a in batch.alignments} != by_id.keys()
    ):
        raise ValueError("Alignments must cover each case exactly once")
    cuts = {}
    for alignment in batch.alignments:
        case = by_id[alignment.case_id]
        if (
            case.relation == "shared_mechanism"
            and (alignment.disposition == "candidate_correspondence")
            and not any(
                c.dimension == "transformation"
                and c.kind == "correspondence"
                and any(e.support == "reported" for e in c.left_evidence)
                and any(e.support == "reported" for e in c.right_evidence)
                for c in alignment.claims
            )
        ):
            raise ValueError(
                "A mechanism correspondence needs an explicit transformation claim "
                "with reported evidence from both contexts"
            )
        if (alignment.left.parent_id, alignment.right.parent_id) != (
            case.left_parent,
            case.right_parent,
        ):
            raise ValueError("Alignment changed the requested parents")
        left, right = context.cut(alignment.left), context.cut(alignment.right)
        cuts[case.id] = {
            "left": left.model_dump(mode="json"),
            "right": right.model_dump(mode="json"),
        }
        for claim in alignment.claims:
            for selection, cut, selected, evidence in (
                (alignment.left, left, claim.left_targets, claim.left_evidence),
                (alignment.right, right, claim.right_targets, claim.right_evidence),
            ):
                if set(selected) - targets(cut):
                    raise ValueError("Comparison targets outside its retained cut/context")
                evidence_check(evidence, context.parents[selection.parent_id]["packet"])
    return cuts


def review_targets(profiles: ProfileBatch, alignments: AlignmentBatch, context: Context):
    required = {}
    for profile in profiles.profiles:
        for facet in profile.facets:
            required[f"profile:{profile.occurrence_id}:{facet.id}"] = {
                context.unit_parents[profile.occurrence_id]
            }
    for alignment in alignments.alignments:
        parents = {alignment.left.parent_id, alignment.right.parent_id}
        required[f"alignment:{alignment.case_id}:decision"] = parents
        for i in range(len(alignment.claims)):
            required[f"alignment:{alignment.case_id}:claim:{i}"] = parents
        for i in range(len(alignment.design_questions)):
            required[f"alignment:{alignment.case_id}:question:{i}"] = parents
    return required


def validate_review(
    review: MatchingReview, profiles: ProfileBatch, alignments: AlignmentBatch, context: Context
):
    if (review.profiles_sha256, review.alignments_sha256) != (digest(profiles), digest(alignments)):
        raise ValueError("Review is not bound to the exact proposals")
    required = review_targets(profiles, alignments, context)
    if (
        len(review.checks) != len(required)
        or {c.target_id for c in review.checks} != required.keys()
    ):
        raise ValueError("Review must check every facet, comparison and design question once")
    for check in review.checks:
        if {c.parent_id for c in check.citations} != required[check.target_id]:
            raise ValueError("Review citations must cover the target's exact parents")
        inspected = {(i.parent_id, i.source_id, i.passage_id) for i in check.inspected}
        if {i.parent_id for i in check.inspected} - required[check.target_id]:
            raise ValueError("Review inspected an unregistered target parent")
        for inspection in check.inspected:
            packet = context.parents[inspection.parent_id]["packet"]
            errors = validate_evidence(inspection, packet)
            if errors:
                raise ValueError("; ".join(errors))
        for citation in check.citations:
            evidence_check(citation.evidence, context.parents[citation.parent_id]["packet"])
            if any(
                (citation.parent_id, e.source_id, e.passage_id) not in inspected
                for e in citation.evidence
            ):
                raise ValueError("Review cites uninspected evidence")
    verdicts = {c.verdict for c in review.checks}
    return (
        "needs_revision"
        if "unsupported" in verdicts
        else ("research_needed" if "unresolved" in verdicts else "supported_local_comparisons")
    )


_STOP = set(
    (
        "a an and are as at be by for from in into is it of on or that the to was what which with"
    ).split()
)


def _tokens(text):
    return [t for t in re.findall(r"\w+", text.casefold()) if t not in _STOP]


def search_profiles(batch: ProfileBatch, query: str, *, dimension=None, limit=10):
    """Inexpensive lexical ranking of interpreted facets; no semantic or scientific gate."""
    if dimension is not None and dimension not in QUESTIONS:
        raise ValueError("Unknown question dimension")
    terms = set(_tokens(query))
    if not terms or type(limit) is not int or limit < 1:
        raise ValueError("Search needs meaningful terms and a positive limit")
    docs = [
        (p, f, _tokens(f.description + " " + " ".join(f.search_terms)))
        for p in batch.profiles
        for f in p.facets
        if f.status != "not_characterized" and (dimension is None or dimension == f.dimension)
    ]
    counts = {t: sum(t in words for _, _, words in docs) for t in terms}
    hits = []
    for profile, facet, words in docs:
        matched = terms & set(words)
        if not matched:
            continue
        score = sum(
            math.log(1 + (len(docs) - counts[t] + 0.5) / (counts[t] + 0.5))
            * words.count(t)
            / (words.count(t) + 0.5 + len(words) / 80)
            for t in matched
        )
        hits.append(
            {
                "occurrence_id": profile.occurrence_id,
                "assertion_id": facet.id,
                "dimension": facet.dimension,
                "score": round(score, 6),
                "matched_terms": sorted(matched),
                "facet": facet.model_dump(mode="json"),
            }
        )
    hits.sort(key=lambda h: (-h["score"], h["occurrence_id"], h["dimension"]))
    return {
        "query": query,
        "dimension": dimension,
        "total_facets": len(hits),
        "hits": hits[:limit],
        "truncated": len(hits) > limit,
        "boundary": "Lexical candidate retrieval only; inferred facets remain hypotheses. "
        "Unknown facets are not indexed as positive facts. No edges are created.",
    }

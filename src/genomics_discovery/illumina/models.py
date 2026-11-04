"""Source-schematic architecture records, review gates and graph projection.

These records start from an inspected explanatory diagram. Primary-paper changes
are explicitly authored and reviewed; corrections retain the literal observation.
Mechanical checks establish references and graph integrity, not entailment.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, deque
from typing import Annotated, Any, Literal
from urllib.parse import quote

from pydantic import Field, StringConstraints, model_validator

from genomics_discovery.core.artifacts import StrictModel

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class Evidence(StrictModel):
    source_id: Text = Field(description="Exact ID in the supplied source registry.")
    locator: Text = Field(description="Figure region/caption or text section actually inspected.")
    basis: Literal["image", "text"]
    support: Literal["depicted", "reported", "inferred"] = Field(description=
        "depicted for visible diagram content; reported for source text; inferred for an "
        "explicit deduction from these sources, requiring its own rationale.")
    quote: Annotated[Text, Field(max_length=800)] | None = Field(default=None, description=
        "Exact bounded source-text excerpt for text evidence; never a reconstructed quotation.")
    bbox: tuple[Unit, Unit, Unit, Unit] | None = Field(default=None, description=
        "Optional image region [left, top, right, bottom], normalized to image dimensions.")
    rationale: Text | None = Field(default=None, description=
        "How the cited content supports this claim; mandatory for an inference.")

    @model_validator(mode="after")
    def coherent(self):
        if self.basis == "text" and (self.quote is None or self.bbox is not None):
            raise ValueError("Text evidence requires an exact quote and no image bounding box")
        if self.support == "depicted" and self.basis != "image":
            raise ValueError("Depicted support requires image evidence")
        if self.support == "reported" and self.basis != "text":
            raise ValueError("Reported support requires text evidence")
        if self.support == "inferred" and not self.rationale:
            raise ValueError("Inferred evidence requires a rationale")
        if self.bbox and not (self.bbox[0] < self.bbox[2] and self.bbox[1] < self.bbox[3]):
            raise ValueError("Image bounding box must have positive width and height")
        return self


EvidenceSet = Annotated[tuple[Evidence, ...], Field(min_length=1)]


class State(StrictModel):
    id: Text
    label: Text = Field(description=
        "Concise source-supported name of the actual material or data at this boundary, "
        "consistent with kind, features and connected Operations. Do not substitute the "
        "biological target for its physical carrier or readout. Preserve a misleading source "
        "caption in Evidence/Correction, not as the asserted label with a prose disclaimer.")
    kind: Literal["material", "data"]
    features: tuple[Text, ...] = Field(default=(), description=
        "Only consequential supported features: ends, attachment, compartment, barcode association. "
        "Unknown chemistry is not supplied from convention.")
    evidence: EvidenceSet


class Operation(StrictModel):
    id: Text
    label: Text = Field(description=
        "Concise source-supported action on the declared input, consistent with its output. "
        "Reuse a source caption when it conveys that action accurately; otherwise retain "
        "literal wording in Evidence/Correction. Do not invent hidden suboperations.")
    description: Text = Field(description=
        "What changes or is inspected at schematic scope. Do not expand routine execution details.")
    inputs: tuple[Text, ...]
    outputs: tuple[Text, ...]
    mechanism: Text | None = Field(default=None, description=
        "Only a source-supported mechanism, not an inferred enzyme or invented chemical recipe.")
    information_effect: Text | None = Field(default=None, description=
        "What association or information is introduced, retained or lost and its physical carrier, "
        "when supported. The biological analyte need not be the molecule sequenced.")
    evidence: EvidenceSet


class Flow(StrictModel):
    id: Text
    source: Text
    target: Text
    kind: Literal["material_input", "material_output", "data_input", "data_output", "precedes"]
    evidence: EvidenceSet = Field(description=
        "Support for this route or ordering. Proximity on the page alone does not establish flow.")


class MethodScope(StrictModel):
    method: Text
    operation_ids: tuple[Text, ...]
    status: Literal["resolved", "unresolved"]
    rationale: Text = Field(description=
        "Which operations apply to this named method. Do not assign every step of a grouped "
        "diagram to all names. Explain unresolved applicability honestly.")
    evidence: tuple[Evidence, ...]


class PaperMatch(StrictModel):
    id: Text
    source_id: Text = Field(description=
        "Publication/content or metadata source ID. A catalogue source is allowed only for "
        "an unassigned listed_not_read citation/background annotation with its own literal quote; "
        "it never establishes publication identity or primary scientific support.")
    methods: tuple[Text, ...] = Field(description=
        "In-scope methods that the inspected source actually establishes. Use an empty list "
        "for card-level background, catalogue or unresolved citations with no supported member "
        "assignment. Architecture/variant support must identify the supported method(s).")
    assessment: Literal["listed_not_read", "identity_verified", "content_checked"]
    relationship: Literal["catalogue_citation", "supports_architecture", "supports_variant",
                          "background", "unresolved"]
    evidence: tuple[Evidence, ...] = Field(description=
        "Identity support differs from protocol-content support. A PMID or title alone cannot "
        "establish an operation, variant, origin date or historical development.")


class Correction(StrictModel):
    id: Text
    target_id: Text = Field(description=
        "Exact affected scientific record target: state:<id>, operation:<id>, flow:<id>, "
        "scope:<method> or paper:<id>. A state_label/operation_label review judgment is "
        "not a Correction address; use its owning State/Operation.")
    diagram_observation: Text = Field(description="What the source drawing depicts, preserved faithfully.")
    primary_statement: Text = Field(description="What the inspected primary source reports instead.")
    disposition: Literal["use_primary", "keep_unresolved"] = Field(description=
        "use_primary means the affected candidate record is explicitly authored from the "
        "primary source and cites it. It does not instruct software to rewrite free text. "
        "keep_unresolved withholds a supported scientific outcome.")
    evidence: EvidenceSet = Field(description="Both diagram and primary text support are required.")


class Unknown(StrictModel):
    id: Text
    question: Text
    blocking: bool = Field(description=
        "True only if an unresolved point prevents the scoped interpretation. Missing routine "
        "execution settings need not block a schematic architecture.")
    affected_ids: tuple[Text, ...] = Field(default=(), description=
        "Exact scientific record targets, e.g. state:<id> or operation:<id>, not separate "
        "state_label/operation_label review judgments. Empty means a whole-record question.")


class DiagramExtraction(StrictModel):
    format_version: Literal["illumina_diagram/1.0"] = "illumina_diagram/1.0"
    record_id: Text
    title: Text
    method_names: Annotated[tuple[Text, ...], Field(min_length=1)]
    scope: Text = Field(description=
        "Exact assay implementation, component illustration or grouped family described; not "
        "a claim that all protocols follow the same sequence or that this is an execution recipe.")
    inspected_sources: Annotated[tuple[Text, ...], Field(min_length=1)]
    states: Annotated[tuple[State, ...], Field(min_length=1)]
    operations: Annotated[tuple[Operation, ...], Field(min_length=1)]
    flows: Annotated[tuple[Flow, ...], Field(min_length=1)]
    method_scopes: Annotated[tuple[MethodScope, ...], Field(min_length=1)]
    paper_matches: tuple[PaperMatch, ...]
    corrections: tuple[Correction, ...]
    unknowns: tuple[Unknown, ...]

    @model_validator(mode="after")
    def graph(self):
        names = self.method_names
        if len(set(names)) != len(names):
            raise ValueError("Duplicate method names")
        records = (*self.states, *self.operations, *self.flows, *self.paper_matches,
                   *self.corrections, *self.unknowns)
        if len({x.id for x in records}) != len(records):
            raise ValueError("Local record IDs must be unique across record types")
        states, operations = {x.id: x for x in self.states}, {x.id: x for x in self.operations}
        pairs, edges, degree = set(), {x: [] for x in (*states, *operations)}, Counter()
        for flow in self.flows:
            if flow.source not in edges or flow.target not in edges:
                raise ValueError("Unknown Flow endpoint")
            pair = (flow.source, flow.target, flow.kind)
            if pair in pairs:
                raise ValueError("Duplicate Flow for the same endpoints and type")
            pairs.add(pair)
            if flow.kind == "precedes":
                valid = flow.source in operations and flow.target in operations
            elif flow.kind.endswith("_input"):
                valid = (flow.source in states and flow.target in operations and
                         flow.kind == states[flow.source].kind + "_input")
            else:
                valid = (flow.source in operations and flow.target in states and
                         flow.kind == states[flow.target].kind + "_output")
            if not valid:
                raise ValueError("Flow type does not agree with endpoint types")
            edges[flow.source].append(flow.target)
            degree[flow.target] += 1
        for operation in self.operations:
            for direction in ("inputs", "outputs"):
                refs = getattr(operation, direction)
                if len(set(refs)) != len(refs) or set(refs) - states.keys():
                    raise ValueError("Operation I/O must reference distinct existing States")
                actual = {f.source if direction == "inputs" else f.target for f in self.flows
                          if (f.target == operation.id if direction == "inputs"
                              else f.source == operation.id) and f.kind.endswith(
                                  "_input" if direction == "inputs" else "_output")}
                if set(refs) != actual:
                    raise ValueError("Operation I/O and Flows disagree")
        ready, count = deque(x for x in edges if not degree[x]), 0
        while ready:
            node = ready.popleft()
            count += 1
            for target in edges[node]:
                degree[target] -= 1
                if not degree[target]:
                    ready.append(target)
        if count != len(edges):
            raise ValueError("Schematic architecture must be acyclic; describe repeats within an operation")
        scopes = [x.method for x in self.method_scopes]
        if len(scopes) != len(set(scopes)) or set(scopes) != set(names):
            raise ValueError("Exactly one applicability scope is required for each named method")
        for scope in self.method_scopes:
            if len(scope.operation_ids) != len(set(scope.operation_ids)):
                raise ValueError("Duplicate Operation in method scope")
            if set(scope.operation_ids) - operations.keys():
                raise ValueError("Method scope references an unknown Operation")
            if scope.status == "resolved" and (not scope.operation_ids or not scope.evidence):
                raise ValueError("Resolved applicability requires operations and source evidence")
        for paper in self.paper_matches:
            if set(paper.methods) - set(names):
                raise ValueError("Paper match must refer to named methods in this record")
            if not paper.methods and paper.relationship in {"supports_architecture", "supports_variant"}:
                raise ValueError("Architecture/variant support must identify a named method")
        targets = review_targets(self)
        for correction in self.corrections:
            if correction.target_id not in targets or not correction.target_id.startswith(
                    ("state:", "operation:", "flow:", "scope:", "paper:")):
                raise ValueError("Correction references an unknown target")
        for unknown in self.unknowns:
            if set(unknown.affected_ids) - targets:
                raise ValueError("Unknown references an unknown target")
        return self


class ReviewCheck(StrictModel):
    target: Text
    verdict: Literal["supported", "unsupported", "unresolved"]
    action: Literal["accept", "revise", "research"]
    rationale: Text
    evidence: EvidenceSet

    @model_validator(mode="after")
    def action_matches(self):
        if (self.verdict == "supported") != (self.action == "accept"):
            raise ValueError("Only a supported judgment can accept a target")
        return self


class DiagramReview(StrictModel):
    # Preserve historical defaults/digests. New stages use CurrentDiagramReview.
    format_version: Literal["illumina_diagram_review/1.0", "illumina_diagram_review/1.1"] = "illumina_diagram_review/1.0"
    record_id: Text
    candidate_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    inspected_sources: Annotated[tuple[Text, ...], Field(min_length=1)]
    checks: Annotated[tuple[ReviewCheck, ...], Field(min_length=1)]


class CurrentDiagramReview(DiagramReview):
    """Fresh reviews require separate, evidenced State/Operation label judgments."""

    format_version: Literal["illumina_diagram_review/1.1"] = "illumina_diagram_review/1.1"


def digest(record: StrictModel | dict) -> str:
    data = record.model_dump(mode="json") if isinstance(record, StrictModel) else record
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def review_targets(candidate: DiagramExtraction, *, format_version="illumina_diagram_review/1.0") -> set[str]:
    return set(_review_records(candidate, format_version))


def _review_records(candidate, format_version):
    records = _target_records(candidate)
    if format_version == "illumina_diagram_review/1.1":
        records.update(("state_label:" + s.id, s) for s in candidate.states)
        records.update(("operation_label:" + o.id, o) for o in candidate.operations)
    elif format_version != "illumina_diagram_review/1.0":
        raise ValueError("Unknown diagram review contract")
    return records


def _label_owner(target):
    """A label judgment examines its owning record and the same source corrections."""
    kind, separator, ident = target.partition(":")
    if separator and kind in {"state_label", "operation_label"}:
        return kind.removesuffix("_label") + ":" + ident
    return target


def _target_records(candidate: DiagramExtraction) -> dict[str, Any]:
    targets = {"record": candidate}
    for kind, records in (("state", candidate.states), ("operation", candidate.operations),
                          ("flow", candidate.flows), ("paper", candidate.paper_matches),
                          ("correction", candidate.corrections), ("unknown", candidate.unknowns)):
        targets.update((kind + ":" + x.id, x) for x in records)
    targets.update(("scope:" + x.method, x) for x in candidate.method_scopes)
    return targets


def _evidence(record: Any):
    if isinstance(record, Evidence):
        yield record
    elif isinstance(record, StrictModel):
        for key in type(record).model_fields:
            yield from _evidence(getattr(record, key))
    elif isinstance(record, (tuple, list)):
        for value in record:
            yield from _evidence(value)


def _source_errors(record, sources: dict, inspected: tuple[str, ...]) -> list[str]:
    errors = []
    if len(set(inspected)) != len(inspected):
        errors.append("Duplicate inspected source IDs")
    if set(inspected) - sources.keys():
        errors.append("Unknown inspected source IDs")
    for evidence in _evidence(record):
        source = sources.get(evidence.source_id)
        if source is None:
            errors.append("Unknown evidence source: " + evidence.source_id)
            continue
        if evidence.source_id not in inspected:
            errors.append("Evidence source was not declared inspected: " + evidence.source_id)
        if source.get("kind") != evidence.basis:
            errors.append("Evidence basis differs from registered source kind: " + evidence.source_id)
        if evidence.basis == "text" and evidence.quote not in source.get("text", ""):
            errors.append("Evidence quote not present in registered source: " + evidence.source_id)
    return errors


def _identity_only(evidence: Evidence, sources: dict) -> bool:
    """An article title identifies a work; it cannot verify its protocol details."""
    source = sources.get(evidence.source_id, {})
    title = source.get("title")
    normalize = lambda text: " ".join(text.split()).rstrip(". ").casefold()
    return bool(title and evidence.quote and normalize(evidence.quote) == normalize(title))


def _scientific(evidence: Evidence, sources: dict) -> bool:
    source = sources.get(evidence.source_id, {})
    return source.get("kind") == "image" or (
        source.get("kind") == "text" and source.get("content_level") in
        {"catalogue", "abstract", "fulltext"} and not _identity_only(evidence, sources))


def _primary(evidence: Evidence, sources: dict) -> bool:
    source = sources.get(evidence.source_id, {})
    return (evidence.basis == "text" and source.get("kind") == "text" and source.get(
        "content_level") in {"abstract", "fulltext"} and not _identity_only(evidence, sources))


def is_catalogue_annotation(paper: PaperMatch, sources: dict) -> bool:
    """A secondary mention records its actual source and establishes no method."""
    source = sources.get(paper.source_id, {})
    return (source.get("kind") == "text" and source.get("content_level") == "catalogue"
            and paper.assessment == "listed_not_read" and not paper.methods
            and paper.relationship in {"catalogue_citation", "background"}
            and any(e.source_id == paper.source_id and e.basis == "text"
                    and e.support == "reported" and e.quote in source.get("text", "")
                    for e in paper.evidence))


def validate_extraction(candidate: DiagramExtraction, sources: dict, *,
                        expected_record_id: str | None = None,
                        expected_title: str | None = None,
                        expected_method_names: tuple[str, ...] | None = None,
                        require_paper_matches: bool = False) -> list[str]:
    errors = _source_errors(candidate, sources, candidate.inspected_sources)
    if expected_record_id is not None and candidate.record_id != expected_record_id:
        errors.append("Candidate record ID differs from assigned catalogue record")
    if expected_title is not None and candidate.title != expected_title:
        errors.append("Candidate title differs from assigned catalogue record")
    if expected_method_names is not None and set(candidate.method_names) != set(expected_method_names):
        errors.append("Candidate method names differ from source-attested expected members")
    if require_paper_matches or candidate.paper_matches:
        matched_methods = {method for paper in candidate.paper_matches for method in paper.methods}
        for method in set(candidate.method_names) - matched_methods:
            paper_gap = any(unknown.blocking and "scope:" + method in unknown.affected_ids
                            for unknown in candidate.unknowns)
            # A bound, explicit paper gap permits preserving a diagnostic record.
            # Its blocking status still prevents a supported outcome. The reviewer
            # assesses whether the question really describes the missing association.
            if not paper_gap:
                errors.append("Named method lacks a paper-match assessment: " + method)
    if not any(sources.get(s, {}).get("kind") == "image" for s in candidate.inspected_sources):
        errors.append("Diagram extraction requires an inspected registered image")
    records = _target_records(candidate)
    for target, record in records.items():
        if target.startswith(("state:", "operation:", "flow:")) or (
            target.startswith("scope:") and record.status == "resolved"):
            if not any(_scientific(e, sources) for e in _evidence(record)):
                errors.append("Scientific target cannot rely on publication metadata alone: " + target)
    for paper in candidate.paper_matches:
        source = sources.get(paper.source_id)
        if source is None:
            errors.append("Unknown matched publication source: " + paper.source_id)
            continue
        if source.get("content_level") == "catalogue":
            if not is_catalogue_annotation(paper, sources):
                errors.append("Catalogue citation requires listed_not_read, methods=[], "
                              "catalogue_citation/background and its own literal text evidence: " + paper.id)
        elif source.get("kind") != "text" or source.get("content_level") not in {
                "metadata", "abstract", "fulltext"}:
            errors.append("Paper match source must be a publication/metadata text source: " + paper.id)
        if (source.get("identity_status") == "unverified_catalogue_reference"
                and paper.assessment != "listed_not_read"):
            errors.append("Unverified catalogue reference permits only listed_not_read: " + paper.id)
        if paper.assessment != "listed_not_read" and (paper.source_id not in candidate.inspected_sources
                or not any(e.source_id == paper.source_id for e in paper.evidence)):
            errors.append("Verified publication needs inspected identity/content evidence: " + paper.id)
        if paper.relationship in {"supports_architecture", "supports_variant"}:
            supporting = [e for e in paper.evidence if e.basis == "text" and
                          e.source_id == paper.source_id and _primary(e, sources)]
            if paper.assessment != "content_checked" or not supporting:
                errors.append("Publication metadata alone cannot support architecture/variant: " + paper.id)
        if paper.assessment == "content_checked" and not any(
            e.source_id == paper.source_id and _primary(e, sources)
            for e in paper.evidence
        ):
            errors.append("Content-checked publication needs its own inspected content evidence: " + paper.id)
    for correction in candidate.corrections:
        if {e.basis for e in correction.evidence} != {"image", "text"}:
            errors.append("Correction requires both diagram and primary text evidence: " + correction.id)
        if not any(_primary(e, sources) for e in correction.evidence):
            errors.append("Correction requires primary content beyond a title, catalogue or metadata: " + correction.id)
        if correction.disposition == "use_primary":
            primary_ids = {e.source_id for e in correction.evidence if _primary(e, sources)}
            target_evidence = _evidence(records[correction.target_id])
            if not any(e.source_id in primary_ids and _primary(e, sources) for e in target_evidence):
                errors.append("Primary correction must be explicitly evidenced on affected target: " + correction.id)
    return errors


def validate_review(review: DiagramReview, candidate: DiagramExtraction, sources: dict) -> list[str]:
    errors = _source_errors(review, sources, review.inspected_sources)
    if review.record_id != candidate.record_id or review.candidate_sha256 != digest(candidate):
        errors.append("Review is not bound to this exact candidate")
    targets = [x.target for x in review.checks]
    if len(targets) != len(set(targets)) or set(targets) != review_targets(candidate, format_version=review.format_version):
        errors.append("Review requires exactly one judgment for every record target")
    images = {s for s in candidate.inspected_sources if sources.get(s, {}).get("kind") == "image"}
    if not images.issubset(review.inspected_sources):
        errors.append("Reviewer did not declare inspection of every candidate diagram")
    records = _review_records(candidate, review.format_version)
    for check in review.checks:
        if check.verdict != "supported" or check.target not in records:
            continue
        target = records[check.target]
        owner = _label_owner(check.target)
        if owner.startswith(("state:", "operation:", "flow:")) or (
                check.target.startswith("scope:") and target.status == "resolved"):
            own_sources = {e.source_id for e in _evidence(target) if _scientific(e, sources)}
            if not any(e.source_id in own_sources and _scientific(e, sources) for e in check.evidence):
                errors.append("Positive scientific review needs the target's scientific evidence: " + check.target)
        for correction in candidate.corrections:
            if correction.target_id == owner and correction.disposition == "use_primary":
                primary_ids = {e.source_id for e in correction.evidence if _primary(e, sources)}
                if not any(e.source_id in primary_ids and _primary(e, sources) for e in check.evidence):
                    errors.append("Review of corrected target requires the correction's primary source: " + check.target)
        if check.target.startswith("paper:") and target.assessment != "listed_not_read":
            if not any(e.source_id == target.source_id and (target.assessment != "content_checked"
                       or _primary(e, sources)) for e in check.evidence):
                errors.append("Positive paper review needs that paper's identity/content evidence: " + check.target)
        if check.target.startswith("correction:"):
            primary_ids = {e.source_id for e in target.evidence if _primary(e, sources)}
            image_ids = {e.source_id for e in target.evidence if e.basis == "image"}
            if not (any(e.source_id in primary_ids and _primary(e, sources) for e in check.evidence)
                    and any(e.source_id in image_ids and e.basis == "image" for e in check.evidence)):
                errors.append("Positive correction review needs both sides of that discrepancy: " + check.target)
    return errors


def review_reasons(review: DiagramReview, candidate: DiagramExtraction) -> list[dict]:
    """Explain every review/host condition; callers validate records separately."""
    reasons = [{"target": c.target, "basis": "independent_review", "question": c.rationale}
               for c in review.checks if c.action != "accept"]
    reasons.extend({"target": "scope:" + s.method, "basis": "unresolved_method_applicability",
                    "question": s.rationale} for s in candidate.method_scopes if s.status == "unresolved")
    reasons.extend({"target": "unknown:" + u.id, "basis": "blocking_unknown", "question": u.question}
                   for u in candidate.unknowns if u.blocking)
    reasons.extend({"target": "correction:" + c.id, "basis": "unresolved_source_discrepancy",
                    "question": c.primary_statement} for c in candidate.corrections
                   if c.disposition == "keep_unresolved")
    if candidate.paper_matches:
        for method in candidate.method_names:
            if not any(method in p.methods and p.relationship not in {"background", "unresolved"}
                       for p in candidate.paper_matches):
                reasons.append({"target": "scope:" + method,
                    "basis": "publication_association_not_established",
                    "question": "The recorded papers do not establish a non-background association "
                                "for this named method. Resolve the association at the declared scope "
                                "or retain it as unresolved; background content alone is insufficient."})
    return reasons


def review_outcome(review: DiagramReview, candidate: DiagramExtraction,
                   sources: dict | None = None) -> str:
    if sources is not None:
        errors = validate_extraction(candidate, sources) + validate_review(review, candidate, sources)
        if errors:
            raise ValueError("; ".join(errors))
    elif (review.record_id != candidate.record_id or review.candidate_sha256 != digest(candidate)
          or len(review.checks) != len(review_targets(candidate, format_version=review.format_version))
          or {x.target for x in review.checks} != review_targets(candidate, format_version=review.format_version)):
        raise ValueError("Incomplete or stale review")
    # A correctable candidate error (including an erroneously expanded scope)
    # must reach the bounded repair round. Candidate-declared unresolved fields
    # must not override the reviewer's request to repair them. Nothing is admitted
    # while either revisions or research questions remain.
    if any(x.action == "revise" for x in review.checks):
        return "needs_revision"
    if review_reasons(review, candidate):
        return "research_needed"
    return "supported"


def export_graph(candidate: DiagramExtraction, review: DiagramReview, sources: dict) -> dict:
    outcome = review_outcome(review, candidate, sources)
    namespace = "illumina:" + quote(candidate.record_id, safe="")
    node_id = lambda kind, ident: namespace + ":" + kind + ":" + quote(ident, safe="")
    types = {x.id: kind for kind, records in (("state", candidate.states),
                                             ("operation", candidate.operations)) for x in records}
    nodes = [{"id": namespace, "node_type": "source_workflow", "record_id": candidate.record_id,
              "title": candidate.title, "scope": candidate.scope}]
    edges = []
    for kind, records in (("state", candidate.states), ("operation", candidate.operations),
                          ("paper_match", candidate.paper_matches), ("correction", candidate.corrections)):
        for record in records:
            ident = node_id(kind, record.id)
            nodes.append({"id": ident, "node_type": kind, "original": record.model_dump(mode="json")})
            edges.append({"source": namespace, "target": ident, "kind": "has_" + kind})
    for flow in candidate.flows:
        edges.append({"id": node_id("flow", flow.id), "source": node_id(types[flow.source], flow.source),
                      "target": node_id(types[flow.target], flow.target), "kind": flow.kind,
                      "original": flow.model_dump(mode="json")})
    registry = {ident: {key: value for key, value in source.items() if key != "text"}
                for ident, source in sources.items()}
    return {"format_version": "illumina_schematic_graph/1.0", "status": outcome,
            "semantics": "Source-supported architecture with explicit primary-paper corrections; literal diagram observations retained, no automatic historical edges.",
            "record_id": candidate.record_id, "candidate_sha256": digest(candidate),
            "review_sha256": digest(review), "nodes": nodes, "edges": edges,
            "workflow": candidate.model_dump(mode="json"), "review": review.model_dump(mode="json"),
            "source_registry": registry, "source_registry_sha256": digest(sources)}

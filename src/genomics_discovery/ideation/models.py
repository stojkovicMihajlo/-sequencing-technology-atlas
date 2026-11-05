"""An assay proposal uses atlas State–Operation–Flow meanings, not extraction evidence.

The graph records an intended architecture. A separate assessment ledger says
which properties have precedent and which transfers remain hypotheses. Structural
validation cannot establish biochemical compatibility or novelty. These models
cannot be admitted through the Illumina extraction/review publication gate.
"""
from __future__ import annotations

from collections import Counter, deque
from typing import Annotated, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.illumina.models import Text, digest, property_target
from genomics_discovery.illumina.representation import (
    AttributionValue, DATA_PROPERTIES, MATERIAL_PROPERTIES,
)

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AtlasReference(StrictModel):
    record_id: Text
    extraction_sha256: Hash
    node_id: Text = Field(description="Exact existing atlas State or Operation node ID, not a guessed method name.")


class LiteratureReference(StrictModel):
    source_id: Text = Field(description="Exact source ID registered by an evidence tool; not an arbitrary URL.")
    content_sha256: Hash = Field(description="Exact retrieved text content hash; source identity alone does not bind its version.")
    locator: Text
    quote: Annotated[Text, Field(max_length=800)]


class ProposedComponent(StrictModel):
    id: Text
    label: Text


class ProposedProperty(StrictModel):
    """Same property names/value meanings as illumina/representation.py.

    This intentionally does not inherit PropertyAssertion: a proposed property
    is assessed in the ledger instead of being dressed as source evidence.
    """
    id: Text
    subject: Text = "self"
    property: Literal[
        "identity", "strandedness", "topology", "terminal_group", "modification",
        "sequence_segment", "association", "physical_context", "attribution",
        "data_type", "data_content", "defined_other",
    ]
    value: Text | AttributionValue
    end: Literal["five_prime", "three_prime"] | None = None
    partner: Text | None = None
    site: Text | None = None
    location: Text | None = None
    name: Text | None = None
    definition: Text | None = None
    applicable_to: Literal["material", "data"] | None = None

    @model_validator(mode="after")
    def property_meaning(self):
        allowed = {
            "strandedness": {"single_stranded", "double_stranded", "mixed"},
            "topology": {"linear", "covalently_circular", "branched", "hybridized_loop", "mixed"},
            "association": {"hybridized_to", "covalently_linked_to", "bound_to", "contained_in", "co_present_with"},
            "data_type": {"sequence_reads", "alignment", "instrument_signal", "measurement_table", "coordinate_map", "derived_result", "other"},
        }
        if self.property == "attribution":
            if not isinstance(self.value, AttributionValue):
                raise ValueError("Attribution requires unit, carrier and association")
        elif not isinstance(self.value, str):
            raise ValueError("Only attribution uses a structured value")
        if self.property in allowed and self.value not in allowed[self.property]:
            raise ValueError("Property value differs from the atlas property vocabulary")
        extra_fields = {
            "end": {"terminal_group"}, "partner": {"association"},
            "site": {"modification"}, "location": {"sequence_segment"},
            "name": {"defined_other"}, "applicable_to": {"defined_other"},
            "definition": {"defined_other", "data_type"},
        }
        for field, kinds in extra_fields.items():
            if getattr(self, field) is not None and self.property not in kinds:
                raise ValueError(f"{field} is not applicable to this property")
        required = {"terminal_group": ("end",), "association": ("partner",),
                    "defined_other": ("name", "definition", "applicable_to")}
        if any(getattr(self, f) is None for f in required.get(self.property, ())):
            raise ValueError("Missing property-specific field")
        if self.property == "data_type" and self.value == "other" and not self.definition:
            raise ValueError("Other data type requires a definition")
        return self


class ProposedState(StrictModel):
    id: Text
    label: Text = Field(description="Actual preparation or dataset at this boundary, not an action or merely the biological target.")
    kind: Literal["material", "data"]
    components: tuple[ProposedComponent, ...] = ()
    properties: Annotated[tuple[ProposedProperty, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def state_meaning(self):
        components = [c.id for c in self.components]
        if len(set(components)) != len(components) or "self" in components:
            raise ValueError("Component IDs must be unique and cannot be self")
        if self.kind == "data" and components:
            raise ValueError("Data cannot have physical components")
        subjects, ids, singleton_keys, assertions = {"self", *components}, set(), set(), set()
        for prop in self.properties:
            if prop.id in ids or prop.subject not in subjects:
                raise ValueError("Property requires a unique ID and an existing subject")
            ids.add(prop.id)
            if prop.property == "association" and (prop.partner not in subjects or prop.partner == prop.subject):
                raise ValueError("Association requires a different existing local subject")
            if ((self.kind == "data" and prop.property in MATERIAL_PROPERTIES)
                    or (self.kind == "material" and prop.property in DATA_PROPERTIES)):
                raise ValueError("Property is incompatible with State kind")
            if prop.property == "defined_other" and prop.applicable_to != self.kind:
                raise ValueError("Defined-other applicability differs from State kind")
            if prop.property in {"identity", "strandedness", "topology", "terminal_group", "data_type"}:
                key = (prop.subject, prop.property, prop.end)
                if key in singleton_keys:
                    raise ValueError("Repeated/conflicting single-valued property")
                singleton_keys.add(key)
            assertion = digest(prop.model_dump(mode="json", exclude={"id"}))
            if assertion in assertions:
                raise ValueError("Duplicate scientific assertion")
            assertions.add(assertion)
        if not any(p.property == "identity" and p.subject == "self" for p in self.properties):
            raise ValueError("State requires whole-preparation identity")
        if self.kind == "data" and not any(p.property == "data_type" for p in self.properties):
            raise ValueError("Data State requires data_type")
        return self


class ProposedInputRole(StrictModel):
    state_id: Text
    subject: Text = "self"
    roles: Annotated[tuple[Literal[
        "sample", "template", "primer", "probe", "reagent", "catalyst", "capture_support",
        "data", "reference_data", "unspecified",
    ], ...], Field(min_length=1)]
    explanation: Text


class ProposedEffect(StrictModel):
    aspect: Literal["material_transformation", "selection", "organization", "attribution", "readout", "data_processing", "unresolved"]
    input_state_ids: tuple[Text, ...]
    output_state_ids: tuple[Text, ...]
    statement: Text


class ProposedOperation(StrictModel):
    id: Text
    label: Text = Field(description="Action on specified input States; a biological aim alone is not an Operation.")
    description: Text
    inputs: tuple[Text, ...]
    outputs: tuple[Text, ...]
    mechanism: Text | None = None
    information_effect: Text | None = None
    resolution: Literal["single_event", "composite_block", "unresolved_interface"]
    input_roles: tuple[ProposedInputRole, ...]
    effects: Annotated[tuple[ProposedEffect, ...], Field(min_length=1)]
    output_grouping: Literal["no_output", "one_output", "distinct_outputs", "unresolved"]


class ProposedFlow(StrictModel):
    id: Text
    source: Text
    target: Text
    kind: Literal["material_input", "material_output", "data_input", "data_output", "precedes"]


class ProposedWorkflow(StrictModel):
    states: Annotated[tuple[ProposedState, ...], Field(min_length=1)]
    operations: Annotated[tuple[ProposedOperation, ...], Field(min_length=1)]
    flows: Annotated[tuple[ProposedFlow, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def graph_contract(self):
        records = (*self.states, *self.operations, *self.flows)
        if len({x.id for x in records}) != len(records):
            raise ValueError("Local graph IDs must be unique across record types")
        states, operations = {s.id: s for s in self.states}, {o.id: o for o in self.operations}
        adjacency = {key: [] for key in (*states, *operations)}
        pairs, degree = set(), Counter()
        for flow in self.flows:
            if flow.source not in adjacency or flow.target not in adjacency:
                raise ValueError("Unknown Flow endpoint")
            key = (flow.source, flow.target, flow.kind)
            if key in pairs:
                raise ValueError("Duplicate Flow")
            pairs.add(key)
            if flow.kind == "precedes":
                valid = flow.source in operations and flow.target in operations
            elif flow.kind.endswith("_input"):
                valid = flow.source in states and flow.target in operations and flow.kind == states[flow.source].kind + "_input"
            else:
                valid = flow.source in operations and flow.target in states and flow.kind == states[flow.target].kind + "_output"
            if not valid:
                raise ValueError("Flow type differs from endpoint types")
            adjacency[flow.source].append(flow.target)
            degree[flow.target] += 1
        for op in self.operations:
            for direction in ("inputs", "outputs"):
                refs = getattr(op, direction)
                actual = {f.source if direction == "inputs" else f.target for f in self.flows
                          if (f.target == op.id if direction == "inputs" else f.source == op.id)
                          and f.kind.endswith("_input" if direction == "inputs" else "_output")}
                if len(refs) != len(set(refs)) or set(refs) - states.keys() or set(refs) != actual:
                    raise ValueError("Operation I/O must match existing States and Flows")
            role_keys = [(r.state_id, r.subject) for r in op.input_roles]
            if len(role_keys) != len(set(role_keys)) or {r.state_id for r in op.input_roles} != set(op.inputs):
                raise ValueError("Input roles must cover each input with distinct State/subject pairs")
            for role in op.input_roles:
                state = states[role.state_id]
                if role.subject not in {"self", *(c.id for c in state.components)}:
                    raise ValueError("Input role references an unknown subject")
                if len(set(role.roles)) != len(role.roles) or ("unspecified" in role.roles and len(role.roles) != 1):
                    raise ValueError("Input roles must be distinct; unspecified is exclusive")
                known = set(role.roles) - {"unspecified"}
                if ((state.kind == "data" and known - {"data", "reference_data"})
                        or (state.kind == "material" and known & {"data", "reference_data"})):
                    raise ValueError("Input role is incompatible with State kind")
            covered = set()
            for effect in op.effects:
                if set(effect.input_state_ids) - set(op.inputs) or set(effect.output_state_ids) - set(op.outputs):
                    raise ValueError("Effect participants must be this Operation's I/O")
                if len(effect.input_state_ids) != len(set(effect.input_state_ids)) or len(effect.output_state_ids) != len(set(effect.output_state_ids)):
                    raise ValueError("Duplicate effect participant")
                if (op.inputs and not effect.input_state_ids) or not (effect.input_state_ids or effect.output_state_ids):
                    raise ValueError("Effect needs its declared participants")
                if effect.aspect == "readout" and not any(states[s].kind == "data" for s in effect.output_state_ids):
                    raise ValueError("Readout requires a data output")
                covered.update(effect.output_state_ids)
            if covered != set(op.outputs):
                raise ValueError("Every output needs an effect assessment")
            expected = {0: "no_output", 1: "one_output"}.get(len(op.outputs))
            if (expected and op.output_grouping != expected) or (not expected and op.output_grouping not in {"distinct_outputs", "unresolved"}):
                raise ValueError("Output grouping differs from output State count")
        ready, seen = deque(key for key in adjacency if not degree[key]), 0
        while ready:
            current = ready.popleft(); seen += 1
            for target in adjacency[current]:
                degree[target] -= 1
                if not degree[target]: ready.append(target)
        if seen != len(adjacency):
            raise ValueError("Workflow must be acyclic; describe repeated cycles inside an Operation")
        touched = {v for f in self.flows for v in (f.source, f.target)}
        if touched != set(adjacency):
            raise ValueError("Disconnected unused State or Operation")
        return self


class ClaimAssessment(StrictModel):
    target: Text
    status: Literal["precedent", "transfer", "hypothesis"] = Field(description="precedent applies only to the cited setting. A proposed contextual transfer is not demonstrated compatibility.")
    required_property: Text = Field(description="Exact property/behavior required of this graph element, not merely a reagent name. Operation assessments cover all nested roles/effects.")
    rationale: Text
    atlas_refs: tuple[AtlasReference, ...] = ()
    literature_refs: tuple[LiteratureReference, ...] = ()
    compatibility_assumption: Text | None = None
    unsupported_property: Text | None = None
    decisive_test: Text | None = None

    @model_validator(mode="after")
    def epistemic_boundary(self):
        if self.status in {"precedent", "transfer"} and not (self.atlas_refs or self.literature_refs):
            raise ValueError("Precedent/transfer requires an exact evidence reference")
        if self.status == "transfer" and not (self.compatibility_assumption and self.decisive_test):
            raise ValueError("Transfer requires compatibility assumption and decisive test")
        if self.status == "hypothesis" and not (self.unsupported_property and self.decisive_test):
            raise ValueError("Hypothesis requires unsupported property and decisive test")
        if self.status == "precedent" and (self.compatibility_assumption or self.unsupported_property):
            raise ValueError("Unresolved transfer/unsupported property cannot be labeled precedent")
        return self


class DesignChange(StrictModel):
    id: Text
    earlier_refs: tuple[AtlasReference, ...] = Field(default=(), description="Optional exact prior nodes; a genuinely new arrangement need not have an atlas parent.")
    proposed_target_ids: Annotated[tuple[Text, ...], Field(min_length=1)]
    earlier_arrangement: Text
    retained: Text
    changed: Text
    expected_gain: Text = Field(description="Prediction to test, not a reported experimental gain.")


def candidate_targets(value) -> set[str]:
    workflow = value.workflow if hasattr(value, "workflow") else value
    targets = {kind + ":" + item.id for kind, items in (
        ("state", workflow.states), ("operation", workflow.operations), ("flow", workflow.flows)) for item in items}
    targets.update(property_target(s.id, p.id) for s in workflow.states for p in s.properties)
    return targets


class CandidateIdea(StrictModel):
    format_version: Literal["proposed_assay/1.0"] = "proposed_assay/1.0"
    id: Text
    title: Text
    biological_question: Text
    measurement: Text = Field(description="Desired observable quantity and biological unit, specimen and actual sequencing/readout output.")
    expected_benefit: Text
    workflow: ProposedWorkflow
    changes: Annotated[tuple[DesignChange, ...], Field(min_length=1)]
    assessments: Annotated[tuple[ClaimAssessment, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def complete_assessment(self):
        targets = candidate_targets(self)
        readout_states = {s.id for s in self.workflow.states if s.kind == "data" and any(
            p.property == "data_type" and p.value in {"sequence_reads", "instrument_signal"}
            for p in s.properties)}
        if not any(e.aspect == "readout" and readout_states.intersection(e.output_state_ids)
                   for o in self.workflow.operations for e in o.effects):
            raise ValueError("Sequencing assay proposal requires a readout producing sequence reads or instrument signal")
        actual = [a.target for a in self.assessments]
        if len(actual) != len(set(actual)) or set(actual) != targets:
            raise ValueError("Assessment ledger must cover each State, property, Operation and Flow exactly once")
        ids = [c.id for c in self.changes]
        if len(ids) != len(set(ids)):
            raise ValueError("Design change IDs must be unique")
        for change in self.changes:
            if len(set(change.proposed_target_ids)) != len(change.proposed_target_ids) or set(change.proposed_target_ids) - targets:
                raise ValueError("Design change must identify distinct existing proposal targets")
        by_target = {a.target: a for a in self.assessments}
        for op in self.workflow.operations:
            if (op.resolution == "unresolved_interface" or op.output_grouping == "unresolved"
                    or any(e.aspect == "unresolved" for e in op.effects)):
                if by_target["operation:" + op.id].status != "hypothesis":
                    raise ValueError("Unresolved Operation must remain an explicit hypothesis")
        return self


class ResearchAgenda(StrictModel):
    """Request-specific questions, not prescribed search strings or assay answers."""
    capability: Text = Field(description="Question about a useful measurement/improvement: native specimen, biological unit, direct observation versus inference, and closest existing capability.")
    mechanism: Text = Field(description="Question about the exact experimentally demonstrated property needed to create a distinguishing signal; permit mechanisms outside the atlas.")
    interfaces: Text = Field(description="Question about material compatibility and the physical carrier of identity through transformations, branching or pooling.")
    challenge: Text = Field(description="Question seeking competing assays/readouts, a confound or tradeoff that could defeat the benefit, and a comparison that would discriminate it.")


RESEARCH_AREAS = tuple(ResearchAgenda.model_fields)


class ResearchFinding(StrictModel):
    question: Text = Field(description="The assigned research question, or a justified reframing. Do not invent a search history.")
    reframing_reason: Text | None = None
    conclusion: Text = Field(description="Concise evidence-based finding and its consequence for this design, including the tested setting. Proposed gains remain conditional.")
    status: Literal["evidence_found", "unresolved"] = Field(description="Retrieval status, not a scientific acceptance or novelty certificate.")
    targets: Annotated[tuple[Text, ...], Field(min_length=1)] = Field(description="Exact candidate, change:<id>, State, property, Operation or Flow targets affected by the finding.")
    atlas_refs: tuple[AtlasReference, ...] = ()
    literature_refs: tuple[LiteratureReference, ...] = ()
    remaining_uncertainty: Text | None = Field(default=None, description="Missing evidence, access limits, untested transfer or next decision-changing investigation. A no-hit search is not proof of absence.")

    @model_validator(mode="after")
    def evidence_boundary(self):
        if self.status == "evidence_found" and not (self.atlas_refs or self.literature_refs):
            raise ValueError("Research evidence_found requires a located reference")
        if self.status == "unresolved" and not self.remaining_uncertainty:
            raise ValueError("Unresolved research must name the remaining uncertainty")
        if len(self.targets) != len(set(self.targets)):
            raise ValueError("Research finding targets must be distinct")
        return self


class ResearchFindings(StrictModel):
    capability: ResearchFinding
    mechanism: ResearchFinding
    interfaces: ResearchFinding
    challenge: ResearchFinding


class ProposedAssay(CandidateIdea):
    """Current author contract; the legacy class/digests remain unchanged."""
    format_version: Literal["proposed_assay/1.1"] = "proposed_assay/1.1"
    research: ResearchFindings

    @model_validator(mode="after")
    def research_targets_exist(self):
        allowed = candidate_targets(self) | {"candidate"} | {"change:" + c.id for c in self.changes}
        for area in RESEARCH_AREAS:
            if set(getattr(self.research, area).targets) - allowed:
                raise ValueError("Research finding references an unknown graph/change target")
        return self


def parse_candidate(value) -> CandidateIdea:
    """Read old artifacts without adding fields and invalidating their reviews."""
    if isinstance(value, CandidateIdea):
        value = value.model_dump(mode="json")
    version = value.get("format_version", "proposed_assay/1.0")
    if version == "proposed_assay/1.0":
        return CandidateIdea.model_validate(value)
    if version == "proposed_assay/1.1":
        return ProposedAssay.model_validate(value)
    raise ValueError(f"Unknown candidate format: {version}")


def review_targets(idea: CandidateIdea) -> set[str]:
    targets = candidate_targets(idea) | {"candidate"} | {"change:" + c.id for c in idea.changes}
    if isinstance(idea, ProposedAssay):
        targets |= {"research:" + area for area in RESEARCH_AREAS}
    return targets


class NearestPrior(StrictModel):
    """A located comparator, not proof that all relevant prior art was searched."""
    title: Text
    atlas_refs: tuple[AtlasReference, ...] = ()
    literature_refs: tuple[LiteratureReference, ...] = ()
    overlap: Text
    residual_difference: Text

    @model_validator(mode="after")
    def located_prior(self):
        if not (self.atlas_refs or self.literature_refs):
            raise ValueError("Nearest prior requires a retrieved evidence reference")
        return self


class ReviewIssue(StrictModel):
    id: Text
    target: Text
    severity: Literal["blocking", "major", "minor"]
    reason: Text
    next_test: Text


class IdeaReview(StrictModel):
    candidate_id: Text
    candidate_sha256: Hash
    reviewed_targets: Annotated[tuple[Text, ...], Field(min_length=1)] = Field(description="Exactly all State, property, Operation, Flow, change:<id>, candidate and (for 1.1) research:<area> targets. This coverage declaration does not replace substantive scientific review.")
    novelty_status: Literal["direct_overlap", "possible_distinction", "search_incomplete"]
    nearest_prior: tuple[NearestPrior, ...]
    issues: tuple[ReviewIssue, ...]
    disposition: Literal["revise", "retain_for_testing", "hold", "drop"]
    summary: Text = Field(description="Explain the decision, actual search scope/access limits and remaining scientific uncertainties. Nearest-prior retrieval is not exhaustive novelty verification; do not infer absence from a failed or limited search.")

    @model_validator(mode="after")
    def review_boundary(self):
        if self.novelty_status != "search_incomplete" and not self.nearest_prior:
            raise ValueError("A novelty comparison needs a located nearest prior")
        if self.disposition == "retain_for_testing" and any(i.severity == "blocking" for i in self.issues):
            raise ValueError("A blocking issue cannot be retained as ready for testing")
        if self.novelty_status == "direct_overlap" and self.disposition == "retain_for_testing":
            raise ValueError("Direct overlap must be revised, held or dropped")
        if len({i.id for i in self.issues}) != len(self.issues):
            raise ValueError("Review issue IDs must be unique")
        return self


def validate_review_binding(review: IdeaReview, idea: CandidateIdea) -> None:
    if review.candidate_id != idea.id or review.candidate_sha256 != digest(idea):
        raise ValueError("Review is not bound to these exact candidate bytes")
    targets = review_targets(idea)
    if len(review.reviewed_targets) != len(set(review.reviewed_targets)) or set(review.reviewed_targets) != targets:
        raise ValueError("Reviewer must account for every graph, change and candidate target exactly once")
    if any(issue.target not in targets for issue in review.issues):
        raise ValueError("Review issue references an unknown target")

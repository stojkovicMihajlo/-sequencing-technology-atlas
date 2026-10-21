"""Source-scoped protocol candidates and review integrity, never scientific acceptance.

Version 1 supports branches but requires an acyclic graph. Repeated procedures belong
inside an operation's action; this restriction is not a claim that all protocols are DAGs.
Only explicit flows establish ordering. Quote validation establishes provenance, not entailment.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict, deque
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, model_serializer, model_validator

from genomics_discovery.core.artifacts import StrictModel

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PassageRef(StrictModel):
    source_id: Text = Field(description=
        "Exact ID of a registered source in the frozen packet; not a citation invented from memory.")
    passage_id: Text = Field(description=
        "Exact passage ID within source_id. Inspect that passage and relevant context before citing it.")


class Evidence(PassageRef):
    quote: Annotated[Text, Field(max_length=600)] = Field(description=
        "Short exact quotation from the named passage supporting this claim or its inference premises. "
        "Do not paraphrase inside quote; matching text establishes provenance, not entailment.")
    support: Literal["reported", "inferred"] = Field(description=
        "reported when the source states the claimed fact in the relevant scope; inferred when the "
        "claim is a deduction from cited premises. Neither value makes an unsupported assertion valid.")
    rationale: Text = Field(description=
        "Explain how this passage supports this exact claim, including applicable variant, "
        "qualifications and any inference. Do not introduce uncited scientific facts in the explanation.")


EvidenceSet = Annotated[tuple[Evidence, ...], Field(min_length=1)]


class Finding(StrictModel):
    id: Text = Field(description="Stable local finding ID, unique within this reader report.")
    description: Text = Field(description=
        "A source-supported fact relevant to the scoped protocol, retaining consequential "
        "conditions, identity and route qualifications; not a proposed assay improvement.")
    evidence: EvidenceSet = Field(description=
        "Contextual source support for this finding. Different source applicability or deductions "
        "need their own rationale; a paper-level citation alone is insufficient.")


class Gap(StrictModel):
    id: Text = Field(description=
        "Stable local gap ID. Integrators retaining reader gaps use reader_role:original_gap_id.")
    description: Text = Field(description=
        "The specific unresolved fact and its consequence for this scope. Distinguish missing "
        "source access, an unreported fact and an extraction error; absence is not impossibility.")
    blocking: bool = Field(description=
        "True if the unknown prevents support for a consequential scoped claim, interface or route; "
        "false for permitted detail gaps with supported boundaries. False does not supply the missing fact.")
    next_evidence: Text = Field(description=
        "The concrete passage, supplement, dependency or other evidence needed to resolve the "
        "question. State an unknown retrieval target honestly rather than inventing a source.")


class GapResolution(StrictModel):
    """An evidence-backed answer or scope exclusion, still requiring source review."""

    reader_role: Literal["chemistry", "information"] = Field(description=
        "Role of the supplied reader that authored the gap; not an invented role for an integrator gap.")
    gap_id: Text = Field(description="Exact original gap ID from the named reader report.")
    rationale: Text = Field(description=
        "Source-backed answer or scope exclusion for this gap. If only criticality changes, "
        "preserve the unknown fact separately; do not claim it was discovered.")
    evidence: EvidenceSet = Field(description=
        "Inspected evidence supporting the resolution or scope decision, not the earlier reader's opinion.")


def _unique_resolution_keys(resolutions: tuple[GapResolution, ...]) -> None:
    keys = [(item.reader_role, item.gap_id) for item in resolutions]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate gap resolutions for the same reader_role/gap_id")


class ReadingReport(StrictModel):
    role: Literal["chemistry", "information"] = Field(description=
        "The assigned reader perspective; it does not impose a protocol stage or module sequence.")
    inspected_passages: tuple[PassageRef, ...] = Field(description=
        "Passages actually inspected in this session. Include cited finding passages; do not "
        "claim inspection merely because a source appears in the packet catalogue.")
    findings: Annotated[tuple[Finding, ...], Field(min_length=1)] = Field(description=
        "Source-supported findings for the declared method and variant; evidence for later "
        "integration, not an already verified Workflow.")
    unresolved: tuple[Gap, ...] = Field(description=
        "Specific unresolved questions after inspecting relevant available evidence; an empty "
        "list means no identified gaps, not proof that all possible details are known.")


class Resource(StrictModel):
    kind: Literal["reagent", "equipment", "software", "condition"] = Field(description=
        "Type of resource used by this Operation: material reagent, apparatus, computational "
        "tool, or reaction/processing setting. A resource is not automatically a routed State.")
    description: Text = Field(description=
        "Reported resource identity and relevant quantity, sequence, unit or setting for this "
        "Operation and variant. Preserve alternatives and unknowns; do not assign a study-wide "
        "inventory to every reaction or fill omitted conditions with customary defaults.")
    evidence: EvidenceSet = Field(description=
        "Evidence for this resource's identity, stated details and assignment to this Operation; "
        "a reagent's presence somewhere in the paper does not establish that assignment.")


class State(StrictModel):
    id: Text = Field(description=
        "Unique local interface ID referenced by Operation I/O and Flows. Distinct physical "
        "aliquots or routes may need distinct IDs despite identical composition or wording.")
    kind: Literal["material", "data"] = Field(description=
        "material for a physical specimen, fraction, molecule or assembly; data for observations "
        "or derived representations. Barcoded molecules are material; sequencing reads are data.")
    description: Text = Field(description=
        "What material or data exists at this protocol boundary, including consequential stage, "
        "retained fraction, molecular/identity association and branch. This describes an interface, "
        "not an observed historical sample or a full reaction recipe. Qualify unknown properties.")
    evidence: EvidenceSet = Field(description=
        "Source support for the described interface identity and qualifications. A supported "
        "State does not by itself support every Flow connected to it.")


IOSelection = Literal["required", "optional", "alternative"]


def _validate_selection(selection: str | None, group: str | None,
                        condition: str | None) -> None:
    if selection == "alternative":
        if not group or not condition:
            raise ValueError("Alternative I/O requires alternative_group and condition")
    elif group is not None:
        raise ValueError("alternative_group is only valid for alternative I/O")
    if selection == "optional" and not condition:
        raise ValueError("Optional I/O requires a condition")


def _validate_alternatives(items, context: str) -> None:
    groups = Counter(item.alternative_group for item in items
                     if item.selection == "alternative")
    if any(count < 2 for count in groups.values()):
        raise ValueError(f"{context}: alternative groups require at least two members")


class IOBinding(StrictModel):
    """Authored support for one exact association, independent of the operation claim.

    Members of an alternative_group are mutually exclusive: exactly one applies
    when that route is selected. Conditions are source-qualified prose, not an
    executable predicate language. Required inputs outside a group are cumulative.
    """

    state_id: Text = Field(description="Exact local State ID for this single input or output association.")
    basis: Text = Field(description=
        "Why this specific State enters or leaves the Operation, supported in the selected "
        "variant. Distinguish an inspected input from consumed material; do not infer transfer from text order.")
    condition: Text | None = Field(default=None, description=
        "Source-qualified condition under which this association applies. Required for optional "
        "and alternative selections; null when no condition is asserted, not a universal applicability claim.")
    selection: IOSelection = Field(default="required", description=
        "required for cumulative I/O on the selected route, optional for conditional additional "
        "I/O, alternative for mutually exclusive choices in a named group. The default is not evidence.")
    alternative_group: Text | None = Field(default=None, description=
        "Local group ID shared by at least two alternative bindings in the same I/O direction; "
        "exactly one applies when that route is selected. Null for non-alternative bindings.")
    evidence: EvidenceSet = Field(description=
        "Evidence for this exact association and its selection/condition. Evidence that an "
        "Operation occurs is not automatically evidence for every proposed input or output.")

    @model_validator(mode="after")
    def selection_semantics(self) -> IOBinding:
        _validate_selection(self.selection, self.alternative_group, self.condition)
        return self


class Operation(StrictModel):
    id: Text = Field(description="Unique local Operation ID used by Flows, Modules and review targets.")
    name: Text = Field(description=
        "Short descriptive action label for this occurrence. The label does not establish "
        "equivalence to another method or membership in a shared mechanism.")
    action: Text = Field(description=
        "The supported transformation, handling or inspection applied to the specified inputs. "
        "Preserve consequential mechanism and retained material; put recipe values in resources. "
        "Combine routine handling only if no independently needed interface, branch or identity is hidden.")
    inputs: tuple[Text, ...] = Field(description=
        "Exact IDs of States used or inspected by this Operation. Empty only when no input is "
        "represented. List order is not execution order; explain each association in input_bindings.")
    outputs: Annotated[tuple[Text, ...], Field(min_length=1)] = Field(description=
        "Exact IDs of material or data States produced by this Operation. Preserve consequential "
        "retained fractions and branches; describe each association in output_bindings.")
    resources: tuple[Resource, ...] = Field(description=
        "Reported reagents, equipment, software and conditions assigned to this Operation. "
        "An empty list asserts no resources here, not that the operation requires none; retain "
        "consequential unavailable requirements as explicit unknowns or gaps.")
    information_effect: Text = Field(description=
        "Supported change, preservation, measurement or loss of biological/sample identity or "
        "other information at this Operation. Distinguish physical encoding, observed data and "
        "inference. If no effect is established, say so without inventing a purpose or claiming no effect.")
    evidence: EvidenceSet = Field(description=
        "Contextual evidence for the action and stated information effect; resources and "
        "individual I/O associations also need their own support. Do not approve a whole description "
        "because one clause matches a quotation.")
    input_bindings: tuple[IOBinding, ...] | None = Field(default=None, description=
        "One source-qualified binding per input State, covering inputs exactly once. Use these "
        "for new authoring. Null is supported for legacy records and does not certify an association.")
    output_bindings: tuple[IOBinding, ...] | None = Field(default=None, description=
        "One source-qualified binding per output State, covering outputs exactly once. Use these "
        "for new authoring. Null is supported for legacy records and does not certify an association.")

    @model_validator(mode="after")
    def exact_bindings(self) -> Operation:
        for direction, state_ids, bindings in (
            ("input", self.inputs, self.input_bindings),
            ("output", self.outputs, self.output_bindings),
        ):
            if bindings is None:
                continue
            bound = [binding.state_id for binding in bindings]
            if len(bound) != len(set(bound)) or sorted(bound) != sorted(state_ids):
                raise ValueError(f"Operation {self.id}: {direction}_bindings must cover "
                                 "the declared states exactly once")
            _validate_alternatives(bindings, f"Operation {self.id} {direction}")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        data = handler(self)
        for field in ("input_bindings", "output_bindings"):
            if getattr(self, field) is None:
                data.pop(field, None)
        return data


class Flow(StrictModel):
    id: Text = Field(description="Unique local ID for this explicit association or dependency.")
    source: Text = Field(description=
        "Exact source endpoint ID: State for material/data input, Operation for output, "
        "precedence or control. Endpoints describe the asserted relation, not list positions.")
    target: Text = Field(description=
        "Exact target endpoint ID: Operation for material/data input, State for output, "
        "Operation for precedence or control.")
    kind: Literal[
        "material_input", "material_output", "data_input", "data_output", "precedence", "control"
    ] = Field(description=
        "material_input/output or data_input/output connects an Operation with the respective "
        "State; precedence asserts supported ordering; control asserts a reported decision "
        "governing another Operation. Text order or measurement alone establishes neither dependency.")
    basis: Text = Field(description=
        "Explain this exact connection and its qualification; for unqualified I/O describe only "
        "its mechanical construction. For source-qualified connections, include inferred routing "
        "where appropriate. Do not use shared terminology as evidence of transfer or compatibility.")
    condition: Text | None = Field(default=None, description=
        "Source-qualified applicability or decision rule for this Flow. Required for control "
        "and optional/alternative I/O; null means no condition is asserted, not proved unconditionality.")
    evidence: tuple[Evidence, ...] = Field(description=
        "Support for this exact association. Empty only for explicitly unqualified I/O; "
        "an operation citation is not automatically evidence for each of its connections.")
    association: Literal["source_qualified", "unqualified"] | None = Field(default=None, description=
        "For I/O, source_qualified means an authored association with evidence and selection; "
        "unqualified records only mechanical I/O with no scientific support assertion. Null "
        "retains legacy/non-I/O form and must not be silently upgraded to source qualification.")
    selection: IOSelection | None = Field(default=None, description=
        "required, optional or alternative for source-qualified I/O, with the same meaning "
        "as IOBinding.selection. Null for unqualified I/O or non-I/O dependencies.")
    alternative_group: Text | None = Field(default=None, description=
        "Local group of mutually exclusive alternative I/O Flows for the same Operation and "
        "direction. Null unless selection is alternative; preserve the corresponding binding group.")

    @model_validator(mode="after")
    def association_support(self) -> Flow:
        io = self.kind not in {"precedence", "control"}
        if self.association == "unqualified":
            if (not io or self.evidence or self.condition is not None
                    or self.selection is not None or self.alternative_group is not None):
                raise ValueError("Unqualified I/O may not assert evidence, conditions or selection")
        else:
            if not self.evidence:
                raise ValueError("Flow requires at least 1 evidence item unless I/O is unqualified")
            if self.association == "source_qualified" and (not io or self.selection is None):
                raise ValueError("Source-qualified I/O requires an explicit selection")
            if self.selection is not None and self.association != "source_qualified":
                raise ValueError("I/O selection requires a source_qualified association")
        _validate_selection(self.selection, self.alternative_group, self.condition)
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        data = handler(self)
        for field in ("association", "selection", "alternative_group"):
            if getattr(self, field) is None:
                data.pop(field, None)
        return data


class Module(StrictModel):
    id: Text = Field(description="Unique local grouping ID; not a universal mechanism identifier.")
    name: Text = Field(description=
        "Human-readable name for this local group of Operations. A shared name is not evidence "
        "of a shared mechanism or interchangeable assay component.")
    operation_ids: Annotated[tuple[Text, ...], Field(min_length=1)] = Field(description=
        "Existing Operation IDs grouped for navigation. Current schema assigns each Operation "
        "to exactly one Module; this list does not create execution order or additional transformations.")
    boundary_rationale: Text = Field(description=
        "Why these Operations form a useful local group, referring to their documented purpose "
        "or interfaces. Do not invent an intermediate State solely to justify a Module boundary.")


class Workflow(StrictModel):
    format_version: Literal["protocol_workflow/1.0"] = Field(default="protocol_workflow/1.0",
        description="Exact serialization version; not a scientific validation or protocol version.")
    method_id: Text = Field(description="Copy the frozen source packet's method_id exactly.")
    title: Text = Field(description="Copy the frozen source packet's method title exactly.")
    scope: Text = Field(description=
        "Copy the frozen extraction scope exactly. It defines the specimen-to-endpoint claim "
        "and required detail; do not narrow it to avoid a review finding or assert historical execution.")
    variant: Text = Field(description=
        "Copy the frozen published implementation/variant exactly. Preserve conditional routes "
        "within it; do not combine incompatible versions into an invented hybrid.")
    states: Annotated[tuple[State, ...], Field(min_length=1)] = Field(description=
        "Source-supported material/data interfaces required to describe this scoped protocol. "
        "Their array order carries no route or execution semantics.")
    operations: Annotated[tuple[Operation, ...], Field(min_length=1)] = Field(description=
        "Documented transformations, handling units and inspections with explicit I/O. "
        "Use consequential interfaces to decide granularity; array order does not connect them.")
    flows: Annotated[tuple[Flow, ...], Field(min_length=1)] = Field(description=
        "Explicit typed associations and supported dependencies connecting States and Operations. "
        "I/O must agree exactly with Operation references and any authored bindings.")
    modules: Annotated[tuple[Module, ...], Field(min_length=1)] = Field(description=
        "Local navigational groups covering each Operation exactly once. They do not replace "
        "Flows or prescribe a universal assay sequence.")
    unresolved: tuple[Gap, ...] = Field(description=
        "Remaining specific uncertainties and their scope consequences after source investigation. "
        "Retain reader gaps or explicitly resolve them; unknown does not mean physically impossible.")
    gap_resolutions: tuple[GapResolution, ...] = Field(default=(), description=
        "Source-backed answers or scope exclusions for supplied reader gaps. No entry is needed "
        "for a retained gap; do not invent resolutions for integrator-authored gaps.")

    @model_validator(mode="after")
    def explicit_graph(self) -> Workflow:
        _unique_resolution_keys(self.gap_resolutions)
        objects = (*self.states, *self.operations, *self.flows, *self.modules, *self.unresolved)
        ids = [item.id for item in objects]
        if len(ids) != len(set(ids)):
            raise ValueError("All workflow IDs must be unique across states, operations, flows, "
                             "modules and gaps")
        states = {item.id: item for item in self.states}
        operations = {item.id: item for item in self.operations}
        members: Counter[str] = Counter()
        for module in self.modules:
            if len(module.operation_ids) != len(set(module.operation_ids)):
                raise ValueError(f"Module {module.id}: duplicate operation membership")
            if set(module.operation_ids) - operations.keys():
                raise ValueError(f"Module {module.id}: unknown operation")
            members.update(module.operation_ids)
        if any(members[op] != 1 for op in operations):
            raise ValueError("Every operation must belong to exactly one module")
        inputs: dict[str, list[str]] = defaultdict(list)
        outputs: dict[str, list[str]] = defaultdict(list)
        producers: Counter[str] = Counter()
        edges: dict[str, set[str]] = defaultdict(set)
        for flow in self.flows:
            if flow.kind in {"precedence", "control"}:
                if flow.source not in operations or flow.target not in operations:
                    raise ValueError(f"Flow {flow.id}: precedence/control must connect operations")
                if flow.kind == "control" and not flow.condition:
                    raise ValueError(f"Flow {flow.id}: control requires a condition")
            else:
                kind, direction = flow.kind.split("_")
                state_id, op_id = ((flow.source, flow.target) if direction == "input"
                                   else (flow.target, flow.source))
                if op_id not in operations or state_id not in states:
                    raise ValueError(f"Flow {flow.id}: I/O requires state/operation endpoints")
                if states[state_id].kind != kind:
                    raise ValueError(f"Flow {flow.id}: {kind} flow requires a {kind} state")
                (inputs if direction == "input" else outputs)[op_id].append(state_id)
                if direction == "output":
                    producers[state_id] += 1
            edges[flow.source].add(flow.target)
        if any(count > 1 for count in producers.values()):
            raise ValueError("Each state has at most one producer; use an explicit merge operation")
        for op in self.operations:
            if len(op.inputs) != len(set(op.inputs)) or len(op.outputs) != len(set(op.outputs)):
                raise ValueError(f"Operation {op.id}: duplicate input/output state")
            if (sorted(op.inputs) != sorted(inputs[op.id])
                    or sorted(op.outputs) != sorted(outputs[op.id])):
                raise ValueError(f"Operation {op.id}: I/O must exactly match typed flows")
            for direction, bindings in (("input", op.input_bindings),
                                        ("output", op.output_bindings)):
                io_flows = [flow for flow in self.flows
                            if flow.kind.endswith("_" + direction)
                            and (flow.target if direction == "input" else flow.source) == op.id]
                _validate_alternatives(io_flows, f"Operation {op.id} {direction}")
                if bindings is None:
                    continue
                by_state = {(flow.source if direction == "input" else flow.target): flow
                            for flow in io_flows}
                for binding in bindings:
                    flow = by_state[binding.state_id]
                    if (flow.association != "source_qualified"
                            or any(getattr(flow, field) != getattr(binding, field)
                                   for field in ("basis", "condition", "selection",
                                                 "alternative_group", "evidence"))):
                        raise ValueError(f"Operation {op.id}: {direction} binding for "
                                         f"{binding.state_id} differs from its typed flow")
        indegrees = dict.fromkeys(states.keys() | operations.keys(), 0)
        for targets in edges.values():
            for target in targets:
                indegrees[target] += 1
        ready = deque(node for node, degree in indegrees.items() if degree == 0)
        visited = 0
        while ready:
            visited += 1
            for target in edges[ready.popleft()]:
                indegrees[target] -= 1
                if indegrees[target] == 0:
                    ready.append(target)
        if visited != len(indegrees):
            raise ValueError("Workflow v1 rejects self-dependency and routed cycles; encode loops "
                             "inside an operation action")
        return self


class WorkflowDraft(StrictModel):
    """Explicit scientific interfaces before the host materializes their typed I/O flows."""

    # Shared fields retain exactly the same meaning as the assembled Workflow.
    format_version: Literal["protocol_workflow/1.0"] = Field(default="protocol_workflow/1.0",
        description=Workflow.model_fields["format_version"].description)
    method_id: Text = Field(description=Workflow.model_fields["method_id"].description)
    title: Text = Field(description=Workflow.model_fields["title"].description)
    scope: Text = Field(description=Workflow.model_fields["scope"].description)
    variant: Text = Field(description=Workflow.model_fields["variant"].description)
    states: Annotated[tuple[State, ...], Field(min_length=1)] = Field(
        description=Workflow.model_fields["states"].description)
    operations: Annotated[tuple[Operation, ...], Field(min_length=1)] = Field(
        description=Workflow.model_fields["operations"].description)
    ordering_flows: tuple[Flow, ...] = Field(default=(), description=
        "Only source-supported precedence/control relations between Operations. Material/data "
        "I/O Flows are assembled from explicit Operation bindings; do not derive ordering from arrays.")
    modules: Annotated[tuple[Module, ...], Field(min_length=1)] = Field(
        description=Workflow.model_fields["modules"].description)
    unresolved: tuple[Gap, ...] = Field(description=Workflow.model_fields["unresolved"].description)
    gap_resolutions: tuple[GapResolution, ...] = Field(default=(),
        description=Workflow.model_fields["gap_resolutions"].description)

    @model_validator(mode="after")
    def ordering_only(self) -> WorkflowDraft:
        _unique_resolution_keys(self.gap_resolutions)
        if any(flow.kind not in {"precedence", "control"} for flow in self.ordering_flows):
            raise ValueError("Draft ordering_flows may contain only precedence/control relations")
        return self


def validate_io_bindings(document: Workflow | WorkflowDraft, *,
                         require_bindings: bool = False) -> list[str]:
    """Optional policy gate; historical records are not silently requalified.

    Drafts need authored per-direction bindings. Full workflows may instead have
    directly authored, source-qualified flows. Structural consistency is enforced
    by the models regardless of this opt-in completeness check.
    """
    if not require_bindings:
        return []
    if isinstance(document, Workflow):
        return [f"Flow {flow.id}: exact I/O association is not source-qualified"
                for flow in document.flows
                if flow.kind not in {"precedence", "control"}
                and flow.association != "source_qualified"]
    return [f"Operation {op.id}: explicit {direction}_bindings required"
            for op in document.operations
            for direction, states, bindings in (("input", op.inputs, op.input_bindings),
                                                 ("output", op.outputs, op.output_bindings))
            if states and bindings is None]


class Check(StrictModel):
    target_id: Text
    verdict: Literal["supported", "unsupported", "unresolved"]
    rationale: Text
    evidence: tuple[Evidence, ...]


class Omission(StrictModel):
    description: Text
    blocking: bool = Field(description="Whether the omission blocks the scoped scientific claim; "
                           "false does not cancel a required revise_record action.")
    action: Literal["revise_record", "retrieve_evidence", "advisory"] = Field(
        description="Required disposition: revise_record requires a record correction regardless "
                    "of blocking; never use it for optional prose suggestions. retrieve_evidence "
                    "stops scoped support only when blocking is true. advisory retains an "
                    "optional qualification or execution-detail uncertainty that does not "
                    "change any asserted fact or required scoped obligation; it never blocks.")
    evidence: tuple[Evidence, ...]

    @model_validator(mode="after")
    def cited_record_repair(self) -> Omission:
        if self.action == "revise_record" and not self.evidence:
            raise ValueError("revise_record omission requires nonempty source evidence")
        if self.action == "advisory" and self.blocking:
            raise ValueError("An advisory cannot block the declared scope")
        return self


class WorkflowReview(StrictModel):
    workflow_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_packet_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    inspected_passages: tuple[PassageRef, ...]
    checks: tuple[Check, ...]
    omissions: tuple[Omission, ...]
    notes: tuple[str, ...]


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def digest(model_or_dict: BaseModel | dict[str, Any]) -> str:
    content = json.dumps(_plain(model_or_dict), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _targets(workflow: Workflow) -> dict[str, State | Operation | Flow | GapResolution]:
    targets = {f"{kind}:{item.id}": item for kind, items in (
        ("state", workflow.states), ("operation", workflow.operations), ("flow", workflow.flows)
    ) for item in items}
    targets.update({f"resolution:{item.reader_role}:{item.gap_id}": item
                    for item in workflow.gap_resolutions})
    return targets


def target_ids(workflow: Workflow) -> set[str]:
    return set(_targets(workflow))


def _walk(value: Any, path: str = "$"):
    value = _plain(value)
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")


def validate_evidence(document: BaseModel | dict[str, Any], packet: dict) -> list[str]:
    """Check references and exact quotes after whitespace normalization, not truth."""
    passages: dict[tuple[str, str], str] = {}
    errors = []
    for source in packet.get("sources", []):
        for passage in source.get("passages", []):
            key = (source["source_id"], passage["passage_id"])
            if key in passages:
                errors.append(f"Packet contains duplicate passage reference: {key}")
            passages[key] = " ".join(passage["text"].split())
    for path, item in _walk(document):
        if "source_id" not in item and "passage_id" not in item:
            continue
        key = (item.get("source_id"), item.get("passage_id"))
        if key not in passages:
            errors.append(f"{path}: unknown passage reference: {key}")
        elif "quote" in item:
            quote = " ".join(item["quote"].split())
            if not quote or quote not in passages[key]:
                errors.append(f"{path}: quote is not present in cited passage {key}")
    return errors


def validate_review(review: WorkflowReview, workflow: Workflow, packet: dict) -> list[str]:
    """Validate review integrity; a valid negative review still fails the outcome gate."""
    errors = []
    if review.source_packet_sha256 != digest(packet):
        errors.append("Review source_packet_sha256 does not match the frozen source packet")
    errors.extend(validate_evidence(workflow, packet) + validate_evidence(review, packet))
    if review.workflow_sha256 != digest(workflow):
        errors.append("Review workflow_sha256 does not match the workflow")
    targets = _targets(workflow)
    checked = [check.target_id for check in review.checks]
    if len(checked) != len(set(checked)):
        errors.append("Review contains duplicate target checks")
    if missing := targets.keys() - set(checked):
        errors.append(f"Review missing targets: {sorted(missing)}")
    if extra := set(checked) - targets.keys():
        errors.append(f"Review contains unknown targets: {sorted(extra)}")
    inspected = {(ref.source_id, ref.passage_id) for ref in review.inspected_passages}
    if not inspected:
        errors.append("Review requires nonempty inspected_passages")
    for path, item in _walk(review):
        if "quote" in item and (item.get("source_id"), item.get("passage_id")) not in inspected:
            errors.append(f"{path}: review evidence passage was not inspected")
    for check in review.checks:
        if not check.evidence:
            errors.append(f"Check {check.target_id}: requires nonempty evidence")
        if check.verdict == "supported" and check.target_id in targets:
            target = targets[check.target_id]
            if isinstance(target, Flow) and target.association == "unqualified":
                errors.append(f"Check {check.target_id}: unqualified I/O must be qualified "
                              "in the workflow before it can be supported")
            declared = {(ev.source_id, ev.passage_id) for ev in targets[check.target_id].evidence}
            cited = {(ev.source_id, ev.passage_id) for ev in check.evidence}
            if not declared & cited:
                errors.append(f"Check {check.target_id}: supported evidence must overlap declared "
                              "target evidence passages")
            if isinstance(target, Operation):
                for index, resource in enumerate(target.resources):
                    support = {(ev.source_id, ev.passage_id) for ev in resource.evidence}
                    if not support & cited:
                        errors.append(f"Check {check.target_id}: resource {index} evidence "
                                      "passage must be cited and inspected")
    return errors


def validate_reader_gaps(workflow: Workflow, reports: dict[str, ReadingReport],
                         packet: dict) -> list[str]:
    """Require visible gap disposition and provenance, without judging resolution truth."""
    errors = validate_evidence({"gap_resolutions": workflow.gap_resolutions}, packet)
    gaps = {}
    for role, report in reports.items():
        if role not in {"chemistry", "information"} or report.role != role:
            errors.append(f"Reader report role does not match: {role}")
        for gap in report.unresolved:
            key = (role, gap.id)
            if key in gaps:
                errors.append(f"Reader {role}: duplicate gap ID {gap.id}")
            gaps[key] = gap
    resolutions = Counter((item.reader_role, item.gap_id) for item in workflow.gap_resolutions)
    for key, count in resolutions.items():
        if key not in gaps:
            errors.append(f"Gap resolution references unknown reader gap: {key}")
        if count != 1:
            errors.append(f"Duplicate gap resolutions: {key}")
    retained = {gap.id: gap for gap in workflow.unresolved}
    for ident in retained:
        role, _, gap_id = ident.partition(":")
        if role in {"chemistry", "information"} and (role, gap_id) not in gaps:
            errors.append(f"Retained gap references unknown reader gap: {ident}")
    for key, gap in gaps.items():
        ident = f"{key[0]}:{key[1]}"
        if int(ident in retained) + resolutions[key] != 1:
            errors.append(f"Reader gap {ident}: retain or resolve exactly once")
        if ident in retained and retained[ident].blocking != gap.blocking:
            errors.append(f"Reader gap {ident}: retained blocking flag must remain unchanged")
    return errors


def validate_repair_gap_references(workflow: Workflow, previous: Workflow) -> list[str]:
    """Bound new reader references to the seed, without certifying prior readers.

    Repair-only runs do not have fresh ReadingReports. Existing resolutions and
    namespaced unresolved gaps preserve their historical keys, but a workflow-
    authored gap cannot be relabeled as a reader gap. Source review still decides
    whether an answer or a scope correction is scientifically justified.
    """
    known = {(item.reader_role, item.gap_id) for item in previous.gap_resolutions}
    for gap in previous.unresolved:
        role, separator, gap_id = gap.id.partition(":")
        if role in {"chemistry", "information"} and separator and gap_id:
            known.add((role, gap_id))
    return [f"Gap resolution references unknown prior reader gap: {(item.reader_role, item.gap_id)}"
            for item in workflow.gap_resolutions
            if (item.reader_role, item.gap_id) not in known]


def review_outcome(review: WorkflowReview, workflow: Workflow) -> str:
    """Host-derived review status, conditional on separate integrity validation."""
    if any(check.verdict == "unsupported" for check in review.checks) or any(
        omission.action == "revise_record" for omission in review.omissions
    ):
        return "needs_revision"
    if any(check.verdict == "unresolved" for check in review.checks) or any(
        gap.blocking for gap in workflow.unresolved
    ) or any(
        omission.blocking and omission.action == "retrieve_evidence"
        for omission in review.omissions
    ):
        return "research_needed"
    return "supported"

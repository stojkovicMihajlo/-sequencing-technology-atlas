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
    source_id: Text
    passage_id: Text


class Evidence(PassageRef):
    quote: Annotated[Text, Field(max_length=600)]
    support: Literal["reported", "inferred"]
    rationale: Text


EvidenceSet = Annotated[tuple[Evidence, ...], Field(min_length=1)]


class Finding(StrictModel):
    id: Text
    description: Text
    evidence: EvidenceSet


class Gap(StrictModel):
    id: Text
    description: Text
    blocking: bool
    next_evidence: Text


class GapResolution(StrictModel):
    """An evidence-backed answer or scope exclusion, still requiring source review."""

    reader_role: Literal["chemistry", "information"]
    gap_id: Text
    rationale: Text
    evidence: EvidenceSet


def _unique_resolution_keys(resolutions: tuple[GapResolution, ...]) -> None:
    keys = [(item.reader_role, item.gap_id) for item in resolutions]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate gap resolutions for the same reader_role/gap_id")


class ReadingReport(StrictModel):
    role: Literal["chemistry", "information"]
    inspected_passages: tuple[PassageRef, ...]
    findings: Annotated[tuple[Finding, ...], Field(min_length=1)]
    unresolved: tuple[Gap, ...]


class Resource(StrictModel):
    kind: Literal["reagent", "equipment", "software", "condition"]
    description: Text
    evidence: EvidenceSet


class State(StrictModel):
    id: Text
    kind: Literal["material", "data"]
    description: Text
    evidence: EvidenceSet


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

    state_id: Text
    basis: Text
    condition: Text | None = None
    selection: IOSelection = "required"
    alternative_group: Text | None = None
    evidence: EvidenceSet

    @model_validator(mode="after")
    def selection_semantics(self) -> IOBinding:
        _validate_selection(self.selection, self.alternative_group, self.condition)
        return self


class Operation(StrictModel):
    id: Text
    name: Text
    action: Text
    inputs: tuple[Text, ...]
    outputs: Annotated[tuple[Text, ...], Field(min_length=1)]
    resources: tuple[Resource, ...]
    information_effect: Text
    evidence: EvidenceSet
    input_bindings: tuple[IOBinding, ...] | None = None
    output_bindings: tuple[IOBinding, ...] | None = None

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
    id: Text
    source: Text
    target: Text
    kind: Literal[
        "material_input", "material_output", "data_input", "data_output", "precedence", "control"
    ]
    basis: Text
    condition: Text | None = None
    evidence: tuple[Evidence, ...] = Field(description=
        "Support for this exact association. Empty only for explicitly unqualified I/O; "
        "an operation citation is not automatically evidence for each of its connections.")
    association: Literal["source_qualified", "unqualified"] | None = None
    selection: IOSelection | None = None
    alternative_group: Text | None = None

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
    id: Text
    name: Text
    operation_ids: Annotated[tuple[Text, ...], Field(min_length=1)]
    boundary_rationale: Text


class Workflow(StrictModel):
    format_version: Literal["protocol_workflow/1.0"] = "protocol_workflow/1.0"
    method_id: Text
    title: Text
    scope: Text
    variant: Text
    states: Annotated[tuple[State, ...], Field(min_length=1)]
    operations: Annotated[tuple[Operation, ...], Field(min_length=1)]
    flows: Annotated[tuple[Flow, ...], Field(min_length=1)]
    modules: Annotated[tuple[Module, ...], Field(min_length=1)]
    unresolved: tuple[Gap, ...]
    gap_resolutions: tuple[GapResolution, ...] = ()

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

    format_version: Literal["protocol_workflow/1.0"] = "protocol_workflow/1.0"
    method_id: Text
    title: Text
    scope: Text
    variant: Text
    states: Annotated[tuple[State, ...], Field(min_length=1)]
    operations: Annotated[tuple[Operation, ...], Field(min_length=1)]
    ordering_flows: tuple[Flow, ...] = ()
    modules: Annotated[tuple[Module, ...], Field(min_length=1)]
    unresolved: tuple[Gap, ...]
    gap_resolutions: tuple[GapResolution, ...] = ()

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

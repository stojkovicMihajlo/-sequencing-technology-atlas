"""Source-bounded innovation cases and functional claims, without feasibility inference.

These records are additional curation, not automatically derived primitives from
method workflow strings. Matching is lexical; context transfer stays an open question.
"""

from __future__ import annotations

from collections import deque
from typing import Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel


class CaseSource(StrictModel):
    paper_id: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    local_path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    review_scope: str = Field(min_length=1)

    @model_validator(mode="after")
    def coherent_locator(self) -> CaseSource:
        if bool(self.local_path) != bool(self.sha256):
            raise ValueError("Local case sources require both a path and a content hash")
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("A line locator needs both bounds")
        if self.local_path and self.start_line is None:
            raise ValueError("Cached case sources require explicit line bounds")
        if self.start_line is not None and self.end_line < self.start_line:
            raise ValueError("Reversed source line bounds")
        return self


class DocumentedOperation(StrictModel):
    id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    input_state: str | None = None
    output_state: str | None = None
    information_effect: str | None = None
    source_indices: tuple[int, ...] = Field(min_length=1)


class FunctionalPrecedent(StrictModel):
    id: str = Field(min_length=1)
    required_effect: str = Field(min_length=1)
    implementation: str = Field(min_length=1)
    tested_context: str = Field(min_length=1)
    conditions: tuple[str, ...] = ()
    input_state: str | None = None
    output_state: str | None = None
    information_preserved: str | None = None
    limitations: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    operation_ids: tuple[str, ...] = Field(min_length=1)
    source_indices: tuple[int, ...] = Field(min_length=1)
    support: Literal["reported_demonstration", "reported_mechanistic_rationale"]
    search_terms: tuple[str, ...] = ()


class DocumentedDependency(StrictModel):
    upstream: str = Field(min_length=1)
    downstream: str = Field(min_length=1)
    requirement: str = Field(min_length=1)
    support: Literal["reported_workflow", "reported_mechanistic_rationale", "curator_inference"]
    source_indices: tuple[int, ...] = Field(min_length=1)


class InnovationCase(StrictModel):
    id: str = Field(min_length=1)
    method_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    limitation: str = Field(min_length=1)
    design_move: str = Field(min_length=1)
    coordinated_changes: tuple[str, ...] = Field(min_length=1)
    reported_comparison: str | None = None
    reported_gain: str | None = None
    tradeoffs: tuple[str, ...] = ()
    causal_boundary: str = Field(min_length=1)
    sources: tuple[CaseSource, ...] = Field(min_length=1)
    field_source_indices: dict[str, tuple[int, ...]]
    operations: tuple[DocumentedOperation, ...] = Field(min_length=1)
    functions: tuple[FunctionalPrecedent, ...] = Field(min_length=1)
    dependencies: tuple[DocumentedDependency, ...] = ()
    unknowns: tuple[str, ...] = ()
    verification: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> InnovationCase:
        ids = [item.id for item in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate case operation ID")
        function_ids = [item.id for item in self.functions]
        if len(function_ids) != len(set(function_ids)):
            raise ValueError("Duplicate functional precedent ID")
        known = set(ids)
        lists = list(self.field_source_indices.values())
        lists.extend(item.source_indices for item in (*self.operations, *self.functions))
        lists.extend(item.source_indices for item in self.dependencies)
        for field in ("limitation", "design_move", "coordinated_changes"):
            if not self.field_source_indices.get(field):
                raise ValueError(f"Case field {field} needs a source locator")
        for field in ("reported_comparison", "reported_gain", "tradeoffs"):
            if getattr(self, field) and not self.field_source_indices.get(field):
                raise ValueError(f"Case field {field} needs a source locator")
        if any(
            not values or any(i < 0 or i >= len(self.sources) for i in values) for values in lists
        ):
            raise ValueError("Invalid case source index")
        for function in self.functions:
            if not set(function.operation_ids) <= known:
                raise ValueError("Functional claim references an unknown operation")
        adjacency = {op: [] for op in ids}
        indegree = dict.fromkeys(ids, 0)
        pairs = set()
        for dep in self.dependencies:
            if dep.upstream not in known or dep.downstream not in known:
                raise ValueError("Dependency references an unknown operation")
            pair = (dep.upstream, dep.downstream)
            if pair in pairs:
                raise ValueError("Duplicate case dependency")
            pairs.add(pair)
            adjacency[dep.upstream].append(dep.downstream)
            indegree[dep.downstream] += 1
        ready = deque(op for op, degree in indegree.items() if degree == 0)
        visited = 0
        while ready:
            node = ready.popleft()
            visited += 1
            for child in adjacency[node]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if visited != len(ids):
            raise ValueError("Case workflow dependencies must be acyclic")
        return self


class DesignCaseIndex:
    """Deterministic retrieval and dependency traversal of explicitly curated cases."""

    def __init__(self, raw: list[dict], method_ids: set[str], paper_ids: set[str]):
        self.cases = {item.id: item for item in map(InnovationCase.model_validate, raw)}
        if len(self.cases) != len(raw):
            raise ValueError("Duplicate innovation case ID")
        functions = [function.id for case in self.cases.values() for function in case.functions]
        if len(functions) != len(set(functions)):
            raise ValueError("Functional IDs must be distinct across cases")
        for case in self.cases.values():
            if case.method_id not in method_ids:
                raise ValueError(f"{case.id}: unknown method")
            if any(source.paper_id not in paper_ids for source in case.sources):
                raise ValueError(f"{case.id}: unknown source paper")

    def for_method(self, method_id: str) -> list[dict]:
        return [
            case.model_dump(mode="json")
            for case in self.cases.values()
            if case.method_id == method_id
        ]

    def trace(self, case_id: str, changed: tuple[str, ...], proposed_change: str) -> dict:
        case = self.cases.get(case_id)
        if case is None:
            return {"error": {"code": "not_found", "message": f"Unknown case {case_id}"}}
        operations = {item.id: item for item in case.operations}
        missing = set(changed) - operations.keys()
        if missing:
            return {
                "error": {
                    "code": "invalid_query",
                    "message": f"Unknown operations: {sorted(missing)}",
                }
            }
        affected = set(changed)
        pending = deque(changed)
        while pending:
            node = pending.popleft()
            for dep in case.dependencies:
                if dep.upstream == node and dep.downstream not in affected:
                    affected.add(dep.downstream)
                    pending.append(dep.downstream)
        dependencies = [
            dep.model_dump(mode="json")
            for dep in case.dependencies
            if dep.upstream in affected or dep.downstream in affected
        ]
        functions = [
            item.model_dump(mode="json")
            for item in case.functions
            if set(item.operation_ids) & affected
        ]
        return {
            "case_id": case.id,
            "method_id": case.method_id,
            "proposed_change": proposed_change,
            "proposed_change_role": "caller_hypothesis_not_parsed_or_validated",
            "changed_operation_ids": list(changed),
            "affected_operation_ids": sorted(affected),
            "operations": [operations[op].model_dump(mode="json") for op in sorted(affected)],
            "documented_requirements_to_recheck": dependencies,
            "affected_functional_claims": functions,
            "sources": [source.model_dump(mode="json") for source in case.sources],
            "verification": case.verification,
            "unknowns": list(case.unknowns),
            "compatibility_verdict": "not_assessed",
            "coverage_note": (
                "Traverses only curated dependencies. Missing paths are unknown; "
                "affected does not mean invalidated."
            ),
        }

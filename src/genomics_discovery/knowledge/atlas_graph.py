"""Revision-pinned queries over curated technologies, relationship claims and sources.

The snapshot is a knowledge base, not a chemical simulator or a novelty oracle. Its
evidence records are curator annotations. They do not become primary source passages
merely because a caller retrieves them through a tool.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

from genomics_discovery.knowledge.design_cases import DesignCaseIndex
from genomics_discovery.knowledge.operation_concepts import OperationConceptIndex
from genomics_discovery.knowledge.operation_groups import OperationGroupIndex

_STOP = {"a", "an", "and", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
_FIELDS = (
    "name",
    "measurement",
    "biological_input",
    "tested_context",
    "mechanism",
    "bottleneck",
    "innovation",
    "limitations",
    "materials",
    "workflow",
)
_COMPONENT_FIELDS = {
    "operation": ("description", "steps", "materials", "equipment", "parameters"),
    "function": ("purpose", "function_tags", "information_effect", "information_preserved"),
    "input": ("input_state", "input_state_detail"),
    "output": ("output_state", "output_state_detail"),
    "interface": ("interfaces", "dependencies_and_interfaces"),
    "mechanism": ("mechanisms",),
    "concept": ("operation_concepts",),
}
_KINDS = {"method_development", "component_reuse", "comparison", "analogy", "uncertain_relation"}
_STATUSES = {
    "supported",
    "awaiting_evidence",
    "unclassified_candidate",
    "not_established",
    "withheld",
}
_SCOPE = (
    "Curated secondary technology profiles and relationship annotations, not original "
    "source passages or experimental validation. Component reuse, comparison and analogy "
    "are distinct from ancestry; "
    "missing connections do not establish novelty. Legacy review_status labels are separate "
    "from the source-targeted verification records. Inspect primary sources for decisive claims."
)


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_text(item) for item in value)
    return str(value or "")


def _terms(value: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _text(value).casefold())) - _STOP


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _internal_handoff_navigation(record: dict, reference: dict) -> dict:
    """Describe a recorded list without adding its internal edges to traversal."""
    pointer = reference["json_pointer"]
    if "source_module" in record:
        source = record["source_module"]
        pointer += "/source_module"
        if not isinstance(source, dict):
            return {"state": "unavailable", "count": None,
                    "container_ref": {**reference, "json_pointer": pointer}}
    else:
        source = record
    if "internal_handoffs" not in source:
        return {"state": "not_recorded", "count": None}
    value = source["internal_handoffs"]
    return {
        "state": "recorded" if isinstance(value, list) else (
            "null" if value is None else "malformed"),
        "count": len(value) if isinstance(value, list) else None,
        "record_ref": {**reference, "json_pointer": pointer + "/internal_handoffs"},
        "details_require_drilldown": bool(value),
    }


class TechnologyKnowledgeGraph:
    """Immutable snapshot with method, claim, event, evidence and lexical search indexes."""

    def __init__(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise ValueError("Graph snapshot must be a JSON object")
        if "data" in data:
            payload = data["data"]
            if not isinstance(payload, dict) or data.get("snapshot_sha256") != _digest(payload):
                raise ValueError("Exported graph snapshot hash does not match its data")
            data = payload
        self._data = copy.deepcopy(data)
        self._revision_id = str(data.get("revision_id") or "unversioned")
        self._snapshot_sha256 = _digest(data)
        self._methods = self._index("methods", "id")
        self._claims = self._index("claims", "id")
        self._events = self._index("change_events", "id")
        self._evidence = self._index("evidence", "id")
        self._papers = self._index("papers", "paper_id")
        self._component_chains = self._index("component_chains", "method_id")
        self._component_profiles = self._index("component_profiles", "profile_id")
        self._operation_concepts = OperationConceptIndex(self._data)
        self._operation_groups = OperationGroupIndex(self._data)
        self._module_concepts = defaultdict(list)
        for use in self._operation_concepts.uses:
            self._module_concepts[(use["profile_id"], use["module_id"])].append(use)
        self._design_cases = DesignCaseIndex(
            self._data.get("innovation_cases", []), set(self._methods), set(self._papers)
        )
        self._adjacency: dict[str, list[str]] = defaultdict(list)
        for claim in self._claims.values():
            for endpoint in {claim.get("source"), claim.get("target")} - {None}:
                self._adjacency[endpoint].append(claim["id"])
        self._field_terms: dict[str, dict[str, set[str]]] = {}
        self._inverted: dict[str, set[str]] = defaultdict(set)
        for method_id, method in self._methods.items():
            fields = {field: _terms(method.get(field)) for field in _FIELDS}
            fields["name"] |= _terms([method_id, method.get("aliases", [])])
            self._field_terms[method_id] = fields
            for token in set().union(*fields.values()):
                self._inverted[token].add(method_id)
        self._operations = []
        for method_id, method in self._methods.items():
            for position, action in enumerate(method.get("workflow") or [], start=1):
                self._operations.append(
                    {
                        "operation_id": "operation:" + _digest([method_id, position, action])[:20],
                        "method_id": method_id,
                        "position_in_profile": position,
                        "action": action,
                        "input_material_states": None,
                        "output_material_states": None,
                        "information_effect": None,
                        "preconditions": None,
                        "tested_context": None,
                        "source_support": "curator_profile_annotation",
                        "function_verification": "not_assessed_per_operation",
                        "paper_ids": method.get("paper_ids", []),
                    }
                )

    def _index(self, field: str, id_field: str) -> dict[str, dict[str, Any]]:
        records = self._data.get(field, [])
        if not isinstance(records, list):
            raise ValueError(f"Graph {field} must be a list")
        indexed = {}
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get(id_field), str):
                raise ValueError(f"Graph {field} record needs a string {id_field}")
            identifier = record[id_field]
            if not identifier or identifier in indexed:
                raise ValueError(f"Duplicate or empty {field} identifier: {identifier}")
            indexed[identifier] = record
        return indexed

    @property
    def revision_id(self) -> str:
        return self._revision_id

    @property
    def snapshot_sha256(self) -> str:
        return self._snapshot_sha256

    @classmethod
    def from_path(cls, path: Path) -> TechnologyKnowledgeGraph:
        graph = cls(json.loads(Path(path).read_text(encoding="utf-8")))
        result = graph.validate()
        if not result["valid"]:
            raise ValueError("Invalid graph snapshot: " + "; ".join(result["errors"][:10]))
        return graph

    @classmethod
    def from_sibling_atlas(cls) -> TechnologyKnowledgeGraph | None:
        repo = Path(__file__).resolve().parents[3]
        path = repo.parent / "technology_atlas/v2/output/knowledge_graph.json"
        return cls.from_path(path) if path.exists() else None

    def _result(self, **values: Any) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "revision_id": self.revision_id,
                "snapshot_sha256": self.snapshot_sha256,
                "scope_note": _SCOPE,
                **values,
            }
        )

    def _error(self, code: str, message: str) -> dict[str, Any]:
        return self._result(error={"code": code, "message": message})

    def _page(
        self, rows: list[dict], query: dict, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 20:
            return self._error("invalid_arguments", "limit must be an integer from 1 to 20")
        query_hash = _digest(query)
        offset = 0
        if cursor is not None:
            if not isinstance(cursor, str) or len(cursor) > 2000:
                return self._error("invalid_cursor", "Cursor must be a bounded string")
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
                offset = decoded["offset"]
                if (
                    decoded["snapshot"] != self.snapshot_sha256
                    or decoded["query"] != query_hash
                    or type(offset) is not int
                    or not 0 <= offset <= len(rows)
                ):
                    raise ValueError("Cursor belongs to another snapshot or query")
            except (ValueError, TypeError, KeyError, UnicodeError):
                return self._error(
                    "invalid_cursor", "Cursor is invalid for this snapshot and query"
                )
        end = min(offset + limit, len(rows))
        next_cursor = None
        if end < len(rows):
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "snapshot": self.snapshot_sha256,
                        "query": query_hash,
                        "offset": end,
                    },
                    separators=(",", ":"),
                ).encode()
            ).decode("ascii")
        return self._result(
            results=rows[offset:end],
            pagination={
                "offset": offset,
                "returned": end - offset,
                "total_matches": len(rows),
                "truncated": end < len(rows),
                "next_cursor": next_cursor,
            },
        )

    @staticmethod
    def _kind(claim: dict) -> str:
        return claim.get("relationship_kind") or "uncertain_relation"

    def _method_card(self, method: dict) -> dict:
        audit = method.get("scientific_audit")
        return {
            key: method.get(key)
            for key in (
                "id",
                "name",
                "aliases",
                "entity_kind",
                "assigned_categories",
                "original_categories",
                "review_status",
                "year",
                "year_basis",
                "origin_paper_ids",
                "paper_ids",
            )
        } | {
            "biological_input": str(method.get("biological_input") or "")[:500],
            "tested_context": method.get("tested_context"),
            "measurement": str(method.get("measurement") or "")[:700],
            "profile_scope": "conceptual; consult get_method for limitations and review scope",
            "verification": copy.deepcopy(method.get("verification")),
            "scientific_audit_summary": (
                {
                    key: audit.get(key)
                    for key in (
                        "audit_id",
                        "review_level",
                        "complete_main_article_read_in_this_audit",
                        "complete_cached_main_text_read_in_this_audit",
                        "all_supplements_read_in_this_audit",
                        "report_path",
                    )
                }
                if audit
                else None
            ),
        }

    def search_methods(
        self,
        query: str,
        *,
        category: str | None = None,
        category_source: str = "curator",
        review_status: str | None = None,
        entity_kind: str | None = None,
        field: str = "any",
        limit: int = 8,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not _terms(query):
            return self._error("invalid_arguments", "query needs at least one searchable term")
        if field not in (*_FIELDS, "any") or category_source not in ("curator", "illumina"):
            return self._error("invalid_arguments", "Unknown search field or category source")
        query_terms = _terms(query)
        candidates: set[str] = set()
        matched_tokens: dict[str, set[str]] = defaultdict(set)
        for term in query_terms:
            # Prefix search helps discovery; it never resolves or merges method identities.
            for token in self._inverted:
                if token == term or (len(term) >= 3 and token.startswith(term)):
                    candidates |= self._inverted[token]
                    for method_id in self._inverted[token]:
                        matched_tokens[method_id].add(token)
        rows = []
        weights = {
            "name": 4, "measurement": 3, "biological_input": 3,
            "tested_context": 3, "bottleneck": 2,
        }
        for method_id in candidates:
            method = self._methods[method_id]
            categories = (
                method.get("assigned_categories", [])
                if category_source == "curator"
                else [value for path in method.get("original_categories", []) for value in path]
            )
            if category and category not in categories:
                continue
            if review_status and method.get("review_status") != review_status:
                continue
            if entity_kind and method.get("entity_kind") != entity_kind:
                continue
            selected_fields = _FIELDS if field == "any" else (field,)
            matched_fields, rank = [], 0.0
            for selected in selected_fields:
                found = matched_tokens[method_id] & self._field_terms[method_id][selected]
                if found:
                    matched_fields.append(selected)
                    rank += weights.get(selected, 1) * sum(
                        1 + math.log((len(self._methods) + 1) / (len(self._inverted[token]) + 1))
                        for token in found
                    )
            normalized = re.sub(r"[^a-z0-9]", "", query.casefold())
            names = [method_id, method.get("name", ""), *method.get("aliases", [])]
            exact_name = normalized in {re.sub(r"[^a-z0-9]", "", name.casefold()) for name in names}
            if exact_name and field in ("any", "name"):
                rank += 20
            if rank:
                rows.append(
                    {
                        "method": self._method_card(method),
                        "score": round(rank, 5),
                        "matched_fields": matched_fields,
                    }
                )
        rows.sort(key=lambda row: (-row["score"], row["method"]["id"]))
        result = self._page(
            rows,
            {
                "operation": "search_methods",
                "query": query,
                "category": category,
                "category_source": category_source,
                "review_status": review_status,
                "entity_kind": entity_kind,
                "field": field,
            },
            limit,
            cursor,
        )
        if "error" not in result:
            result["retrieval_method"] = (
                "Weighted lexical and prefix retrieval; not compatibility scoring"
            )
        return result

    def get_method(self, method_id: str) -> dict[str, Any]:
        method = self._methods.get(method_id)
        if method is None:
            return self._error("not_found", f"Unknown method ID: {method_id}")
        related = [self._claims[item] for item in self._adjacency.get(method_id, [])]
        return self._result(
            method=method,
            facets=[
                item
                for item in self._data.get("facet_assignments", [])
                if item.get("method_id") == method_id
            ],
            relation_counts=dict(
                Counter(self._kind(item) for item in related if item.get("status") == "supported")
            ),
            uncertain_claim_ids=[
                item["id"] for item in related if item.get("status") != "supported"
            ],
            innovation_case_ids=[
                case.id for case in self._design_cases.cases.values() if case.method_id == method_id
            ],
            event_ids=[
                event["id"]
                for event in self._events.values()
                if (
                    event.get("output_method_id") == method_id
                    or any(item.get("method_id") == method_id for item in event.get("inputs", []))
                )
            ],
        )

    def _workflow_record(self, method_id: str) -> dict:
        return {
            "method": self._method_card(self._methods[method_id]),
            "operations": [item for item in self._operations if item["method_id"] == method_id],
            "representation_scope": (
                "Ordered conceptual actions from the curator profile. This is not a complete "
                "protocol or an experimentally characterized primitive registry. Branches, "
                "controls, dependencies and operational parameters need primary-source curation."
            ),
            "unknown_state_instruction": (
                "Null material states, preconditions and information effects are unknown, "
                "not empty requirements. Reopen the original methods and supplements before "
                "using an action as functional precedent or assessing an interface."
            ),
        }

    def get_workflow(self, method_id: str) -> dict[str, Any]:
        """Expose conceptual actions without turning profile prose into verified primitives."""
        if method_id not in self._methods:
            return self._error("not_found", f"Unknown method ID: {method_id}")
        return self._result(**self._workflow_record(method_id))

    def get_component_chain(
        self,
        method_id: str | None = None,
        *,
        profile_id: str | None = None,
        representation: str = "default",
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Inspect scoped components; unknown ports do not become empty requirements."""
        if (method_id is None) == (profile_id is None):
            return self._error("invalid_arguments", "Choose one method_id or profile_id")
        if representation not in {"default", "overview"} or (
            profile_id is not None and representation != "default"
        ):
            return self._error("invalid_arguments", "Overview applies to a method only")
        if method_id is not None:
            if method_id not in self._methods:
                return self._error("not_found", f"Unknown method ID: {method_id}")
            chain = self._component_chains.get(method_id)
        else:
            chain = self._component_profiles.get(profile_id)
        if chain is None:
            return self._error("not_curated", "No component representation in this snapshot")
        if representation == "overview":
            chain = chain.get("baseline_summary_chain", chain)
        chain = {key: value for key, value in chain.items() if key != "baseline_summary_chain"}
        pagination = None
        if limit is not None or cursor is not None:
            page = self._page(
                chain.get("components", []),
                {"operation": "get_component_chain", "method_id": method_id,
                 "profile_id": profile_id, "representation": representation},
                8 if limit is None else limit, cursor,
            )
            if "error" in page:
                return page
            chain = chain | {"components": page["results"]}
            pagination = page["pagination"] | {"unit": "complete_components"}
        return self._result(
            component_chain=chain,
            **({"pagination": pagination} if pagination is not None else {}),
            interpretation=(
                "Source-linked descriptions and component boundaries are curator records. "
                "next_in_summary is presentation order; typed connections retain their "
                "source scope and unknowns. Neither establishes transferable chemical "
                "compatibility, complete recipes or historical ancestry."
                " Component pages preserve complete records; follow next_cursor for the "
                "rest. Connections and topology describe the whole profile, including "
                "components on other pages."
            ),
        )

    def compare_protocols(
        self, profile_id: str, other_profile_id: str, *, module_matches=None,
        view: str = "full", limit: int = 8, cursor: str | None = None,
        comparison_scope: str = "whole_profile",
    ) -> dict[str, Any]:
        """Compare explicitly aligned scoped profiles without asserting scientific equivalence."""
        from genomics_discovery.knowledge.protocol_comparison import (
            compare_protocol_graphs,
            protocol_comparison_navigation,
        )

        left = self._component_profiles.get(profile_id)
        right = self._component_profiles.get(other_profile_id)
        if left is None or right is None:
            return self._error("not_curated", "Choose two indexed component profile IDs")
        if view not in {"full", "navigation"} or (view == "full" and cursor is not None):
            return self._error("invalid_arguments", "Choose full or navigation comparison view")
        if comparison_scope not in {"whole_profile", "module_fields"}:
            return self._error("invalid_arguments", "Choose whole_profile or module_fields scope")
        try:
            comparison = compare_protocol_graphs(
                left, right, module_matches=module_matches, max_differences=None
            )
        except (ValueError, TypeError) as exc:
            return self._error("invalid_arguments", str(exc))
        if comparison.get("error"):
            error = comparison["error"]
            return self._error(error["code"], error["message"])
        if comparison_scope == "module_fields":
            all_rows = comparison["differences"]
            kinds = {"module_field_changed", "module_field_uncertain", "module_provenance_changed"}
            selected = [row for row in all_rows if row["kind"] in kinds]
            comparison["whole_profile_summary"] = copy.deepcopy(comparison["summary"])
            comparison["differences"] = selected
            comparison["summary"] = {
                "difference_counts": dict(Counter(row["kind"] for row in selected)),
                "total_differences": len(selected), "returned_differences": len(selected),
                "truncated": False, "max_differences": None,
            }
            comparison["selection"] = {
                "comparison_scope": comparison_scope,
                "excluded_difference_counts": dict(Counter(row["kind"] for row in all_rows
                                                          if row["kind"] not in kinds)),
                "boundary": "Only fields of explicitly matched modules. Unaligned modules "
                            "and connections remain in whole_profile view; trace dependencies "
                            "to inspect surrounding interfaces. "
                            "No chemical equivalence is implied.",
            }
        if view == "navigation":
            comparison = protocol_comparison_navigation(
                comparison, left, right, snapshot_sha256=self.snapshot_sha256
            )
            page = self._page(comparison.pop("records"), {
                "operation": "compare_protocols", "profile_id": profile_id,
                "other_profile_id": other_profile_id, "module_matches": module_matches,
                "view": view, "comparison_scope": comparison_scope,
            }, limit, cursor)
            if "error" in page:
                return page
            comparison["records"] = page["results"]
            return self._result(
                comparison=comparison, profile_ids=[profile_id, other_profile_id],
                verification=[comparison["profiles"][side]["verification"]
                              for side in ("left", "right")],
                pagination=page["pagination"] | {"unit": "comparison_difference_records"},
            )
        return self._result(
            comparison=comparison,
            profile_ids=[profile_id, other_profile_id],
            verification=[left.get("verification"), right.get("verification")],
        )

    def trace_protocol_dependencies(
        self,
        profile_id: str,
        changed_module_ids: tuple[str, ...],
        *,
        relation_types: tuple[str, ...] = (
            "material", "information", "control", "ordering_requirement"
        ),
        direction: str = "downstream",
        max_depth: int = 8,
        view: str = "full",
        limit: int = 8,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Trace recorded dependencies within any indexed profile, with explicit limits."""
        from genomics_discovery.knowledge.protocol_dependencies import trace_protocol_dependencies

        profile = self._component_profiles.get(profile_id)
        if profile is None:
            return self._error("not_curated", "Choose an indexed component profile ID")
        if view == "full" and cursor is not None:
            return self._error("invalid_arguments", "Full traces use transport cursors in the tool")
        try:
            trace = trace_protocol_dependencies(
                profile,
                changed_module_ids,
                relation_types=relation_types,
                direction=direction,
                max_depth=max_depth,
                max_modules=100,
                max_edges=500,
                view=view,
                snapshot_sha256=self.snapshot_sha256 if view == "navigation" else None,
            )
        except (ValueError, TypeError) as exc:
            return self._error("invalid_arguments", str(exc))
        if trace.get("error"):
            error = trace["error"]
            return self._error(error["code"], error["message"])
        if view == "navigation":
            rows = []
            kinds = {
                "modules_requiring_recheck": "module",
                "documented_requirements_to_recheck": "connection",
                "witness_steps": "witness_step",
                "unclassified_connections": "unclassified_connection",
            }
            for field, kind in kinds.items():
                for item in trace.pop(field):
                    row = {"record_type": kind, **item}
                    if kind == "module":
                        reference = item["record_ref"]
                        field_name, index = reference["json_pointer"].strip("/").split("/")
                        record = profile[field_name][int(index)]
                        row["internal_handoffs"] = _internal_handoff_navigation(record, reference)
                    rows.append(row)
            trace["internal_handoff_scope"] = (
                "Traversal follows only between-module topology connections. Internal handoff "
                "descriptors require separate inspection; missing or zero recorded entries "
                "do not prove no internal interfaces or scientific independence."
            )
            page = self._page(rows, {
                "operation": "trace_protocol_dependencies", "profile_id": profile_id,
                "changed_module_ids": list(changed_module_ids),
                "relation_types": list(relation_types),
                "direction": direction, "max_depth": max_depth, "view": view,
            }, limit, cursor)
            if "error" in page:
                return page
            trace["records"] = page["results"]
            trace["navigation_record_counts"] = dict(Counter(row["record_type"] for row in rows))
            return self._result(
                dependency_trace=trace, profile_id=profile_id,
                verification=trace["profile_context"].get("verification"),
                pagination=page["pagination"] | {"unit": "navigation_records"},
            )
        return self._result(
            dependency_trace=trace,
            profile_id=profile_id,
            verification=profile.get("verification"),
        )

    def get_protocol_record(
        self, profile_id: str, snapshot_sha256: str, json_pointer: str
    ) -> dict[str, Any]:
        """Read one exact profile-relative JSON pointer in the referenced immutable snapshot."""
        if snapshot_sha256 != self.snapshot_sha256:
            return self._error("stale_snapshot", "Reference belongs to another graph snapshot")
        profile = self._component_profiles.get(profile_id)
        if profile is None:
            return self._error("not_curated", "Choose an indexed component profile ID")
        if not isinstance(json_pointer, str) or len(json_pointer) > 2000 or (
            json_pointer != "" and not json_pointer.startswith("/")
        ):
            return self._error("invalid_pointer", "Use a bounded profile-relative JSON pointer")
        value = profile
        try:
            for part in json_pointer.split("/")[1:] if json_pointer else ():
                if re.search(r"~(?:[^01]|$)", part):
                    raise ValueError("Invalid JSON pointer escape")
                key = part.replace("~1", "/").replace("~0", "~")
                if isinstance(value, dict):
                    value = value[key]
                elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key):
                    value = value[int(key)]
                else:
                    raise ValueError("Pointer does not resolve")
        except (KeyError, IndexError, ValueError):
            return self._error("invalid_pointer", "JSON pointer does not resolve in this profile")
        return self._result(
            protocol_record={"profile_id": profile_id, "snapshot_sha256": self.snapshot_sha256,
                             "json_pointer": json_pointer, "value": value},
            interpretation=(
                "Exact curated field/subtree; retrieval does not verify scientific support."
            ),
        )

    def search_components(
        self,
        query: str,
        *,
        method_id: str | None = None,
        component_field: str = "any",
        source_scoped_only: bool = False,
        limit: int = 8,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Search distinct contextual blocks, without canonicalizing shared words."""
        if not isinstance(query, str) or not _terms(query):
            return self._error("invalid_arguments", "query needs a searchable term")
        if method_id is not None and method_id not in self._methods:
            return self._error("not_found", f"Unknown method ID: {method_id}")
        if component_field != "any" and component_field not in _COMPONENT_FIELDS:
            return self._error("invalid_arguments", "Unknown component search field")
        if type(source_scoped_only) is not bool:
            return self._error("invalid_arguments", "source_scoped_only must be boolean")
        terms, rows = _terms(query), []
        # Keep alternate implementations searchable, including those that are not
        # a method's default. Distinct contextual profiles are never merged by text.
        chains = list(self._component_chains.values())
        default_ids = {chain.get("profile_id") for chain in chains}
        chains += [profile for pid, profile in self._component_profiles.items()
                   if pid not in default_ids]
        for chain in chains:
            method_ids = chain.get("method_ids") or (
                [chain["method_id"]] if chain.get("method_id") else [])
            if method_id is not None and method_id not in method_ids:
                continue
            if source_scoped_only and chain.get("representation") != (
                "source_scoped_component_profile"
            ):
                continue
            for component in chain.get("components", []):
                concepts = self._module_concepts.get(
                    (chain.get("profile_id"), component.get("source_module_id")), [])
                fields = {
                    name: _terms([component.get(key) for key in keys])
                    for name, keys in _COMPONENT_FIELDS.items()
                    if component_field in {"any", name}
                }
                if component_field in {"any", "concept"}:
                    fields.setdefault("concept", set()).update(_terms([
                        {k: u.get(k) for k in ("concept_id", "kind", "name", "definition", "role")}
                        for u in concepts]))
                matched_fields = {}
                for name, words in fields.items():
                    found = {term for term in terms
                             if any(word == term or (len(term) >= 3 and word.startswith(term))
                                    for word in words)}
                    if found:
                        matched_fields[name] = sorted(found)
                matched = set().union(*matched_fields.values()) if matched_fields else set()
                if matched:
                    rows.append({
                        "component": component,
                        "method_id": chain.get("method_id") or (
                            method_ids[0] if len(method_ids) == 1 else None),
                        "method_ids": method_ids,
                        "method_name": chain.get("method_name"),
                        "profile_id": chain.get("profile_id"),
                        "representation_status": chain.get("representation_status"),
                        "score": len(matched) / len(terms),
                        "matched_terms": sorted(matched),
                        "matched_fields": matched_fields,
                        **({"concept_matches": [{
                            "concept_id": u["concept_id"], "kind": u["kind"],
                            "annotation_ref": u["annotation_ref"],
                            "review_outcome": u["concept_review"]["outcome"]}
                            for u in concepts]} if "concept" in matched_fields else {}),
                    })
        rows.sort(key=lambda row: (-row["score"], row["component"]["id"]))
        result = self._page(
            rows, {"operation": "search_components", "query": query, "method_id": method_id,
                   "component_field": component_field, "source_scoped_only": source_scoped_only},
            limit, cursor,
        )
        if "error" not in result:
            result["retrieval_method"] = (
                "Lexical component retrieval across scoped profiles. Matches are navigation "
                "leads, not established equivalence, novelty or experimental compatibility."
            )
            result["source_scope_note"] = (
                "source_scoped_only selects detailed representations, not an acceptance or "
                "completeness verdict. Inspect the profile verification and remaining gaps. "
                "Function matches identify recorded purpose or effect; operation matches "
                "identify described actions/materials, not canonical mechanism equivalence."
            )
        return result

    def find_mechanism_uses(self, mechanism_id: str, *, limit=8, cursor=None) -> dict[str, Any]:
        """Page explicit contextual membership without creating method relationships."""
        from .module_queries import mechanism_uses
        if not isinstance(mechanism_id, str) or not mechanism_id.strip():
            return self._error("invalid_arguments", "Choose a nonblank mechanism ID")
        rows, concept = mechanism_uses(
            self._component_profiles, mechanism_id, self.snapshot_sha256)
        result = self._page(rows, {"operation": "find_mechanism_uses",
                                  "mechanism_id": mechanism_id}, limit, cursor)
        return result | {"mechanism": concept}

    def inspect_interface(self, profile_id: str, other_profile_id: str,
                          module_id: str, other_module_id: str) -> dict[str, Any]:
        from .module_queries import inspect_interface
        left, right = (self._component_profiles.get(pid)
                       for pid in (profile_id, other_profile_id))
        if left is None or right is None:
            return self._error("not_curated", "Choose indexed source-scoped profile IDs")
        try:
            inspection = inspect_interface(left, right, module_id, other_module_id,
                                           self.snapshot_sha256)
        except (ValueError, TypeError, KeyError) as exc:
            return self._error("invalid_arguments", str(exc))
        return self._result(interface=inspection,
                            verification=[left.get("verification"), right.get("verification")])

    def search_operation_concepts(self, query: str, *, limit=8, cursor=None) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            return self._error("invalid_arguments", "Choose a nonblank concept query")
        rows = self._operation_concepts.search(query)
        return self._page(rows, {"operation": "search_operation_concepts", "query": query},
                          limit, cursor) | {"concept_scope":
            "Explicit operation, mechanism and function concepts. Definition consistency is "
            "recorded identity, not biochemical equivalence or reviewed transfer.",
            "unavailable_source_module_count": len(self._operation_concepts.unavailable_modules)}

    def find_operation_concept_uses(self, concept_id: str, *, limit=8, cursor=None):
        if not isinstance(concept_id, str) or not concept_id.strip():
            return self._error("invalid_arguments", "Choose a nonblank concept ID")
        rows = self._operation_concepts.find_uses(concept_id)
        for row in rows:
            row["module_ref"]["snapshot_sha256"] = self.snapshot_sha256
            row["annotation_ref"]["snapshot_sha256"] = self.snapshot_sha256
        result = self._page(rows, {"operation": "find_operation_concept_uses",
                                  "concept_id": concept_id}, limit, cursor)
        return result | {"concept": self._operation_concepts.concepts.get(
            concept_id, {"concept_id": concept_id, "identity_status": "not_curated"}),
            "concept_scope": "Contextual memberships with individual source and review status; "
                             "shared membership does not establish borrowing or compatibility. "
                             "State descriptions are summaries; reopen module_ref for all details.",
            "unavailable_source_module_count": len(self._operation_concepts.unavailable_modules)}

    def search_operation_groups(self, query: str, *, limit=8, cursor=None):
        if not isinstance(query, str) or not query.strip():
            return self._error("invalid_arguments", "Choose a nonblank group query")
        return self._page(self._operation_groups.search(query), {
            "operation": "search_operation_groups", "query": query}, limit, cursor) | {
            "group_scope": "Reviewed navigation collections, not synonyms, primitives or ancestry."}

    def find_operation_group_uses(self, group_id: str, *, limit=8, cursor=None):
        if group_id not in self._operation_groups.groups:
            return self._error("not_curated", "Unknown reviewed operation group")
        rows = self._operation_groups.uses(group_id, self._operation_concepts)
        for row in rows:
            row["module_ref"]["snapshot_sha256"] = self.snapshot_sha256
            row["annotation_ref"]["snapshot_sha256"] = self.snapshot_sha256
        return self._page(rows, {"operation": "find_operation_group_uses", "group_id": group_id},
                          limit, cursor) | {"group": self._operation_groups.groups[group_id],
            "group_scope": "Classification preserves separate concept and module identities. "
                           "chemical compatibility and historical dependence are not assessed."}

    def suggest_relation_candidates(self, method_id: str, *, limit=8, cursor=None):
        from .relation_candidates import suggest_candidates
        if method_id not in self._methods:
            return self._error("not_found", "Unknown method ID: " + str(method_id))
        rows = suggest_candidates(self, method_id)
        return self._page(rows, {"operation": "suggest_relation_candidates",
                                "method_id": method_id}, limit, cursor) | {
            "candidate_scope": "References, concepts and description overlap generate a "
                "review queue. No ancestry, reuse, compatibility or novelty verdict is inferred. "
                "No candidates means no retrieved leads, not independent invention."}

    def explain_innovation(self, method_id: str) -> dict[str, Any]:
        """Retrieve specifically curated change cases, not an invented origin story."""
        if method_id not in self._methods:
            return self._error("not_found", f"Unknown method ID: {method_id}")
        return self._result(
            method_id=method_id,
            cases=self._design_cases.for_method(method_id),
            related_claim_ids=list(self._adjacency.get(method_id, [])),
            coverage_note=(
                "Only separately source-located cases appear here. No case means a curation "
                "gap, not absence of an innovation. Reported explanation is not isolated "
                "causal evidence; see each case's causal_boundary and verification."
            ),
        )

    def find_functional_precedents(
        self, query: str, target_context: str, *, limit: int = 8, cursor: str | None = None
    ) -> dict[str, Any]:
        """Match explicitly recorded effects; do not score compatibility or transfer."""
        terms = _terms(query)
        if not terms or not isinstance(target_context, str) or not target_context.strip():
            return self._error("invalid_arguments", "Supply a required effect and target context")
        rows = []
        for case in self._design_cases.cases.values():
            for function in case.functions:
                matched = terms & _terms([function.required_effect, function.search_terms])
                if not matched:
                    continue
                rows.append(
                    {
                        "case_id": case.id,
                        "method_id": case.method_id,
                        "function": function.model_dump(mode="json"),
                        "sources": [source.model_dump(mode="json") for source in case.sources],
                        "verification": case.verification,
                        "matched_terms": sorted(matched),
                        "score": len(matched),
                        "requested_target_context": target_context,
                        "context_transfer_status": "not_assessed",
                        "transfer_questions": [
                            "Does the requested material state meet the demonstrated requirements?",
                            "Will molecular attribution and readout survive the new processing?",
                            "Which differences from tested_context require new evidence?",
                        ],
                    }
                )
        rows.sort(key=lambda item: (-item["score"], item["function"]["id"]))
        result = self._page(
            rows,
            {
                "operation": "find_functional_precedents",
                "query": query,
                "target_context": target_context,
            },
            limit,
            cursor,
        )
        result["retrieval_method"] = "Lexical matching of curated required effects and search terms"
        result["coverage_note"] = (
            "Not an exhaustive mechanism search. No match is not evidence of impossibility "
            "or novelty. Target context is returned for comparison, never assumed compatible."
        )
        return result

    def trace_design_dependencies(
        self, case_id: str, changed_operation_ids: tuple[str, ...], proposed_change: str
    ) -> dict[str, Any]:
        """Expose documented requirements around a caller-specified conceptual change."""
        if not changed_operation_ids or len(set(changed_operation_ids)) != len(
            changed_operation_ids
        ):
            return self._error("invalid_arguments", "Specify distinct changed operation IDs")
        return self._result(
            **self._design_cases.trace(case_id, changed_operation_ids, proposed_change)
        )

    def search_operations(
        self,
        query: str,
        *,
        method_id: str | None = None,
        limit: int = 8,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Find action descriptions across methods, never infer functional equivalence."""
        if not isinstance(query, str) or not _terms(query):
            return self._error("invalid_arguments", "query needs at least one searchable term")
        if method_id is not None and method_id not in self._methods:
            return self._error("not_found", f"Unknown method ID: {method_id}")
        terms = _terms(query)
        rows = []
        for operation in self._operations:
            if method_id is not None and operation["method_id"] != method_id:
                continue
            action_terms = _terms(operation["action"])
            matched = {
                term
                for term in terms
                if any(
                    token == term or (len(term) >= 3 and token.startswith(term))
                    for token in action_terms
                )
            }
            if matched:
                rows.append(
                    {
                        "operation": operation,
                        "method": self._method_card(self._methods[operation["method_id"]]),
                        "score": len(matched) / len(terms),
                        "matched_terms": sorted(matched),
                    }
                )
        rows.sort(key=lambda row: (-row["score"], row["operation"]["operation_id"]))
        result = self._page(
            rows,
            {
                "operation": "search_operations",
                "query": query,
                "method_id": method_id,
            },
            limit,
            cursor,
        )
        if "error" not in result:
            result["retrieval_method"] = (
                "Lexical action retrieval. Shared words do not demonstrate interchangeable "
                "functions, experimental precedent, or compatible input/output states."
            )
        return result

    def compare_methods(self, method_id: str, other_method_id: str) -> dict[str, Any]:
        """Present documented changes alongside explicitly heuristic overlap leads."""
        for identifier in (method_id, other_method_id):
            if identifier not in self._methods:
                return self._error("not_found", f"Unknown method ID: {identifier}")
        if method_id == other_method_id:
            return self._error("invalid_arguments", "Choose two distinct methods")
        pair = {method_id, other_method_id}
        direct = [
            claim
            for claim in self._claims.values()
            if {claim.get("source"), claim.get("target")} == pair
        ]
        component_inputs = {}
        for identifier in (method_id, other_method_id):
            component_inputs[identifier] = defaultdict(list)
            for claim_id in self._adjacency.get(identifier, []):
                claim = self._claims[claim_id]
                if (
                    claim.get("target") == identifier
                    and claim.get("status") == "supported"
                    and self._kind(claim) == "component_reuse"
                ):
                    component_inputs[identifier][claim["source"]].append(claim)
        common = sorted(set(component_inputs[method_id]) & set(component_inputs[other_method_id]))
        suggestions = []
        left = [item for item in self._operations if item["method_id"] == method_id]
        right = [item for item in self._operations if item["method_id"] == other_method_id]
        for first in left:
            for second in right:
                a, b = _terms(first["action"]), _terms(second["action"])
                if a & b:
                    suggestions.append(
                        {
                            "operation_ids": [first["operation_id"], second["operation_id"]],
                            "actions": [first["action"], second["action"]],
                            "shared_terms": sorted(a & b),
                            "lexical_score": round(len(a & b) / len(a | b), 5),
                            "alignment_status": "lexical_candidate_only",
                        }
                    )
        suggestions.sort(key=lambda item: (-item["lexical_score"], item["operation_ids"]))
        event_ids = {
            event_id for claim in direct for event_id in claim.get("joint_change_event_ids", [])
        }
        return self._result(
            workflows=[
                self._workflow_record(identifier) for identifier in (method_id, other_method_id)
            ],
            documented_change_claims=[
                c
                for c in direct
                if c.get("status") == "supported" and self._kind(c) == "method_development"
            ],
            direct_component_reuse_claims=[
                c
                for c in direct
                if c.get("status") == "supported" and self._kind(c) == "component_reuse"
            ],
            direct_comparison_claims=[
                c
                for c in direct
                if c.get("status") == "supported" and self._kind(c) == "comparison"
            ],
            direct_analogy_claims=[
                c
                for c in direct
                if c.get("status") == "supported" and self._kind(c) == "analogy"
            ],
            uncertain_pair_claims=[c for c in direct if c.get("status") != "supported"],
            joint_change_events=[
                self._context_event(self._events[identifier])
                for identifier in sorted(event_ids)
                if identifier in self._events
            ],
            shared_component_leads=[
                {
                    "component_method_id": identifier,
                    "contribution_claims": (
                        component_inputs[method_id][identifier]
                        + component_inputs[other_method_id][identifier]
                    ),
                    "transfer_compatibility": "not_assessed",
                }
                for identifier in common
            ],
            lexical_alignment_candidates=suggestions[:6],
            lexical_alignment_pagination={
                "returned": min(6, len(suggestions)),
                "total_candidates": len(suggestions),
                "truncated": len(suggestions) > 6,
            },
            comparison_scope=(
                "documented_change_claims contain only supported method_development claims. "
                "Direct component reuse, comparison and analogy claims retain their distinct "
                "relation types and do not establish development ancestry. All claims are "
                "curator annotations. Lexical alignments and shared components are research "
                "leads, not verified protocol equivalence, chemical compatibility or "
                "exhaustive identification of differences."
            ),
        )

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        claim = self._claims.get(claim_id)
        if claim is None:
            return self._error("not_found", f"Unknown claim ID: {claim_id}")
        return self._result(
            claim=claim,
            evidence=[
                self._evidence_record(self._evidence[item])
                for item in claim.get("evidence_ids", [])
                if item in self._evidence
            ],
            joint_change_events=[
                self._context_event(self._events[item])
                for item in claim.get("joint_change_event_ids", [])
                if item in self._events
            ],
        )

    @staticmethod
    def _context_event(event: dict) -> dict:
        # All roles remain present, even when only the historical layer was traversed.
        return event | {"context_only": True}

    def follow_relations(
        self,
        method_id: str,
        *,
        direction: str = "ancestors",
        relationship_kinds: tuple[str, ...] = ("method_development",),
        statuses: tuple[str, ...] = ("supported",),
        depth: int = 1,
        limit: int = 8,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if method_id not in self._methods:
            return self._error("not_found", f"Unknown method ID: {method_id}")
        if (
            direction not in ("ancestors", "descendants", "both")
            or type(depth) is not int
            or not 1 <= depth <= 6
            or not relationship_kinds
            or not set(relationship_kinds) <= _KINDS
            or not statuses
            or not set(statuses) <= _STATUSES
        ):
            return self._error(
                "invalid_arguments", "Invalid direction, depth, relation kinds or statuses"
            )
        queue = deque([(method_id, 0)])
        expanded_nodes = {method_id}
        seen_claims: set[str] = set()
        rows = []
        while queue:
            current, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for claim_id in sorted(self._adjacency.get(current, [])):
                claim = self._claims[claim_id]
                if (
                    self._kind(claim) not in relationship_kinds
                    or claim.get("status") not in statuses
                ):
                    continue
                source, target = claim.get("source"), claim.get("target")
                if target == current and direction in ("ancestors", "both"):
                    other = source
                elif source == current and direction in ("descendants", "both"):
                    other = target
                else:
                    continue
                # Deduplicate expansion, not the incoming claims. This retains diamonds
                # and parallel design moves between the same methods.
                if claim_id not in seen_claims:
                    seen_claims.add(claim_id)
                    rows.append(
                        {
                            "depth": current_depth + 1,
                            "from_method_id": current,
                            "to_method_id": other,
                            "claim": claim,
                        }
                    )
                if other in self._methods and other not in expanded_nodes:
                    expanded_nodes.add(other)
                    queue.append((other, current_depth + 1))
        rows.sort(key=lambda row: (row["depth"], row["claim"]["id"]))
        result = self._page(
            rows,
            {
                "operation": "follow_relations",
                "method_id": method_id,
                "direction": direction,
                "relationship_kinds": sorted(set(relationship_kinds)),
                "statuses": sorted(set(statuses)),
                "depth": depth,
            },
            limit,
            cursor,
        )
        if "error" not in result:
            selected_events = {
                event_id
                for row in result["results"]
                for event_id in row["claim"].get("joint_change_event_ids", [])
            }
            result["joint_change_events"] = copy.deepcopy(
                [
                    self._context_event(self._events[event_id])
                    for event_id in sorted(selected_events)
                    if event_id in self._events
                ]
            )
            result["query"] = {
                "method_id": method_id,
                "direction": direction,
                "depth": depth,
                "relationship_kinds": list(relationship_kinds),
                "statuses": list(statuses),
            }
        return result

    def get_change_event(self, event_id: str) -> dict[str, Any]:
        event = self._events.get(event_id)
        if event is None:
            return self._error("not_found", f"Unknown change event ID: {event_id}")
        return self._result(
            event=event,
            contributing_claims=[
                self._claims[item["supporting_claim_id"]]
                for item in event.get("inputs", [])
                if item.get("supporting_claim_id") in self._claims
            ],
            evidence=[
                self._evidence_record(self._evidence[item])
                for item in event.get("evidence_ids", [])
                if item in self._evidence
            ],
        )

    def _evidence_record(self, record: dict) -> dict:
        paper = self._papers.get(record.get("paper_id"), {})
        claim = self._claims.get(record.get("claim_id"), {})
        event = self._events.get(record.get("event_id"), {})
        return record | {
            "evidence_kind": "curator_annotation",
            "original_source_presented": False,
            "current_scientific_audit": claim.get("scientific_audit")
            or event.get("scientific_audit"),
            "verification": copy.deepcopy(claim.get("verification") or event.get("verification")),
            "paper": {
                key: paper.get(key)
                for key in ("paper_id", "title", "doi", "url", "publication_year")
            },
            "review_scopes": [
                {
                    key: review.get(key)
                    for key in (
                        "review_scope",
                        "sections_read",
                        "supplement_status",
                        "limitations_of_review",
                    )
                }
                for review in paper.get("reviews", [])
            ],
            "source_read_instruction": (
                "Find this paper by title or DOI with the literature tools, then read original "
                "passages to verify the cited locator. This annotation is not a source excerpt."
            ),
        }

    def get_evidence(self, evidence_id: str) -> dict[str, Any]:
        record = self._evidence.get(evidence_id)
        if record is None:
            return self._error("not_found", f"Unknown evidence ID: {evidence_id}")
        return self._result(evidence=self._evidence_record(record))

    def export_snapshot(self) -> dict[str, Any]:
        return self._result(data=self._data)

    def validate(self) -> dict[str, Any]:
        """Check referential integrity and evidence bookkeeping, not scientific entailment."""
        errors: list[str] = []
        warnings: list[str] = []
        history: dict[str, list[str]] = defaultdict(list)
        for method_id, method in self._methods.items():
            for paper_id in method.get("paper_ids", []) + method.get("origin_paper_ids", []):
                if paper_id not in self._papers:
                    errors.append(f"{method_id}: unknown paper {paper_id}")
        if self._component_chains and set(self._component_chains) != set(self._methods):
            errors.append("Component chains must cover all methods exactly once")
        component_records = (
            list(self._component_chains.values()) + list(self._component_profiles.values())
        )
        for chain in component_records:
            component_ids = [c.get("id") for c in chain.get("components", [])]
            if len(component_ids) != len(set(component_ids)):
                errors.append(f"{chain['id']}: duplicate component ID")
            for connection in chain.get("connections", []):
                if any(connection.get(key) not in component_ids for key in ("source", "target")):
                    errors.append(f"{chain['id']}: unknown component connection endpoint")
            if chain.get("representation_status") == "overview_component_chain":
                if any(c.get("input_state") is not None or c.get("output_state") is not None
                       for c in chain.get("components", [])):
                    errors.append(f"{chain['id']}: overview states must remain unknown")
                if any(c.get("relation") != "next_in_summary"
                       for c in chain.get("connections", [])):
                    errors.append(f"{chain['id']}: summary order is not a physical dependency")
        for claim_id, claim in self._claims.items():
            supported = claim.get("status") == "supported"
            if claim.get("status") not in _STATUSES:
                errors.append(f"{claim_id}: unknown status {claim.get('status')}")
            if self._kind(claim) not in _KINDS:
                errors.append(f"{claim_id}: unknown relationship kind")
            if supported:
                endpoints_valid = all(
                    claim.get(key) in self._methods for key in ("source", "target")
                )
                if not endpoints_valid:
                    errors.append(f"{claim_id}: supported claim has missing method endpoint")
                if not claim.get("paper_ids") or not claim.get("evidence_ids"):
                    errors.append(
                        f"{claim_id}: supported claim needs paper and evidence references"
                    )
                if not claim.get("evidence_location"):
                    errors.append(f"{claim_id}: supported claim needs a source locator")
                if self._kind(claim) == "uncertain_relation":
                    errors.append(
                        f"{claim_id}: supported claim needs an explicit relationship kind"
                    )
                if self._kind(claim) == "method_development" and endpoints_valid:
                    history[claim.get("source")].append(claim.get("target"))
            for paper_id in claim.get("paper_ids", []):
                if paper_id not in self._papers:
                    errors.append(f"{claim_id}: unknown paper {paper_id}")
            for evidence_id in claim.get("evidence_ids", []):
                evidence = self._evidence.get(evidence_id)
                if evidence is None or evidence.get("claim_id") != claim_id:
                    errors.append(f"{claim_id}: missing or mismatched evidence {evidence_id}")
                elif supported and not evidence.get("locator"):
                    errors.append(f"{claim_id}: supporting evidence lacks a locator")
                elif evidence.get("paper_id") not in claim.get("paper_ids", []):
                    errors.append(f"{claim_id}: evidence paper does not match the claim citations")
            for event_id in claim.get("joint_change_event_ids", []):
                if event_id not in self._events:
                    errors.append(f"{claim_id}: unknown event {event_id}")
                elif not any(
                    item.get("supporting_claim_id") == claim_id
                    for item in self._events[event_id].get("inputs", [])
                ):
                    errors.append(f"{claim_id}: event backlink is not a contributing claim")
        for evidence_id, evidence in self._evidence.items():
            if evidence.get("paper_id") not in self._papers:
                errors.append(f"{evidence_id}: unknown paper {evidence.get('paper_id')}")
            owners = [key for key in ("claim_id", "event_id") if evidence.get(key)]
            if len(owners) != 1:
                errors.append(f"{evidence_id}: evidence needs exactly one claim or event owner")
            elif evidence[owners[0]] not in (
                self._claims if owners[0] == "claim_id" else self._events
            ):
                errors.append(f"{evidence_id}: unknown owner {evidence[owners[0]]}")
        for event_id, event in self._events.items():
            if event.get("status") != "supported":
                errors.append(f"{event_id}: joint change event is not supported")
            if not event.get("paper_ids") or not event.get("evidence_ids"):
                errors.append(f"{event_id}: event needs paper and evidence references")
            for paper_id in event.get("paper_ids", []):
                if paper_id not in self._papers:
                    errors.append(f"{event_id}: unknown paper {paper_id}")
            if event.get("output_method_id") not in self._methods:
                errors.append(f"{event_id}: unknown output method")
            inputs = event.get("inputs", [])
            if len(inputs) < 2 or len({item.get("method_id") for item in inputs}) != len(inputs):
                errors.append(f"{event_id}: needs at least two distinct inputs")
            for item in inputs:
                claim = self._claims.get(item.get("supporting_claim_id"), {})
                if (
                    item.get("method_id") not in self._methods
                    or not item.get("role")
                    or claim.get("status") != "supported"
                    or claim.get("source") != item.get("method_id")
                    or claim.get("target") != event.get("output_method_id")
                ):
                    errors.append(f"{event_id}: input lacks a matching supported role claim")
                if item.get("edge_relation") and item["edge_relation"] != claim.get("relation"):
                    errors.append(f"{event_id}: input relation differs from its supporting claim")
                if not set(event.get("paper_ids", [])) & set(claim.get("paper_ids", [])):
                    errors.append(f"{event_id}: input claim does not share an event paper")
                if event_id not in claim.get("joint_change_event_ids", []):
                    errors.append(f"{event_id}: input claim lacks its event backlink")
            for evidence_id in event.get("evidence_ids", []):
                record = self._evidence.get(evidence_id, {})
                if record.get("event_id") != event_id:
                    errors.append(f"{event_id}: missing or mismatched evidence {evidence_id}")
                elif record.get("paper_id") not in event.get("paper_ids", []):
                    errors.append(f"{event_id}: evidence paper does not match event citations")
                elif not record.get("locator"):
                    errors.append(f"{event_id}: event evidence lacks a source locator")
        # Iterative topological checking also supports histories longer than Python's
        # recursion limit. Only documented development must form a DAG.
        indegree = dict.fromkeys(self._methods, 0)
        for children in history.values():
            for child in children:
                if child in indegree:
                    indegree[child] += 1
        ready = deque(method_id for method_id, degree in indegree.items() if degree == 0)
        visited_count = 0
        while ready:
            method_id = ready.popleft()
            visited_count += 1
            for child in history.get(method_id, []):
                if child in indegree:
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        ready.append(child)
        if visited_count != len(indegree):
            errors.append("Historical development cycle prevents topological ordering")
        coverage = self._data.get("record_coverage", {})
        if not isinstance(coverage, dict):
            errors.append("record_coverage must be an object")
            coverage = {}
        for record_id, method_ids in coverage.items():
            if not method_ids or any(method_id not in self._methods for method_id in method_ids):
                errors.append(f"{record_id}: unresolved catalogue coverage")
        for facet in self._data.get("facet_assignments", []):
            if facet.get("method_id") not in self._methods:
                errors.append(f"Facet {facet.get('id')}: unknown method")
        if any(not paper.get("publication_year") for paper in self._papers.values()):
            warnings.append(
                "Some source years are unknown; this snapshot is not a "
                "date-filtered benchmark corpus"
            )
        warnings.append(
            "Source locators and review ledgers are curator records; "
            "semantic support needs primary-source review"
        )
        supported = [claim for claim in self._claims.values() if claim.get("status") == "supported"]
        connected = {
            claim[key]
            for claim in supported
            for key in ("source", "target")
            if claim.get(key) in self._methods
        }
        return self._result(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            statistics={
                "method_count": len(self._methods),
                "claim_count": len(self._claims),
                "supported_claim_count": len(supported),
                "evidence_count": len(self._evidence),
                "event_count": len(self._events),
                "paper_count": len(self._papers),
                "isolated_method_count": len(set(self._methods) - connected),
                "catalogue_record_count": len(coverage),
                "claim_kind_counts": dict(Counter(self._kind(claim) for claim in supported)),
                "claim_status_counts": dict(
                    Counter(claim.get("status") for claim in self._claims.values())
                ),
            },
        )

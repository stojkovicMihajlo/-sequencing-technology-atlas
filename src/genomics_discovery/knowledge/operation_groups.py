"""Reviewed collections of operation concepts; no synonym merge or ancestry claim."""

from __future__ import annotations

import copy
import re

from genomics_discovery.curation.validation import _require, _text, canonical_hash

from .operation_concepts import OperationConceptIndex

GROUP_FORMAT = "operation_concept_groups/1.0"
GROUP_FIELD = "operation_concept_groups"


def reviewed_cards(data: dict) -> dict:
    """Only individually source-reviewed annotations enter grouping, with all variants."""
    index = OperationConceptIndex(data)
    cards = {}
    for use in index.uses:
        if use["concept_review"].get("outcome") != "supported":
            continue
        cid = use["concept_id"]
        card = cards.setdefault(cid, {"concept_id": cid, "definitions": [], "uses": []})
        definition = {"kind": use["kind"], "name": use["name"], "definition": use["definition"]}
        if definition not in card["definitions"]:
            card["definitions"].append(definition)
        card["uses"].append({"profile_id": use["profile_id"], "module_id": use["module_id"],
            "method_ids": use["method_ids"], "role": use["role"],
            "evidence_status": use["evidence_status"], "source_refs": use["source_refs"],
            "annotation_ref": use["annotation_ref"], "module_ref": use["module_ref"],
            "annotation_sha256": use["concept_review"]["annotation_sha256"],
            "review_report": {"path": use["concept_review"].get("report_path"),
                              "sha256": use["concept_review"].get("report_sha256")}})
    for card in cards.values():
        card["definitions"].sort(key=lambda row: (row["kind"], row["definition"], row["name"]))
    return cards


def validate_groups(groups: list, cards: dict, *, max_groups: int = 12) -> None:
    _require(isinstance(groups, list) and len(groups) <= max_groups, "Unbounded concept groups")
    seen = set()
    for group in groups:
        _require(isinstance(group, dict), "Invalid concept group")
        gid = group.get("group_id")
        _require(isinstance(gid, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,239}", gid)
                 and gid not in seen and gid not in cards, "Duplicate or invalid group ID")
        seen.add(gid)
        _require(group.get("kind") in {"operation", "mechanism", "function"}, "Invalid group kind")
        for field in ("name", "definition", "use_for_agents"):
            _require(_text(group.get(field)), "Missing group " + field)
        members = group.get("members")
        _require(isinstance(members, list) and 1 <= len(members) <= 64,
                 "A collection needs an explicitly identified member")
        member_ids = set()
        for member in members:
            _require(isinstance(member, dict), "Invalid group member")
            cid = member.get("concept_id")
            _require(isinstance(cid, str) and cid in cards and cid not in member_ids,
                     "Unknown or duplicate group member")
            member_ids.add(cid)
            _require(member.get("concept_sha256") == canonical_hash(cards[cid])
                     and _text(member.get("reason")), "Stale or unexplained group member")
            _require({d["kind"] for d in cards[cid]["definitions"]} == {group["kind"]},
                     "Grouping cannot merge operation, mechanism and function kinds")


class OperationGroupIndex:
    def __init__(self, data: dict):
        self.groups = {}
        self.cards = reviewed_cards(data)
        packet = data.get(GROUP_FIELD)
        if packet is None:
            return
        base = {k: v for k, v in data.items() if k != GROUP_FIELD}
        _require(packet.get("format_version") == GROUP_FORMAT
                 and packet.get("source_snapshot_sha256") == canonical_hash(base),
                 "Concept groups target a changed source snapshot")
        validate_groups(packet.get("groups"), self.cards)
        for group in packet["groups"]:
            review = group.get("review")
            original = {k: v for k, v in group.items() if k != "review"}
            _require(isinstance(review, dict) and review.get("outcome") == "supported"
                     and review.get("group_sha256") == canonical_hash(original)
                     and _text(review.get("reviewer")) and _text(review.get("reason")),
                     "Unreviewed or stale operation group")
            checks = review.get("member_checks", [])
            expected = {m["concept_id"]: m for m in group["members"]}
            _require(len(checks) == len(expected), "Incomplete group-member review")
            seen = set()
            for check in checks:
                cid = check.get("concept_id")
                _require(cid in expected and cid not in seen
                         and check.get("outcome") == "supported"
                         and check.get("member_sha256") == canonical_hash(expected[cid])
                         and check.get("group_sha256") == canonical_hash(original)
                         and _text(check.get("reason")), "Stale or unsupported group member")
                seen.add(cid)
            self.groups[group["group_id"]] = copy.deepcopy(group)

    def search(self, query: str) -> list:
        terms = set(re.findall(r"[a-z0-9]+", query.casefold()))
        rows = []
        for group in self.groups.values():
            words = set(re.findall(r"[a-z0-9]+", " ".join(
                group[k] for k in ("group_id", "name", "definition", "use_for_agents")).casefold()))
            if matched := sorted(terms & words):
                rows.append({**copy.deepcopy(group), "matched_terms": matched})
        return sorted(rows, key=lambda row: (-len(row["matched_terms"]), row["group_id"]))

    def uses(self, group_id: str, operation_index: OperationConceptIndex) -> list:
        group = self.groups.get(group_id)
        if group is None:
            return []
        result = []
        for member in group["members"]:
            for use in operation_index.find_uses(member["concept_id"]):
                if use["concept_review"].get("outcome") == "supported":
                    result.append({**use, "group_id": group_id,
                                   "group_membership_reason": member["reason"]})
        return result


def attach_operation_groups(data: dict, packet: dict) -> dict:
    _require(GROUP_FIELD not in data, "Use a snapshot without an existing grouping layer")
    result = copy.deepcopy(data)
    result[GROUP_FIELD] = copy.deepcopy(packet)
    OperationGroupIndex(result)
    return result

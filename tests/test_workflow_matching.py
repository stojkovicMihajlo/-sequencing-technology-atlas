"""Integrity/uncertainty controls, not scientific labels from synthetic sources."""

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_workflow_transformations import cases as cases

from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_matching.models import (
    QUESTIONS,
    AlignmentBatch,
    Case,
    MatchingReview,
    ProfileBatch,
)
from genomics_discovery.curation.workflow_matching.tools import (
    load_context,
    review_targets,
    search_profiles,
    validate_alignments,
    validate_profiles,
    validate_review,
)


@pytest.fixture
def example(cases, tmp_path):
    parents = {
        k: {
            field: str(tmp_path / f"{k}.{field}.json") for field in ("workflow", "packet", "review")
        }
        for k in ("a", "b")
    }
    registry, manifest = tmp_path / "parents.json", tmp_path / "manifest.json"
    registry.write_text(json.dumps(parents))
    manifest.write_text(
        json.dumps(
            {
                u.id: {
                    **parents[u.method_id],
                    "occurrence": str(tmp_path / f"{u.method_id}.occurrence.json"),
                }
                for u in cases[0].values()
            }
        )
    )
    context = load_context(registry, manifest)
    evidence = next(iter(context.occurrences.values())).operations[0].evidence[0].model_dump()
    profiles = ProfileBatch.model_validate(
        {
            "profiles": [
                {
                    "occurrence_id": u.id,
                    "occurrence_sha256": digest(u),
                    "facets": [
                        {
                            "id": dimension,
                            "dimension": dimension,
                            "description": "Retain insert and adapter identity",
                            "status": "reported",
                            "targets": ["operation:insert"],
                            "evidence": [evidence],
                            "search_terms": ["adapter insertion"],
                        }
                        for dimension in QUESTIONS
                    ],
                }
                for u in context.occurrences.values()
            ]
        }
    )
    case = Case(
        id="comparison",
        left_parent="a",
        right_parent="b",
        question="Compare adapter-bearing DNA production",
        relation="shared_purpose",
    )
    alignments = AlignmentBatch.model_validate(
        {
            "alignments": [
                {
                    "case_id": case.id,
                    "left": {"parent_id": "a", "operation_ids": ["insert", "repair"]},
                    "right": {"parent_id": "b", "operation_ids": ["insert"]},
                    "disposition": "candidate_correspondence",
                    "rationale": "A local comparison candidate",
                    "claims": [
                        {
                            "dimension": "transformation",
                            "kind": "correspondence",
                            "description": "Both insert adapters; left also includes repair",
                            "left_targets": ["operation:insert"],
                            "right_targets": ["operation:insert"],
                            "left_evidence": [evidence],
                            "right_evidence": [evidence],
                        }
                    ],
                }
            ]
        }
    )
    checks = [
        {
            "target_id": target,
            "verdict": "supported",
            "rationale": "Synthetic gate check",
            "citations": [{"parent_id": parent, "evidence": [evidence]} for parent in ids],
            "inspected": [
                {
                    "parent_id": parent,
                    "source_id": evidence["source_id"],
                    "passage_id": evidence["passage_id"],
                }
                for parent in ids
            ],
        }
        for target, ids in review_targets(profiles, alignments, context).items()
    ]
    review = MatchingReview.model_validate(
        {
            "profiles_sha256": digest(profiles),
            "alignments_sha256": digest(alignments),
            "checks": checks,
        }
    )
    return context, profiles, alignments, [case], review, registry, manifest


def test_different_group_sizes_retain_branches_and_parent_disposition(example):
    context, profiles, alignments, cases, review, *_ = example
    validate_profiles(profiles, context)
    cuts = validate_alignments(alignments, cases, context)
    assert len(cuts["comparison"]["left"]["operations"]) == 2
    assert len(cuts["comparison"]["right"]["operations"]) == 1
    assert "assay" in {r["operation_id"] for r in cuts["comparison"]["left"]["external_operations"]}
    assert cuts["comparison"]["left"]["workflow_review_status"] == "needs_revision"
    assert validate_review(review, profiles, alignments, context) == "supported_local_comparisons"
    # A local comparison never upgrades its inherited whole-workflow review.
    assert (
        context.occurrences[next(iter(context.occurrences))].workflow_review_status
        == "needs_revision"
    )


def test_unknown_is_not_retrieved_as_a_positive_property(example):
    profiles = example[1].model_dump(mode="json")
    for profile in profiles["profiles"]:
        for facet in profile["facets"]:
            facet.update(
                description="Amplification feedback is not characterized",
                status="not_characterized",
                search_terms=[],
                evidence=[],
                follow_up="Find a source specifying feedback",
            )
    batch = ProfileBatch.model_validate(profiles)
    assert search_profiles(batch, "amplification feedback")["hits"] == []
    profiles["profiles"][0]["facets"][0]["search_terms"] = ["feedback"]
    with pytest.raises(ValidationError):
        ProfileBatch.model_validate(profiles)


def test_no_common_word_or_search_result_creates_a_relation(example):
    result = search_profiles(example[1], "adapter insertion", dimension="transformation")
    assert len(result["hits"]) == 2
    assert "relation" not in result and "edges" not in result
    assert {h["dimension"] for h in result["hits"]} == {"transformation"}


@pytest.mark.parametrize("defect", ["stale", "unknown_target", "bad_quote", "missing_profile"])
def test_profile_corruption_is_rejected(example, defect):
    context, profiles, *_ = example
    data = profiles.model_dump(mode="json")
    first = data["profiles"][0]
    if defect == "stale":
        first["occurrence_sha256"] = "0" * 64
    elif defect == "unknown_target":
        first["facets"][0]["targets"] = ["state:invented_intermediate"]
    elif defect == "bad_quote":
        first["facets"][0]["evidence"][0]["quote"] = "An invented chemical guarantee"
    else:
        data["profiles"].pop()
    with pytest.raises(ValueError):
        validate_profiles(ProfileBatch.model_validate(data), context)


def test_disconnected_or_unknown_alignment_selection_is_rejected(example):
    context, _, alignments, cases, *_ = example
    for operations in (["repair", "assay"], ["invented"]):
        data = alignments.model_dump(mode="json")
        data["alignments"][0]["left"]["operation_ids"] = operations
        with pytest.raises(ValueError):
            validate_alignments(AlignmentBatch.model_validate(data), cases, context)


@pytest.mark.parametrize("defect", ["missing_check", "wrong_parent", "uninspected", "stale"])
def test_incomplete_review_cannot_support_an_index(example, defect):
    context, profiles, alignments, _, review, *_ = example
    data = review.model_dump(mode="json")
    if defect == "missing_check":
        data["checks"].pop()
    elif defect == "wrong_parent":
        data["checks"][-1]["citations"].pop()
    elif defect == "uninspected":
        data["checks"][0]["inspected"][0]["passage_id"] = "absent"
    else:
        data["profiles_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        validate_review(MatchingReview.model_validate(data), profiles, alignments, context)


def test_negative_review_and_explicit_no_match_remain_distinct(example):
    context, profiles, alignments, cases, review, *_ = example
    changed = deepcopy(review.model_dump(mode="json"))
    changed["checks"][-1]["verdict"] = "unsupported"
    assert (
        validate_review(MatchingReview.model_validate(changed), profiles, alignments, context)
        == "needs_revision"
    )
    changed["checks"][-1]["verdict"] = "unresolved"
    assert (
        validate_review(MatchingReview.model_validate(changed), profiles, alignments, context)
        == "research_needed"
    )
    data = alignments.model_dump(mode="json")
    data["alignments"][0]["disposition"] = "no_supported_correspondence"
    data["alignments"][0]["claims"][0]["kind"] = "difference"
    batch = AlignmentBatch.model_validate(data)
    assert validate_alignments(batch, cases, context)


def test_source_change_fails_before_profiles_can_be_reused(example):
    *_, registry, manifest = example
    (registry.parent / "a.txt").write_text("Different original source")
    with pytest.raises(ValueError, match="Source changed"):
        load_context(registry, manifest)


def test_a_purpose_only_alignment_cannot_be_promoted_to_shared_mechanism(example):
    context, _, alignments, cases, *_ = example
    mechanism_case = [cases[0].model_copy(update={"relation": "shared_mechanism"})]
    validate_alignments(alignments, mechanism_case, context)
    data = alignments.model_dump(mode="json")
    data["alignments"][0]["claims"][0]["dimension"] = "information"
    with pytest.raises(ValueError, match="mechanism correspondence"):
        validate_alignments(AlignmentBatch.model_validate(data), mechanism_case, context)


def test_agent_review_check_accepts_a_valid_negative_verdict(
    example, tmp_path, monkeypatch, capsys
):
    from genomics_discovery.curation.workflow_matching.__main__ import main

    _, profiles, alignments, cases, review, registry, manifest = example
    data = review.model_dump(mode="json")
    data["checks"][0]["verdict"] = "unsupported"
    documents = [
        profiles.model_dump(mode="json"),
        alignments.model_dump(mode="json"),
        [c.model_dump(mode="json") for c in cases],
        data,
    ]
    paths = [tmp_path / f"input_{i}.json" for i in range(4)]
    for path, document in zip(paths, documents, strict=True):
        path.write_text(json.dumps(document))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "matching",
            "--parents",
            str(registry),
            "--manifest",
            str(manifest),
            "review-check",
            *map(str, paths),
        ],
    )
    main()
    assert json.loads(capsys.readouterr().out) == {
        "integrity": "valid_not_entailment",
        "status": "needs_revision",
    }


def test_known_and_unknown_assertions_can_coexist_without_retagging(example):
    context, profiles, alignments, *_ = example
    data = profiles.model_dump(mode="json")
    unknown = deepcopy(data["profiles"][0]["facets"][-1])
    unknown.update(
        id="unmeasured_loss",
        status="not_characterized",
        evidence=[],
        search_terms=[],
        description="Transfer losses are not characterized here",
        follow_up="Find a source measuring the losses",
    )
    data["profiles"][0]["facets"].append(unknown)
    batch = ProfileBatch.model_validate(data)
    validate_profiles(batch, context)
    required = review_targets(batch, alignments, context)
    assert len(required) == len(review_targets(profiles, alignments, context)) + 1
    assert len([f for f in batch.profiles[0].facets if f.dimension == "scope_limits"]) == 2
    assert all(
        h["assertion_id"] != "unmeasured_loss"
        for h in search_profiles(batch, "transfer losses")["hits"]
    )
    data["profiles"][0]["facets"][-1]["id"] = "scope_limits"
    with pytest.raises(ValidationError, match="assertion IDs"):
        ProfileBatch.model_validate(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("change_input", [False, True])
async def test_native_handoff_and_mutable_state_boundary(
    example, tmp_path, monkeypatch, change_input
):
    from genomics_discovery.curation.codex_worker import CodexWorker, WorkerResult
    from genomics_discovery.curation.workflow_matching import consumer
    from playground import workflow_matching_live as live

    _, profiles, alignments, cases, review, registry, manifest = example
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps([c.model_dump() for c in cases]))
    run = tmp_path / "native_boundary_test"

    def assessment(path):
        return SimpleNamespace(complete=True, thread_id=path.name, usage={})

    monkeypatch.setattr(live, "assess_archived_attempt", assessment)
    monkeypatch.setattr(consumer, "assess_archived_attempt", assessment)

    class BoundaryWorker(CodexWorker):
        def __init__(self):
            super().__init__(web_search=False, network_access=False)

        async def run(self, prompt, attempt):
            (attempt / "prompt.txt").write_text(prompt)
            if "compiler" in attempt.name:
                assert not (run / "profiles.json").exists()
                (attempt / "profiles.json").write_text(profiles.model_dump_json())
                (attempt / "alignments.json").write_text(alignments.model_dump_json())
                (attempt / "searches.json").write_text('[{"query":"adapter insertion"}]')
                if change_input:
                    (run / "cases.json").write_text("[]")
            else:
                assert (run / "profiles.json").read_bytes() == (
                    run / "001_compiler/profiles.json"
                ).read_bytes()
                (attempt / "artifact.json").write_text(review.model_dump_json())
            return WorkerResult(
                exit_code=0, thread_id=attempt.name, usage={}, elapsed_seconds=0, error=None
            )

    result = await live.execute(registry, manifest, cases_path, run, worker=BoundaryWorker())
    if change_input:
        assert result["status"] == "failed" and "Frozen matching input" in result["error"]
        assert not (run / "matching_index.json").exists()
        return
    assert result["status"] == "completed"
    pins = json.loads((run / "config.json").read_text())["files"]
    assert str(run / "state.json") not in pins
    snapshots = json.loads((run / "implementation_snapshot/manifest.json").read_text())
    assert snapshots and all(Path(item["snapshot"]).exists() for item in snapshots)
    index_path = run / "matching_index.json"
    hits = consumer.query_reviewed(index_path, query="adapter insertion")
    assert hits["index_review_status"] == "source_reviewed_with_integrity_replay"
    compact = consumer.query_reviewed(index_path, case_id="comparison")
    full = consumer.query_reviewed(index_path, case_id="comparison", full_context=True)
    assert compact["context_view"] == "navigation_summary_not_full_interface"
    assert full["context_view"] == "full_record"
    assert compact["requested_comparison"]["question"] == cases[0].question
    for side in ("left", "right"):
        small, raw = compact["retained_views"][side], full["retained_views"][side]
        assert small["flows"] == [
            {k: flow[k] for k in ("id", "source", "target", "kind", "condition")}
            for flow in raw["flows"]
        ]
        assert small["external_operations"] == raw["external_operations"]
    frozen_cases = (run / "cases.json").read_bytes()
    changed_cases = json.loads(frozen_cases)
    changed_cases[0]["question"] = "A different scientific comparison"
    (run / "cases.json").write_text(json.dumps(changed_cases))
    with pytest.raises(ValueError, match="Frozen comparison context"):
        consumer.query_reviewed(index_path, case_id="comparison")
    (run / "cases.json").write_bytes(frozen_cases)
    negative = review.model_dump(mode="json")
    negative["checks"][0]["verdict"] = "unsupported"
    (run / "002_reviewer/artifact.json").write_text(json.dumps(negative))
    index = json.loads(index_path.read_text())
    index["review_sha256"] = digest(MatchingReview.model_validate(negative))
    index_path.write_text(json.dumps(index))
    with pytest.raises(ValueError, match="nonpositive"):
        consumer.query_reviewed(index_path, query="adapter insertion")

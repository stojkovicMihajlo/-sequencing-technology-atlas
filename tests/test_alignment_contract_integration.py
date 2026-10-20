"""Adversarial layer-crossing contracts; synthetic fixtures are not scientific validation."""

import json

import pytest
from pydantic import ValidationError

from test_operation_registry import (
    admission_for, assignment, decision_for, registry as registry, review,
)
from test_shared_registry import bundle
from test_workflow_transformations import cases as cases, definition

from genomics_discovery.curation.operation_registry.admission import (
    CurrentVocabularyReview, VocabularyProposal,
)
from genomics_discovery.curation.operation_registry.definition_comparison import (
    ComparisonReport, FIELDS, FieldComparison, PairComparison,
)
from genomics_discovery.curation.operation_registry.proposal_queue import (
    attach_comparisons, attach_review, enqueue, missing_plan, plan, submission,
)
from genomics_discovery.curation.operation_registry.publication import publish
from genomics_discovery.curation.operation_registry.storage import connect
from genomics_discovery.curation.operation_registry.store import catalogue, graph_overlay
from genomics_discovery.curation.technology_graph.embeddings import EmbeddingCache
from genomics_discovery.curation.workflow_extraction.models import WorkflowReview, digest
from genomics_discovery.curation.workflow_extraction.transformations import cut_transformation


class ParaphraseBackend:
    """Known vectors isolate host retrieval/admission policy from model quality."""

    metadata = {"model": "fixture-paraphrase-contract", "revision": "1"}

    def encode(self, texts):
        return [[1.0, 0.0] if "saffron" in text or "turquoise" in text else [0.0, 1.0]
                for text in texts]


def opaque_bundle(cases, snapshot, tmp_path, *, token, cid, index=0, label=None):
    """Disjoint vocabularies defeat lexical overlap without pretending to be real biology."""
    proposal, _, manifest = bundle(cases, snapshot, tmp_path, index=index)
    original = proposal.decisions[0].assignment
    fields = original.definition.model_dump(mode="json")
    for field in fields:
        if field not in {"id", "mechanism_unknown_reason"}:
            fields[field] = token
    fields["id"] = cid
    if label is not None:
        fields["name"] = label
    assigned = type(original).model_validate({**original.model_dump(mode="json"),
        "definition": fields, "aliases": [token + "alias"], "scope_note": token})
    decision = decision_for(assigned)
    proposed = VocabularyProposal(decisions=(decision,))
    checked = CurrentVocabularyReview(proposal_sha256=digest(proposed),
        reviews=(admission_for(decision, review(assigned, cases)),))
    return proposed, checked, manifest


def report_for_plan(planned, output="unknown"):
    return ComparisonReport(plan_sha256=digest(planned), comparisons=tuple(
        PairComparison(pair_id=pair["pair_id"], fields=tuple(
            FieldComparison(field=field, comparison=output if field == "output" else "same",
                rationale="Synthetic comparison to exercise admission controls")
            for field in FIELDS)) for pair in planned["pairs"]))


def compare_current(registry, key, embeddings, *, output="unknown"):
    planned = missing_plan(registry.path, key, embeddings=embeddings)
    checked = report_for_plan(planned, output)
    attach_comparisons(registry.path, key, planned, checked, embeddings=embeddings)
    return planned


def comparison_profile_for(workflow, packet):
    from genomics_discovery.curation.workflow_matching.comparison_profiles import (
        ComparisonProfile, OPERATION_QUESTIONS, STATE_QUESTIONS,
    )

    def unknown(ident, *, mechanism=False):
        return {"id": ident, "status": "unknown" if mechanism else "not_assessed",
            "applicability": "applicable", "rationale": "unestablishedfixtureterm",
            **({"unknown_reason": "not_reported"} if mechanism else {})}

    def record(item, kind, questions):
        target = f"{kind}:{item.id}"
        fields = {field: [unknown(f"{target}:{field}", mechanism=field == "mechanism")]
                  for field in questions}
        field = "entity" if kind == "state" else "transformation"
        fields[field] = [{"id": f"{target}:{field}", "status": "reported",
            "applicability": "applicable", "value_kind": "value",
            "value": item.description if kind == "state" else item.action,
            "rationale": "Synthetic source-bound annotation",
            "evidence": [e.model_dump(mode="json") for e in item.evidence]}]
        return {"target_id": item.id, **fields}

    return ComparisonProfile.model_validate({"workflow_sha256": digest(workflow),
        "source_packet_sha256": digest(packet),
        "states": [record(s, "state", STATE_QUESTIONS) for s in workflow.states],
        "operations": [record(o, "operation", OPERATION_QUESTIONS) for o in workflow.operations]})


@pytest.mark.parametrize("committed", [False, True])
def test_profile_uncertainty_and_exact_bytes_survive_queue_boundaries(
        cases, registry, tmp_path, committed):
    from genomics_discovery.curation.workflow_matching.comparison_profiles import (
        ComparisonProfile, profile_retrieval_documents, validate_profile,
    )

    proposed, checked, manifest = bundle(cases, registry.snapshot(), tmp_path)
    workflow = cases[2][0]
    packet = cases[1][proposed.decisions[0].occurrence_id]
    profile = comparison_profile_for(workflow, packet)
    assert not validate_profile(profile, workflow, packet)
    documents = profile_retrieval_documents(profile, workflow, packet)
    assert documents
    assert not any(document["field"] == "mechanism" for document in documents)
    assert all("unestablishedfixtureterm" not in document["text"] for document in documents)
    profile_path = tmp_path / "a.comparison-profile.json"
    profile_path.write_text(profile.model_dump_json())
    next(iter(manifest.values()))["comparison_profile"] = str(profile_path)
    key = enqueue(registry.path, proposed, catalogue(registry.snapshot()), manifest)
    attach_review(registry.path, key, checked)
    if committed:
        assert publish(registry.path, key)["status"] == "committed"
    before = registry.snapshot()
    # The scientific assertions and workflow hashes can stay valid while the
    # reviewed sidecar changes. The immutable queue must bind the exact sidecar.
    changed = profile.model_dump(mode="json")
    changed["operations"][0]["mechanism"][0]["rationale"] = "A revised uncertainty explanation"
    revised = ComparisonProfile.model_validate(changed)
    assert not validate_profile(revised, workflow, packet)
    profile_path.write_text(revised.model_dump_json())
    with connect(registry.path) as db:
        with pytest.raises(ValueError, match="[Pp]rofile.*changed"):
            submission(db, key)
    with pytest.raises(ValueError, match="[Pp]rofile.*changed"):
        publish(registry.path, key)
    assert registry.snapshot() == before


@pytest.mark.parametrize("missing", ["selected_operation", "interface_state"])
def test_partial_profiles_cannot_omit_a_selected_operation_or_boundary_state(
        cases, registry, tmp_path, missing):
    from genomics_discovery.curation.workflow_matching.comparison_profiles import ComparisonProfile

    proposed, _, manifest = bundle(cases, registry.snapshot(), tmp_path)
    profile = comparison_profile_for(cases[2][0], cases[1][proposed.decisions[0].occurrence_id])
    data = profile.model_dump(mode="json")
    if missing == "selected_operation":
        data["operations"] = [row for row in data["operations"] if row["target_id"] != "repair"]
    else:
        data["states"] = [row for row in data["states"] if row["target_id"] != "dna"]
    path = tmp_path / "partial-profile.json"
    path.write_text(ComparisonProfile.model_validate(data).model_dump_json())
    next(iter(manifest.values()))["comparison_profile"] = str(path)
    with pytest.raises(ValueError, match="[Pp]rofile"):
        enqueue(registry.path, proposed, catalogue(registry.snapshot()), manifest)
    assert not registry.snapshot()["concepts"]


def test_requested_hybrid_policy_cannot_be_omitted_or_changed_at_publication(
        cases, registry, tmp_path):
    encoder = EmbeddingCache(tmp_path / "vectors.sqlite", ParaphraseBackend())
    proposed, checked, manifest = bundle(cases, registry.snapshot(), tmp_path)
    visible = catalogue(registry.snapshot())
    lexical_key = enqueue(registry.path, proposed, visible, manifest)
    semantic_key = enqueue(registry.path, proposed, visible, manifest,
        embeddings=encoder, semantic_limit=3)
    assert lexical_key != semantic_key
    attach_review(registry.path, semantic_key, checked)
    before = registry.snapshot()
    with pytest.raises(ValueError):
        publish(registry.path, semantic_key)
    changed_backend = ParaphraseBackend()
    changed_backend.metadata = {**changed_backend.metadata, "revision": "2"}
    changed_encoder = EmbeddingCache(tmp_path / "other-vectors.sqlite", changed_backend)
    with pytest.raises(ValueError):
        publish(registry.path, semantic_key, embeddings=changed_encoder)
    with pytest.raises(ValueError):
        plan(registry.path, semantic_key, embeddings=encoder, semantic_limit=4)
    assert registry.snapshot() == before
    assert publish(registry.path, semantic_key, embeddings=encoder)["status"] == "committed"


def test_committed_hybrid_replay_does_not_depend_on_a_later_candidate_universe(
        cases, registry, tmp_path):
    encoder = EmbeddingCache(tmp_path / "vectors.sqlite", ParaphraseBackend())
    proposed, checked, manifest = opaque_bundle(cases, registry.snapshot(), tmp_path,
        token="saffron", cid="committed")
    key = enqueue(registry.path, proposed, catalogue(registry.snapshot()), manifest,
        embeddings=encoder)
    attach_review(registry.path, key, checked)
    committed = publish(registry.path, key, embeddings=encoder)
    assert committed["status"] == "committed"
    late, _, late_manifest = opaque_bundle(cases, registry.snapshot(), tmp_path,
        token="turquoise", cid="later", index=1)
    enqueue(registry.path, late, catalogue(registry.snapshot()), late_manifest)
    before = registry.snapshot()
    replay = publish(registry.path, key)
    assert replay == {**committed, "idempotent_replay": True}
    assert registry.snapshot() == before


@pytest.mark.parametrize("same_label", [False, True])
def test_late_pending_candidate_requires_review_above_exhaustive_threshold(
        cases, registry, tmp_path, same_label):
    from genomics_discovery.curation.operation_registry.candidate_index import SMALL_CATALOGUE

    encoder = EmbeddingCache(tmp_path / "vectors.sqlite", ParaphraseBackend())
    # Pending proposals count even though none has entered the shared catalogue.
    for index in range(SMALL_CATALOGUE + 1):
        proposed, _, manifest = opaque_bundle(cases, registry.snapshot(), tmp_path,
            token=f"pepper{index}", cid=f"distractor-{index}")
        enqueue(registry.path, proposed, catalogue(registry.snapshot()), manifest)
    proposed, checked, manifest = opaque_bundle(cases, registry.snapshot(), tmp_path,
        token="saffron", cid="query")
    key = enqueue(registry.path, proposed, catalogue(registry.snapshot()), manifest,
        embeddings=encoder, semantic_limit=1)
    attach_review(registry.path, key, checked)
    compare_current(registry, key, encoder, output="different")
    late, _, late_manifest = opaque_bundle(cases, registry.snapshot(), tmp_path,
        token="turquoise", cid="late", index=1, label="saffron" if same_label else None)
    enqueue(registry.path, late, catalogue(registry.snapshot()), late_manifest)
    pending = publish(registry.path, key, embeddings=encoder)
    assert pending["status"] == "awaiting_comparisons"
    planned = compare_current(registry, key, encoder)
    assert len(planned["pairs"]) == 1
    assert planned["pairs"][0]["target"]["definition"]["id"] == "late"
    assert "exhaustive_small_catalogue" not in planned["pairs"][0]["routes"]
    assert publish(registry.path, key, embeddings=encoder)["status"] == "unresolved"
    assert not registry.snapshot()["concepts"]


def test_finished_pair_review_survives_late_candidate_without_skipping_new_pair(
        cases, registry, tmp_path):
    encoder = EmbeddingCache(tmp_path / "vectors.sqlite", ParaphraseBackend())
    proposed, checked, manifest = opaque_bundle(cases, registry.snapshot(), tmp_path,
        token="saffron", cid="query")
    key = enqueue(registry.path, proposed, catalogue(registry.snapshot()), manifest,
        embeddings=encoder)
    attach_review(registry.path, key, checked)
    peer, _, peer_manifest = opaque_bundle(cases, registry.snapshot(), tmp_path,
        token="turquoise", cid="peer", index=1)
    enqueue(registry.path, peer, catalogue(registry.snapshot()), peer_manifest)
    original_plan = plan(registry.path, key, embeddings=encoder)
    original_report = report_for_plan(original_plan, "different")
    assert len(original_plan["pairs"]) == 1
    late, _, late_manifest = opaque_bundle(cases, registry.snapshot(), tmp_path,
        token="paprika", cid="late", index=1)
    enqueue(registry.path, late, catalogue(registry.snapshot()), late_manifest)
    attach_comparisons(registry.path, key, original_plan, original_report, embeddings=encoder)
    remaining = missing_plan(registry.path, key, embeddings=encoder)
    assert len(remaining["pairs"]) == 1
    assert remaining["pairs"][0]["target"]["definition"]["id"] == "late"
    assert publish(registry.path, key, embeddings=encoder)["status"] == "awaiting_comparisons"
    compare_current(registry, key, encoder)
    assert publish(registry.path, key, embeddings=encoder)["status"] == "unresolved"
    assert not registry.snapshot()["concepts"]


def test_reconciliation_preserves_modes_and_profiles_without_using_a_foreign_backend(
        cases, registry, tmp_path):
    from genomics_discovery.curation.operation_registry.reconciliation import (
        replace_drafts, request, unresolved_components,
    )

    encoder = EmbeddingCache(tmp_path / "vectors.sqlite", ParaphraseBackend())
    keys, manifests = [], []
    for index, token in enumerate(("saffron", "turquoise")):
        proposed, checked, manifest = opaque_bundle(cases, registry.snapshot(), tmp_path,
            token=token, cid=f"parent-{index}", index=index)
        ident = proposed.decisions[0].occurrence_id
        profile = comparison_profile_for(cases[2][index], cases[1][ident])
        path = tmp_path / f"parent-{index}.profile.json"
        path.write_text(profile.model_dump_json())
        manifest[ident]["comparison_profile"] = str(path)
        key = enqueue(registry.path, proposed, catalogue(registry.snapshot()), manifest,
            embeddings=encoder, semantic_limit=3)
        attach_review(registry.path, key, checked)
        keys.append(key)
        manifests.append(manifest)
    compare_current(registry, keys[0], encoder)
    foreign_backend = ParaphraseBackend()
    foreign_backend.metadata = {**foreign_backend.metadata, "revision": "foreign"}
    foreign_encoder = EmbeddingCache(tmp_path / "foreign-vectors.sqlite", foreign_backend)
    foreign, foreign_review, foreign_manifest = opaque_bundle(cases, registry.snapshot(), tmp_path,
        token="ginger", cid="foreign")
    foreign_key = enqueue(registry.path, foreign, catalogue(registry.snapshot()), foreign_manifest,
        embeddings=foreign_encoder)
    attach_review(registry.path, foreign_key, foreign_review)
    assert unresolved_components(registry.path, embeddings=encoder, submission_ids=keys) == [sorted(keys)]
    with pytest.raises(ValueError):
        request(registry.path, keys)
    context = request(registry.path, keys, embeddings=encoder)
    assert context["retrieval_policy"]["semantic_limit"] == 3
    assert context["manifest"] == {ident: row for manifest in manifests for ident, row in manifest.items()}
    assert all(row.get("comparison_profile") for row in context["manifest"].values())
    parts = [opaque_bundle(cases, registry.snapshot(), tmp_path, token="sage",
        cid="replacement", index=index) for index in (0, 1)]
    replacement = VocabularyProposal(decisions=tuple(part[0].decisions[0] for part in parts))
    replacement_review = CurrentVocabularyReview(proposal_sha256=digest(replacement),
        reviews=tuple(part[1].reviews[0] for part in parts))
    lexical_key = enqueue(registry.path, replacement, catalogue(registry.snapshot()), context["manifest"])
    attach_review(registry.path, lexical_key, replacement_review)
    with pytest.raises(ValueError, match="[Rr]etrieval"):
        replace_drafts(registry.path, keys, lexical_key)
    with connect(registry.path) as db:
        assert all(submission(db, key)["result"] is None for key in keys)
    preserved_key = enqueue(registry.path, replacement, catalogue(registry.snapshot()), context["manifest"],
        embeddings=encoder, semantic_limit=3)
    attach_review(registry.path, preserved_key, replacement_review)
    assert replace_drafts(registry.path, keys, preserved_key)["status"] == "drafts_replaced"
    assert publish(registry.path, preserved_key, embeddings=encoder)["status"] == "awaiting_comparisons"
    assert not registry.snapshot()["concepts"]


def test_shared_purpose_with_unknown_mechanism_preserves_unequal_source_regions(
        cases, registry, tmp_path):
    occurrences, packets, workflows = cases
    long, previous_short = list(occurrences.values())
    parent_review = WorkflowReview.model_validate_json((tmp_path / "b.review.json").read_bytes())
    short = cut_transformation(workflows[1], packets[previous_short.id], ["insert"],
        review=parent_review)
    (tmp_path / "b.occurrence.json").write_text(short.model_dump_json())
    selected = ({long.id: long, short.id: short},
        {long.id: packets[long.id], short.id: packets[previous_short.id]}, workflows)
    paths = [tmp_path / f"{method}.{kind}.json" for method in ("a", "b")
        for kind in ("workflow", "occurrence", "review", "packet")]
    before = {path: path.read_bytes() for path in paths}
    shared = definition(id="tagging-purpose", name="Prepare tagged DNA",
        operation="Produce tagged DNA", output="Tagged DNA",
        physical_mechanism=None, mechanism_unknown_reason="Not assessed in this fixture")
    for index in (0, 1):
        snapshot = registry.snapshot()
        assigned = assignment(selected, snapshot, index=index, definition=shared,
            relation="shared_purpose", decision="new" if index == 0 else "reuse")
        decision = decision_for(assigned)
        proposed = VocabularyProposal(decisions=(decision,))
        checked = CurrentVocabularyReview(proposal_sha256=digest(proposed),
            reviews=(admission_for(decision, review(assigned, selected)),))
        method = (long, short)[index].method_id
        manifest = {decision.occurrence_id: {field: str(tmp_path / f"{method}.{field}.json")
            for field in ("workflow", "packet", "review", "occurrence")}}
        key = enqueue(registry.path, proposed, catalogue(snapshot), manifest)
        attach_review(registry.path, key, checked)
        assert publish(registry.path, key)["status"] == "committed"
    snapshot = registry.snapshot()
    assert sorted(len(row["occurrence"]["operations"])
        for row in snapshot["assignments"].values()) == [1, 2]
    assert {row["occurrence_sha256"] for row in snapshot["assignments"].values()} == {
        digest(long), digest(short)}
    assert {row["occurrence"]["variant"] for row in snapshot["assignments"].values()} == {
        "Purified DNA", "Chromatin-associated DNA"}
    overlay = graph_overlay(snapshot)
    assert len(overlay["edges"]) == 2
    assert {edge["kind"] for edge in overlay["edges"]} == {"instance_of_concept"}
    assert {edge["basis"] for edge in overlay["edges"]} == {"shared_purpose"}
    assert all(path.read_bytes() == original for path, original in before.items())
    assigned_data = assigned.model_dump(mode="json")
    assigned_data["relation"] = "shared_mechanism"
    with pytest.raises(ValidationError, match="unknown mechanism"):
        type(assigned).model_validate(assigned_data)


async def test_required_profiles_cannot_be_dropped_before_source_review(
        cases, registry, tmp_path):
    from playground.workflow_concepts_live import execute

    _, _, manifest = bundle(cases, registry.snapshot(), tmp_path)
    manifest_path = tmp_path / "required-profile-manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    class NeverRun:
        calls = 0

        async def run(self, *_):
            self.calls += 1
            raise AssertionError("Missing required profiles must fail before a model worker")

    worker = NeverRun()
    with pytest.raises(ValueError, match="[Pp]rofile"):
        await execute(manifest_path, registry.path, tmp_path / "profile-run", worker=worker,
            curate=True, require_comparison_profiles=True)
    assert worker.calls == 0
    assert not registry.snapshot()["concepts"]


async def test_profile_and_hybrid_modes_reach_both_fresh_source_review_stages(
        cases, registry, tmp_path):
    from test_operation_registry_live import WorkerFixture
    from playground.workflow_concepts_live import execute
    from genomics_discovery.curation.run_state import sha

    proposed, _, manifest = bundle(cases, registry.snapshot(), tmp_path)
    profile = comparison_profile_for(cases[2][0], cases[1][proposed.decisions[0].occurrence_id])
    profile_path = tmp_path / "required-profile.json"
    profile_path.write_text(profile.model_dump_json())
    next(iter(manifest.values()))["comparison_profile"] = str(profile_path)
    manifest_path = tmp_path / "profile-manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    run = tmp_path / "profile-aware-review"
    encoder = EmbeddingCache(tmp_path / "vectors.sqlite", ParaphraseBackend())

    class InspectingWorker(WorkerFixture):
        async def run(self, prompt, attempt):
            assert str(run / "comparison_profiles.json") in prompt
            assert "Unknown and not_assessed assertions are not agreement" in prompt
            views = json.loads((run / "comparison_profiles.json").read_text())["selections"]
            view = views[proposed.decisions[0].occurrence_id]
            assert view["parent_profile_sha256"] == digest(profile)
            assert view["occurrence_sha256"] == proposed.decisions[0].assignment.members[0].occurrence_sha256
            assert {row["target_id"] for row in view["profile"]["operations"]} == {"insert", "repair"}
            return await super().run(prompt, attempt)

    worker = InspectingWorker(cases, registry.snapshot())
    result = await execute(manifest_path, registry.path, run, worker=worker,
        curate=True, defer_admission=True, embeddings=encoder, semantic_limit=3,
        require_comparison_profiles=True)
    assert result["status"] == "reviewed_pending_admission"
    assert worker.calls == 2
    assert len({row["thread_id"] for row in result["attempts"]}) == 2
    bindings = json.loads((run / "bindings.json").read_text())
    assert bindings[str(profile_path)] == sha(profile_path)
    assert bindings[str(run / "comparison_profiles.json")] == sha(run / "comparison_profiles.json")
    with connect(registry.path) as db:
        saved = submission(db, result["submission_id"])
    assert saved["comparison_profile_files"][str(profile_path)] == sha(profile_path)
    assert saved["retrieval_policy"]["semantic_limit"] == 3
    assert not registry.snapshot()["concepts"]
    assert publish(registry.path, result["submission_id"], embeddings=encoder)["status"] == "committed"

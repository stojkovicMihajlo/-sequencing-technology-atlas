"""Synthetic membership judgments test gates, not scientific extraction quality."""

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from genomics_discovery.curation.workflow_extraction.models import (
    Workflow,
    WorkflowReview,
    digest,
    target_ids,
)
from genomics_discovery.curation.workflow_extraction.sources import build_packet
from genomics_discovery.curation.workflow_extraction.transformations import (
    MembershipCandidate,
    MembershipProposal,
    MembershipReview,
    TransformationDefinition,
    build_membership_prompt,
    cut_transformation,
    load_occurrences,
    main,
    review_membership,
    validate_membership,
    validate_membership_review,
)


def test_summary_check_returns_only_integrity_bindings(cases, tmp_path, monkeypatch, capsys):
    candidate = candidate_for(cases)
    review = review_for(candidate, cases)
    cp, rp, mp = (tmp_path / name for name in ("candidate.json", "review.json", "manifest.json"))
    cp.write_text(candidate.model_dump_json())
    rp.write_text(review.model_dump_json())
    mp.write_text(json.dumps({ident: {key: f"{unit.method_id}.{key}.json"
                                     for key in ("occurrence", "packet", "workflow", "review")}
                             for ident, unit in cases[0].items()}))
    other_directory = tmp_path / "different_cwd"
    other_directory.mkdir()
    monkeypatch.chdir(other_directory)
    monkeypatch.setattr(sys, "argv", ["transformations", "check", str(cp), str(rp), str(mp),
                                    "--summary"])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result == {"integrity": "valid", "candidate_sha256": digest(candidate),
                      "review_sha256": digest(review)}


@pytest.fixture
def cases(tmp_path):
    occurrences, packets, workflows = {}, {}, []
    for method, substrate in (("a", "Purified DNA"), ("b", "Chromatin-associated DNA")):
        source = tmp_path / f"{method}.txt"
        source.write_text(
            "Expose double-stranded DNA substrate.\n\n"
            "Fragment and attach adapters with a transposase; repair by polymerase copying. "
            "Keep an analytical aliquot and measure its signal.\n\nUse buffer at 37 C."
        )
        packet = build_packet(
            method_id=method,
            title=method,
            scope="Synthetic transformation",
            variant=substrate,
            sources=[
                {
                    "source_id": "paper",
                    "title": "Synthetic source",
                    "url": "https://example.org/fixture",
                    "role": "primary_paper",
                    "path": str(source),
                }
            ],
        )
        evidence = [
            {
                "source_id": "paper",
                "passage_id": p["passage_id"],
                "quote": p["text"],
                "support": "reported",
                "rationale": "Synthetic contract evidence",
            }
            for p in packet["sources"][0]["passages"]
        ]
        states = [
            {
                "id": sid,
                "kind": kind,
                "description": description,
                "evidence": [evidence[0 if sid == "dna" else 1]],
            }
            for sid, kind, description in (
                ("dna", "material", substrate),
                ("tagged", "material", "Tagged DNA"),
                ("library", "material", "Repaired DNA"),
                ("signal", "data", "Analytical signal"),
            )
        ]
        operations = [
            {
                "id": oid,
                "name": oid,
                "action": action,
                "inputs": inputs,
                "outputs": outputs,
                "information_effect": "Retain insert and adapter identity",
                "resources": [
                    {"kind": "condition", "description": "37 C", "evidence": [evidence[2]]}
                ]
                if oid == "insert"
                else [],
                "evidence": [evidence[1]],
            }
            for oid, action, inputs, outputs in (
                ("insert", "Transposase fragmentation and adapter insertion", ["dna"], ["tagged"]),
                ("repair", "Polymerase copying repair", ["tagged"], ["library"]),
                ("assay", "Measure analytical aliquot", ["tagged"], ["signal"]),
            )
        ]
        flows = [
            {
                "id": f"f{index}",
                "source": start,
                "target": end,
                "kind": kind,
                "basis": "Declared synthetic route",
                "condition": None,
                "evidence": [evidence[1]],
            }
            for index, (start, end, kind) in enumerate(
                (
                    ("dna", "insert", "material_input"),
                    ("insert", "tagged", "material_output"),
                    ("tagged", "repair", "material_input"),
                    ("repair", "library", "material_output"),
                    ("tagged", "assay", "material_input"),
                    ("assay", "signal", "data_output"),
                )
            )
        ]
        workflow = Workflow.model_validate(
            {
                **{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
                "states": states,
                "operations": operations,
                "flows": flows,
                "modules": [
                    {
                        "id": "unit",
                        "name": "unit",
                        "operation_ids": ["insert", "repair", "assay"],
                        "boundary_rationale": "Synthetic module",
                    }
                ],
                "unresolved": [
                    {
                        "id": "detail",
                        "description": "Chemical detail remains unknown",
                        "blocking": False,
                        "next_evidence": "Inspect original dependency",
                    }
                ],
            }
        )
        review = WorkflowReview.model_validate(
            {
                "workflow_sha256": digest(workflow),
                "source_packet_sha256": digest(packet),
                "inspected_passages": [
                    {"source_id": "paper", "passage_id": e["passage_id"]} for e in evidence
                ],
                "checks": [
                    {
                        "target_id": target,
                        "verdict": "supported",
                        "rationale": "Fixture only",
                        "evidence": evidence,
                    }
                    for target in target_ids(workflow)
                ],
                "omissions": [
                    {
                        "description": "Record correction remains",
                        "blocking": False,
                        "action": "revise_record",
                        "evidence": evidence,
                    }
                ],
                "notes": [],
            }
        )
        occurrence = cut_transformation(workflow, packet, ["insert", "repair"], review=review)
        for name, content in (("workflow", workflow.model_dump(mode="json")),
                              ("review", review.model_dump(mode="json")),
                              ("occurrence", occurrence.model_dump(mode="json")),
                              ("packet", packet)):
            (tmp_path / f"{method}.{name}.json").write_text(json.dumps(content))
        occurrences[occurrence.id], packets[occurrence.id] = occurrence, packet
        workflows.append(workflow)
    return occurrences, packets, workflows


def definition(**updates):
    return TransformationDefinition.model_validate(
        {
            "id": "adapter-bearing-fragment-production",
            "name": "Produce repaired adapter-bearing DNA",
            "substrate": "Double-stranded DNA",
            "operation": "Fragment and attach adapters, then repair",
            "physical_mechanism": "Transposase strand transfer followed by polymerase copying",
            "output": "Repaired tagged DNA",
            "information_effect": "Retain insert identity with adapter domains",
            "purpose": "Prepare adapter-bearing DNA fragments",
            "context_requirements": "Substrate accessibility and reported enzymes; "
                                    "side branches remain distinct",
            **updates,
        }
    )


def candidate_for(cases, **updates):
    occurrences, packets, _ = cases
    return MembershipCandidate.model_validate(
        {
            "definition": definition(),
            "relation": "shared_mechanism",
            "members": [
                {
                    "occurrence_id": ident,
                    "occurrence_sha256": digest(item),
                    "rationale": "Synthetic membership, not a scientific judgment",
                    "evidence": [
                        e.model_dump(mode="json") for op in item.operations for e in op.evidence
                    ],
                }
                for ident, item in occurrences.items()
            ],
            **updates,
        }
    )


def review_for(candidate, cases, verdict="supported"):
    occurrences, packets, _ = cases
    checks = []
    for ident in occurrences:
        evidence = [
            {
                "source_id": "paper",
                "passage_id": p["passage_id"],
                "quote": p["text"],
                "support": "reported",
                "rationale": "Synthetic source inspection",
            }
            for p in packets[ident]["sources"][0]["passages"]
        ]
        checks.append(
            {
                "occurrence_id": ident,
                "verdict": verdict,
                "rationale": "Fixture only",
                "evidence": evidence,
                "inspected_passages": [
                    {"source_id": "paper", "passage_id": e["passage_id"]} for e in evidence
                ],
            }
        )
    return MembershipReview.model_validate(
        {"candidate_sha256": digest(candidate), "checks": checks, "notes": []}
    )


def test_whole_cut_preserves_substrate_actions_information_and_external_branches(cases):
    occurrences, _, _ = cases
    for occurrence in occurrences.values():
        assert [state.id for state in occurrence.inputs] == ["dna"]
        assert {state.id for state in occurrence.outputs} == {"tagged", "library"}
        assert [state.id for state in occurrence.internal_states] == ["tagged"]
        assert any(flow.target == "assay" for flow in occurrence.flows)
        assert len(occurrence.information_effects) == 2
        assert occurrence.requirements[0].description == "37 C"
        assert occurrence.unresolved[0].id == "detail"
        assert occurrence.workflow_review_status == "needs_revision"
        assert occurrence.physical_mechanism == "not_separately_assessed"
        assert [(ref.operation_id, ref.json_pointer) for ref in occurrence.external_operations] == [
            ("assay", "/operations/2")]


@pytest.mark.parametrize("selection", [[], ["insert", "insert"], ["missing"], ["repair", "assay"]])
def test_words_or_adjacent_display_positions_cannot_define_a_whole_transformation(cases, selection):
    _, packets, workflows = cases
    with pytest.raises(ValueError, match="selection|routes"):
        cut_transformation(workflows[0], next(iter(packets.values())), selection)


@pytest.fixture
def decision_case(tmp_path):
    original = tmp_path / "decision.txt"
    original.write_text("Measure concentration from an analytical aliquot. Load the retained "
                        "library only if the measured concentration exceeds "
                        "the reported threshold.")
    packet = build_packet(method_id="decision", title="Decision-coupled unit",
        scope="QC to loading",
        variant="reported decision", sources=[{"source_id": "paper", "title": "Fixture protocol",
            "url": "https://example.org/decision", "role": "primary_paper", "path": str(original)}])
    passage = packet["sources"][0]["passages"][0]
    evidence = {"source_id": "paper", "passage_id": passage["passage_id"], "quote": passage["text"],
                "support": "reported", "rationale": "Synthetic explicitly reported decision"}
    states = [{"id": ident, "kind": kind, "description": ident, "evidence": [evidence]}
              for ident, kind in (("aliquot", "material"), ("library", "material"),
                                   ("concentration", "data"), ("loaded", "material"))]
    operations = [{"id": ident, "name": ident, "action": action, "inputs": inputs,
                   "outputs": outputs, "resources": [], "evidence": [evidence],
                   "information_effect": "Reported concentration governs library loading"}
                  for ident, action, inputs, outputs in (
                      ("quantify", "Measure concentration", ["aliquot"], ["concentration"]),
                      ("load", "Load only when threshold passes", ["library"], ["loaded"]))]
    flows = [{"id": f"f{index}", "source": start, "target": end, "kind": kind,
              "basis": "Declared synthetic route", "evidence": [evidence]}
             for index, (start, end, kind) in enumerate((
                 ("aliquot", "quantify", "material_input"),
                 ("quantify", "concentration", "data_output"),
                 ("library", "load", "material_input"), ("load", "loaded", "material_output")))]
    flows.append({"id": "decision", "source": "quantify", "target": "load", "kind": "control",
                  "basis": "Reported concentration decides whether to load", "evidence": [evidence],
                  "condition": "Measured concentration exceeds reported threshold"})
    raw = {**{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
           "states": states, "operations": operations, "flows": flows, "unresolved": [],
           "modules": [{"id": "unit", "name": "Decision-coupled unit",
                        "operation_ids": ["quantify", "load"],
                        "boundary_rationale": "The reported decision couples the actions"}]}
    return raw, packet


def test_explicit_decision_control_connects_a_whole_unit_and_retains_parent_replay(
    decision_case, tmp_path,
):
    raw, packet = decision_case
    workflow = Workflow.model_validate(raw)
    cut = cut_transformation(workflow, packet, ["quantify", "load"])
    assert {state.id for state in cut.inputs} == {"aliquot", "library"}
    assert {state.id for state in cut.outputs} == {"concentration", "loaded"}
    control = next(flow for flow in cut.flows if flow.kind == "control")
    assert control.condition == "Measured concentration exceeds reported threshold"
    assert control.evidence
    assert cut.workflow_review_status == "not_reviewed"
    rows = {"workflow": workflow.model_dump(mode="json"), "packet": packet,
            "occurrence": cut.model_dump(mode="json")}
    manifest = {cut.id: {name: str(tmp_path / f"{name}.json") for name in rows}}
    for name, content in rows.items():
        Path(manifest[cut.id][name]).write_text(json.dumps(content))
    rebuilt, _ = load_occurrences(manifest)
    assert digest(rebuilt[cut.id]) == digest(cut)


def test_reported_precedence_alone_cannot_replace_decision_or_material_data_routes(decision_case):
    raw, packet = decision_case
    raw["flows"][-1].update(kind="precedence", condition=None)
    with pytest.raises(ValueError, match="reported precedence alone"):
        cut_transformation(Workflow.model_validate(raw), packet, ["quantify", "load"])


@pytest.mark.parametrize(
    "facet",
    ["substrate", "operation", "output", "information_effect", "purpose", "context_requirements"],
)
def test_definition_requires_complete_facets_not_broad_words(facet):
    raw = definition().model_dump(mode="json")
    raw.pop(facet)
    with pytest.raises(ValidationError):
        TransformationDefinition.model_validate(raw)
    with pytest.raises(ValidationError):
        TransformationDefinition.model_validate({"id": "ligation", "name": "ligation"})


def test_shared_purpose_does_not_establish_a_shared_known_mechanism(cases):
    unknown = definition(
        physical_mechanism=None, mechanism_unknown_reason="Source leaves it unresolved"
    )
    candidate_for(cases, definition=unknown, relation="shared_purpose")
    with pytest.raises(ValidationError, match="unknown mechanism"):
        candidate_for(cases, definition=unknown, relation="shared_mechanism")
    with pytest.raises(ValidationError, match="explicit reason"):
        definition(physical_mechanism=None)
    assert (
        MembershipProposal(notes=("Broad labels alone establish no correspondence",)).candidates
        == ()
    )


@pytest.mark.parametrize("relation", ["equivalent", "compatible", "derived_from", "merge"])
def test_no_substitution_ancestry_or_unsupported_merge_relation(cases, relation):
    with pytest.raises(ValidationError):
        candidate_for(cases, relation=relation)


def test_membership_support_retains_original_workflow_review_and_distinct_contexts(cases):
    occurrences, packets, workflows = cases
    candidate = candidate_for(cases)
    before = [digest(workflow) for workflow in workflows]
    result = review_membership(candidate, review_for(candidate, cases), occurrences, packets)
    assert result["status"] == "supported_membership_candidate"
    assert {row["workflow_review_status"] for row in result["members"]} == {"needs_revision"}
    assert {item.inputs[0].description for item in occurrences.values()} == {
        "Purified DNA",
        "Chromatin-associated DNA",
    }
    assert (
        result["compatibility"]
        == result["ancestry"]
        == result["production_acceptance"]
        == ("not_assessed")
    )
    assert result["transitive_inference"] is False
    assert before == [digest(workflow) for workflow in workflows]


@pytest.mark.parametrize(
    "verdict,expected", [("unsupported", "needs_revision"), ("unresolved", "research_needed")]
)
def test_fresh_negative_membership_review_cannot_accept_a_merge(cases, verdict, expected):
    occurrences, packets, _ = cases
    candidate = candidate_for(cases)
    assert (
        review_membership(candidate, review_for(candidate, cases, verdict), occurrences, packets)[
            "status"
        ]
        == expected
    )


@pytest.mark.parametrize(
    "fault",
    [
        "stale_candidate",
        "missing_check",
        "duplicate_check",
        "fabricated_quote",
        "uninspected",
        "missing_interface",
    ],
)
def test_membership_review_must_reopen_every_bound_interface(cases, fault):
    occurrences, packets, _ = cases
    candidate = candidate_for(cases)
    raw = review_for(candidate, cases).model_dump(mode="json")
    if fault == "stale_candidate":
        raw["candidate_sha256"] = "0" * 64
    elif fault == "missing_check":
        raw["checks"].pop()
    elif fault == "duplicate_check":
        raw["checks"].append(deepcopy(raw["checks"][0]))
    elif fault == "fabricated_quote":
        raw["checks"][0]["evidence"][0]["quote"] = "Invented biochemical proof"
    elif fault == "uninspected":
        raw["checks"][0]["inspected_passages"].pop()
    else:
        raw["checks"][0]["evidence"] = raw["checks"][0]["evidence"][1:]
    assert validate_membership_review(
        MembershipReview.model_validate(raw), candidate, occurrences, packets
    )


def test_stale_member_or_expanded_source_context_cannot_reuse_review(cases):
    occurrences, packets, _ = cases
    candidate = candidate_for(cases)
    raw = candidate.model_dump(mode="json")
    raw["members"][0]["occurrence_sha256"] = "0" * 64
    assert any(
        "Stale occurrence" in error
        for error in validate_membership(
            MembershipCandidate.model_validate(raw), occurrences, packets
        )
    )
    changed = deepcopy(packets)
    first = next(iter(changed))
    changed[first]["scope"] += " Changed applicability"
    assert any(
        "Changed source packet" in error
        for error in validate_membership(candidate, occurrences, changed)
    )


@pytest.mark.parametrize("fault", [None, "authored_action", "authored_review_status",
                                  "missing_review"])
def test_occurrences_are_rebuilt_from_parent_records_before_native_review_gates(cases, fault):
    occurrences, packets, _ = cases
    manifest = {}
    for ident, occurrence in occurrences.items():
        root = Path(packets[ident]["sources"][0]["original_path"]).parent
        manifest[ident] = {name: str(root / f"{occurrence.method_id}.{name}.json")
                           for name in ("occurrence", "packet", "workflow", "review")}
    ident = next(iter(manifest))
    path = Path(manifest[ident]["occurrence"])
    raw = json.loads(path.read_text())
    if fault == "authored_action":
        raw["operations"][0]["action"] = "Invent a different molecular reaction"
    elif fault == "authored_review_status":
        raw["workflow_review_status"] = "supported"
    elif fault == "missing_review":
        manifest[ident].pop("review")
    path.write_text(json.dumps(raw))
    if fault:
        with pytest.raises(ValueError, match="bound parent workflow/review"):
            load_occurrences(manifest)
    else:
        rebuilt, _ = load_occurrences(manifest)
        assert digest(rebuilt[ident]) == digest(occurrences[ident])


def test_native_prompt_provides_complete_definition_and_independent_review_paths():
    inputs = {"one": Path("/frozen/occurrence.json")}
    packets = {"one": Path("/frozen/packet.json")}
    compiler = build_membership_prompt(
        "compiler",
        occurrences=inputs,
        packets=packets,
        schema=Path("/frozen/schema.json"),
        artifact=Path("/attempt/artifact.json"),
    )
    assert "candidates=[]" in compiler and "hypotheses pending fresh review" in compiler
    assert "PYTHONPATH=/" in compiler and "catalog /frozen/packet.json" in compiler
    assert "search /frozen/packet.json 'specific terms'" in compiler
    assert "--source-id SOURCE_ID --passage-id PASSAGE_ID" in compiler
    assert "feedback controller" in compiler and "fresh source-first membership review" in compiler
    reviewer = build_membership_prompt(
        "reviewer",
        occurrences=inputs,
        packets=packets,
        schema=Path("/frozen/schema.json"),
        artifact=Path("/attempt/artifact.json"),
        candidate=Path("/frozen/candidate.json"),
    )
    assert (
        "do not read compiler conversation" in reviewer
        and "Emit checks ONLY for candidate.members" in reviewer
    )
    with pytest.raises(ValueError):
        build_membership_prompt(
            "reviewer",
            occurrences=inputs,
            packets=packets,
            schema=Path("schema.json"),
            artifact=Path("artifact.json"),
        )


@pytest.mark.parametrize("claim", ["route", "internal_state", "gap_resolution"])
def test_supported_membership_inspects_each_distinct_whole_cut_claim(cases, claim):
    occurrences, packets, workflows = cases
    original_id = next(iter(occurrences))
    parent = occurrences[original_id]
    old_packet = packets[original_id]
    original = Path(old_packet["sources"][0]["original_path"])
    text = {"route": "The analytical aliquot is not returned to the production library.",
            "internal_state": "Tagged DNA retains adapter-bearing sequence before repair.",
            "gap_resolution": "The exact molecular nick structure is outside this declared unit."
            }[claim]
    original.write_text(original.read_text() + "\n\n" + text)
    source = old_packet["sources"][0]
    packet = build_packet(**{key: old_packet[key]
                            for key in ("method_id", "title", "scope", "variant")}, sources=[{
        **{key: source[key] for key in ("source_id", "title", "url", "role")},
        "path": str(original),
    }])
    passage = packet["sources"][0]["passages"][-1]
    evidence = {"source_id": source["source_id"], "passage_id": passage["passage_id"],
                "quote": passage["text"], "support": "reported",
                "rationale": "This distinct passage supports a retained whole-unit claim."}
    raw = workflows[0].model_dump(mode="json")
    if claim == "route":
        next(flow for flow in raw["flows"] if flow["target"] == "assay")["evidence"] = [evidence]
    elif claim == "internal_state":
        next(state for state in raw["states"] if state["id"] == "tagged")["evidence"] = [evidence]
        # Make tagged DNA exclusively internal, so input/output coverage cannot satisfy this test.
        raw["operations"] = [op for op in raw["operations"] if op["id"] != "assay"]
        raw["states"] = [state for state in raw["states"] if state["id"] != "signal"]
        raw["flows"] = [flow for flow in raw["flows"]
                        if not {flow["source"], flow["target"]} & {"assay", "signal"}]
        raw["modules"][0]["operation_ids"].remove("assay")
    else:
        raw["gap_resolutions"] = [{"reader_role": "chemistry", "gap_id": "nick-detail",
                                   "rationale": "Source-backed scope exclusion requires review",
                                   "evidence": [evidence]}]
    workflow = Workflow.model_validate(raw)
    rebuilt = cut_transformation(workflow, packet, [op.id for op in parent.operations])
    updated_occurrences = {key: item for key, item in occurrences.items() if key != original_id}
    updated_packets = {key: item for key, item in packets.items() if key != original_id}
    updated_occurrences[rebuilt.id], updated_packets[rebuilt.id] = rebuilt, packet
    updated = updated_occurrences, updated_packets, workflows
    candidate = candidate_for(updated)
    complete_review = review_for(candidate, updated)
    assert validate_membership_review(complete_review, candidate,
                                      updated_occurrences, updated_packets) == []
    review_raw = complete_review.model_dump(mode="json")
    check = next(item for item in review_raw["checks"] if item["occurrence_id"] == rebuilt.id)
    check["evidence"] = [item for item in check["evidence"]
                         if item["passage_id"] != passage["passage_id"]]
    check["inspected_passages"] = [item for item in check["inspected_passages"]
                                   if item["passage_id"] != passage["passage_id"]]
    review = MembershipReview.model_validate(review_raw)
    errors = validate_membership_review(review, candidate, updated_occurrences, updated_packets)
    assert any("Supported membership lacks evidence" in error for error in errors), errors


def test_review_record_roundtrips_full_membership_evidence_checks_and_status(cases):
    from playground.workflow_transformations_live import comparison_graph

    occurrences, packets, _ = cases
    candidate = candidate_for(cases)
    review = review_for(candidate, cases)
    record = review_membership(candidate, review, occurrences, packets)
    restored = json.loads(json.dumps(record))
    assert MembershipCandidate.model_validate(restored["candidate"]) == candidate
    assert MembershipReview.model_validate(restored["review"]) == review
    assert digest(restored["candidate"]) == restored["candidate_sha256"]
    assert digest(restored["review"]) == restored["review_sha256"]
    graph = json.loads(json.dumps(comparison_graph(occurrences, [restored])))
    definition_node = next(node for node in graph["nodes"]
                           if node["kind"] == "transformation_definition")
    assert definition_node["definition"] == candidate.definition.model_dump(mode="json")
    assert definition_node["candidate"]["members"][0]["rationale"]
    assert definition_node["candidate"]["members"][0]["evidence"]
    assert definition_node["review"]["checks"][0]["rationale"]
    assert definition_node["review"]["checks"][0]["evidence"]
    assert definition_node["review"]["checks"][0]["inspected_passages"]
    shared = [edge for edge in graph["edges"] if edge["kind"] == "shared_mechanism"]
    assert {edge["source"] for edge in shared} == {member.occurrence_id
                                                   for member in candidate.members}
    assert all(edge["review_sha256"] == digest(review) for edge in shared)
    assert {member["workflow_review_status"] for member in record["members"]} == {"needs_revision"}
    assert record["production_acceptance"] == record["compatibility"] == record["ancestry"] == (
        "not_assessed")


def test_negative_membership_keeps_source_judgments_without_shared_edges(cases):
    from playground.workflow_transformations_live import comparison_graph

    occurrences, packets, _ = cases
    candidate = candidate_for(cases)
    review = review_for(candidate, cases, verdict="unsupported")
    record = review_membership(candidate, review, occurrences, packets)
    assert record["status"] == "needs_revision"
    assert record["review"]["checks"][0]["verdict"] == "unsupported"
    assert record["review"]["checks"][0]["evidence"]
    graph = comparison_graph(occurrences, [record])
    assert graph["comparisons"][0]["review"] == review.model_dump(mode="json")
    assert not any(edge["kind"] in {"shared_mechanism", "shared_purpose"}
                   for edge in graph["edges"])


def test_reviewer_exact_ids_digest_and_self_check_are_bound_to_candidate(cases, tmp_path):
    candidate = candidate_for(cases)
    path = tmp_path / "candidate.json"
    path.write_text(candidate.model_dump_json())
    occurrences = {member.occurrence_id: tmp_path / "occurrence.json"
                   for member in candidate.members}
    occurrences["context-only"] = tmp_path / "extra.json"
    packets = {ident: tmp_path / "packet.json" for ident in occurrences}
    manifest = tmp_path / "manifest with spaces.json"
    prompt = build_membership_prompt("reviewer", occurrences=occurrences, packets=packets,
        schema=tmp_path / "schema.json", artifact=tmp_path / "artifact.json", candidate=path,
        manifest=manifest)
    required = next(line for line in prompt.splitlines()
                    if line.startswith("Required occurrence_id"))
    assert set(json.loads(required.split(": ", 1)[1])) == {
        member.occurrence_id for member in candidate.members}
    assert "context-only" not in required
    assert digest(candidate) in prompt
    assert "Other provided occurrences are context, not review targets" in prompt
    assert "workflow_extraction.transformations check" in prompt
    assert "'" + str(manifest) + "'" in prompt


def test_reviewer_cannot_start_without_a_candidate_members_source_inputs(cases, tmp_path):
    candidate = candidate_for(cases)
    path = tmp_path / "candidate.json"
    path.write_text(candidate.model_dump_json())
    ident = candidate.members[0].occurrence_id
    with pytest.raises(ValueError, match="Missing source inputs"):
        build_membership_prompt("reviewer", occurrences={ident: tmp_path / "occurrence.json"},
            packets={ident: tmp_path / "packet.json"}, schema=tmp_path / "schema.json",
            artifact=tmp_path / "artifact.json", candidate=path)

"""Synthetic provenance/uncertainty gates, not extraction or biological validation."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from genomics_discovery.curation.workflow_extraction.models import Workflow, digest
from genomics_discovery.curation.workflow_extraction.sources import build_packet
from genomics_discovery.curation.workflow_matching.comparison_profiles import (
    OPERATION_QUESTIONS,
    STATE_QUESTIONS,
    ComparisonProfile,
    ProfileAssertion,
    comparison_profile_schema,
    comparison_profile_view,
    load_comparison_profile,
    profile_retrieval_documents,
    profile_retrieval_text,
    validate_profile,
    validate_profile_selection,
)


def assertion(ident, **updates):
    return {"id": ident, "status": "not_assessed", "applicability": "applicable",
            "rationale": "This question has not been assessed.", **updates}


@pytest.fixture
def example(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text(
        "Separate DNA and RNA from each nucleus into fractions that retain the indexed well.\n\n"
        "Reverse transcribe RNA to cDNA. DNA is not amplified."
    )
    packet = build_packet(method_id="fractionation", title="Synthetic workflow",
                          scope="Nuclear fractionation and reverse transcription", variant="well",
                          sources=[{"source_id": "paper", "title": "Synthetic source",
                                    "url": "https://example.org/fixture", "role": "primary_paper",
                                    "path": str(source)}])
    evidence = [{"source_id": "paper", "passage_id": p["passage_id"], "quote": p["text"],
                 "support": "reported", "rationale": "Synthetic quoted support."}
                for p in packet["sources"][0]["passages"]]
    states = [{"id": ident, "kind": "material", "description": description,
               "evidence": [evidence[0 if ident != "cdna" else 1]]}
              for ident, description in (("nucleus", "Indexed nucleus"), ("dna", "DNA fraction"),
                                         ("rna", "RNA fraction"), ("cdna", "cDNA product"))]
    operations = [{"id": ident, "name": name, "action": name, "inputs": inputs,
                   "outputs": outputs, "resources": [], "information_effect": effect,
                   "evidence": [evidence[index]]}
                  for ident, name, inputs, outputs, effect, index in (
                      ("separate", "Separate nuclear fractions", ["nucleus"], ["dna", "rna"],
                       "Retain indexed-well attribution", 0),
                      ("rt", "Reverse transcription", ["rna"], ["cdna"],
                       "Represent RNA information in cDNA", 1))]
    routes = (("nucleus", "separate", "material_input"),
              ("separate", "dna", "material_output"),
              ("separate", "rna", "material_output"),
              ("rna", "rt", "material_input"), ("rt", "cdna", "material_output"))
    workflow = Workflow.model_validate({
        **{field: packet[field] for field in ("method_id", "title", "scope", "variant")},
        "states": states, "operations": operations,
        "flows": [{"id": f"f{i}", "source": start, "target": end, "kind": kind,
                   "basis": "Declared synthetic route", "evidence": [evidence[i > 2]]}
                  for i, (start, end, kind) in enumerate(routes)],
        "modules": [{"id": "module", "name": "Fractionation and conversion",
                     "operation_ids": ["separate", "rt"], "boundary_rationale": "Fixture"}],
        "unresolved": [],
    })
    data = {"workflow_sha256": digest(workflow), "source_packet_sha256": digest(packet),
            "states": [{"target_id": "dna", **{
                field: [assertion(f"dna-{field}")] for field in STATE_QUESTIONS}}],
            "operations": [{"target_id": "separate", **{
                field: [assertion(f"separate-{field}")] for field in OPERATION_QUESTIONS}}]}
    data["states"][0]["entity"] = [assertion(
        "dna-entity", status="reported", value="DNA", value_kind="value", evidence=[evidence[0]])]
    data["operations"][0]["purpose"] = [assertion(
        "separate-purpose", status="inferred", value="Obtain separately accessible DNA and RNA",
        value_kind="value", evidence=[{**evidence[0], "support": "inferred"}])]
    return workflow, packet, data, evidence, source


def test_sidecar_binds_unchanged_workflow_and_exact_io(example):
    workflow, packet, data, *_ = example
    original = workflow.model_dump(mode="json")
    profile = ComparisonProfile.model_validate(data)
    assert validate_profile(profile, workflow, packet) == []
    view = comparison_profile_view(profile, workflow, packet)
    assert view["target_context"]["operation:separate"]["input_target_ids"] == ["state:nucleus"]
    assert view["target_context"]["operation:separate"]["output_target_ids"] == ["state:dna", "state:rna"]
    assert workflow.model_dump(mode="json") == original


def test_prescribed_fields_have_open_answers_and_forbid_new_schema_fields(example):
    workflow, packet, data, *_ = example
    data["states"][0]["entity"][0]["value"] = "Previously unnamed molecular association α"
    assert validate_profile(ComparisonProfile.model_validate(data), workflow, packet) == []
    schema = comparison_profile_schema()
    for name, questions in (("StateProfile", STATE_QUESTIONS), ("OperationProfile", OPERATION_QUESTIONS)):
        assert set(schema["$defs"][name]["required"]) == {"target_id", *questions}
        assert schema["$defs"][name]["additionalProperties"] is False
    data["states"][0]["universal_material_class"] = "DNA"
    with pytest.raises(ValidationError, match="Extra inputs"):
        ComparisonProfile.model_validate(data)


@pytest.mark.parametrize("update", [
    {"status": "reported", "value": "DNA", "value_kind": "value"},
    {"status": "unknown", "unknown_reason": "not_reported", "value": "No lineage"},
    {"status": "not_assessed", "value_kind": "absent"},
    {"status": "unknown"},
    {"status": "not_assessed", "unknown_reason": "not_reported"},
    {"applicability": "not_applicable"},
])
def test_unassessed_or_unknown_is_never_a_supported_negative(update):
    with pytest.raises(ValidationError):
        ProfileAssertion.model_validate(assertion("uncertain", **update))


def test_support_status_cannot_hide_an_inference_or_invent_one(example):
    *_, evidence, _ = example
    for status, support in (("reported", "inferred"), ("inferred", "reported")):
        with pytest.raises(ValidationError, match="evidence"):
            ProfileAssertion.model_validate(assertion(
                "support", status=status, value="Scoped answer", value_kind="value",
                evidence=[{**evidence[0], "support": support}]))
    with pytest.raises(ValidationError, match="cannot contain evidence"):
        ProfileAssertion.model_validate(assertion("unread", evidence=[evidence[0]]))


def test_supported_absence_and_inapplicability_are_separate_from_unknown(example):
    workflow, packet, data, evidence, _ = example
    data["states"][0]["context"] = [assertion(
        "no-dna-amplification", status="reported", value_kind="absent", evidence=[evidence[1]],
        rationale="DNA amplification is absent within the reported synthetic route.")]
    data["states"][0]["form_or_association"] = [assertion(
        "not-applicable", status="inferred", applicability="not_applicable",
        evidence=[{**evidence[0], "support": "inferred"}],
        rationale="This narrow annotation concerns the recorded well identifier, not a compartment.")]
    profile = ComparisonProfile.model_validate(data)
    assert validate_profile(profile, workflow, packet) == []
    docs = profile_retrieval_documents(profile, workflow, packet)
    assert any(d["value_kind"] == "absent" and "ABSENT" in d["text"] for d in docs)
    assert all(d["assertion_id"] != "not-applicable" for d in docs)
    data["states"][0]["context"][0]["evidence"] = []
    with pytest.raises(ValidationError, match="requires source evidence"):
        ComparisonProfile.model_validate(data)


@pytest.mark.parametrize("reason", ["documentation_absent", "not_reported", "conflicting_evidence"])
def test_unknown_reason_is_retained_but_never_indexed(example, reason):
    workflow, packet, data, evidence, _ = example
    data["states"][0]["lineage"] = [assertion(
        "lineage", status="unknown", unknown_reason=reason, evidence=[evidence[0]],
        rationale="Unique molecular ancestry is not established.")]
    profile = ComparisonProfile.model_validate(data)
    assert profile.states[0].lineage[0].unknown_reason == reason
    text = profile_retrieval_text(profile, workflow, packet)
    assert "Unique molecular ancestry" not in text
    assert "Separate nuclear fractions" in text and "DNA fraction" in text


def test_two_unassessed_profiles_cannot_create_positive_retrieval_agreement(example):
    workflow, packet, data, *_ = example
    for record, fields in ((data["states"][0], STATE_QUESTIONS),
                           (data["operations"][0], OPERATION_QUESTIONS)):
        for field in fields:
            record[field] = [assertion(f"{record['target_id']}-{field}")]
    for _ in range(2):
        profile = ComparisonProfile.model_validate(deepcopy(data))
        assert profile_retrieval_documents(profile, workflow, packet) == []
        assert profile_retrieval_text(profile, workflow, packet) == ""


def test_unit_level_lineage_does_not_collapse_dna_and_rna_identity(example):
    workflow, packet, data, evidence, _ = example
    rna = deepcopy(data["states"][0])
    rna["target_id"] = "rna"
    for field in STATE_QUESTIONS:
        rna[field] = [assertion(f"rna-{field}")]
    rna["entity"] = [assertion("rna-entity", status="reported", value_kind="value", value="RNA",
                               evidence=[evidence[0]])]
    data["states"].append(rna)
    for record in data["states"]:
        record["lineage"] = [assertion(
            record["target_id"] + "-well", status="reported", value_kind="value",
            value="Attribution to the indexed well; molecular parentage has not been assessed",
            evidence=[evidence[0]])]
    profile = ComparisonProfile.model_validate(data)
    docs = profile_retrieval_documents(profile, workflow, packet)
    identities = {d["target_id"]: d["text"] for d in docs if d["field"] == "entity"}
    assert "DNA" in identities["dna"] and "RNA" in identities["rna"]
    assert "relation" not in comparison_profile_view(profile, workflow, packet)


@pytest.mark.parametrize("defect", ["workflow_hash", "packet_hash", "target", "quote", "metadata"])
def test_stale_or_foreign_bindings_fail_closed(example, defect):
    workflow, packet, data, *_ = example
    if defect == "workflow_hash":
        data["workflow_sha256"] = "0" * 64
    elif defect == "packet_hash":
        data["source_packet_sha256"] = "0" * 64
    elif defect == "target":
        data["states"][0]["target_id"] = "foreign"
    elif defect == "quote":
        data["states"][0]["entity"][0]["evidence"][0]["quote"] = "Invented source quotation"
    else:
        packet["method_id"] = "foreign-workflow"
        data["source_packet_sha256"] = digest(packet)
    profile = ComparisonProfile.model_validate(data)
    assert validate_profile(profile, workflow, packet)
    with pytest.raises(ValueError):
        profile_retrieval_text(profile, workflow, packet)


def test_duplicates_and_missing_questions_are_rejected(example):
    data = example[2]
    for change in ("duplicate_target", "duplicate_assertion", "missing_field"):
        mutated = deepcopy(data)
        if change == "duplicate_target":
            mutated["states"].append(deepcopy(mutated["states"][0]))
        elif change == "duplicate_assertion":
            mutated["states"][0]["context"][0]["id"] = "dna-entity"
        else:
            del mutated["operations"][0]["mechanism"]
        with pytest.raises(ValidationError):
            ComparisonProfile.model_validate(mutated)


def test_validated_state_record_cannot_target_an_operation(example):
    workflow, packet, data, *_ = example
    data["states"][0]["target_id"] = "rt"
    assert "unknown state target" in "; ".join(validate_profile(
        ComparisonProfile.model_validate(data), workflow, packet))


def test_profile_selection_requires_all_retained_states_and_operations(example):
    from genomics_discovery.curation.workflow_extraction.transformations import cut_transformation

    workflow, packet, data, *_ = example
    cut = cut_transformation(workflow, packet, ["separate", "rt"])
    profile = ComparisonProfile.model_validate(data)
    errors = validate_profile_selection(profile, cut)
    assert any("rna" in error and "cdna" in error for error in errors)
    assert any("rt" in error for error in errors)
    for state in ("nucleus", "rna", "cdna"):
        data["states"].append({"target_id": state, **{
            field: [assertion(f"{state}-{field}")] for field in STATE_QUESTIONS}})
    data["operations"].append({"target_id": "rt", **{
        field: [assertion(f"rt-{field}")] for field in OPERATION_QUESTIONS}})
    profile = ComparisonProfile.model_validate(data)
    assert validate_profile_selection(profile, cut) == []
    assert validate_profile_selection(profile, cut.model_copy(update={"workflow_sha256": "0" * 64}))


def test_integrated_load_checks_original_source_hash(example, tmp_path):
    workflow, packet, data, _, source = example
    paths = [tmp_path / name for name in ("profile.json", "workflow.json", "packet.json")]
    for path, value in zip(paths, (data, workflow.model_dump(mode="json"), packet)):
        path.write_text(json.dumps(value))
    assert load_comparison_profile(*paths).workflow_sha256 == digest(workflow)
    source.write_text("Changed original source bytes")
    with pytest.raises(ValueError, match="hash|changed"):
        load_comparison_profile(*paths)

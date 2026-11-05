"""Negative controls for proposal/evidence boundaries, not assay validation."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from genomics_discovery.illumina.models import digest, parse_extraction, property_target
from genomics_discovery.ideation.models import (
    CandidateIdea, IdeaReview, ProposedWorkflow, candidate_targets, validate_review_binding,
)


def candidate_data():
    states = [
        {"id": "rna", "label": "RNA preparation", "kind": "material", "properties": [
            {"id": "identity", "property": "identity", "value": "RNA preparation"}]},
        {"id": "cdna", "label": "cDNA library", "kind": "material", "properties": [
            {"id": "identity", "property": "identity", "value": "cDNA library"}]},
        {"id": "reads", "label": "Sequencing reads", "kind": "data", "properties": [
            {"id": "identity", "property": "identity", "value": "Sequencing reads"},
            {"id": "type", "property": "data_type", "value": "sequence_reads"}]},
    ]
    operations = [
        {"id": "prepare", "label": "Prepare a cDNA library", "description": "Proposed coupled library preparation block.",
         "inputs": ["rna"], "outputs": ["cdna"], "resolution": "composite_block",
         "input_roles": [{"state_id": "rna", "roles": ["sample", "template"], "explanation": "Proposed template."}],
         "effects": [{"aspect": "material_transformation", "input_state_ids": ["rna"], "output_state_ids": ["cdna"], "statement": "Produce cDNA library."}],
         "output_grouping": "one_output"},
        {"id": "sequence", "label": "Sequence the library", "description": "Measure library sequence.",
         "inputs": ["cdna"], "outputs": ["reads"], "resolution": "single_event",
         "input_roles": [{"state_id": "cdna", "roles": ["sample"], "explanation": "Material to read."}],
         "effects": [{"aspect": "readout", "input_state_ids": ["cdna"], "output_state_ids": ["reads"], "statement": "Produce sequence reads."}],
         "output_grouping": "one_output"},
    ]
    flows = [
        {"id": "f1", "source": "rna", "target": "prepare", "kind": "material_input"},
        {"id": "f2", "source": "prepare", "target": "cdna", "kind": "material_output"},
        {"id": "f3", "source": "cdna", "target": "sequence", "kind": "material_input"},
        {"id": "f4", "source": "sequence", "target": "reads", "kind": "data_output"},
    ]
    workflow = {"states": states, "operations": operations, "flows": flows}
    targets = candidate_targets(ProposedWorkflow.model_validate(workflow))
    assessments = [{"target": target, "status": "hypothesis", "required_property": "Fixture assertion, deliberately without scientific support.",
                    "rationale": "Software test only.", "unsupported_property": "Not established in this fixture.",
                    "decisive_test": "Retrieve and assess an appropriate source, then test compatibility."}
                   for target in sorted(targets)]
    return {"id": "idea1", "title": "Test candidate", "biological_question": "Test measurement question.",
            "measurement": "RNA-derived sequencing reads.", "expected_benefit": "Unproven prediction.",
            "workflow": workflow, "assessments": assessments,
            "changes": [{"id": "change1", "earlier_refs": [], "proposed_target_ids": ["operation:prepare"],
                         "earlier_arrangement": "No parent asserted.", "retained": "Readout strategy.",
                         "changed": "Proposed library block.", "expected_gain": "Requires an experiment."}]}


def atlas_ref():
    return {"record_id": "example", "extraction_sha256": "a" * 64, "node_id": "illumina:example:operation:rt"}


def review_data(idea):
    return {"candidate_id": idea.id, "candidate_sha256": digest(idea),
            "reviewed_targets": sorted(candidate_targets(idea) | {"candidate"} | {"change:" + c.id for c in idea.changes}),
            "novelty_status": "search_incomplete", "nearest_prior": [], "issues": [],
            "disposition": "hold", "summary": "Not a novelty result; software fixture."}


def test_proposal_has_same_topology_meanings_but_is_not_extraction():
    idea = CandidateIdea.model_validate(candidate_data())
    assert idea.format_version == "proposed_assay/1.0"
    assert len(candidate_targets(idea)) == 13
    with pytest.raises((ValidationError, ValueError)):
        parse_extraction(idea.model_dump(mode="json"))
    assert not any("evidence" in x for x in idea.model_dump(mode="json")["workflow"]["states"])


@pytest.mark.parametrize("mutation", [
    lambda d: d["workflow"]["flows"][0].update(target="missing"),
    lambda d: d["workflow"]["flows"][0].update(kind="data_input"),
    lambda d: d["workflow"]["operations"][0].update(inputs=["cdna"]),
    lambda d: d["workflow"]["flows"].append(deepcopy(d["workflow"]["flows"][0])),
    lambda d: d["workflow"]["operations"][0].update(id="rna"),
    lambda d: d["workflow"]["flows"].append({"id": "cycle", "source": "sequence", "target": "prepare", "kind": "precedes"}),
    lambda d: d["workflow"]["operations"][0]["effects"][0].update(aspect="readout"),
    lambda d: d["workflow"]["operations"][0]["effects"][0].update(output_state_ids=["reads"]),
    lambda d: d["workflow"]["operations"][0]["input_roles"][0].update(subject="missing"),
    lambda d: d["workflow"]["operations"][0]["input_roles"][0].update(roles=["data"]),
    lambda d: d["workflow"]["operations"][0]["input_roles"][0].update(roles=["unspecified", "sample"]),
    lambda d: d["workflow"]["operations"][0].update(output_grouping="distinct_outputs"),
])
def test_invalid_routes_and_semantic_types_are_rejected(mutation):
    data = candidate_data(); mutation(data)
    with pytest.raises(ValidationError): CandidateIdea.model_validate(data)


@pytest.mark.parametrize("mutation", [
    lambda d: d["workflow"]["states"][2]["properties"].append({"id": "chemical", "property": "terminal_group", "end": "three_prime", "value": "hydroxyl"}),
    lambda d: d["workflow"]["states"][0]["properties"].append({"id": "data", "property": "data_type", "value": "sequence_reads"}),
    lambda d: d["workflow"]["states"][0]["properties"].append({"id": "strand", "property": "strandedness", "value": "DNA"}),
    lambda d: d["workflow"]["states"][0]["properties"].append({"id": "a", "property": "attribution", "value": "cell barcode"}),
    lambda d: d["workflow"]["states"][0]["properties"].append({"id": "a", "property": "association", "value": "bound_to", "partner": "missing"}),
    lambda d: d["workflow"]["states"][0]["properties"].append({"id": "id2", "property": "identity", "value": "Something contradictory"}),
    lambda d: d["workflow"]["states"][0]["properties"][0].update(partner="nonsensical"),
    lambda d: d["workflow"]["states"][2]["properties"].pop(),
])
def test_properties_preserve_existing_material_data_distinctions(mutation):
    data = candidate_data(); mutation(data)
    with pytest.raises(ValidationError): CandidateIdea.model_validate(data)


def test_multiple_properties_and_scoped_components_are_allowed():
    data = candidate_data(); state = data["workflow"]["states"][0]
    state["components"] = [{"id": "primer", "label": "Capture primer"}]
    additions = [
        {"id": "bound", "subject": "self", "property": "association", "value": "hybridized_to", "partner": "primer"},
        {"id": "tag", "subject": "primer", "property": "sequence_segment", "value": "Cell barcode"},
        {"id": "attribution", "subject": "primer", "property": "attribution", "value": {
            "unit": "cell", "carrier": "Barcode sequence", "association": "Proposed cell assignment."}},
    ]
    state["properties"].extend(additions)
    for prop in additions:
        assessment = deepcopy(data["assessments"][0])
        assessment["target"] = property_target("rna", prop["id"])
        data["assessments"].append(assessment)
    idea = CandidateIdea.model_validate(data)
    assert len(idea.workflow.states[0].properties) == 4


@pytest.mark.parametrize("mutation", [
    lambda d: d["assessments"].pop(),
    lambda d: d["assessments"].append(deepcopy(d["assessments"][0])),
    lambda d: d["assessments"][0].update(target="operation:missing"),
    lambda d: d["assessments"][0].update(status="precedent", unsupported_property=None),
    lambda d: d["assessments"][0].update(decisive_test=None),
    lambda d: d["changes"][0].update(proposed_target_ids=["state:missing"]),
])
def test_unsupported_or_unassessed_graph_cannot_look_supported(mutation):
    data = candidate_data(); mutation(data)
    with pytest.raises(ValidationError): CandidateIdea.model_validate(data)


def test_transfer_needs_property_precedent_assumption_and_test():
    data = candidate_data(); assessment = data["assessments"][0]
    assessment.update(status="transfer", atlas_refs=[atlas_ref()], unsupported_property=None,
                      compatibility_assumption="Whether the demonstrated primitive accepts this new specimen.")
    CandidateIdea.model_validate(data)
    assessment["compatibility_assumption"] = None
    with pytest.raises(ValidationError, match="compatibility assumption"):
        CandidateIdea.model_validate(data)


def test_hypothesis_cannot_use_reported_support_field():
    data = candidate_data(); data["workflow"]["states"][0]["evidence"] = [{"support": "reported"}]
    with pytest.raises(ValidationError): CandidateIdea.model_validate(data)


def test_unresolved_operation_cannot_be_labeled_demonstrated():
    data = candidate_data(); data["workflow"]["operations"][0]["resolution"] = "unresolved_interface"
    assessment = next(a for a in data["assessments"] if a["target"] == "operation:prepare")
    assessment.update(status="precedent", atlas_refs=[atlas_ref()], unsupported_property=None)
    with pytest.raises(ValidationError, match="explicit hypothesis"):
        CandidateIdea.model_validate(data)


def test_review_binds_exact_candidate_and_targets():
    idea = CandidateIdea.model_validate(candidate_data())
    review = IdeaReview.model_validate(review_data(idea))
    validate_review_binding(review, idea)
    data = candidate_data(); data["expected_benefit"] = "Changed prediction."
    with pytest.raises(ValueError, match="exact candidate"):
        validate_review_binding(review, CandidateIdea.model_validate(data))
    revised = review.model_dump(); revised["issues"] = [{"id": "i1", "target": "operation:missing", "severity": "major", "reason": "Wrong target.", "next_test": "Read source."}]
    with pytest.raises(ValueError, match="unknown target"):
        validate_review_binding(IdeaReview.model_validate(revised), idea)


def test_review_does_not_certify_novelty_or_ignore_blockers():
    idea = CandidateIdea.model_validate(candidate_data()); data = review_data(idea)
    data["novelty_status"] = "possible_distinction"
    with pytest.raises(ValidationError, match="located nearest prior"):
        IdeaReview.model_validate(data)
    data["nearest_prior"] = [{"title": "Prior assay", "atlas_refs": [atlas_ref()], "overlap": "Same output.", "residual_difference": "Proposed interface."}]
    data.update(novelty_status="direct_overlap", disposition="retain_for_testing")
    with pytest.raises(ValidationError, match="Direct overlap"):
        IdeaReview.model_validate(data)
    data.update(novelty_status="possible_distinction", issues=[{"id": "b", "target": "candidate", "severity": "blocking", "reason": "Unsupported essential primitive.", "next_test": "Establish the required property."}])
    with pytest.raises(ValidationError, match="blocking issue"):
        IdeaReview.model_validate(data)


def test_review_cannot_skip_targets_or_add_unknown_ones():
    idea = CandidateIdea.model_validate(candidate_data()); data = review_data(idea)
    data["reviewed_targets"].pop()
    with pytest.raises(ValueError, match="every graph"):
        validate_review_binding(IdeaReview.model_validate(data), idea)
    data = review_data(idea); data["reviewed_targets"].append("state:invented")
    with pytest.raises(ValueError, match="every graph"):
        validate_review_binding(IdeaReview.model_validate(data), idea)


def test_biological_aim_without_sequencing_readout_is_not_an_assay_proposal():
    data = candidate_data()
    data["workflow"]["states"][2]["properties"][1]["value"] = "measurement_table"
    with pytest.raises(ValidationError, match="Sequencing assay proposal"):
        CandidateIdea.model_validate(data)

"""Representability controls from the source audit, not biological accuracy scores.

The fixtures are synthetic: they test whether source-level mixtures, broad
boundaries and operation-local constituent roles can be represented without
inventing chemistry or separate physical fractions.
"""

import copy

import pytest
from pydantic import ValidationError

from genomics_discovery.illumina.representation import (
    StructuredDiagramExtraction,
    StructuredState,
)


EVIDENCE = [{"source_id": "synthetic:diagram", "locator": "test schematic",
             "basis": "image", "support": "depicted"}]


def state(ident, label):
    return {"id": ident, "label": label, "kind": "material", "evidence": EVIDENCE,
            "properties": [{"id": "identity", "subject": "self", "property": "identity",
                            "value": label, "evidence": EVIDENCE}]}


def component(ident, label):
    return {"id": ident, "label": label, "evidence": EVIDENCE}


def copying_example():
    prepared = state("prepared", "RNA hybridized to a DNA primer")
    prepared["components"] = [component("rna", "RNA template"), component("primer", "DNA primer")]
    prepared["properties"].append({"id": "hybrid", "subject": "rna", "property": "association",
                                   "value": "hybridized_to", "partner": "primer", "evidence": EVIDENCE})
    product = state("product", "Reported copied product")
    operation = {"id": "copy", "label": "Copy the template", "description": "Depicted copying block",
                 "inputs": ["prepared"], "outputs": ["product"], "evidence": EVIDENCE,
                 "resolution": "composite_block", "output_grouping": "one_output",
                 "input_roles": [
                     {"state_id": "prepared", "subject": "rna", "roles": ["template"],
                      "explanation": "RNA is copied", "evidence": EVIDENCE},
                     {"state_id": "prepared", "subject": "primer", "roles": ["primer"],
                      "explanation": "The DNA primer initiates copying", "evidence": EVIDENCE}],
                 "effects": [{"aspect": "material_transformation", "input_state_ids": ["prepared"],
                              "output_state_ids": ["product"], "statement": "The primer is extended using the template",
                              "evidence": EVIDENCE}]}
    return {"format_version": "illumina_diagram/1.1", "record_id": "synthetic-copy",
            "title": "Synthetic mixture control", "method_names": ["Synthetic"],
            "scope": "Schematic only", "inspected_sources": ["synthetic:diagram"],
            "states": [prepared, product], "operations": [operation],
            "flows": [{"id": "f1", "source": "prepared", "target": "copy", "kind": "material_input", "evidence": EVIDENCE},
                      {"id": "f2", "source": "copy", "target": "product", "kind": "material_output", "evidence": EVIDENCE}],
            "method_scopes": [{"method": "Synthetic", "operation_ids": ["copy"], "status": "resolved",
                               "rationale": "Fixture scope", "evidence": EVIDENCE}],
            "paper_matches": [], "corrections": [], "unknowns": []}


def test_source_broad_preparation_does_not_require_invented_chemistry():
    parsed = StructuredState.model_validate(state("input", "Total RNA"))
    assert parsed.components == ()
    assert [p.property for p in parsed.properties] == ["identity"]


def test_three_end_chemistries_can_remain_one_mixed_preparation():
    mixed = state("mixture", "RNA preparation containing several terminal chemistries")
    mixed["components"] = [component("a", "RNA subpopulation A"), component("b", "RNA subpopulation B"),
                           component("c", "RNA subpopulation C")]
    for ident, value in (("a", "hydroxyl"), ("b", "phosphate"), ("c", "2-prime,3-prime cyclic phosphate")):
        mixed["properties"].append({"id": "end-" + ident, "subject": ident, "property": "terminal_group",
                                    "end": "three_prime", "value": value, "evidence": EVIDENCE})
    parsed = StructuredState.model_validate(mixed)
    assert len(parsed.components) == 3
    assert {p.subject for p in parsed.properties if p.property == "terminal_group"} == {"a", "b", "c"}


def test_template_and_primer_roles_do_not_force_separate_fractions():
    parsed = StructuredDiagramExtraction.model_validate(copying_example())
    op = parsed.operations[0]
    assert op.inputs == ("prepared",)
    assert {(r.subject, r.roles) for r in op.input_roles} == {("rna", ("template",)), ("primer", ("primer",))}
    assert len(parsed.states) == 2


def test_role_cannot_claim_an_unrecorded_constituent():
    raw = copying_example()
    raw["operations"][0]["input_roles"][0]["subject"] = "unrecorded-rna"
    with pytest.raises(ValidationError):
        StructuredDiagramExtraction.model_validate(raw)


def test_unknown_transition_can_be_recorded_without_a_fake_mechanism():
    raw = copying_example()
    op = raw["operations"][0]
    op.update(label="Reported processing block", description="The source supplies only its boundaries",
              resolution="unresolved_interface")
    op["input_roles"] = [{"state_id": "prepared", "subject": "self", "roles": ["unspecified"],
                          "explanation": "Internal roles are not established", "evidence": EVIDENCE}]
    op["effects"][0].update(aspect="unresolved", statement="The source does not resolve the internal transition")
    with pytest.raises(ValidationError):
        StructuredDiagramExtraction.model_validate(raw)
    with_unknown = copy.deepcopy(raw)
    with_unknown["unknowns"] = [{"id": "transition", "question": "Which transformation links the stated boundaries?",
                                "blocking": True, "affected_ids": ["operation:copy"]}]
    parsed = StructuredDiagramExtraction.model_validate(with_unknown)
    assert parsed.operations[0].mechanism is None

"""Versioned scientific assertions on the State–Operation–Flow graph.

This contract adapts the distinctions recorded in docs/ATLAS_PLAN.txt and the
source audits under experiments/representation_review_20261104. It constrains
where a claim lives and what it references; neither valid JSON nor a property
name establishes scientific truth. Legacy records are never upgraded by guessing
properties from prose. New global concepts/equivalences are not admitted here.
"""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel
from .models import DiagramExtraction, DiagramReview, EvidenceSet, Operation, State, Text, property_target


class StateComponent(StrictModel):
    id: Text = Field(description="Local address of a consequential constituent/subpopulation; not a global concept ID.")
    label: Text = Field(description="Specific constituent, e.g. capture primer or residual peptide, not a mandatory substance bin.")
    evidence: EvidenceSet


class PropertyAssertion(StrictModel):
    id: Text = Field(description="Stable within this State; used for an independent property review.")
    subject: Text = Field(description="self for the whole preparation/dataset, or an exact local component ID.")
    evidence: EvidenceSet


class IdentityProperty(PropertyAssertion):
    property: Literal["identity"]
    value: Text = Field(description="Actual named preparation/dataset at this boundary, e.g. cap-selected cDNA. Not the biological target unless that target is physically present.")


class StrandednessProperty(PropertyAssertion):
    property: Literal["strandedness"]
    value: Literal["single_stranded", "double_stranded", "mixed"]


class TopologyProperty(PropertyAssertion):
    property: Literal["topology"]
    value: Literal["linear", "covalently_circular", "branched", "hybridized_loop", "mixed"]


class TerminalGroupProperty(PropertyAssertion):
    property: Literal["terminal_group"]
    end: Literal["five_prime", "three_prime"]
    value: Text = Field(description="Reported terminal chemical state at this end, e.g. hydroxyl or 2-prime,3-prime cyclic phosphate. Describe additional attached moieties with modification/association, or in the explicitly reported combined terminal state. Protection does not imply extension blocking.")


class ModificationProperty(PropertyAssertion):
    property: Literal["modification"]
    value: Text = Field(description="Modification actually present on this subject, not a mark on its former template.")
    site: Text | None = Field(default=None, description="Position/context when reported and consequential; do not invent localization.")


class SequenceSegmentProperty(PropertyAssertion):
    property: Literal["sequence_segment"]
    value: Text = Field(description="Supported segment identity/function, e.g. cell barcode or amplification handle. Exact nucleotide sequence only if sourced.")
    location: Text | None = Field(default=None, description="Reported placement/orientation; no assumed adapter architecture.")


class AssociationProperty(PropertyAssertion):
    property: Literal["association"]
    value: Literal["hybridized_to", "covalently_linked_to", "bound_to", "contained_in", "co_present_with"]
    partner: Text = Field(description="Exact local component ID or self. Co-presence does not imply binding or covalent linkage.")


class PhysicalContextProperty(PropertyAssertion):
    property: Literal["physical_context"]
    value: Text = Field(description="Supported location/organization of this preparation, such as intact droplets or pooled beads. Does not itself establish independent fractions or a pooling operation.")


class AttributionValue(StrictModel):
    unit: Literal["molecule", "cell", "sample", "spatial_location", "time_window", "contact", "other"]
    carrier: Text = Field(description="Actual physical/informational carrier in the named subject: barcode segment, separate compartment, recorded coordinate map, etc.")
    association: Text = Field(description="What this carrier associates with what, with the reported resolution/scope. A barcode alone does not establish an available decoding map.")


class AttributionProperty(PropertyAssertion):
    property: Literal["attribution"]
    value: AttributionValue


class DataTypeProperty(PropertyAssertion):
    property: Literal["data_type"]
    value: Literal["sequence_reads", "alignment", "instrument_signal", "measurement_table", "coordinate_map", "derived_result", "other"]
    definition: Text | None = None

    @model_validator(mode="after")
    def defined_other(self):
        if self.value == "other" and not self.definition:
            raise ValueError("Other data type requires a definition")
        return self


class DataContentProperty(PropertyAssertion):
    property: Literal["data_content"]
    value: Text = Field(description="Observed fields/identifiers and what they represent. Describe a dataset, not physical molecule chemistry.")


class OtherProperty(PropertyAssertion):
    property: Literal["defined_other"]
    name: Text
    definition: Text = Field(description="Meaning/applicability of this local property; not an admitted shared vocabulary term or equivalence.")
    applicable_to: Literal["material", "data"]
    value: Text


StateProperty = Annotated[
    IdentityProperty | StrandednessProperty | TopologyProperty | TerminalGroupProperty |
    ModificationProperty | SequenceSegmentProperty | AssociationProperty |
    PhysicalContextProperty | AttributionProperty | DataTypeProperty |
    DataContentProperty | OtherProperty,
    Field(discriminator="property"),
]

PROPERTY_NAMES = (
    "identity", "strandedness", "topology", "terminal_group", "modification",
    "sequence_segment", "association", "physical_context", "attribution",
    "data_type", "data_content", "defined_other",
)
MATERIAL_PROPERTIES = frozenset({"strandedness", "topology", "terminal_group", "modification",
                               "sequence_segment", "association", "physical_context"})
DATA_PROPERTIES = frozenset({"data_type", "data_content"})


class StructuredState(State):
    source_label: Text | None = Field(default=None, description="Literal caption when available; kept separately from the asserted scientific label.")
    features: Annotated[tuple[Text, ...], Field(max_length=0)] = Field(default=(), description=
        "Legacy field: empty in this contract. Record scientific facts as typed properties.")
    components: tuple[StateComponent, ...] = Field(default=(), description=
        "Optional addressable constituents/subpopulations. Several components in one State are not separate fractions. Do not decompose every cell or reagent.")
    properties: Annotated[tuple[StateProperty, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def property_contract(self):
        component_ids = [c.id for c in self.components]
        if len(component_ids) != len(set(component_ids)) or "self" in component_ids:
            raise ValueError("Component IDs must be unique and cannot use reserved subject self")
        if self.kind == "data" and self.components:
            raise ValueError("Data content belongs in data_content, not physical components")
        subjects = {"self", *component_ids}
        ids, singleton_keys, assertions = set(), set(), set()
        for prop in self.properties:
            if prop.id in ids:
                raise ValueError("Property IDs must be unique within a State")
            ids.add(prop.id)
            if prop.subject not in subjects:
                raise ValueError("Property subject must be self or an exact local component ID")
            if isinstance(prop, AssociationProperty) and (prop.partner not in subjects or prop.partner == prop.subject):
                raise ValueError("Association partner must be a different local subject")
            if self.kind == "data" and prop.property in MATERIAL_PROPERTIES:
                raise ValueError("Data cannot carry physical material properties")
            if self.kind == "material" and prop.property in DATA_PROPERTIES:
                raise ValueError("Material cannot carry dataset properties")
            if isinstance(prop, OtherProperty) and prop.applicable_to != self.kind:
                raise ValueError("Defined-other property applicability disagrees with State kind")
            # A one-subject conflict is not a mixture: name the subpopulations or retain an Unknown.
            if prop.property in {"identity", "strandedness", "topology", "data_type", "terminal_group"}:
                key = (prop.subject, prop.property, getattr(prop, "end", None))
                if key in singleton_keys:
                    raise ValueError("Conflicting/repeated single-valued property; use scoped subjects or an Unknown")
                singleton_keys.add(key)
            signature = repr(prop.model_dump(exclude={"id", "evidence"}, mode="json"))
            if signature in assertions:
                raise ValueError("Duplicate scientific assertion")
            assertions.add(signature)
        if not any(p.property == "identity" and p.subject == "self" for p in self.properties):
            raise ValueError("Every State requires the whole preparation/dataset identity at subject self")
        if self.kind == "data" and not any(p.property == "data_type" for p in self.properties):
            raise ValueError("Data State requires a data_type assessment")
        return self


class InputRole(StrictModel):
    state_id: Text
    subject: Text = Field(default="self", description="self for the input preparation, or an exact component ID within that input State. Co-present template and primer can have separate roles without separate fractions.")
    roles: Annotated[tuple[Literal[
        "sample", "template", "primer", "probe", "reagent", "catalyst", "capture_support",
        "data", "reference_data", "unspecified"
    ], ...], Field(min_length=1)] = Field(description=
        "Roles in this Operation only. Multiple supported roles are allowed; template may be RNA or DNA. unspecified is exclusive. Outputs are products via declared outputs, not permanent product labels.")
    explanation: Text
    evidence: EvidenceSet


class OperationEffect(StrictModel):
    aspect: Literal["material_transformation", "selection", "organization", "attribution", "readout", "data_processing", "unresolved"]
    input_state_ids: tuple[Text, ...]
    output_state_ids: tuple[Text, ...]
    statement: Text = Field(description=
        "Source-supported change/selection/observation for these exact participants. Joint participation is not all-to-all molecular derivation. A copied product is not its template.")
    evidence: EvidenceSet


class StructuredOperation(Operation):
    resolution: Literal["single_event", "composite_block", "unresolved_interface"] = Field(description=
        "Source-level resolution, not an atomic-chemistry claim. Retain coupled/composite blocks unless an evidenced distinction requires subdivision.")
    input_roles: tuple[InputRole, ...]
    effects: Annotated[tuple[OperationEffect, ...], Field(min_length=1)]
    output_grouping: Literal["no_output", "one_output", "distinct_outputs", "unresolved"] = Field(description=
        "Separate output States imply distinguishable output preparations/products/datasets. Molecular classes coexisting in one preparation belong in one State's components; they do not create separation flows.")


class StructuredDiagramExtraction(DiagramExtraction):
    format_version: Literal["illumina_diagram/1.1"] = "illumina_diagram/1.1"
    states: Annotated[tuple[StructuredState, ...], Field(min_length=1)]
    operations: Annotated[tuple[StructuredOperation, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def annotation_contract(self):
        states = {s.id: s for s in self.states}
        for op in self.operations:
            role_ids = [r.state_id for r in op.input_roles]
            role_subjects = [(r.state_id, r.subject) for r in op.input_roles]
            if len(role_subjects) != len(set(role_subjects)) or set(role_ids) != set(op.inputs):
                raise ValueError("Input role assessments must cover every input with distinct State/subject pairs")
            for role in op.input_roles:
                valid_subjects = {"self", *(part.id for part in states[role.state_id].components)}
                if role.subject not in valid_subjects:
                    raise ValueError("Input role subject must exist in its declared State")
                if len(role.roles) != len(set(role.roles)) or ("unspecified" in role.roles and len(role.roles) != 1):
                    raise ValueError("Input roles must be distinct; unspecified cannot also assert a known role")
                known = set(role.roles) - {"unspecified"}
                if states[role.state_id].kind == "data" and known - {"data", "reference_data"}:
                    raise ValueError("Data State cannot have a physical input role")
                if states[role.state_id].kind == "material" and known & {"data", "reference_data"}:
                    raise ValueError("Material State cannot have a data input role")
            covered = set()
            for effect in op.effects:
                if (set(effect.input_state_ids) - set(op.inputs)
                        or set(effect.output_state_ids) - set(op.outputs)):
                    raise ValueError("Operation effect must reference its own declared inputs/outputs")
                if (len(effect.input_state_ids) != len(set(effect.input_state_ids))
                        or len(effect.output_state_ids) != len(set(effect.output_state_ids))):
                    raise ValueError("Duplicate effect participant")
                if op.inputs and not effect.input_state_ids:
                    raise ValueError("Effect must identify an input when the Operation has inputs")
                if not effect.input_state_ids and not effect.output_state_ids:
                    raise ValueError("Effect needs a declared participant")
                covered.update(effect.output_state_ids)
            if covered != set(op.outputs):
                raise ValueError("Every declared output needs an effect assessment")
            expected = {0: "no_output", 1: "one_output"}.get(len(op.outputs))
            if expected and op.output_grouping != expected:
                raise ValueError("Output grouping must agree with the number of output States")
            if expected is None and op.output_grouping not in {"distinct_outputs", "unresolved"}:
                raise ValueError("Multiple outputs require distinct_outputs or unresolved grouping")
            if (op.resolution == "unresolved_interface" or op.output_grouping == "unresolved"
                    or any(e.aspect == "unresolved" for e in op.effects)):
                if not any("operation:" + op.id in u.affected_ids for u in self.unknowns):
                    raise ValueError("Unresolved operation interpretation requires an explicit bound Unknown")
        return self


class StructuredDiagramReview(DiagramReview):
    format_version: Literal["illumina_diagram_review/1.2"] = "illumina_diagram_review/1.2"


def property_targets(candidate):
    """Reviewer-only targets; Unknown/Correction continue to address owning States."""
    return {property_target(state.id, prop.id): prop
            for state in candidate.states for prop in state.properties}


def property_view(state):
    """Retrieve recorded assertions; missing is never inferred absent or unchanged."""
    properties = state.get("properties", [])
    return {name: {"status": "recorded" if any(p["property"] == name for p in properties) else "not_recorded",
                   "claims": [p for p in properties if p["property"] == name]}
            for name in PROPERTY_NAMES}


STRUCTURED_INSTRUCTIONS = """
STRUCTURED REPRESENTATION — illumina_diagram/1.1
CONSTRUCT THE WORKFLOW IN THIS ORDER
1. Start from the source's action blocks: supported actions or explicitly grouped
   procedures. Reagent names, material icons and biological goals are not actions.
2. Identify the actual preparation/dataset before and after each block. These are
   States. A broad boundary such as total RNA is legitimate; do not invent detail.
3. Preserve independently routed preparations/products/data as distinct States.
   A mixture can contain multiple chemical classes in one State; different colors,
   end groups or component names do not establish isolated fractions. Several
   intact droplets may be one collection State, without implying mixed contents.
4. Connect actual input/output States to Operations using the existing Flow types.
   Split a source block only when an evidenced routing, consequential interface or
   distinguishing design change requires it. Otherwise retain a composite_block.

STATE ASSERTIONS
State.label is a readable scientific name, not a semantic identity key.
Operation labels likewise remain local source-supported action descriptions;
this version does not normalize operation concepts or establish shared mechanisms.
source_label preserves the literal caption; neither a disclaimer nor quotation
marks makes a scientifically wrong asserted label acceptable. State.features is
empty. properties holds individually addressed assertions with id, subject,
property, value and evidence. subject=self names the whole preparation/dataset;
optional components name only consequential constituents/subpopulations. A State
requires one self identity. Data additionally requires data_type. Do not decompose
every cell or reagent or create a new global concept for each preparation.

The initial property families are not exhaustive. Property meanings are defined
in the JSON schema. End chemistry needs a subject
and five_prime/three_prime end. One terminal_group value describes the reported
terminal chemical state; additional attached moieties can use modification or
association instead of contradictory values for the same end. Topology distinguishes a covalent circle from a
hybridized loop. association names both local partners and distinguishes binding,
hybridization, covalent linkage and co-presence. physical_context describes the
actual organization, not an unreported separation. attribution names the linked
unit, the actual carrier and the supported association. A spatial barcode without
its decoding map does not establish recoverable coordinates. defined_other is a
locally defined property, not a shared vocabulary admission or semantic merge.

Missing properties mean NOT RECORDED, not absent, unchanged, or not applicable.
Record a consequential ambiguity as an Unknown against the owning State/Operation.
Do not add unsupported properties simply to satisfy a list. Derive in-scope origin
and selection history from producing Operations; a prepared starting boundary can
retain explicitly sourced history in its identity without inventing a producer.

OPERATION ASSERTIONS
Inputs/outputs are exact State IDs. Assess roles for every input using subject=self
or its exact component ID. Several components can have different roles in one
preparation, without creating separate fractions. Each State/subject pair occurs
once; allowed roles are sample,
template, primer, probe, reagent, catalyst, capture_support, data, reference_data
or unspecified. Multiple supported roles can coexist. Roles do not assert chemical
consumption. A template can be RNA or DNA; the enzyme name cannot decide that.
Outputs are this Operation's products, not a permanent material role.
Each effect identifies its own input/output State IDs and describes the supported
change, selection, routing, information association or observation. Multiple effects
can remain in one composite block. Joint participation does not establish all-to-all
molecular derivation. Output grouping distinguishes one preparation from genuinely
separate products/fractions/datasets. Unresolved interpretations need a bound Unknown.
No forced source-to-cell-to-RNA-to-DNA chain or universal module sequence applies.

SOURCE-DERIVED REVIEW COUNTEREXAMPLES (not answers to every assay)
- RASL: the RNA target/template and ligated DNA probes are not the same product;
  do not label a reporter as copied cDNA without source support.
- cP-RNA: end-chemistry subpopulations may coexist; they are not automatically
  isolated fractions. Place each end property on its actual subject.
- Cap-selected cDNA: selection of capped RNA does not put an RNA cap on the DNA.
- Smart-seq comparisons: a shared action diagram can conceal a distinguishing
  reagent/condition change; retrieve only the detail needed for that comparison.
- CITE-seq: reverse-transcriptase use can copy a synthetic DNA tag as well as RNA;
  do not infer template origin from the enzyme name or poly(A) capture alone.

REVIEW
The construction target checks the decomposition itself: each State must be an
actual supported interface; reuse the intervening State for the same interface;
do not invent isolated fractions, pooling, waste destinations, or hidden actions.
Check unjustified splitting and source blocks that conceal a necessary supported
branch or distinguishing change. A valid graph alone does not settle these claims.
In addition to normal record and label judgments, review each
state_property:<encoded_state_id>:<encoded_property_id> for subject, meaning, placement and source
support (the runtime supplies exact target IDs; do not reconstruct them).
Review state_properties:<state_id> for consequential omissions and overall
consistency (a short list of true facts can still omit the distinguishing feature).
Review operation_semantics:<operation_id> for local roles, effects, resolution,
output grouping and agreement with adjacent States/Flows. These are scientific
judgments; JSON validation checks references/types, not truth. Source image/text
supports card extraction; primary sources support consequential corrections and
historical claims. Do not demand a full experimental recipe for a schematic.
"""

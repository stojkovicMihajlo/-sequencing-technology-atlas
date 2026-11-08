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
    value: Text = Field(description="What actually exists at this boundary: a concrete sample, molecular material/mixture, construct or dataset, e.g. cells before lysis, bead-bound RNA, cap-selected cDNA, or aligned reads. Include the relevant experimental form; broad domain, intended next action or biological measurement target alone is insufficient. Keep demonstrated specimen versus broader assay applicability explicit without inventing unsupported specificity.")


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
    value: Text = Field(description="Supported physical form/location/organization: cells before lysis versus lysate, isolated nuclei, a tissue section, solution, bead-associated material, intact droplets or pooled beads, when consequential. Do not infer viability, fixation, free/bound status or mixing from generic names. Does not itself establish independent fractions or a pooling operation.")


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
    value: Text = Field(description="What observations/fields/identifiers actually exist at this processing stage and what they represent; distinguish observed signals from inferred quantities. Raw reads do not already have coordinates supplied by a later alignment. Describe a dataset, not physical molecule chemistry.")


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
    properties: Annotated[tuple[StateProperty, ...], Field(min_length=1, description="Source-supported identity of the whole material/dataset plus consequential properties. Record physical form, molecular features, associations or attribution when omitting them would hide the retained transformation, interface, measurement meaning or method distinction. Missing properties mean not recorded, not absent or not applicable.")]

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


EFFECT_ASPECT_GUIDE = """Effect aspects describe what the source-supported action does:
- material_transformation: changes or produces physical molecules/material, such
  as copying RNA into cDNA or attaching an adapter. Preparing a library for later
  sequencing is a material transformation, not itself a readout.
- selection: enriches, retains or excludes a specified population from an input.
- organization: changes physical arrangement, partitioning or routing, such as
  pooling or aliquoting. It does not by itself establish chemical conversion.
- attribution: records or preserves an evidenced association with a sample,
  molecule, cell, location or measured property; identify the actual carrier.
- readout: obtains measurement data from an observation, such as sequencing.
  The effect must reference at least one output State of kind data. A physical
  library or reporter that could later be measured is not yet measurement data.
- data_processing: transforms or interprets existing data, such as alignment or
  counting; it does not itself describe a physical sample transformation.
- unresolved: the available evidence does not establish the effect; bind an
  Unknown to the Operation instead of asserting an unsupported mechanism.
These aspects are not mutually exclusive descriptions of an Operation: record
separate supported effects when needed. They do not establish shared mechanisms
or equivalence between methods.
"""


class OperationEffect(StrictModel):
    aspect: Literal["material_transformation", "selection", "organization", "attribution", "readout", "data_processing", "unresolved"] = Field(description=EFFECT_ASPECT_GUIDE)
    input_state_ids: tuple[Text, ...]
    output_state_ids: tuple[Text, ...]
    statement: Text = Field(description=
        "Source-supported change/selection/observation for these exact participants. Joint participation is not all-to-all molecular derivation. A copied product is not its template.")
    evidence: EvidenceSet


class StructuredOperation(Operation):
    resolution: Literal["single_event", "composite_block", "unresolved_interface"] = Field(description=
        "Source-level grouping: single_event for one described action; composite_block for several described actions retained together; unresolved_interface when the transition between boundaries is not established. Not an atomic-chemistry claim or a cross-method equivalence key. Retain coupled/composite blocks unless an evidenced distinction requires subdivision.")
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
                if effect.aspect == "readout" and not any(
                    states[state_id].kind == "data" for state_id in effect.output_state_ids
                ):
                    raise ValueError(f"Readout effect must reference a data output State; a physical library is not measurement data (operation {op.id})")
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
   Repeated equivalent processing may use collection States only when the unit
   identities, separation and input-to-output correspondence remain explicit;
   use distinct States for differing routes that a collection cannot express.
   A mixture can contain multiple chemical classes in one State; different colors,
   end groups or component names do not establish isolated fractions. Several
   intact droplets may be one collection State, without implying mixed contents.
4. Connect actual input/output States to Operations using the existing Flow types.
   Split a source block only when an evidenced routing, consequential interface or
   distinguishing design change requires it. Otherwise retain a composite_block.
   First try recording the required detail inside the existing State or Operation.
   Explain why an added boundary is necessary if the existing inputs, outputs and
   fields cannot express the supported route, order or distinction. A chemical
   distinction alone does not require a new State. Do not expand a repeated cartoon
   into a fixed number of experimental units without source support, or treat
   alternative implementations as obligatory branches of one execution.

STATE ASSERTIONS
State.label is a readable scientific name, not a semantic identity key.
Name the actual material/data preparation, rather than its intended use. Keep
the purpose of the next action in the Operation. Broad biological applicability
does not justify erasing physical preparation: cells before lysis, nuclei,
tissue sections, lysate, extracted nucleic acid and libraries are distinct inputs.
Use existing identity/physical_context properties when that distinction matters.
Keep a demonstrated specimen and the method's broader reported scope explicit;
neither force every application to one cell line nor broaden a demonstrated
preparation into an unspecified material. A quoted fact alone is not a recorded
State property. Review State labels/properties for this distinction as well as
for unsupported specificity; retrieve adjacent Operations to assess its meaning.
Operation labels likewise remain local source-supported action descriptions;
this version does not normalize operation concepts or establish shared mechanisms.
source_label preserves the literal caption; neither a disclaimer nor quotation
marks makes a scientifically wrong asserted label acceptable. State.features is
empty. properties holds individually addressed assertions with id, subject,
property, value and evidence. subject=self names the whole preparation/dataset;
optional components name only consequential constituents/subpopulations. A State
requires one self identity. Data additionally requires data_type. Do not decompose
every cell or reagent or create a new global concept for each preparation.

STATE CONTENT REQUIREMENTS — AUTHOR AND REVIEWER
For each State, inspect its producing and consuming Operations and the registered
sources. Write the applicable content below as explicit, source-supported factual
statements in the existing State properties and Operation descriptions, mechanisms,
roles and effects. Do not return questions, a Q&A checklist or intentions to inspect
something later. These are not six new fields, mandatory paragraphs, or a demand
to enumerate all chemistry.
At an assigned starting/ending boundary, do not invent a preceding/following
Operation merely to fill a requirement. A source-supported broad boundary is valid.
1. Identify the actual preparation or dataset at this boundary: for example,
   intact cells, nuclei, lysate, extracted RNA, DNA fragments, a molecular complex
   or a library. For data, identify the actual dataset and processing stage.
   A purpose such as material for sequencing does not identify the object.
2. Identify the processed molecule and what its reporter represents. Distinguish
   the original biological molecule, a copied product and a synthetic reporter.
   When consequential, distinguish genomic DNA, RNA-derived cDNA and probe-derived
   DNA. For data, identify the observations or derived quantities represented.
   Do not infer template origin merely from the enzyme or assay name.
3. Describe the result of the producing Operation. Record the supported
   property, selection, association, organization or data-processing change on
   the actual subject. Treated or processed alone does not explain a consequential
   change. A copied sequence does not transfer every chemical feature of its
   template. An unchanged property need not be redescribed as a new modification.
4. Specify the supported input requirements of the consuming Operation and whether
   this State satisfies them. Include relevant molecular ends, strandedness,
   topology, binding or compartment when established by the sources. For data,
   record the required observations, assignments or reference information. Do not assume
   compatibility from an operation label or add unsupported molecular precision.
5. State what remains together and what is separate. Distinguish a mixture,
   independently processed samples, an actual pool and support-bound material. Track separate
   datasets/assignments when relevant. Different colors or molecular classes do
   not establish isolated fractions; repeated icons do not establish sample count.
6. Explain the connection to the biological measurement. When that connection
   is introduced, changed or removed, identify the actual carrier: sequence,
   barcode, molecular association, chemical mark or sample metadata. Distinguish
   physical measurement from inference and preserve the required attribution.
   Existing Flows and producing Operations can supply unchanged history; do not
   copy the entire assay explanation into every State.
Every factual assertion needs source support. A consequential fact absent from
the available evidence belongs in a targeted Unknown, not a guessed property.
Keep a genuine open scientific question in Unknown, rather than phrasing an
asserted State property as a question. These requirements are not an exhaustive
ontology: record other supported distinctions when needed to explain this assigned
assay.

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
resolution describes source grouping, not whether two methods use the same
transformation. A single_event is one described action; composite_block retains
several described actions together; unresolved_interface marks a transition the
available evidence does not establish. Compare the supported transformations and
their input/output boundaries, not resolution flags or matching action labels.

""" + EFFECT_ASPECT_GUIDE + """

SOURCE-DERIVED REVIEW COUNTEREXAMPLES (non-exhaustive in-context guidance)
These illustrate field interpretation and granularity, not target-method evidence
or complete worked extractions. Verify the assigned sources. Do not copy an
example's chemistry, specimen, node count or routes into another method, and do
not require every method to exhibit these features.
- If a source establishes intact cells before lysis, cellular material is an
  insufficient identity even if the adjacent Operation mentions the cells. Record
  the supported preparation; do not force a particular cell line into general scope.
- RASL: the RNA target/template and ligated DNA probes are not the same product.
  Where the source establishes ligation of synthetic DNA probes, record probe-derived
  DNA rather than RNA-derived cDNA. The cDNA/probe distinction concerns origin and
  what the sequence reports, not an extra chemical group or an automatic new node.
- cP-RNA: end-chemistry subpopulations may coexist; they are not automatically
  isolated fractions. Keep the mixture as one State and put supported terminal
  groups on their actual subjects; treated RNA alone does not explain selection.
- Cap-selected cDNA: selection of capped RNA does not put an RNA cap on the DNA.
- Smart-seq comparisons: a shared action diagram can conceal a distinguishing
  reagent/condition change; retrieve only the detail needed for that comparison.
- CITE-seq: reverse-transcriptase use can copy a synthetic DNA tag as well as RNA;
  do not infer template origin from the enzyme name or poly(A) capture alone.
- A source-supported total-RNA starting boundary can already be sufficient.
  Do not add unreported end groups or an upstream cell-isolation procedure just
  to fill these requirements. Likewise, raw reads must not assert genomic coordinates
  that only a later alignment Operation establishes.

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
Apply the six STATE CONTENT REQUIREMENTS to the actual assertions and their adjacent
Operations, not only to facts buried in evidence quotations. For a deficiency,
name the unmet requirement, missing fact, exact affected record/field and source
basis in the existing review rationale. Distinguish an available fact omitted from assertions
(revise), consequential unavailable evidence (research), a correct fact already
recorded but poorly displayed (presentation), and a detail outside the required
scope (no added assertion). Use the supplied review targets and existing action
values; these distinctions do not create new statuses or review-target IDs.
Do not reject a concise sufficient State solely for lacking additional properties.
Do not request a new node when an existing assertion can resolve the deficiency.
Review operation_semantics:<operation_id> for local roles, effects, resolution,
output grouping and agreement with adjacent States/Flows. These are scientific
judgments; JSON validation checks references/types, not truth. Source image/text
supports card extraction; primary sources support consequential corrections and
historical claims. Do not demand a full experimental recipe for a schematic.
"""

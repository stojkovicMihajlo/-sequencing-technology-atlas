"""Paper-grounded content for native image generation, not a drawing language."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Evidence(Record):
    source_id: Text
    quote: Annotated[Text, Field(max_length=800)]


class Stage(Record):
    id: Text
    caption: Text
    inputs: list[Text] = Field(min_length=1, description='Actual materials or data, including consequential molecular form and attachment.')
    transformation: Text
    outputs: list[Text] = Field(min_length=1)
    identity_carriers: list[Text] = Field(description='Cell, spatial, molecule or target association and the physical/data feature carrying it; empty only when inapplicable.')
    illustration: Text = Field(description='What the image should depict, including the supported molecular change; no drawing code.')
    evidence: list[Evidence] = Field(min_length=1)


class Connection(Record):
    source: Text
    target: Text
    carries: Text
    evidence: list[Evidence] = Field(min_length=1)


class Readout(Record):
    id: Text
    stage_id: Text
    detected_signal: Text
    sequenced_molecule: Text | None
    read_fields: list[Text]
    assignment: Text = Field(description='How the detected signal is attributed to the measured feature, cell or location; name consequential decoding/calibration dependencies.')
    biological_quantity: Text
    evidence: list[Evidence] = Field(min_length=1)


class DiagramPlan(Record):
    format_version: Literal['assay_diagram_plan/1']
    method_id: Text
    method_name: Text
    measurement_goal: Text
    starting_specimen: Text
    measurement_evidence: list[Evidence] = Field(min_length=1)
    stages: list[Stage] = Field(min_length=1)
    connections: list[Connection]
    distinguishing_design: Text
    design_stage_ids: list[Text] = Field(min_length=1)
    design_evidence: list[Evidence] = Field(min_length=1)
    readouts: list[Readout] = Field(min_length=1)
    legend: list[Text]
    unknowns: list[Text]
    image_generation_prompt: Annotated[Text, Field(max_length=5000)]

    @model_validator(mode='after')
    def references_resolve(self):
        ids = [stage.id for stage in self.stages]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate diagram Stage ID')
        if any(edge.source not in ids or edge.target not in ids for edge in self.connections):
            raise ValueError('Connection refers to an absent diagram Stage')
        if any(mid not in ids for mid in self.design_stage_ids):
            raise ValueError('Distinguishing design refers to an absent Stage')
        if any(readout.stage_id not in ids for readout in self.readouts):
            raise ValueError('Readout refers to an absent Stage')
        if len({readout.id for readout in self.readouts}) != len(self.readouts):
            raise ValueError('Duplicate readout ID')
        if not any(readout.sequenced_molecule for readout in self.readouts):
            raise ValueError('A sequencing assay needs an identified sequenced molecule')
        if any(readout.sequenced_molecule and not readout.read_fields for readout in self.readouts):
            raise ValueError('A sequencing readout must identify its actual informative read fields')
        return self


class ReviewCheck(Record):
    target: Text
    decision: Literal['accept','revise','needs_evidence']
    explanation: Text
    evidence: list[Evidence]


class PlanReview(Record):
    format_version: Literal['assay_diagram_review/1']
    checks: list[ReviewCheck]
    summary: Text


def check_evidence(items, sources):
    for item in items:
        if item.source_id not in sources:
            raise ValueError('Evidence is not a registered target-paper source: '+item.source_id)
        source = sources[item.source_id]
        if source.get('content_level') != 'fulltext' or item.quote not in source['text']:
            raise ValueError('Evidence quote is absent from its full-text source')
        if item.quote.strip().rstrip('.') == str(source.get('title') or '').strip().rstrip('.'):
            raise ValueError('A paper title does not establish an assay transformation')


def validate_plan(document, method_id, sources):
    plan = DiagramPlan.model_validate(document)
    if plan.method_id != method_id:
        raise ValueError('Output belongs to a different target method')
    groups = [plan.measurement_evidence, plan.design_evidence]
    groups += [stage.evidence for stage in plan.stages]
    groups += [edge.evidence for edge in plan.connections]
    groups += [readout.evidence for readout in plan.readouts]
    for group in groups:
        check_evidence(group,sources)
    return plan


def validate_review(document, plan, sources):
    review = PlanReview.model_validate(document)
    required = {'measurement','design','image_prompt', *[stage.id for stage in plan.stages],
                *['readout:'+readout.id for readout in plan.readouts]}
    actual = [check.target for check in review.checks]
    if len(actual) != len(set(actual)) or set(actual) != required:
        raise ValueError('Independent review must cover every Stage/readout and measurement/design/image prompt')
    for check in review.checks:
        if check.decision == 'accept' and not check.evidence:
            raise ValueError('An accepted scientific review check needs target-paper evidence')
        check_evidence(check.evidence,sources)
    return review

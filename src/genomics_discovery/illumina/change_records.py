"""Explicit, source-backed experimental changes; no fixed assay-stage taxonomy.

Relationship summaries do not replace these facts. A record can span several
Operations, or concern a State property or a Flow inside a broader operation.
"""
from __future__ import annotations

from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Evidence(Record):
    source_id: str
    quote: str = Field(min_length=12)


class WorkflowAnchor(Record):
    kind: Literal['state', 'operation', 'flow']
    record_id: str = Field(min_length=1, description='An exact supplied workflow record ID, not a new biological label.')
    field: str | None = Field(description='Optional JSON Pointer into that record for a changed property; null anchors the complete record.')


class WorkflowLocations(Record):
    earlier_scope: Literal['paper_only', 'bound_workflow'] = Field(description='paper_only means no earlier workflow was supplied; do not invent earlier record IDs.')
    earlier: list[WorkflowAnchor]
    later: list[WorkflowAnchor]

    @model_validator(mode='after')
    def scope_matches(self):
        if bool(self.earlier) != (self.earlier_scope == 'bound_workflow'):
            raise ValueError('Earlier anchors require a supplied bound workflow; paper-only has no invented IDs')
        for anchors in (self.earlier, self.later):
            keys = [(a.kind, a.record_id, a.field) for a in anchors]
            if len(keys) != len(set(keys)):
                raise ValueError('Duplicate workflow anchor')
        return self


class ChangeEvidence(Record):
    before: list[Evidence] = Field(description='Earlier-paper passages establishing the before-side fact; empty only when it is unavailable and unresolved.')
    after: list[Evidence] = Field(min_length=1, description='Later-paper passages establishing the actual later implementation.')
    retained: list[Evidence] = Field(description='Evidence for claimed retained features, covering both implementations; empty if none is claimed.')
    inheritance: list[Evidence] = Field(description='Later attribution of actual use/adaptation, distinct from evidence that two procedures differ. Empty when inheritance is not established.')


class ReportedEffect(Record):
    status: Literal['measured', 'reported_qualitative', 'author_rationale']
    description: str = Field(min_length=1, description='What improved, worsened, stayed similar, or was proposed to change; do not presume a benefit.')
    metric: str | None
    reported_value: str | None = Field(description='Preserve the source value and units; do not calculate an unsupported effect.')
    comparison_basis: Literal['comparative', 'absolute', 'not_reported'] = Field(description='Comparative results have an actual comparator; an absolute capability measurement need not have one. Do not infer improvement from an absolute value.')
    comparator: str | None = Field(description='The actual comparator in this result, which need not be the historical donor.')
    tested_context: str | None
    attribution_scope: Literal['this_change', 'joint_changes', 'whole_assay']
    joint_change_ids: list[str] = Field(description='For joint_changes, name every jointly tested change; otherwise empty.')
    evidence: list[Evidence] = Field(min_length=1)

    @model_validator(mode='after')
    def measured_has_context(self):
        if self.status == 'measured' and not all((self.metric, self.reported_value, self.tested_context)):
            raise ValueError('A measured effect needs metric, source value and tested context')
        if self.comparison_basis == 'comparative' and not self.comparator:
            raise ValueError('A comparative result needs its actual comparator')
        if self.comparison_basis != 'comparative' and self.comparator:
            raise ValueError('Only a comparative result declares a comparator')
        if self.attribution_scope == 'joint_changes':
            if len(set(self.joint_change_ids)) < 2 or len(set(self.joint_change_ids)) != len(self.joint_change_ids):
                raise ValueError('Joint effects must identify at least two distinct changes')
        elif self.joint_change_ids:
            raise ValueError('Only a joint effect may list joint change IDs')
        return self


class ConsequenceAndLimits(Record):
    effect_status: Literal['reported', 'not_established']
    reported_effects: list[ReportedEffect]
    limits: list[str] = Field(description='Source-qualified trade-offs or limits of interpretation/transfer. Missing reports do not establish that none exist.')
    limit_evidence: list[Evidence]
    unresolved: list[str] = Field(description='Explicit unknown compatibility or attribution questions; do not present these as demonstrated limits.')

    @model_validator(mode='after')
    def report_matches(self):
        if bool(self.reported_effects) != (self.effect_status == 'reported'):
            raise ValueError('Effect status must distinguish reported results from no established result')
        if self.limits and not self.limit_evidence:
            raise ValueError('Asserted limits require evidence; unsupported questions belong in unresolved')
        return self


class ExperimentalChange(Record):
    id: str = Field(min_length=1, description='Local address for this comparison, not a primitive class or mechanism label.')
    compared_feature: str = Field(min_length=1, description='Identify the experimental feature being compared; not a vague benefit such as better library preparation.')
    before: str | None = Field(description='The specified earlier implementation at this feature; null if the earlier evidence is unavailable.')
    after: str = Field(min_length=1, description='What the later implementation does at the same feature, including consequential material/mechanism/placement distinctions.')
    retained: list[str] = Field(description='Features retained within the specific contribution being compared, when needed to delimit what is adopted or changed. Do not inventory everything the assays share. Shared features alone do not establish inheritance. Empty is allowed when no such feature is established.')
    workflow_locations: WorkflowLocations
    evidence: ChangeEvidence
    consequence_and_limits: ConsequenceAndLimits

    @model_validator(mode='after')
    def factual_support(self):
        if self.before and not self.evidence.before:
            raise ValueError('An asserted earlier arrangement needs earlier evidence')
        if self.retained and not self.evidence.retained:
            raise ValueError('Asserted retained features require evidence')
        if self.before is None and not self.consequence_and_limits.unresolved:
            raise ValueError('Unavailable before-side evidence must remain an explicit unresolved question')
        return self


def resolve_pointer(document, pointer):
    if pointer is None or pointer == '':
        return document
    if not pointer.startswith('/'):
        raise ValueError('Field anchor must be a JSON Pointer')
    value = document
    for token in pointer[1:].split('/'):
        if re.search(r'~(?![01])', token):
            raise ValueError('Invalid JSON Pointer escape')
        token = token.replace('~1', '/').replace('~0', '~')
        try:
            if isinstance(value, list):
                if not re.fullmatch(r'0|[1-9][0-9]*', token):
                    raise ValueError('Invalid JSON Pointer array index')
                value = value[int(token)]
            else:
                value = value[token]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError('Field anchor does not exist in the supplied record') from exc
    return value


def workflow_records(extraction):
    return {(kind, row['id']): row for kind, key in
            (('state', 'states'), ('operation', 'operations'), ('flow', 'flows'))
            for row in extraction[key]}


def validate_changes(changes, *, sources, later_records, earlier_records=None,
                     later_source_ids, earlier_source_ids, require_later_anchor=True):
    """Check references, quote presence and evidence sides; entailment needs review."""
    ids = [c.id for c in changes]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate experimental change ID')
    for change in changes:
        locations = change.workflow_locations
        if require_later_anchor and not locations.later:
            raise ValueError('A workflow change needs an actual later State, Operation or Flow anchor')
        for anchors, records in ((locations.later, later_records), (locations.earlier, earlier_records)):
            for anchor in anchors:
                if records is None or (anchor.kind, anchor.record_id) not in records:
                    raise ValueError('Workflow anchor absent from its supplied side')
                resolve_pointer(records[anchor.kind, anchor.record_id], anchor.field)
        sets = [(change.evidence.before, earlier_source_ids),
                (change.evidence.after, later_source_ids),
                (change.evidence.retained, earlier_source_ids | later_source_ids),
                (change.evidence.inheritance, later_source_ids),
                (change.consequence_and_limits.limit_evidence, earlier_source_ids | later_source_ids)]
        if change.retained:
            cited = {e.source_id for e in change.evidence.retained}
            if not cited.intersection(earlier_source_ids) or not cited.intersection(later_source_ids):
                raise ValueError('Retained-feature evidence must cover both implementations')
        for effect in change.consequence_and_limits.reported_effects:
            sets.append((effect.evidence, later_source_ids))
            if effect.attribution_scope == 'joint_changes' and not set(effect.joint_change_ids) <= set(ids):
                raise ValueError('Joint effect refers to a missing change')
        for evidence, allowed in sets:
            for item in evidence:
                if item.source_id not in sources or item.source_id not in allowed:
                    raise ValueError('Change evidence belongs to the wrong or an unregistered paper side')
                source = sources[item.source_id]
                if source.get('content_level') not in {'fulltext', 'abstract'}:
                    raise ValueError('Experimental change evidence requires primary content')
                if item.quote not in source['text']:
                    raise ValueError('Change quote absent from its registered source')
                if source.get('title') and item.quote.strip().rstrip('.') == source['title'].strip().rstrip('.'):
                    raise ValueError('A publication title is not support for an experimental change')


CHANGE_RECORD_INSTRUCTIONS = """EXPLICIT EXPERIMENTAL CHANGE RECORDS
For every scoped contribution, fill the supplied compared_feature, before, after,
retained, workflow_locations, evidence, and consequence_and_limits fields.
Identify the same experimental feature on both sides. Several Operations may
implement one feature; one Operation may contain several distinct changes.
For documented inheritance, establish the specific earlier contribution actually
used in the later assay, its role there, and how that use changes or remains
unchanged. Do not infer inheritance from shared features. The retained list only
delimits this contribution; it is not a checklist of similarities across assays.
The before/after facts and technical consequence are primary. A broad relationship
summary is not their substitute, and component size does not determine ancestry.

Evidence.before comes from the cited earlier work; evidence.after from the later
work. evidence.inheritance is the later source's actual attribution of use or
adaptation. A difference, a citation, and a reported gain are different facts.
Retained features need evidence from both sides. An unchanged adopted contribution
can have identical before/after descriptions; do not invent a change or gain.
Keep measured effects, qualitative reports, author rationale and unknown effects
distinct. Name the actual result comparator and context; it may differ from the
historical donor. A joint intervention or whole-assay result must not become an
individually demonstrated effect of each component. Keep downside and null results.
An absolute measured capability can have no comparator; mark comparison_basis
accordingly. Such a value does not by itself establish an improvement.

Anchor the affected State, Operation, Flow or property using exact supplied IDs
and optional JSON Pointers. Paper-only earlier contributions have no invented
workflow IDs. A missing relevant distinction can request a targeted refinement;
do not expand the complete workflow into an execution recipe. Existing graphs
are read-only. Unreported details and source access failures stay explicit.
"""

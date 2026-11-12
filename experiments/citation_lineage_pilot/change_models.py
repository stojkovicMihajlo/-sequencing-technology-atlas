"""Version4 citation output: explicit changes, with derived legacy query summaries."""
from typing import Literal

from pydantic import Field, model_validator

from models import Record, Finding, Review
from genomics_discovery.illumina.change_records import ExperimentalChange, Evidence
from genomics_discovery.illumina.history_coverage import CoverageFinding


def evidence_union(items):
    result, seen = [], set()
    for item in items:
        key = (item.source_id, item.quote)
        if key not in seen:
            seen.add(key); result.append(item)
    return result


class ChangeClaim(Record):
    id: str
    reference_id: str
    contributor: str = Field(description='Name the specific contribution of the cited work relevant to this claim: a mechanism, reagent design, analysis rule, linked procedure or assay strategy. For documented_reuse, identify what the later assay actually uses; a paper title or generic similarity is insufficient.')
    contributor_kind: Literal['assay', 'experimental_component', 'general_method', 'other']
    relation: Literal['documented_reuse', 'mechanistic_precedent', 'comparison', 'background', 'no_established_relation']
    citation_context: str
    claim_scope: Literal['seed_workflow', 'supporting_experiment', 'unlocalized']
    seed_operation_ids: list[str] = Field(description='Existing Operations providing workflow context. Exact changed States/Operations/Flows/properties are located inside change_records; this does not claim the whole context was inherited.')
    change_records: list[ExperimentalChange] = Field(min_length=1)
    catalogue_candidates: list[str]
    unresolved: list[str]

    @property
    def seed_evidence(self):
        return evidence_union(e for c in self.change_records for e in
                              [*c.evidence.after, *c.evidence.inheritance])

    @property
    def predecessor_evidence(self):
        return evidence_union(e for c in self.change_records for e in c.evidence.before)

    @property
    def earlier_demonstration(self):
        return '\n'.join(c.before or 'Not established' for c in self.change_records)

    @property
    def seed_design_change(self):
        return '\n'.join(c.compared_feature + ': ' + c.after for c in self.change_records)

    def query_record(self):
        # Compatibility fields are a deterministic projection, never independently
        # generated summaries whose wording can drift from the explicit records.
        return {**self.model_dump(mode='json'),
                'seed_evidence': [e.model_dump() for e in self.seed_evidence],
                'predecessor_evidence': [e.model_dump() for e in self.predecessor_evidence],
                'earlier_demonstration': self.earlier_demonstration,
                'seed_design_change': self.seed_design_change}


class ChangeAnalysis(Record):
    format_version: Literal['citation_analysis/4'] = 'citation_analysis/4'
    seed_method: str
    claims: list[ChangeClaim]
    reference_dispositions: dict[str, str]
    summary: str = Field(description='Explain the method contribution, including source-supported new approaches with no established historical donor. Do not invent a parent.')


class ChangeCheck(Record):
    change_id: str
    decision: Literal['accept', 'revise', 'needs_evidence', 'reject']
    factual_assessment: str = Field(min_length=1, description='Check the compared feature, before, after and retained features against both papers. Independently check contributor_kind: an experimental assay/design contribution or supporting software/resource context; a workflow data Operation alone does not make a generic tool experimental ancestry.')
    workflow_assessment: str = Field(min_length=1, description='Check the exact State/Operation/Flow/property locations and consequential interfaces.')
    evidence_assessment: str = Field(min_length=1, description='Verify what the later assay actually uses from the cited contribution and how the later paper establishes that use. Distinguish experimental inheritance, supporting software/resource use, comparison and mechanistic precedent. Shared features and a real quotation alone do not establish inheritance; do not expand component reuse into adoption of the whole earlier assay. Catalogue candidate names/IDs are not verified method implementation identities.')
    consequence_assessment: str = Field(min_length=1, description='Check improvement/change, comparator, context, causal scope, trade-offs and unknowns; no reported gain is allowed.')
    evidence: list[Evidence] = Field(min_length=1)


class ChangeFinding(Finding):
    change_checks: list[ChangeCheck]


class ChangeReview(Review):
    format_version: Literal['citation_change_review/1'] = 'citation_change_review/1'
    findings: list[ChangeFinding]


class CoverageChangeReview(Record):
    format_version: Literal['citation_change_review/2'] = 'citation_change_review/2'
    analysis_sha256: str
    findings: list[ChangeFinding]
    coverage_findings: list[CoverageFinding]
    summary: str

    @property
    def omitted_candidates(self):
        """Schedule bounded correction only for established missing contributions."""
        return [f.description for f in self.coverage_findings if f.kind == 'omitted_supported_contribution']


class MethodMatch(Record):
    status: Literal['resolved','not_in_atlas','unresolved','ambiguous','not_applicable']
    record_id: str | None = Field(description='Known catalogue method ID only when identity and relevant implementation are independently resolved; otherwise null.')
    method_name: str | None = Field(default=None, description='Reviewed name of an identified earlier method absent from the atlas. Required and nonempty for not_in_atlas; null when no method identity is established. Do not invent a method merely to create an ancestor.')
    rationale: str = Field(min_length=1, description='Explain publication identity and relevant implementation match, mismatch or ambiguity after checking the supplied catalogue first. For not_in_atlas, identify known candidates considered and why their implementations do not match, or state that none is present. A shared paper or matching name alone is insufficient; a same-named different implementation can remain absent. Earlier Operation/State mapping is not required.')
    evidence: list[Evidence] = Field(description='For resolved, quote the cited earlier full text establishing the relevant assay/implementation. Catalogue text alone cannot resolve a method identity.')

    @model_validator(mode='after')
    def resolution_is_explicit(self):
        if self.status == 'resolved':
            if not self.record_id or not self.record_id.strip() or not self.evidence:
                raise ValueError('Resolved method match needs a method ID and earlier full-text evidence')
        elif self.record_id is not None:
            raise ValueError('Unresolved/ambiguous/not-applicable method match cannot choose an endpoint')
        if self.status == 'not_in_atlas' and (not self.method_name or not self.method_name.strip() or not self.evidence):
            raise ValueError('Identified method absent from atlas needs a reviewed method name and earlier full-text evidence')
        return self


class MethodChangeFinding(ChangeFinding):
    method_match: MethodMatch


class CoverageMethodReview(CoverageChangeReview):
    format_version: Literal['citation_change_review/3'] = 'citation_change_review/3'
    findings: list[MethodChangeFinding]


def parse_review(document):
    model = (CoverageMethodReview if document.get('format_version') == 'citation_change_review/3'
             else CoverageChangeReview if document.get('format_version') == 'citation_change_review/2'
             else ChangeReview if document.get('format_version') == 'citation_change_review/1' else Review)
    return model.model_validate(document)

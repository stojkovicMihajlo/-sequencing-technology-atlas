"""Version4 citation output: explicit changes, with derived legacy query summaries."""
from typing import Literal

from pydantic import Field

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
    contributor: str
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
    factual_assessment: str = Field(min_length=1, description='Check the compared feature, before, after and retained features against both papers.')
    workflow_assessment: str = Field(min_length=1, description='Check the exact State/Operation/Flow/property locations and consequential interfaces.')
    evidence_assessment: str = Field(min_length=1, description='Distinguish source support for facts from actual historical attribution; a real quote alone is insufficient.')
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


def parse_review(document):
    model = (CoverageChangeReview if document.get('format_version') == 'citation_change_review/2'
             else ChangeReview if document.get('format_version') == 'citation_change_review/1' else Review)
    return model.model_validate(document)

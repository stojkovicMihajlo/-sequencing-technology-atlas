"""Distinguish unrepresented supported contributions from evidence requests."""
from typing import Literal

from pydantic import Field

from .change_records import Record, Evidence


class CoverageFinding(Record):
    id: str = Field(min_length=1)
    kind: Literal['omitted_supported_contribution', 'research_lead']
    reference_id: str | None = Field(description='Exact earlier source ID for an established omitted contribution; null is allowed for an unidentified/unregistered research lead.')
    description: str = Field(min_length=1, description='The missing contribution or unanswered source question. Do not recast an already held claim as a missing established relation.')
    affected_claim_ids: list[str] = Field(description='Existing claim IDs whose interpretation is affected. Empty for an independent missing contribution or future research lead.')
    blocks_affected_claims: bool = Field(description='True only if the issue prevents acceptance of the specified existing claims. A lead does not automatically invalidate independent claims.')
    evidence: list[Evidence] = Field(min_length=1)
    requested_evidence: str | None = Field(description='For research_lead, the specific source/property needed. For omitted_supported_contribution, null: its evidence must already be available.')


def validate_coverage(findings, sources, claim_ids):
    ids = [f.id for f in findings]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate coverage finding ID')
    blocked = set()
    for finding in findings:
        affected = finding.affected_claim_ids
        if len(affected) != len(set(affected)) or set(affected) - set(claim_ids):
            raise ValueError('Coverage findings must name distinct existing claim IDs')
        if finding.blocks_affected_claims and not affected:
            raise ValueError('A blocking coverage finding needs specific affected claims')
        for evidence in finding.evidence:
            source = sources.get(evidence.source_id)
            if not source or evidence.quote not in source.get('text', ''):
                raise ValueError('Coverage evidence must quote a registered source exactly')
        if finding.kind == 'omitted_supported_contribution':
            required = {'seed', finding.reference_id}
            if (not finding.reference_id or finding.requested_evidence is not None
                    or not required <= {e.source_id for e in finding.evidence}
                    or any(sources.get(sid, {}).get('content_level') != 'fulltext' for sid in required)):
                raise ValueError('An established omitted contribution requires both full papers; missing evidence is a research lead')
        elif not finding.requested_evidence:
            raise ValueError('A research lead must state the specific evidence needed')
        if finding.blocks_affected_claims:
            blocked.update(affected)
    return blocked


def coverage_record(findings):
    return {'format_version': 'citation_coverage/1',
        'completeness': 'incomplete' if any(f.kind == 'omitted_supported_contribution' for f in findings)
                        else 'no_supported_omission_identified',
        'findings': [f.model_dump(mode='json') for f in findings],
        'boundary': 'Coverage of the supplied evidence, not exhaustive literature knowledge. Research leads remain unresolved; individual claims require their own independent acceptance.'}


def check_published_coverage(record, sources, links, withheld):
    if record.get('format_version') != 'citation_coverage/1':
        raise ValueError('Unknown historical coverage contract')
    findings = [CoverageFinding.model_validate(row) for row in record['findings']]
    all_ids = [row['claim']['id'] for row in [*links, *withheld]]
    blocked = validate_coverage(findings, sources, all_ids)
    if record != coverage_record(findings):
        raise ValueError('Coverage status differs from the recorded findings')
    if blocked.intersection(row['claim']['id'] for row in links):
        raise ValueError('A published historical claim has a blocking coverage finding')

"""Claim-addressed definition checks; exact quotations do not prove entailment."""

from typing import Annotated, Literal, get_args

from pydantic import Field

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.curation.workflow_extraction.models import Evidence, Text, validate_evidence

DefinitionField = Literal[
    "substrate", "operation", "physical_mechanism", "output",
    "information_effect", "purpose", "context_requirements",
]
DEFINITION_FIELDS = get_args(DefinitionField)


class DefinitionCheck(StrictModel):
    # The parent decision hash binds the actual text; do not make a second,
    # independently editable copy of the scientific claim here.
    fields: Annotated[tuple[DefinitionField, ...], Field(min_length=1)]
    verdict: Literal["supported", "unsupported", "unresolved", "not_asserted"]
    rationale: Text
    evidence: tuple[Evidence, ...]


def definition_outcome(assignment, checks):
    """Validate coverage/explicit verdicts without pretending to judge their truth."""
    if assignment is None:
        if checks:
            raise ValueError("Absent assignment cannot have definition checks")
        return "not_proposed"
    fields = [field for check in checks for field in check.fields]
    if len(fields) != len(set(fields)) or set(fields) != set(DEFINITION_FIELDS):
        raise ValueError("Definition review must cover each definition field exactly once")
    for check in checks:
        absent = [field for field in check.fields
                  if getattr(assignment.definition, field) is None]
        if check.verdict == "not_asserted":
            if len(absent) != len(check.fields) or check.evidence:
                raise ValueError("Only an absent definition claim can be not_asserted, without evidence")
        elif absent:
            raise ValueError("An absent mechanism must be reviewed as not_asserted")
        elif check.verdict != "unresolved" and not check.evidence:
            raise ValueError("Supported or contradicted definition fields require scoped evidence")
    verdicts = {check.verdict for check in checks}
    return ("needs_revision" if "unsupported" in verdicts else
            "research_needed" if "unresolved" in verdicts else "supported_definition")


def validate_definition_review(assignment, checks, source_review, packet):
    outcome = definition_outcome(assignment, checks)
    if assignment is None:
        return outcome
    errors = []
    inspected = {(ref.source_id, ref.passage_id)
                 for check in source_review.checks for ref in check.inspected_passages}
    for check in checks:
        errors.extend(validate_evidence(check, packet))
        if {(ev.source_id, ev.passage_id) for ev in check.evidence} - inspected:
            errors.append("Definition check cites a passage absent from declared source inspection")
    if errors:
        raise ValueError("; ".join(errors))
    return outcome

"""Bind complete scientific unit inputs while withholding earlier review judgments."""

from copy import deepcopy

from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import TransformationOccurrence

HIDDEN_REVIEW_FIELDS = ("selected_checks", "workflow_review_status", "workflow_review_sha256")


def review_view(occurrence: TransformationOccurrence) -> dict:
    """Display all recorded science/uncertainty, with an opaque full-cut binding.

    This deliberately omits prior judgments and is not a lossless serialization
    of the complete occurrence. The host retains and validates the original.
    Candidate-visible review and source-model errors remain possible.
    """
    record = occurrence.model_dump(mode="json")
    for field in HIDDEN_REVIEW_FIELDS:
        del record[field]
    return {
        "format_version": "transformation_review_view/1.0",
        "occurrence_sha256": digest(occurrence),
        "record": deepcopy(record),
        "withheld_fields": list(HIDDEN_REVIEW_FIELDS),
        "boundary": "Scientific input view; prior target verdicts/rationales and parent "
        "review disposition withheld. The binding hashes the complete host-retained cut. "
        "This view establishes neither entailment nor acceptance.",
    }


def validate_review_view(view: dict, occurrence: TransformationOccurrence) -> None:
    if view != review_view(occurrence):
        raise ValueError("Reviewer input differs from its bound scientific cut "
                         "or exposes judgments")

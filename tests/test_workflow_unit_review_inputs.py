"""Review inputs preserve scientific claims without exposing previous judgments."""

import json
from copy import deepcopy

import pytest
from test_workflow_transformations import cases as cases

from genomics_discovery.curation.workflow_extraction.models import digest
from playground.workflow_unit_review_inputs import (
    HIDDEN_REVIEW_FIELDS,
    review_view,
    validate_review_view,
)


def test_view_preserves_every_scientific_field_and_opaque_original_binding(cases):
    occurrence = next(iter(cases[0].values()))
    original = occurrence.model_dump(mode="json")
    view = review_view(occurrence)
    assert view["occurrence_sha256"] == digest(occurrence)
    assert view["record"] == {k: v for k, v in original.items() if k not in HIDDEN_REVIEW_FIELDS}
    assert not set(HIDDEN_REVIEW_FIELDS) & view["record"].keys()
    assert "Fixture only" not in json.dumps(view)
    assert view["record"]["requirements"] and view["record"]["unresolved"]
    validate_review_view(view, occurrence)
    assert occurrence.model_dump(mode="json") == original


@pytest.mark.parametrize("change", ["opinion", "resource", "unknown", "binding", "extra"])
def test_altered_or_leaking_input_is_rejected_against_host_retained_cut(cases, change):
    occurrence = next(iter(cases[0].values()))
    view = deepcopy(review_view(occurrence))
    if change == "opinion":
        view["record"]["workflow_review_status"] = "supported"
    elif change == "resource":
        view["record"]["requirements"] = []
    elif change == "unknown":
        view["record"]["unresolved"] = []
    elif change == "binding":
        view["occurrence_sha256"] = "0" * 64
    else:
        view["review_verdict"] = "supported"
    with pytest.raises(ValueError):
        validate_review_view(view, occurrence)

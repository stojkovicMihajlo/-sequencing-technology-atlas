"""Display wiring must preserve adverse reviews, branches and source data."""

import json
import sys
from copy import deepcopy
from html.parser import HTMLParser

import pytest

from test_workflow_extraction_overview import feedback as feedback
from test_workflow_extraction_contract import candidate as candidate
from test_workflow_extraction_bindings import case as conditional_case
from test_workflow_extraction_runtime import source_case as source_case, make_review
from genomics_discovery.curation.workflow_extraction.__main__ import main
from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.models import Workflow, WorkflowDraft, WorkflowReview, digest
from genomics_discovery.curation.workflow_extraction.overview_html import overview_html
from genomics_discovery.curation.workflow_extraction.projection import export_candidate, export_diagnostic


class Surface(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.links, self.flows = [], [], []
        self.details = []
        self.in_summary = False
        self.default_text = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "a":
            self.links.append(attrs["href"])
        if "data-flow-id" in attrs:
            self.flows.append(attrs["data-flow-id"])
        if tag == "details":
            self.details.append("open" in attrs)
        if tag == "summary":
            self.in_summary = True

    def handle_endtag(self, tag):
        if tag == "details":
            self.details.pop()
        if tag == "summary":
            self.in_summary = False

    def handle_data(self, text):
        if all(self.details) or (self.in_summary and all(self.details[:-1])):
            self.default_text.append(text)


def test_html_keeps_every_flow_once_and_material_feedback_distinct(feedback):
    before = feedback.model_dump_json()
    html = overview_html(feedback)
    surface = Surface(html)
    assert sorted(surface.flows) == sorted(flow.id for flow in feedback.flows)
    assert len(surface.ids) == len(set(surface.ids))
    assert all(link[1:] in surface.ids for link in surface.links if link.startswith("#"))
    default = " ".join(surface.default_text)
    assert "control" in default and "data_input" in default
    assert "Use the measured signal for the documented adjustment." in default
    assert "retained" in default and "aliquot" in default
    assert "selection unspecified" in default and "association: unspecified" in default
    assert feedback.model_dump_json() == before
    assert digest(feedback) in html


def test_html_alternative_and_optional_conditions_survive(conditional_case):
    raw, _, _ = conditional_case
    workflow = assemble_workflow(WorkflowDraft.model_validate(raw), require_bindings=True)
    html = overview_html(workflow)
    default = " ".join(Surface(html).default_text)
    assert "exactly one of 2 State choices" in default
    assert "Control is available." in default and "optional" in default
    assert "source_qualified" in default
    assert sorted(Surface(html).flows) == sorted(flow.id for flow in workflow.flows)


def test_large_alternative_inventory_is_folded_without_deleting_choices(conditional_case):
    raw, _, _ = conditional_case
    workflow = assemble_workflow(WorkflowDraft.model_validate(raw), require_bindings=True)
    data = workflow.model_dump(mode="json")
    operation = next(op for op in data["operations"] if op["id"] == "measure")
    binding = next(b for b in operation["input_bindings"] if b["state_id"] == "standard")
    original = next(f for f in data["flows"] if f["source"] == "standard" and f["target"] == "measure")
    state = next(s for s in data["states"] if s["id"] == "standard")
    for index in range(22):
        sid = f"choice_{index}"
        data["states"].append({**deepcopy(state), "id": sid, "description": f"Choice {index}"})
        operation["inputs"].append(sid)
        operation["input_bindings"].append({**deepcopy(binding), "state_id": sid})
        data["flows"].append({**deepcopy(original), "id": f"choice_flow_{index}", "source": sid})
    large = Workflow.model_validate(data)
    html = overview_html(large)
    surface = Surface(html)
    assert "<details><summary>Alternative input group library_route" in html
    assert "exactly one of 24 State choices" in " ".join(surface.default_text)
    assert "Conditions apply to individual choices; expand to inspect them." in " ".join(surface.default_text)
    assert "Choice 21" not in " ".join(surface.default_text)
    assert "Choice 21" in html
    assert sorted(surface.flows) == sorted(flow.id for flow in large.flows)


def test_alternative_group_can_span_material_and_data_inputs(conditional_case):
    raw, _, _ = conditional_case
    data = assemble_workflow(WorkflowDraft.model_validate(raw), require_bindings=True).model_dump(mode="json")
    next(s for s in data["states"] if s["id"] == "standard")["kind"] = "data"
    for flow in data["flows"]:
        if flow["source"] == "standard":
            flow["kind"] = "data_input"
        elif flow["target"] == "standard":
            flow["kind"] = "data_output"
    workflow = Workflow.model_validate(data)
    html = overview_html(workflow)
    assert html.count("Alternative input group library_route") == 1
    assert "exactly one of 2 State choices" in html
    assert "exactly one of 1 State choices" not in html


def test_adverse_existing_review_is_visible_even_without_workflow_gaps(source_case):
    packet, _, raw = source_case
    workflow = Workflow.model_validate(raw)
    review_raw = make_review(raw, packet, verdict="unsupported")
    review_raw["omissions"] = [{"description": "Missing independently used fraction.",
        "blocking": False, "action": "revise_record", "evidence": raw["states"][0]["evidence"]}]
    review = WorkflowReview.model_validate(review_raw)
    html = overview_html(workflow, review=review)
    default = " ".join(Surface(html).default_text)
    assert "needs_revision" in default
    assert "Missing independently used fraction." in default
    assert "revise_record" in default and "non-blocking" in default
    assert "Review omission · advisory" not in html
    assert "unsupported" in default


def test_html_escapes_source_text_and_rejects_stale_review(source_case):
    packet, _, raw = source_case
    raw["title"] = '<script>bad()</script>'
    raw["operations"][0]["name"] = '<img src=x onerror="bad()">'
    workflow = Workflow.model_validate(raw)
    html = overview_html(workflow)
    assert '<script>bad()' not in html and '<img src=x' not in html
    assert '&lt;script&gt;bad()&lt;/script&gt;' in html
    assert '&lt;img src=x onerror=&quot;bad()&quot;&gt;' in html
    review = WorkflowReview.model_validate(make_review(raw, packet))
    changed = Workflow.model_validate({**raw, "title": "Different graph"})
    with pytest.raises(ValueError, match="[Ww]orkflow"):
        overview_html(changed, review=review)


@pytest.mark.parametrize("format", ["text", "json", "html"])
def test_cli_overview_creates_new_display_without_mutating_or_overwriting(
        source_case, tmp_path, monkeypatch, capsys, format):
    _, _, raw = source_case
    source = tmp_path / "workflow.json"
    source.write_text(json.dumps(raw))
    original = source.read_bytes()
    output = tmp_path / f"overview.{format}"
    monkeypatch.setattr(sys, "argv", ["atlas-extract", "overview", str(source),
                                     "--format", format, "--output", str(output)])
    main()
    assert output.is_file() and source.read_bytes() == original
    if format == "json":
        assert json.loads(output.read_text())["review_context"]["status"] == "unreviewed_projection"
    saved = output.read_bytes()
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert output.read_bytes() == saved and source.read_bytes() == original


@pytest.mark.parametrize("diagnostic", [False, True])
def test_exported_overviews_bind_original_workflow_and_review(source_case, tmp_path, diagnostic):
    packet, _, raw = source_case
    workflow = Workflow.model_validate(raw)
    review = WorkflowReview.model_validate(make_review(raw, packet,
        verdict="unsupported" if diagnostic else "supported"))
    exporter = export_diagnostic if diagnostic else export_candidate
    exporter(workflow, review, packet, tmp_path)
    stem = "overview_diagnostic" if diagnostic else "overview"
    view = json.loads((tmp_path / f"{stem}.json").read_text())
    assert view["workflow_sha256"] == digest(workflow)
    assert view["review_context"]["review_sha256"] == digest(review)
    assert view["review_context"]["outcome"] == ("needs_revision" if diagnostic else "supported")
    for suffix in ("txt", "html"):
        assert (tmp_path / f"{stem}.{suffix}").is_file()
    if diagnostic:
        assert not (tmp_path / "overview.html").exists()
        assert "unsupported" in (tmp_path / f"{stem}.html").read_text()
    else:
        assert "Operation 1" in (tmp_path / "protocol.txt").read_text()

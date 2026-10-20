"""Standalone reading surface over declared records; no inferred scientific grouping."""

from __future__ import annotations

import json
from html import escape

from .models import Workflow, WorkflowReview, digest
from .overview import workflow_overview
from .projection import _ordered_operations


def overview_html(workflow: Workflow, *, review: WorkflowReview | None = None) -> str:
    """Keep routes visible and place recipes and evidence behind native HTML disclosure.

    Labels may be visibly abbreviated but always link to the complete State record.
    No workflow is rewritten, accepted, filtered or semantically summarized here.
    """
    view = workflow_overview(workflow, review=review)
    states = {state.id: state for state in workflow.states}
    operations = {op.id: op for op in workflow.operations}
    anchors = {item.id: f"record-{index}" for index, item in enumerate(
        (*workflow.operations, *workflow.states))}

    def code(value) -> str:
        return "<pre>" + escape(json.dumps(value, indent=2, ensure_ascii=False)) + "</pre>"

    def link(identifier: str) -> str:
        item = states.get(identifier) or operations[identifier]
        label = item.description if identifier in states else item.name
        preview = label if len(label) <= 90 else label[:90] + "…"
        return (f'<a href="#{anchors[identifier]}" title="{escape(label, quote=True)}">'
                f'{escape(identifier)} · {escape(preview)}</a>')

    def route(flow) -> str:
        qualifier = (f'association: {flow.association or "unspecified"} · '
                     f'{flow.selection or "selection unspecified"}'
                     if flow.kind.endswith(("_input", "_output")) else "")
        if flow.alternative_group:
            qualifier += f" · exactly one in {flow.alternative_group}"
        condition = (f'<p class="condition">Condition: {escape(flow.condition)}</p>'
                     if flow.condition else "")
        return (f'<li data-flow-id="{escape(flow.id, quote=True)}">'
                f'<span class="kind">{escape(flow.kind)}</span> '
                f'{link(flow.source)} → {link(flow.target)} '
                f'<span class="qualifier">{escape(qualifier)}</span>{condition}'
                f'<details><summary>Connection {escape(flow.id)}: basis and evidence</summary>'
                f'{code(flow.model_dump(mode="json"))}</details></li>')

    cards = []
    for oid in _ordered_operations(workflow):
        op = operations[oid]
        # Each I/O flow appears once, beside its Operation; direct dependencies
        # appear once beside their target. No producer/consumer cross-product.
        attached = [flow for flow in workflow.flows
                    if (flow.kind.endswith("_input") and flow.target == oid)
                    or (flow.kind.endswith("_output") and flow.source == oid)
                    or (flow.kind in {"control", "precedence"} and flow.target == oid)]
        resources = "".join(f'<dt>{escape(resource.kind)}</dt>'
                            f'<dd>{escape(resource.description)}</dd>' for resource in op.resources)
        route_rows, emitted = [], set()
        for flow in attached:
            if flow.id in emitted:
                continue
            if flow.selection == "alternative":
                direction = "input" if flow.kind.endswith("_input") else "output"
                choices = [f for f in attached if f.selection == "alternative"
                           and f.kind.endswith("_" + direction)
                           and f.alternative_group == flow.alternative_group]
                emitted.update(f.id for f in choices)
                conditions = {f.condition for f in choices}
                qualification = ("Condition: " + next(iter(conditions))
                                 if len(conditions) == 1 and None not in conditions else
                                 "Conditions apply to individual choices; expand to inspect them.")
                route_rows.append(
                    '<li><details' + (' open' if len(choices) <= 3 else '') + '><summary>'
                    f'Alternative {direction} group {escape(flow.alternative_group)} · '
                    f'exactly one of {len(choices)} State choices</summary>'
                    '<ul>' + "".join(route(f) for f in choices) + '</ul></details>'
                    f'<p class="condition">{escape(qualification)}</p></li>')
            else:
                emitted.add(flow.id)
                route_rows.append(route(flow))
        cards.append(
            f'<article id="{anchors[oid]}"><h2>{escape(op.name)}</h2>'
            f'<p class="identifier">Operation {escape(oid)}</p><ul class="routes">'
            + "".join(route_rows) + '</ul>'
            '<details><summary>Action, recipe and evidence</summary>'
            f'<dl><dt>Action</dt><dd>{escape(op.action)}</dd>'
            f'<dt>Information effect</dt><dd>{escape(op.information_effect)}</dd>{resources}</dl>'
            '<details><summary>Exact Operation record</summary>'
            + code(op.model_dump(mode="json")) + '</details></details></article>')

    state_records = "".join(
        f'<details id="{anchors[state.id]}"><summary>{escape(state.id)} · '
        f'{escape(state.kind)}</summary><p>{escape(state.description)}</p>'
        '<details><summary>Exact State record and evidence</summary>'
        + code(state.model_dump(mode="json")) + '</details></details>'
        for state in workflow.states)
    gaps = ("".join(
        f'<li><strong>{"Blocking" if gap.blocking else "Non-blocking"} · '
        f'{escape(gap.id)}</strong><p>{escape(gap.description)}</p>'
        f'<p>Next evidence: {escape(gap.next_evidence)}</p></li>'
        for gap in workflow.unresolved) or '<li>No gaps declared in this record.</li>')
    groups = "".join(
        f'<details><summary>{escape(group.name)} · Module {escape(group.id)}</summary>'
        f'<p>{escape(group.boundary_rationale)}</p><ul>'
        + "".join(f'<li>{link(oid)}</li>' for oid in group.operation_ids)
        + '</ul></details>' for group in workflow.modules)
    review_section = '<p class="metadata">No review was supplied to this display.</p>'
    if review is not None:
        context = view["review_context"]
        findings = "".join(
            f'<li><strong>{escape(check["verdict"])} · {escape(check["target_id"])}</strong>'
            f'<p>{escape(check["rationale"])}</p><details><summary>Exact review check</summary>'
            + code(check) + '</details></li>' for check in context["nonpositive_checks"])
        omissions = "".join(
            f'<li><strong>Review omission · '
            f'{"blocking" if omission["blocking"] else "non-blocking"}</strong>'
            f'<p>{escape(omission["description"])}</p>'
            f'<p>Action: {escape(omission["action"])}</p>'
            '<details><summary>Exact review omission</summary>'
            + code(omission) + '</details></li>' for omission in context["omissions"])
        review_section = (
            f'<p class="metadata">Existing model/source review: '
            f'<strong>{escape(context["outcome"])}</strong>. No new review performed.</p>'
            + ('<ul class="gaps">' + findings + omissions + '</ul>' if findings or omissions else '')
            + '<details><summary>Exact supplied review</summary>'
            + code(review.model_dump(mode="json")) + '</details>')
    css = """*{box-sizing:border-box}body{margin:0;background:#f5f6f8;color:#202631;font:16px/1.5 Arial,Helvetica,sans-serif}
main{max-width:1080px;margin:auto;padding:36px 24px}h1{font-size:30px;line-height:1.2;margin:0 0 10px}
h2{font-size:19px;margin:0 0 5px}h3{margin-top:30px}p{margin:8px 0}.boundary,.metadata{color:#505d6d}
.boundary{padding:14px;background:#e9eef5;border-left:3px solid #42668a}.identifier{font-size:13px;color:#657184}
article{padding:20px;margin:16px 0;background:white;border:1px solid #d8dee6;border-radius:8px;scroll-margin-top:16px}
ul.routes{padding:0;list-style:none}.routes>li{padding:9px 0;border-top:1px solid #e8ebef}
a{color:#204f7a;text-decoration:none}a:hover{text-decoration:underline}.kind{font-size:12px;background:#eaf0f6;padding:3px 6px;border-radius:3px}
.qualifier{display:block;color:#505d6d;font-size:13px;margin-top:5px}.condition{color:#6b4916;font-size:14px}
details{margin:8px 0}summary{cursor:pointer;color:#294f73}details:target{outline:2px solid #42668a;padding:8px}
details:target>summary{font-weight:bold}dl{margin:12px 0}dt{font-weight:bold}dd{margin:3px 0 12px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f2f5;padding:12px;font:12px/1.45 monospace}
.gaps{padding:16px 30px;background:#fff6e8;border:1px solid #e6d1ac;border-radius:8px}.gaps li+li{margin-top:18px}
@media(max-width:600px){main{padding:20px 12px}article{padding:14px}h1{font-size:25px}}
"""
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(workflow.title)} · Overview</title><style>{css}</style><main>'
        f'<h1>{escape(workflow.title)}</h1><p class="metadata">{escape(workflow.method_id)} · '
        f'{escape(workflow.variant)}</p><p><strong>Scope:</strong> {escape(workflow.scope)}</p>'
        '<details><summary>How to read this view</summary>'
        '<p class="boundary">Reading view of the supplied extraction. No fresh source review or '
        'experimental validation. Every Operation and Flow is retained. Recipes and evidence expand '
        'below each Operation. State labels are text previews; follow a label for the full record. '
        'Card order follows declared dependencies, with ID ties; it is not an execution schedule.</p>'
        f'<p class="identifier">Workflow SHA-256: {digest(workflow)}</p></details>'
        + review_section
        + f'<p><a href="#limitations">{len(workflow.unresolved)} declared gaps and limitations</a> · '
        f'{sum(gap.blocking for gap in workflow.unresolved)} blocking</p>'
        + '<h3>Operations and connections</h3>' + "".join(cards)
        + '<h3 id="limitations">Declared gaps and limitations</h3><ul class="gaps">' + gaps + '</ul>'
        + '<h3>States: full material and data descriptions</h3>' + state_records
        + '<details><summary>Modules: optional navigation groups</summary>' + groups + '</details>'
        + '<details><summary>Recorded gap resolutions</summary>'
        + code([resolution.model_dump(mode="json") for resolution in workflow.gap_resolutions])
        + '</details></main><script>'
        'function reveal(){const target=document.getElementById(location.hash.slice(1));'
        'for(let item=target;item;item=item.parentElement){'
        'if(item.tagName==="DETAILS")item.open=true;}}'
        'addEventListener("hashchange",reveal);reveal();'
        '</script></html>\n')

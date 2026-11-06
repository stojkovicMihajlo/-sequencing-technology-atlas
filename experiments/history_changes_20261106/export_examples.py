"""Render an inspection snapshot of completed history results; no model calls.

Scientific records are copied unchanged. Short captions are display annotations,
not additional accepted claims or a replacement for the original evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
from html import escape
import json
import math
from pathlib import Path
import shutil
from xml.etree import ElementTree
import zipfile


FEATURED = [
    ("R11_tagmentation", "Purified DNA → native chromatin",
     "Tn5 library preparation → ATAC-seq",
     "The transferred fragmentation/tagging mechanism acts on a different substrate. Accessible chromatin becomes the measured selection."),
    ("c_R3_tagmentation", "Move tagmentation before DNA recovery",
     "Tn5 library preparation → ChIPmentation",
     "Tagment antibody-selected chromatin while it remains on beads; elution, crosslink reversal and DNA purification follow."),
    ("c_R4_primers", "Reuse a primer design",
     "ATAC-seq → ChIPmentation",
     "Credited reuse of custom Nextera PCR primers. This edge does not transfer ATAC's entire amplification procedure or claim a new primer benefit."),
    ("c_r8_stop", "Retain a positional interpretation",
     "iCLIP → irCLIP",
     "Both assign a crosslink nucleotide from the boundary of a crosslink-truncated cDNA. The RT enzyme and subsequent handling can differ."),
    ("c_r20_rt", "Transfer an experimentally demonstrated RT family",
     "Thermostable group II intron RT → irCLIP",
     "The earlier work establishes an RT family; irCLIP uses commercial TGIRT-III on recovered CLIP RNA. Its measured comparator is SuperScript III, not the historical donor construct."),
]


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def txt(value):
    return escape(str(value))


def para(value):
    return "<p>" + txt(value) + "</p>" if value else "<p class='muted'>Not established in this record.</p>"


def bullets(values):
    return "<ul>" + "".join("<li>" + txt(v) + "</li>" for v in values) + "</ul>" if values else para(None)


def quotes(values, sources):
    out = []
    for value in values:
        sid = value["source_id"]
        source = sources[sid]
        url = source.get("url") or ""
        title = txt(source.get("title") or sid)
        label = "<a href='" + txt(url) + "'>" + title + "</a>" if url.startswith("https://") else title
        out.append("<div class='citation'>" + label + "<small>Source " + txt(sid) +
                   "</small><blockquote>" + txt(value["quote"]) + "</blockquote></div>")
    return "".join(out) if out else para(None)


def overview():
    # Each numbered arrow selects an unchanged reviewed claim below. The paper
    # node is deliberately not presented as a second extracted workflow.
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 410" role="img" aria-label="Four selected documented reuse relationships">
<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="4" orient="auto"><path d="M0,0 L10,4 L0,8 Z" fill="#386c80"/></marker></defs>
<style>text{font-family:Arial,sans-serif;fill:#152a38}.node{fill:#f5f8fa;stroke:#8198a8;stroke-width:1.5}.paper{fill:#fff9ed;stroke:#b78a39}.edge{fill:none;stroke:#386c80;stroke-width:2.5;marker-end:url(#arrow)}.unbound{stroke-dasharray:7 5}.sub{font-size:14px;fill:#526675}.title{font-size:21px;font-weight:bold}.edge-label{font-size:15px;fill:#245568}.back{fill:white}</style>
<rect class="node paper" x="20" y="90" width="270" height="85" rx="12"/><text class="title" x="38" y="122">Tn5 library preparation</text><text class="sub" x="38" y="150">Adey paper contribution</text>
<rect class="node" x="430" y="90" width="220" height="85" rx="12"/><text class="title" x="455" y="123">ATAC-seq</text><text class="sub" x="455" y="151">Extracted workflow</text>
<rect class="node" x="830" y="90" width="260" height="85" rx="12"/><text class="title" x="852" y="123">ChIPmentation</text><text class="sub" x="852" y="151">Extracted workflow</text>
<a href="#R11_tagmentation"><path class="edge unbound" d="M290 125 L430 125"/><text class="edge-label" x="315" y="75">1 · Substrate</text><text class="edge-label" x="315" y="96">and readout</text></a>
<a href="#c_R4_primers"><path class="edge" d="M650 125 L830 125"/><text class="edge-label" x="677" y="75">3 · Custom-primer</text><text class="edge-label" x="677" y="96">design reuse</text></a>
<a href="#c_R3_tagmentation"><path class="edge unbound" d="M155 175 L155 222 L960 222 L960 175"/><rect class="back" x="400" y="209" width="355" height="25"/><text class="edge-label" x="413" y="226">2 · Substrate and workflow placement</text></a>
<rect class="node" x="245" y="300" width="230" height="85" rx="12"/><text class="title" x="270" y="333">iCLIP</text><text class="sub" x="270" y="360">Extracted workflow</text>
<rect class="node" x="755" y="300" width="230" height="85" rx="12"/><text class="title" x="780" y="333">irCLIP</text><text class="sub" x="780" y="360">Extracted workflow</text>
<a href="#c_r8_stop"><path class="edge" d="M475 341 L755 341"/><text class="edge-label" x="502" y="297">4 · Crosslink-coordinate</text><text class="edge-label" x="502" y="319">interpretation reuse</text></a>
</svg>"""
    # Inline the few style rules so the standalone SVG also survives scientific
    # figure renderers that do not implement SVG style sheets.
    root = ElementTree.fromstring(svg)
    rules = {
        'node': {'fill': '#f5f8fa', 'stroke': '#8198a8', 'stroke-width': '1.5'},
        'paper': {'fill': '#fff9ed', 'stroke': '#b78a39'},
        'edge': {'fill': 'none', 'stroke': '#386c80', 'stroke-width': '2.5', 'marker-end': 'url(#arrow)'},
        'unbound': {'stroke-dasharray': '7 5'},
        'sub': {'font-size': '14', 'fill': '#526675'},
        'title': {'font-size': '21', 'font-weight': 'bold'},
        'edge-label': {'font-size': '15', 'fill': '#245568'},
        'back': {'fill': 'white'},
    }
    for element in root.iter():
        if element.tag.endswith('}text'):
            element.attrib.update({'font-family': 'Arial, sans-serif', 'fill': '#152a38'})
        for name in element.get('class', '').split():
            element.attrib.update(rules.get(name, {}))
    for element in list(root):
        if element.tag.endswith('}style'):
            root.remove(element)
    for parent in list(root.iter()):
        for element in list(parent):
            if element.tag.endswith('}a'):
                position = list(parent).index(element)
                parent.remove(element)
                for offset, child in enumerate(list(element)):
                    parent.insert(position + offset, child)
    ns = '{http://www.w3.org/2000/svg}'
    for element in root.iter(ns + 'path'):
        element.attrib.pop('marker-end', None)
        if 'unbound' not in element.get('class', '').split():
            continue
        points = ([(290, 125), (430, 125)] if element.get('d') == 'M290 125 L430 125'
                  else [(155, 175), (155, 222), (960, 222), (960, 175)])
        segments = []
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            length = math.hypot(x2-x1, y2-y1)
            for start in range(0, math.ceil(length), 12):
                stop = min(start + 7, length)
                segments.append(f'M{x1+(x2-x1)*start/length:g} {y1+(y2-y1)*start/length:g} L{x1+(x2-x1)*stop/length:g} {y1+(y2-y1)*stop/length:g}')
        element.set('d', ' '.join(segments))
        element.attrib.pop('stroke-dasharray', None)
    for points in ['420,119 430,125 420,131', '820,119 830,125 820,131',
                   '745,335 755,341 745,347', '954,185 960,175 966,185']:
        ElementTree.SubElement(root, ns + 'polygon', {'points': points, 'fill': '#386c80'})
    ElementTree.register_namespace('', 'http://www.w3.org/2000/svg')
    return ElementTree.tostring(root, encoding='unicode')


def build(project, output, *, render_png=False):
    experiment = project / "experiments/history_changes_20261106"
    batch = experiment / "completed_batch_51341499/batch_10_01"
    index_path = batch / "connections/history_index.json"
    index = read(index_path)
    result = read(batch / "RESULT.json")
    output.mkdir(parents=True, exist_ok=False)
    raw = output / "records"; raw.mkdir()
    shutil.copy2(index_path, output / "history_index.json")

    # Content addressing locates the copied original documents without editing
    # any absolute paths inside historical records.
    roots = [batch, experiment / "completed_mapping_51339109",
             project / "deliverables/atlas_presentation_packet_20261106/history"]
    local_by_hash = {}
    for root in roots:
        for name in ["graph.json", "citation_graph.json", "sources.json", "citation_sources.json"]:
            for path in root.rglob(name):
                local_by_hash.setdefault(digest(path), path)

    unique_quotes = set(); artifacts = {}; source_files = {}

    def verify_quotes(value, sources, pin):
        if isinstance(value, dict):
            if "source_id" in value and "quote" in value:
                sid, quote = value["source_id"], value["quote"]
                assert sid in sources, sid
                assert quote and quote in sources[sid]["text"], (sid, quote[:90])
                unique_quotes.add((pin, sid, quote))
            for child in value.values():
                verify_quotes(child, sources, pin)
        elif isinstance(value, list):
            for child in value:
                verify_quotes(child, sources, pin)

    edge_html = {}; count_by_target = {}; nodes = index["nodes"]
    featured = {row[0]: row for row in FEATURED}
    for number, edge in enumerate(index["edges"], 1):
        pin = edge["artifact"]["sha256"]
        graph_file = local_by_hash[pin]
        registry = edge["artifact"]["source_registry"]
        source_file = local_by_hash[registry["sha256"]]
        sources = read(source_file)
        artifacts[pin] = str(graph_file.relative_to(project))
        source_files[registry["sha256"]] = str(source_file.relative_to(project))
        assert edge["review"]["decision"] == "accept"
        verify_quotes(edge["claim"], sources, registry["sha256"])
        verify_quotes(edge["review"], sources, registry["sha256"])
        if edge.get("mapping_review"):
            assert all(v["decision"] == "accept" for v in edge["mapping_review"]["findings"])
            verify_quotes(edge["mapping_review"], sources, registry["sha256"])
        cid = edge["claim_id"]
        filename = f"{number:02d}_{cid}.json"
        write(raw / filename, edge)
        source = nodes[edge["source"]]["name"]
        target = nodes[edge["target"]]["name"]
        count_by_target.setdefault(target, Counter())[edge["relation"]] += 1
        mapped = bool(edge.get("source_citation"))
        endpoint_text = ("Both extracted workflows located and independently reviewed" if mapped else
                         "Earlier paper contribution; no exact earlier-workflow mapping")
        intro = ""
        if cid in featured:
            _, title, short_path, caption = featured[cid]
            intro = "<h2>" + txt(title) + "</h2><p class='path'>" + txt(short_path) + "</p>" + para(caption)
        else:
            intro = "<h3>" + txt(source) + " → " + txt(target) + "</h3>"
        block = (intro + "<p><span class='badge'>" + txt(edge["relation"]) +
                 "</span><span class='badge " + ("mapped" if mapped else "paper") + "'>" +
                 txt(endpoint_text) + "</span></p>")
        for change in edge["claim"]["change_records"]:
            block += "<h3>Compared feature</h3>" + para(change["compared_feature"])
            block += "<div class='pair'><div><h3>Before · earlier implementation</h3>" + para(change["before"]) + "</div>"
            block += "<div><h3>After · later implementation</h3>" + para(change["after"]) + "</div></div>"
            block += "<h3>Retained</h3>" + bullets(change["retained"])
            effects = change["consequence_and_limits"]
            block += "<h3>Reported result and attribution</h3>"
            if not effects["reported_effects"]:
                block += para("No isolated benefit is established for this reused feature.")
            for effect in effects["reported_effects"]:
                block += "<div class='effect'><strong>" + txt(effect["status"]) + "</strong>" + para(effect["description"])
                block += "<dl>" + "".join("<dt>" + txt(k.replace("_", " ")) + "</dt><dd>" + txt(effect[k]) + "</dd>"
                    for k in ["metric", "reported_value", "comparator", "tested_context", "attribution_scope"] if effect.get(k)) + "</dl></div>"
            block += "<h3>Limits and unresolved details</h3>" + bullets(effects["limits"] + effects["unresolved"])
            location = change["workflow_locations"]
            block += "<details><summary>Exact State, Operation and Flow locations</summary>"
            for side in ["earlier", "later"]:
                locs = location[side]
                if side == "earlier" and edge.get("mapping"):
                    locs = next(v["earlier_locations"] for v in edge["mapping"]["mappings"] if v["change_id"] == change["id"])
                block += "<h4>" + side.title() + "</h4>" + ("<pre>" + txt(json.dumps(locs, ensure_ascii=False, indent=2)) + "</pre>" if locs else para("Paper-level contribution; no extracted earlier-workflow location is asserted."))
            block += "</details><details><summary>Primary-paper evidence: before, after, retained and historical attribution</summary>"
            for key in ["before", "after", "retained", "inheritance"]:
                block += "<h4>" + txt(key.title()) + "</h4>" + quotes(change["evidence"][key], sources)
            block += "</details>"
        block += "<details><summary>Independent scientific review</summary>" + para(edge["review"]["rationale"]) + quotes(edge["review"]["evidence"], sources) + "</details>"
        if mapped:
            block += "<details><summary>Independent review of exact workflow locations</summary>"
            for finding in edge["mapping_review"]["findings"]:
                block += para(finding["rationale"]) + quotes(finding["evidence"], sources)
            block += "</details>"
        block += "<p class='muted'>Unchanged source record: <a href='records/" + txt(filename) + "'>" + txt(cid) + " · JSON</a></p>"
        edge_html[edge["id"]] = "<article id='" + txt(cid) + "'>" + block + "</article>"

    counts = Counter(e["relation"] for e in index["edges"])
    summary = {"job_id": result["job_id"], "status": result["status"],
        "elapsed": "01:44:36", "new_or_freshly_reviewed_histories": result["completed_citation_runs"],
        "reused_history": "ChIPmentation", "relationship_counts": dict(counts),
        "reviewed_workflow_connections": sum(bool(e.get("source_citation")) for e in index["edges"]),
        "earlier_paper_contributions_without_exact_workflow_mapping": sum(e["relation"] == "documented_reuse" and not e.get("source_citation") for e in index["edges"]),
        "source_holds": {k:v for k,v in result["citation_runs"].items() if v["status"] == "source_hold"},
        "failed": {k:v for k,v in result["citation_runs"].items() if v["status"] == "failed"},
        "per_method_relationships": {k:dict(v) for k,v in count_by_target.items()},
        "boundary": "Pilot evidence graph. Counts include software and reference-data reuse; they are not counts of new assays, innovations, or experimentally validated evolutionary transitions."}
    write(output / "STATUS.json", summary)
    write(output / "CHECKS.json", {"history_index_sha256": digest(index_path), "artifact_hashes_verified": artifacts,
        "source_registry_hashes_verified": source_files, "unique_registered_quotes_found_verbatim": len(unique_quotes),
        "all_published_claims_have_accepting_review": True,
        "all_workflow_mappings_have_accepting_review": True,
        "boundary": "Local byte, reference and quotation checks. These do not rejudge scientific entailment or substitute for human expert review."})
    overview_svg = overview()
    ElementTree.fromstring(overview_svg)
    (output / "evolution_examples.svg").write_text(overview_svg)
    shown = set()
    sections = []
    for cid, *_ in FEATURED:
        edge = next(e for e in index["edges"] if e["claim_id"] == cid)
        sections.append(edge_html[edge["id"]]); shown.add(edge["id"])
    rest = "".join("<details class='relationship'><summary>" + txt(nodes[e["source"]]["name"]) + " → " + txt(nodes[e["target"]]["name"]) + " · " + txt(e["relation"]) + "</summary>" + edge_html[e["id"]] + "</details>" for e in index["edges"] if e["id"] not in shown)
    html = """<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Sequencing technology · evolutionary examples</title><style>
*{box-sizing:border-box}body{font:17px/1.6 Arial,Helvetica,sans-serif;background:#f3f6f8;color:#172a38;margin:0}main{max-width:1180px;margin:auto;padding:40px 28px}h1{font-size:34px;line-height:1.2}h2{font-size:26px}h3{font-size:18px;color:#20546a;margin-bottom:8px}h4{margin-bottom:6px}.lead{max-width:950px}.stats{display:flex;gap:12px;flex-wrap:wrap;margin:25px 0}.stats div{background:white;border:1px solid #cedbe3;border-radius:10px;padding:14px 20px}.stats b{font-size:27px;display:block}.figure,article{background:white;border:1px solid #cad8e1;border-radius:12px;padding:24px;margin:24px 0}.figure svg{width:100%;height:auto;min-width:700px}.figure{overflow-x:auto}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}.pair>div,.effect{background:#f5f8fa;padding:8px 18px;border-radius:8px}.effect{margin:12px 0}.badge{font-size:13px;border:1px solid #a9bbc7;border-radius:5px;display:inline-block;padding:4px 8px;margin:2px 6px 2px 0}.mapped{background:#e9f5ee}.paper{background:#fff6e5}.path{font-size:21px;font-weight:bold}.muted,small{color:#576e7c}a{color:#145a7b}details{border-top:1px solid #dae3e9;margin-top:15px;padding-top:12px}summary{cursor:pointer;font-weight:bold}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;background:#f3f6f8;padding:12px}blockquote{margin:10px 0 22px;border-left:3px solid #8aa7b7;padding:4px 16px}.citation small{display:block}dl{display:grid;grid-template-columns:160px 1fr;font-size:15px}dt{font-weight:bold}dd{margin-left:0}.relationship{background:white;padding:16px;border:1px solid #cad8e1;border-radius:8px}.relationship article{border:none;padding:0}.notice{border-left:4px solid #ad8437;padding:4px 18px;background:#fff9ee}@media(max-width:760px){.pair{grid-template-columns:1fr}main{padding:20px 14px}}@media print{body{background:white}.figure,article{break-inside:avoid}details{display:block}main{padding:0}}
</style><main><p class='muted'>Sequencing technology atlas · completed pilot · 6 November 2026</p>
<h1>What changed between technologies?</h1>
<p class='lead'>Read a directed edge as a specific, evidenced contribution to the later assay. The before/after record identifies what changed or was retained. A credited reuse does not automatically imply a new capability or an improvement.</p>
<div class='stats'><div><b>13</b>Documented reuse</div><div><b>15</b>Mechanistic precedents</div><div><b>29</b>Comparisons</div><div><b>2</b>Exact workflow connections</div></div>
<p>These are 57 scoped relationships across seven method neighborhoods, including analysis software and reference-data use. Only the 13 documented-reuse relationships assert actual use; the other types remain separate.</p>
<div class='figure'>""" + overview_svg + """<p class='muted'>Solid arrows: both extracted workflow locations reviewed. Dashed arrows: earlier contribution supported by its paper, with no exact earlier-workflow mapping. All four arrows above are documented reuse; their labels state the feature. Following two arrows does not prove transitive inheritance of the same feature.</p></div>
<p><a href='#R11_tagmentation'>1. ATAC substrate change</a> · <a href='#c_R3_tagmentation'>2. ChIPmentation placement</a> · <a href='#c_R4_primers'>3. Primer reuse</a> · <a href='#c_r8_stop'>4. Positional interpretation</a> · <a href='#c_r20_rt'>5. RT transfer</a></p>
<div class='notice'><strong>Current boundary.</strong> This is a reviewed pilot, not the full evolutionary atlas. Six histories completed in this batch and the existing ChIPmentation neighborhood was reused. Capture-C and G&amp;T-seq were held at primary-paper assignment; ClickSeq failed its native analysis stage. Eleven of the 13 reuse relationships still have paper-level earlier endpoints. Independent model review is recorded; this is not wet-lab or human expert validation.</div>
""" + "".join(sections) + "<h2>Remaining reviewed relationships</h2><p>Expand a relationship to inspect its full change record. Comparisons and mechanistic precedents are not displayed as ancestral arrows above.</p>" + rest + """<p><a href='history_index.json'>Complete relationship index · JSON</a> · <a href='STATUS.json'>Batch outcomes and counts</a> · <a href='CHECKS.json'>Local integrity checks</a> · <a href='evolution_examples.svg'>Standalone graph · SVG</a></p></main></html>"""
    (output / "index.html").write_text(html)
    if render_png:
        import fitz
        document = fitz.open(stream=overview_svg.encode(), filetype='svg')
        document[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(output / 'evolution_examples.png')
    manifest = {str(p.relative_to(output)):digest(p) for p in output.rglob('*') if p.is_file()}
    write(output / "MANIFEST.json", manifest)
    with zipfile.ZipFile(output.with_suffix('.zip'), 'w', zipfile.ZIP_DEFLATED) as z:
        for p in output.rglob('*'):
            if p.is_file():z.write(p, output.name + '/' + str(p.relative_to(output)))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--png', action='store_true', help='Also render the graph PNG using PyMuPDF')
    args = parser.parse_args()
    print(json.dumps(build(args.project.resolve(), args.output.resolve(), render_png=args.png), indent=2))

"""Build an offline presentation source packet from exact observed artifacts.

No LLM calls, workflow rewriting, or scientific graph inference occurs here.
Graphviz drawings contain exactly the recorded State/Operation nodes and Flows.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
from pathlib import Path
import shutil
import textwrap

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / 'deliverables/atlas_presentation_packet_20261106'
METHODS = ('atac-seq', 'clickseq', 'chipmentation')


def read(path):
    return json.loads(path.read_text())


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + '\n')


def dump(path, value):
    write(path, json.dumps(value, indent=2, ensure_ascii=False))


def code(value):
    return '<pre><code>' + html.escape(json.dumps(value, indent=2, ensure_ascii=False)) + '</code></pre>'


def table(headers, rows):
    def cells(row, tag):
        return '<tr>' + ''.join('<' + tag + '>' + html.escape(str(v)) + '</' + tag + '>' for v in row) + '</tr>'
    return '<table><thead>' + cells(headers, 'th') + '</thead><tbody>' + ''.join(cells(r, 'td') for r in rows) + '</tbody></table>'


def dot_graph(document):
    lines = ['digraph workflow {',
        'graph [rankdir=TB,bgcolor="white",pad="0.25",nodesep="0.3",ranksep="0.4",dpi=160];',
        'node [fontname="Helvetica",fontsize=13,margin="0.16,0.10",color="#344054",penwidth=1.2];',
        'edge [fontname="Helvetica",fontsize=10,color="#475467",arrowsize=0.7];']
    for kind, rows in (('STATE', document['states']), ('OPERATION', document['operations'])):
        for row in rows:
            label = kind + '\n' + '\n'.join(textwrap.wrap(row['label'], 38))
            style = 'rounded,filled' if kind == 'STATE' else 'filled'
            fill = '#f2f4f7' if kind == 'STATE' else '#e7f0fa'
            lines.append(json.dumps(row['id']) + ' [shape=box,style=' + json.dumps(style)
                + ',fillcolor=' + json.dumps(fill) + ',label=' + json.dumps(label, ensure_ascii=False) + '];')
    for flow in document['flows']:
        style = ',style=dashed' if flow['kind'] == 'precedes' else ''
        lines.append(json.dumps(flow['source']) + ' -> ' + json.dumps(flow['target'])
            + ' [label=' + json.dumps(flow['kind']) + style + '];')
    return '\n'.join([*lines, '}'])


def method_page(mid):
    directory = PACK / 'examples' / mid
    d = read(directory / 'extraction.json')
    # This is a readable projection, never a second model-generated extraction.
    from genomics_discovery.illumina.graph import protocol_text
    from genomics_discovery.illumina.models import parse_extraction, parse_review
    if not (directory / 'workflow.txt').exists():
        write(directory / 'workflow.txt', protocol_text(parse_extraction(d), parse_review(read(directory / 'review.json'))))
    write(directory / 'workflow.dot', dot_graph(d))
    op = d['operations'][0]
    chosen = set(op['inputs']) | set(op['outputs'])
    excerpt = {'excerpt_of': 'extraction.json', 'not_a_complete_extraction': True,
        'format_version': d['format_version'], 'record_id': d['record_id'],
        'states': [s for s in d['states'] if s['id'] in chosen], 'operations': [op],
        'flows': [f for f in d['flows'] if f['source'] == op['id'] or f['target'] == op['id']]}
    dump(directory / 'one_operation_excerpt.json', excerpt)
    properties = [[s['id'], s['label'], s['kind'], '\n'.join(p['property'] + ': ' + str(p.get('value')) for p in s['properties'])] for s in d['states']]
    operations = [[o['id'], o['label'], ', '.join(o['inputs']), ', '.join(o['outputs']), o['resolution']] for o in d['operations']]
    flows = [[f['id'], f['source'], f['kind'], f['target']] for f in d['flows']]
    body = '<p><a href="../../OPEN_FIRST.html">Back to packet</a></p><h1>' + html.escape(d['title']) + '</h1>'
    body += '<p>' + html.escape(d['scope']) + '</p><h2>Original Illumina PNG</h2>'
    body += '<p>Original registered asset, unchanged. Transparent pixels are displayed against white. Original resolution may limit terminal-symbol detail.</p><a href="illumina_diagram.png"><img class="source" src="illumina_diagram.png" alt="Original Illumina diagram"></a>'
    body += '<h2>Recorded workflow</h2><p>' + str(len(d['states'])) + ' States · ' + str(len(d['operations'])) + ' Operations · ' + str(len(d['flows'])) + ' Flows. All recorded nodes and Flows are drawn; no hidden operations were added for this illustration.</p>'
    body += '<a href="extracted_workflow.png"><img class="workflow" src="extracted_workflow.svg" alt="Exact extracted State–Operation–Flow graph"></a>'
    body += '<p><a href="extraction.json">Full extraction JSON</a> · <a href="workflow.txt">Readable projection</a> · <a href="review.json">Independent review</a> · <a href="author_prompt.txt">Exact author prompt</a> · <a href="reviewer_prompt.txt">Exact reviewer prompt</a></p>'
    body += '<h2>States</h2>' + table(['Local ID', 'Label', 'Kind', 'Properties'], properties)
    body += '<h2>Operations</h2>' + table(['Local ID', 'Action', 'Input State IDs', 'Output State IDs', 'Resolution'], operations)
    body += '<h2>Flows</h2>' + table(['Local ID', 'Source', 'Kind', 'Target'], flows)
    body += '<h2>One complete Operation</h2><p>Verbatim record from extraction.json, including its evidence. Expand when explaining how the fields are used.</p><details><summary>Show actual Operation JSON</summary>' + code(op) + '</details>'
    body += '<h2>Unresolved qualifications</h2>' + (table(['Question', 'Blocking'], [(u['question'], u['blocking']) for u in d['unknowns']]) if d['unknowns'] else '<p>No Unknown objects were recorded for this scoped extraction. This is not proof that all experimental details are known.</p>')
    body += '<p>Accepted by the software gates and a separate LLM review. This is not human expert certification or a fully executable wet-lab protocol.</p>'
    write(directory / 'READ_WORKFLOW.html', page(d['title'], body))
    return d


def page(title, body):
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>' + html.escape(title) + '</title><style>' + STYLE + '</style></head><body><main>' + body + '</main></body></html>'


STYLE = '''body{font-family:Arial,Helvetica,sans-serif;color:#182230;background:white;margin:0;font-size:17px;line-height:1.5}main{max-width:1150px;margin:auto;padding:42px 32px}h1{font-size:36px;line-height:1.2}h2{font-size:25px;margin-top:38px}a{color:#175b8e}table{border-collapse:collapse;width:100%;font-size:15px;margin:18px 0}th,td{border-bottom:1px solid #d0d5dd;padding:10px;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#f2f4f7}pre{font:13px/1.5 Menlo,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f7f9;padding:18px}img.source{width:100%;height:auto;background:white;border:1px solid #eaecf0;margin:20px 0}img.workflow{display:block;max-width:100%;max-height:1050px;margin:20px auto}details{margin:20px 0}summary{cursor:pointer}small{color:#475467}.note{border-left:3px solid #98a2b3;padding-left:15px}@media print{main{padding:10px}details{display:block}a{color:#182230}h2{break-after:avoid}}'''


def main():
    receipt = read(PACK / 'COLLECTION_RECEIPT.json')
    for item in receipt['copied_source_files']:
        assert hashlib.sha256((PACK / item['path']).read_bytes()).hexdigest() == item['sha256'], item['path']
    shutil.rmtree(PACK / 'source_only', ignore_errors=True)  # user meant an interesting example, not Cool-seq
    counts = read(ROOT / 'docs/reports/illumina_inventory_numbers_20261106.json')
    for suffix in ('json', 'html'):
        shutil.copyfile(ROOT / ('docs/reports/illumina_inventory_numbers_20261106.' + suffix), PACK / ('inventory/counts_report.' + suffix))
    report = PACK / 'inventory/counts_report.html'
    content = report.read_text()
    import re
    for link in re.findall(r'href="([^"]+)"', content):
        if link.startswith('../../provenance/'):
            source = (ROOT / 'docs/reports' / link).resolve()
            target = PACK / 'inventory/supporting_records' / source.name
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
            content = content.replace('href="' + link + '"', 'href="supporting_records/' + source.name + '"')
    content = content.replace('href="illumina_inventory_numbers_20261106.json"', 'href="counts_report.json"')
    report.write_text(content)
    identity = ROOT / 'provenance/experiments/technology_sources_20261105/release_01/IDENTITY_AND_CLASSIFICATION.csv'
    shutil.copyfile(identity, PACK / 'inventory/IDENTITY_AND_CLASSIFICATION.csv')
    details = read(ROOT / 'provenance/experiments/technology_sources_20261105/card_coverage_20261106/delivered/CARD_DETAILS.json')
    card_ids = {d['card_id'] for d in details if d['saved_card_record']}
    rows = []
    for row in csv.DictReader(identity.open()):
        if row['entry_type'] != 'named_method': continue
        sources = row['source_ids'].split('; ')
        rows.append({**row, 'on_website_card': any(s in card_ids for s in sources),
            'on_poster': any(s.startswith('poster_') for s in sources)})
    assert (len(rows), sum(r['on_website_card'] for r in rows), sum(r['on_poster'] for r in rows)) == (273, 174, 271)
    with (PACK / 'inventory/ALL_273_METHODS.csv').open('w') as f:
        out = csv.DictWriter(f, fieldnames=list(rows[0])); out.writeheader(); out.writerows(rows)
    count_rows = [
        ('Website cards',154,'Source cards; one card can describe several named methods.'),
        ('Named method/implementation entries on cards',174,'182 named appearances minus 8 repeats.'),
        ('DNA poster rows / named entries', '105 / 126','Multiple names can occupy one row.'),
        ('RNA poster rows / named entries', '102 / 116','Counts are within that poster.'),
        ('Single-cell poster rows / named entries', '78 / 81','Third poster; overlaps both other posters.'),
        ('All main poster rows',285,'105 + 102 + 78.'),
        ('Distinct poster method entries',271,'126 + 116 + 81 = 323 appearances; cross-poster overlap removed.'),
        ('Methods in cards AND posters',172,'Identity-reviewed intersection.'),
        ('Card-only / poster-only methods','2 / 99','Separate coverage additions, not duplicate jobs.'),
        ('Union of named method entries',273,'174 + 271 − 172 = 273.'),
        ('Footer chemical/legend panels',39,'Source panels; not 39 additional methods.'),
        ('All recorded source occurrences',478,'154 cards + 285 rows + 39 panels; not technologies.'),
        ('Shared card-image groups',21,'Involve 53 methods; common image does not prove identical workflows.'),
        ('Distinct website-card image hashes',153,'Image identity is separate from card or method identity.'),
        ('Primary folder groups','61 / 135 / 77','Genome / Transcriptome / Epigenome; sums to 273. Secondary placements can overlap.')]
    with (PACK / 'inventory/COUNTS_FOR_SLIDES.csv').open('w') as f:
        out = csv.writer(f);out.writerow(['Level','Count','Meaning']);out.writerows(count_rows)
    write(PACK / 'inventory/COUNT_DEFINITIONS.txt', '''Counts belong to the reviewed 6 November 2026 library snapshot, not an exhaustive census of sequencing methods.
154 cards -> 189 total card-to-entry assignments -> minus 7 non-method assignments -> 182 named method appearances -> minus 8 repeated appearances -> 174 named method/implementation entries.
Cards by assigned-entry count: 132 have 1, 12 have 2, 8 have 3, 1 has 4, 1 has 5. Entry is not always a named method.
Posters: DNA 105 rows/126 named entries; RNA 102/116; single-cell 78/81. There are THREE posters in this source set.
323 appearances across the three poster method sets resolve to 271 entries. 223 occur in one poster, 44 in two and 4 in all three.
Union: 174 card methods + 271 poster methods − 172 shared = 273 entries. This adds 99 poster-only entries; 2 card entries do not appear in the main poster rows.
478 is a source-occurrence count: 154 cards + 285 main poster rows + 39 footer panels.
21 groups of shared original card PNGs involve 53 named methods. Shared depiction is not verified workflow identity; each member retains its own scope/paper assignments.
The older 201-entry atlas used an earlier inventory and is not a count of the current curated union or completed extractions.
The classification counts 61 Genome +135 Transcriptome +77 Epigenome refer to the primary folder assignment. Secondary source classifications can overlap.
File availability counts in counts_report.json are from their stated source snapshot; they are not live download or extraction totals.
Known source exceptions: the MDA/IMS-MDA/MIDAS CMS card is empty; scBS-seq is a category-tree/404 reference but has a poster method; a malformed MPE-seq citation URL is recorded separately. Do not invent missing descriptions.
See ALL_273_METHODS.csv for every named entry, its source IDs, classifications, aliases and paper IDs. See counts_report.html for reviewed grouped-card examples and source exceptions.''')
    documents = [method_page(mid) for mid in METHODS]
    graph = read(PACK / 'history/chipmentation/graph.json')
    inherited = [row for row in graph['links'] if row['kind'] == 'documented_reuse']
    dump(PACK / 'history/ACTUAL_REUSE_EXAMPLES.json', inherited)
    history_counts = {}
    from collections import Counter
    for mid in ('atac-seq', 'chipmentation'):
        g = read(PACK / 'history' / mid / 'graph.json')
        history_counts[mid] = {'published_by_type': dict(Counter(r['kind'] for r in g['links'])),
            'published_total': len(g['links']), 'withheld': len(g['withheld'])}
    dump(PACK / 'history/RESULTS_SNAPSHOT.json', history_counts)
    history_body = '<p><a href="../OPEN_FIRST.html">Back to packet</a></p><h1>What a historical connection records</h1><p>These are exact published claims from the completed pilot. The earlier endpoints remain paper-scoped contributions until the separate workflow mapping stage is reviewed.</p>'
    for row in inherited:
        for c in row['claim']['change_records']:
            history_body += '<h2>' + html.escape(c['compared_feature']) + '</h2>'
            history_body += table(['Field','Actual recorded content'], [(k, c[k]) for k in ('before','after')])
            history_body += '<details><summary>Complete actual change, evidence and limits</summary>' + code(c) + '</details>'
    history_body += '<h2>Actual review outcomes</h2>' + code(history_counts)
    history_body += '<p>ChIPmentation: 2 reuse claims, 2 mechanistic precedents and 8 comparisons. Only the reuse claims are published historical contributions. ATAC-seq: all 24 candidate claims were withheld in this pilot. Completion is not acceptance.</p><p>The workflow-connection correction has passed 902 local software tests at this snapshot. Its live mapping test has not yet run; no newly reviewed method-to-method connection is claimed here.</p>'
    write(PACK / 'history/READ_CHANGES.html', page('Historical changes', history_body))
    write(PACK / 'SLIDE_NOTES.txt', OUTLINE)
    write(PACK / 'HOW_TO_READ_OUTPUT.txt', OUTPUT_GUIDE)
    write(PACK / 'README.txt', '''Start with OPEN_FIRST.html (double-click; no server or installation needed).
SLIDE_NOTES.txt contains a short 7-slide outline with concrete assets and suggested wording.
examples/atac-seq and examples/clickseq contain original Illumina PNGs, actual accepted extraction/review JSON, exact executed author/reviewer prompts and schemas, readable text and graph images.
examples/chipmentation provides the workflow behind the history example.
history/READ_CHANGES.html shows the exact before/after records, with full underlying results alongside.
inventory/COUNTS_FOR_SLIDES.csv and COUNT_DEFINITIONS.txt explain all count levels. ALL_273_METHODS.csv provides the full named-method inventory.
harness/extraction_runtime contains selected exact source files from the runtime that generated these examples. This is a code-reading reference, not a complete standalone installation; the runnable project is on GitHub.
HOW_TO_READ_OUTPUT.txt explains every record and which artifact is authoritative.
COLLECTION_RECEIPT.json pins copied source artifacts. MANIFEST.json pins the final packet. Run python3 VERIFY.py to check packet files and relative HTML links without model calls.
Original Illumina assets remain their authors' material. Sources and record locators are retained; no new rights are granted by packaging them.
No model calls or scientific result edits were made to build this packet. Readable text and graph drawings are deterministic views of extraction.json.''')
    body = '<h1>Sequencing technology atlas</h1><p>Presentation source packet · 6 November 2026</p><p><a href="SLIDE_NOTES.txt">7-slide outline</a> · <a href="HOW_TO_READ_OUTPUT.txt">How to read the output</a> · <a href="inventory/counts_report.html">Detailed inventory report</a></p>'
    body += '<h2>What the harness does</h2><p>Curated method + original diagram + catalogue prose + registered papers → author extracts State–Operation–Flow JSON → Python checks references and evidence → fresh reviewer checks claims and omissions → accepted workflow graph or an explicit hold.</p><p>The diagram guides decomposition. Papers confirm consequential molecular features, mechanisms, routing, identity carriers and interfaces. The current full-card run uses 10 concurrent method tasks; each task has its own author and fresh reviewer. It permits no automatic correction rounds in that run. The underlying engine supports a bounded correction loop.</p>'
    body += '<h2>Catalogue counts</h2>' + table(['Level','Count','Meaning'],count_rows)
    body += '<p><a href="inventory/ALL_273_METHODS.csv">All 273 named entries</a> · <a href="inventory/COUNT_DEFINITIONS.txt">The arithmetic and exceptions</a></p><h2>Real examples</h2>'
    body += table(['Method','Recorded graph','Open example'],[(d['title'], f"{len(d['states'])} States / {len(d['operations'])} Operations / {len(d['flows'])} Flows", 'examples/' + d['record_id'] + '/READ_WORKFLOW.html') for d in documents])
    for mid,d in zip(METHODS,documents):
        body += '<p><a href="examples/' + mid + '/READ_WORKFLOW.html">Open ' + html.escape(d['title']) + ': diagram, graph, records and exact prompts</a></p>'
    body += '<h2>Connecting methods</h2><p><a href="history/READ_CHANGES.html">Actual evidence-backed before/after examples</a>. Citation-directed research nominates predecessors. Comparison and mechanistic precedent remain separate from documented reuse. A separately reviewed mapping is required to attach a cited contribution to an existing exact workflow version.</p>'
    body += '<h2>Scope of the demonstrated result</h2><p>Accepted workflow extractions and completed historical research are included. Software checks and independent LLM review do not establish zero errors or wet-lab executability. The new workflow-connection correction is implemented and software-tested; its live test remains pending in this packet.</p><p>Optional live-run checkpoint: ' + html.escape(receipt['batch_updated_at']) + ' — ' + html.escape(str(receipt['batch_counts'])) + '. These are changing extraction outcomes, not catalogue counts.</p>'
    write(PACK / 'OPEN_FIRST.html', page('Sequencing technology atlas — presentation packet',body))


OUTLINE = '''SEVEN-SLIDE OUTLINE — exact source files are in this packet
Use a white background and Arial/Helvetica. Keep the original PNGs at their aspect ratio. Do not enlarge a low-resolution strip and describe the enlargement as added source detail.

1. From published assay diagrams to a queryable technology atlas
On slide: “Represent what each assay does, then record what changed between assays.”
One line: “Illumina diagrams provide the decomposition; catalogue descriptions explain the assay; original papers confirm consequential scientific details.”
Say: “This gives us comparable representations without trying to reconstruct every experiment’s executable recipe.”

2. What is being counted?
On slide: 154 website cards → 174 named method/implementation entries. Three posters → 271 method entries. 172 shared → 273 entries in the combined library.
Small second line: 478 = source occurrences, not technologies.
Explain: A card or poster row can group names; the same method can recur in several sources. We consolidate sources by reviewed method identity, while retaining separate implementations when required.
Asset: inventory/COUNTS_FOR_SLIDES.csv. Detailed arithmetic and complete entry list are in the adjacent files.

3. What we ask the extraction author to produce
Show: State → Operation → State, with each arrow stored as a typed Flow.
State = the actual preparation or dataset at a boundary, with evidenced properties.
Operation = a source-supported action on declared input States, producing declared output States.
Flow = the typed, directional relationship connecting an input/output State and an Operation, or a separately evidenced ordering relation.
Show exact prompt excerpt: “The diagram guides granularity, not an image-only inclusion rule.”
Show exact prompt excerpt: “Include a detail when it distinguishes the scoped method, determines a depicted transformation, or is needed to interpret an important material, readout or interface.”
Code to inspect: harness/extraction_runtime/src/genomics_discovery/illumina/representation.py (StructuredState, StructuredOperation); models.py (Flow); prompts.py (build_prompt).
The full prompts actually executed are in each example folder. Excerpts here are deliberately shorter.

4. ATAC-seq: diagram to extracted graph
Place examples/atac-seq/illumina_diagram.png above extracted_workflow.png.
On slide: “4 Operations · 6 States · 9 Flows.”
Trace: prepared nuclei + adapter-loaded Tn5 → tagmented preparation → purified amplified DNA library → sequencing reads → genome-aligned fragments.
Explain: The paper identifies the prepared-nuclei input and the preferential accessibility mechanism. The diagram guides grouping; alignment is a compact paper-supported data interface. “Open DNA” describes the starting chromatin; it is not an invented operation to open it.
Use examples/atac-seq/one_operation_excerpt.json if you want one real dictionary example. It is a labeled excerpt, not the complete output.
Important qualification: the source image’s far-right ends are clipped; the graph does not infer unknown end chemistry from the colors. The review treats independently supported library identity separately from image completeness.

5. ClickSeq: a chemically distinct example
Place examples/clickseq/illumina_diagram.png and extracted_workflow.png.
On slide: “Unfragmented RNA → azido-terminated cDNA → click-ligated adapter → amplified cDNA library.”
Explain: The unusual move is stochastic chain termination during copying, followed by click-ligation. It does not require the starting RNA to be physically fragmented. The extraction records the relevant 3′ end chemistry and the adapter-bearing product, rather than treating all adapter addition as the same mechanism.
This graph has 4 Operations · 7 States · 10 Flows. It ends at a physical library; it does not pretend sequencing data already exist.
Show the real o_click Operation (description, mechanism, input/output IDs, typed effects, evidence) in extraction.json, not an invented miniature schema.

6. How review and publication work
On slide: “Author → structural/evidence checks → independent reviewer → accepted graph or explicit hold.”
Python checks exact IDs, State/Operation/Flow consistency, typed properties, evidence references/quotes and source/artifact versions. The reviewer checks scientific support, consequential omissions, scope, labels, interfaces and source discrepancies.
Current full-card run: 10 concurrent method tasks; independent author/reviewer per method; no automatic correction rounds. Missing evidence is held for recovery, not invented. The underlying extractor can perform bounded revisions in other configurations.
Show one actual reviewer finding from examples/atac-seq/review.json alongside the affected record. “Accepted” means the configured gates passed, not human certification or no remaining uncertainty.

7. From workflow graphs to evolutionary connections
On slide: “Read the later paper → identify attributed earlier contributions → retrieve earlier paper → describe the exact change → independent review → locate it in the extracted workflows.”
Actual example 1: Tn5 fragmentation/adapter tagging demonstrated on extracted DNA → ChIPmentation applies it to immunoprecipitated chromatin still on beads, before DNA recovery.
Actual example 2: ChIPmentation explicitly reuses ATAC-seq’s custom primer design; that does not imply adopting all ATAC amplification conditions.
The edge records: compared feature; before; after; retained features; workflow locations; evidence; consequences and limits. Reported whole-assay gains stay distinct from proof that one component caused the gain.
Open history/READ_CHANGES.html or ACTUAL_REUSE_EXAMPLES.json for exact records.
Status to state honestly: the historical claims are reviewed paper-contribution edges. The missing exact earlier-workflow connection stage has been implemented and software-tested; its live test is pending at packet creation. Do not present it as already demonstrated.
Use the atlas later to retrieve concrete design moves, interface requirements and supporting sources for ideation. This packet demonstrates extraction/history artifacts; it is not a validated new assay or a completed ideation experiment.
'''

OUTPUT_GUIDE = '''WHAT EACH FILE MEANS

1. card.json and source_registry.json are inputs. They assign the method/version, source descriptions, actual diagram files and registered paper texts with provenance. They are not an extracted assay.
2. author_prompt.txt and author_schema.json are the exact executed author instructions and output schema. The caller compiled them using build_prompt; the prompt builder itself does not run a model.
3. extraction.json is the authoritative structured author output after validation. Current format: illumina_diagram/1.1.
   record_id/title/method_names/scope establish the assigned implementation and what it measures.
   states[] stores material or data boundaries. A State has an ID, label, kind, optional addressable components, typed properties and evidence. These are aspects of one preparation, not necessarily separate physical fractions.
   operations[] stores actions. Each Operation has its own ID, label, description, input/output State IDs, supported mechanism and information_effect, resolution, input_roles, typed effects, output_grouping and evidence.
   flows[] stores edges separately. source and target are exact local record IDs. Flow kinds are material_input (State→Operation), material_output (Operation→State), data_input, data_output, and precedes (Operation→Operation). It is not an extra step.
   method_scopes[] assigns Operations to the method; paper_matches[] records source relevance; corrections[] preserves source discrepancies/resolutions; unknowns[] records specific unresolved questions and blocking status.
   Current representation does not require a separate Module layer.
4. reviewer_prompt.txt and reviewer_schema.json describe the independently invoked reviewer. review.json binds the exact candidate and checks its required record targets. The author does not choose final publication by labeling itself accepted.
5. Python uses the checked extraction and accepted review to produce graph.json. It namespaces the same local IDs and preserves typed connections/evidence. It does not infer new biochemical steps or ancestry.
6. workflow.txt, READ_WORKFLOW.html, workflow.dot, extracted_workflow.svg and extracted_workflow.png are deterministic views of extraction.json. No second LLM “summarized” them. Original JSON strings remain available even when a drawing wraps the label.
7. one_operation_excerpt.json contains a copied Operation and its actual incident States and Flows, retaining evidence. It is explicitly marked incomplete.

READ A SPECIFIC EXAMPLE
ATAC: find Operation tagment_accessible_chromatin. Its inputs are State IDs native_chromatin and tn5_transposome; its output is tagmented_preparation. Each matching Flow is stored in flows[], with its own evidence. The same output State becomes the input to purify_and_amplify. IDs are references, not scientific labels; read label/properties/description for meaning.
ClickSeq: find o_click. Inspect its input end-group properties, the Operation mechanism, then its output and outgoing material_output Flow. The input/output properties and evidence carry the chemically meaningful distinction.

EVIDENCE
Evidence objects are nested on the precise assertion or record. source_id resolves to source_registry.json. Image evidence has a locator and optionally a normalized region; text evidence has an exact quotation and section locator. depicted, reported and inferred are distinct. A quotation proves where text came from; its relevance/entailment still needs scientific review. The original PNG is included unchanged; a small quoted image region does not mean the source file itself was recropped for this packet.

HISTORY
history/<method>/analysis.json is the full-paper change author output. review.json records a separate review. graph.json contains published relationships and withheld candidates. A completed run can publish zero edges; ATAC does in this pilot.
Change fields are compared_feature, before, after, retained, workflow_locations, evidence and consequence_and_limits. Locations can address States, Operations, Flows or properties within records. Evidence supporting the before/after facts is separate from evidence that the later work actually adopted the earlier contribution.
documented_reuse is a source-backed attributed contribution. mechanistic_precedent and comparison do not establish ancestry. An earlier cited contribution stays paper_only until its exact existing workflow version is independently mapped and reviewed. Matching a PMID or name alone cannot establish that connection.

HOW THE ACTUAL SCRIPTS FIT
experiments/card_methods_full_20261106/run.py schedules reviewed unique method identities, not each source depiction.
experiments/diagram_paper_pilot_20261106/run.py prepares pinned source work units and calls extract_registered_workflow.
src/genomics_discovery/illumina/extraction.py::extract_registered_workflow runs separate native stages, schema/source checks, independent review and publication.
prompts.py::build_prompt combines shared instructions, role instructions, typed representation instructions, caller context and exact schema path. The actual compiled text is archived in each example.
representation.py defines the current structured State/Operation contract; models.py supplies shared evidence/Flow models, graph export and review gates. The legacy State class in models.py alone is not the active 1.1 State contract.
graph.py::protocol_text makes the readable projection; graph.py::build_graph assembles supported method graphs without automatically connecting their histories.
Historical research and the new reviewed workflow connection are separate stages. The new connection code is not the runtime that produced these extraction examples.

RUN LOCATION AND MODEL
These native outputs were generated in Qiu Sherlock Slurm, gpt-6.1-sol, xhigh. Model access uses the configured dedicated Codex login. This packet contains no credentials and starts no jobs. Selected code copies support explanation; use the GitHub repository for runnable setup.
'''


if __name__ == '__main__':
    main()

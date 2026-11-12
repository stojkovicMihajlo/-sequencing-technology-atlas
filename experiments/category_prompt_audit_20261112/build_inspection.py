"""Render saved irCLIP comparisons without rewriting their scientific records.

This is an experiment report, not a production extraction or alignment algorithm.
The seven explicit correspondences below were inspected manually. Additional
Operations stay visible rather than being forced into that alignment.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / "deliverables/category_prompt_comparison_20261112"
ALIGNMENT = [
    ("Crosslink and prepare lysate", "o1", "crosslink_lyse", "o_cross"),
    ("Select the target protein and its RNA", "o2", "immunoprecipitate", "o_ip"),
    ("Trim RNA on beads", "o3", "digest", "o_nuclease"),
    ("Attach the RNA adapter", "o4", "end_treat_ligate", "o_ligate"),
    ("Recover RNA after protein digestion", "o5", "separate_recover", "o_recover"),
    ("Copy RNA into cDNA and recover it", "o6", "reverse_transcribe_capture", "o_rt"),
    ("Produce the amplified library", "o7", "circularize_pcr", "o_library"),
]


def read(path):
    return json.loads(path.read_text())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def embed(value):
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    paths = {
        "compact": HERE / "completed/case_10/cards/irclip/attempt_001",
        "current": HERE / "completed/case_11/cards/irclip/attempt_001",
    }
    baseline = read(HERE / "baseline_records.json")["irclip"]
    sources_path = paths["current"] / "source_registry.json"
    sources_hash = digest_bytes(sources_path.read_bytes())
    # The source bundle and card really are identical, including the archive.
    assert sources_hash == baseline["pins"]["source_registry.json"]
    assert sources_hash == digest_bytes((paths["compact"] / "source_registry.json").read_bytes())
    sources = read(sources_path)
    diagram = paths["current"] / "attempts/001_author/diagram_00.png"
    image_uri = "data:image/png;base64," + base64.b64encode(diagram.read_bytes()).decode()
    for source in sources.values():
        if source["kind"] == "image":
            source["local_asset"] = image_uri
        if source.get("content_level") == "catalogue":
            source["display_text"] = source.get("text", "")
    arms = {
        "archive": {"name": "Archived extraction · 6 November", "candidate": baseline["extraction"],
                    "review": baseline["review"], "card": baseline["card"], "outcome": baseline["baseline"]},
    }
    for key, path in paths.items():
        state = read(path.parents[2] / "state.json")
        arms[key] = {
            "name": "Fresh full-prompt run" if key == "current" else "Fresh compact-prompt run",
            "candidate": read(path / "extraction.json"), "review": read(path / "review.json"),
            "card": read(path / "card.json"), "outcome": state["cards"]["irclip"],
        }
        for filename, pin in arms[key]["outcome"]["artifact_pins"].items():
            assert digest_bytes((path / filename).read_bytes()) == pin, (key, filename)
    for key, arm in arms.items():
        assert arm["card"] == baseline["card"]
        assert digest_bytes(canonical(arm["candidate"]).encode()) == arm["review"]["candidate_sha256"], key
        arm["attributions"] = [
            {"state": s["label"], "state_id": s["id"], "property": p}
            for s in arm["candidate"]["states"] for p in s.get("properties", [])
            if p["property"] == "attribution"
        ]
        # Exact assertions/evidence; no rewriting or model summaries here.
        (OUT / (key + ".json")).write_text(json.dumps(arm, ensure_ascii=False, indent=2) + "\n")
        record = {
            "record_id": "irclip", "title": "irCLIP — " + arm["name"], "batch": "scoped_method",
            "status": arm["outcome"]["status"], "file": key + ".json", "card": arm["card"],
            "candidate": arm["candidate"], "review": arm["review"], "sources": sources,
            "attempt_directory": arm["outcome"]["attempt_directory"],
            "research_questions": arm["outcome"].get("research_questions", []),
        }
        script = (REPO / "scripts/illumina/review_site/app.js").read_text()
        style = (REPO / "scripts/illumina/review_site/style.css").read_text()
        # The saved author attachment is white-composited/enlarged, not newly cropped.
        script = script.replace("Unmodified original Illumina diagram", "Saved author diagram attachment")
        script = script.replace("Original source asset. Enlargement cannot restore detail missing from the source.",
                                "Saved author attachment. Display enlargement cannot restore detail missing from the source.")
        page = ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>irCLIP extraction inspection</title><style>" + style +
                "body{display:block}main{max-width:1500px;margin:auto;padding:24px}"
                "</style><main><a href='index.html'>← Back to comparison</a>"
                "<p>Exact saved extraction and model review. Acceptance is not an independent guarantee of scientific correctness.</p>"
                "<div id='detail'></div></main><script id='record-data' type='application/json'>" + embed(record) +
                "</script><script>" + script.replace("</script", "<\\/script") + "</script></html>")
        (OUT / (key + ".html")).write_text(page)

    first = read(paths["compact"] / "attempts/001_author/artifact.json")
    final = arms["compact"]["candidate"]
    differences = []

    def diff(a, b, pointer=""):
        if a == b:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            # Validation serializes absent optional fields as null. Keep raw
            # artifacts intact, but don't call that a scientific revision.
            for k in sorted(a.keys() | b.keys()):
                diff(a.get(k), b.get(k), pointer + "/" + str(k))
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                diff(x, y, pointer + "/" + str(i))
        else:
            differences.append({"pointer": pointer, "before": a, "after": b})

    diff(first, final)
    initial_review = read(paths["compact"] / "attempts/002_reviewer/artifact.json")
    requested = [c for c in initial_review["checks"] if c["verdict"] != "supported"]
    report = {"arms": arms, "alignment": ALIGNMENT, "revision_changes": differences,
              "revision_requests": requested, "source_registry_sha256": sources_hash}
    (OUT / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    template = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>irCLIP · prompt comparison</title>
<style>
*{box-sizing:border-box}body{margin:0;color:#172c3d;background:#f4f7fa;font:16px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
main{max-width:1520px;margin:auto;padding:30px}h1{margin-bottom:6px}h2{font-size:23px;margin-top:32px}p{max-width:1080px}
a{color:#16608c}nav,.cards{display:flex;gap:16px;flex-wrap:wrap}.cards>article{flex:1;min-width:250px;background:white;padding:18px;border:1px solid #ced8df;border-radius:9px}
button,select{font:inherit;background:white;border:1px solid #95acbc;border-radius:5px;padding:8px 12px;cursor:pointer}label{display:block;margin-bottom:12px}
table{width:100%;border-collapse:collapse;table-layout:fixed;background:white}th,td{vertical-align:top;padding:15px;text-align:left;border:1px solid #cfd9e0;overflow-wrap:anywhere}th{background:#e9f0f5}
blockquote{margin:0;white-space:pre-wrap}.muted{color:#526471;font-size:14px}.note{padding:16px;background:#fff5dc;border-left:4px solid #bf8c22}
details{background:white;border:1px solid #ced8df;padding:12px;margin:12px 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,monospace}
@media(max-width:800px){main{padding:14px}table{font-size:14px}th,td{padding:8px}}
</style><main>
<h1>irCLIP: what did the instructions preserve?</h1>
<p>Two fresh runs use the same card, diagram, paper bundle, model, effort and reviewer. Only the author instructions differ.
The archived extraction provides historical context; it is not a controlled third arm.</p>
<div class="cards" id="cards"></div>
<p class="note"><strong>Initial finding:</strong> both fresh outputs passed independent model review. The compact run is not uniformly more informative:
it explains template versus reporter clearly, but omits typed attribution properties and expands beyond the library-ending diagram.
The reviewer required it to restore the catalogue's circularization caution. These are different issues, requiring different checks.</p>
<h2>Compare the exact scientific fields</h2>
<p>These operation correspondences were inspected manually. IDs and operation counts were not used to infer equivalence.
Select a transformation and a field; text below is copied verbatim from the saved results.</p>
<label>Transformation <select id="stage"></select></label>
<label>Field <select id="field"><option value="information_effect">Information effect</option><option value="description">Description</option><option value="mechanism">Mechanism</option><option value="output_states">Output State properties</option><option value="effects">Structured effects</option></select></label>
<table><thead><tr><th>Archived · 6 November</th><th>Fresh full prompt</th><th>Fresh compact prompt</th></tr></thead><tbody><tr id="comparison"></tr></tbody></table>
<h2>Can an agent retrieve the attribution from States?</h2>
<p>Exact query: <code>State.properties where property == "attribution"</code>. An empty result means <em>unrecorded in that property</em>, not biologically absent.
The number of entries is not a correctness score. The issue is whether consequential carrier and interpretation information is stored where a query expects it.</p>
<table><thead><tr><th>Archived</th><th>Fresh full prompt</th><th>Fresh compact prompt</th></tr></thead><tbody><tr id="attribution"></tr></tbody></table>
<h2>What the compact run added</h2><div id="additional"></div>
<p>The full and archived graphs end at the DNA library. The compact graph adds sequencing and mapping, supported by paper text.
This is a scope-policy difference, not evidence that either run invented extra biochemistry. We have not yet standardized whether downstream interpretation should be separate Operations or a library/readout description.</p>
<h2>What review changed</h2><div id="revision"></div>
<h2>The earlier information-effect correction still matters</h2>
<p>The November 8 refinement required the statement to be true for each actual condition or fraction, identify its material/data carrier, and distinguish the recorded feature from later inference.
The new compact contract retains the carrier/inference distinction and a cross-field consistency check. However, “Treatment and control can have different effects” is less explicit than requiring a correct statement for each input condition.</p>
<p>In the prior Pseudo-seq comparison, the refined RT field distinguished treated cDNA endpoints from background mock-control endpoints and said a stop was not a pseudouridine call until mapping and paired comparison.
That is required scientific meaning, not optional elaboration. The compact Pseudo-seq run was not complete when this irCLIP comparison was assembled, so this test does not yet show that the condition requirement survived.</p>
<p class="muted">Preserved original records, exact citations and complete graphs are accessible from the three inspection links above. Source bundle SHA256: <span id="source-hash"></span>.
This is an engineering assessment of one completed matched pair, not a measured general error rate. No scientific extraction has been rewritten for this page.</p>
</main><script id="comparison-data" type="application/json">__DATA__</script><script>
const D=JSON.parse(document.getElementById('comparison-data').textContent), keys=['archive','current','compact'];
const el=(tag,text)=>{let e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
for(const k of keys){const a=D.arms[k],c=a.candidate,n=el('article');n.append(el('h3',a.name),el('p',`${c.operations.length} Operations · ${c.states.length} States · ${c.flows.length} Flows`),el('p',`Review: ${a.outcome.status} · author revisions: ${a.outcome.revision}`));let link=el('a','Open the complete graph and evidence →');link.href=k+'.html';n.append(link);document.getElementById('cards').append(n);}
for(const [i,row] of D.alignment.entries()){let o=el('option',row[0]);o.value=i;document.getElementById('stage').append(o);}
function redraw(){const row=D.alignment[Number(document.getElementById('stage').value)],f=document.getElementById('field').value,tr=document.getElementById('comparison');tr.replaceChildren();keys.forEach((k,i)=>{const c=D.arms[k].candidate,op=c.operations.find(o=>o.id===row[i+1]),td=el('td');td.append(el('b',op.label),el('p',op.id));if(f==='output_states'){for(const id of op.outputs){const s=c.states.find(s=>s.id===id);td.append(el('h4',s.label));for(const p of s.properties||[]){td.append(el('p',p.property+' · '+p.subject),el('pre',JSON.stringify(Object.fromEntries(Object.entries(p).filter(([k])=>k!=='evidence')),null,2)));}}}else if(f==='effects'){td.append(el('pre',JSON.stringify(op.effects.map(e=>Object.fromEntries(Object.entries(e).filter(([k])=>k!=='evidence'))),null,2)));}else td.append(el('blockquote',op[f]??'Not recorded'));tr.append(td);});}
document.getElementById('stage').value=5;document.getElementById('stage').onchange=redraw;document.getElementById('field').onchange=redraw;redraw();
for(const k of keys){const td=el('td'),items=D.arms[k].attributions;td.append(el('b',`${items.length} entries`));for(const x of items){td.append(el('h4',x.state),el('pre',JSON.stringify(x.property.value,null,2)));}if(!items.length)td.append(el('p','No typed attribution properties. Contact/endpoints remain described in Operation prose and mapped-data properties; this query cannot retrieve them at the intermediate material boundaries.'));document.getElementById('attribution').append(td);}
for(const op of D.arms.compact.candidate.operations.filter(o=>!D.alignment.some(r=>r[3]===o.id))){let d=el('details');d.append(el('summary',op.label),el('p',op.description),el('p',op.mechanism),el('p',op.information_effect));document.getElementById('additional').append(d);}
for(const r of D.revision_requests){let d=el('details');d.append(el('summary',r.target+' · '+r.action),el('p',r.rationale));document.getElementById('revision').append(d);}
let changes=el('details');changes.append(el('summary','First-author → final-author value changes (absent optional fields and null treated as equivalent)'));for(const x of D.revision_changes){changes.append(el('h4',x.pointer),el('pre',JSON.stringify({before:x.before,after:x.after},null,2)));}document.getElementById('revision').append(changes);
document.getElementById('source-hash').textContent=D.source_registry_sha256;
</script></html>"""
    (OUT / "index.html").write_text(template.replace("__DATA__", embed(report)))
    receipt = {"scientific_records_modified": False, "source_bundle_identical": True,
               "source_registry_sha256": sources_hash, "alignment_kind": "manually inspected correspondences",
               "review_hash_bindings_checked": True,
               "files": {p.name: digest_bytes(p.read_bytes()) for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "receipt.json"}}
    (OUT / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(OUT / "index.html")
    print("Verified source equality, result pins and reviewer/candidate binding for all three records.")
    print("Revision leaf differences:", [x["pointer"] for x in differences])


if __name__ == "__main__":
    main()

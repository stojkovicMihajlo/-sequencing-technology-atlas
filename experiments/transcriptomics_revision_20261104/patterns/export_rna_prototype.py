"""Make an inspectable RNA prototype from exact archived, previously reviewed records.

This export makes no model calls and no new scientific admission. It reuses the
Illumina review renderer; history/comparison claims are supplied separately.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from genomics_discovery.illumina.models import digest, parse_extraction, parse_review

ARCHIVE = ROOT / "provenance/experiments/illumina_extraction_20261102/audit_01/records.jsonl.gz"
RECORDS = (
    "smart-seq-nanocage-cagescan", "smart-seq2", "cel-seq", "drop-seq",
    "cage-seq", "cp-rna-seq", "rasl-seq", "clickseq",
)
ALIASES = {"smart-seq": "smart-seq-nanocage-cagescan"}
HISTORY = {"method_development", "component_adoption", "mechanistic_precedent"}
COMPARISON = {"shared_mechanism", "shared_purpose", "comparison"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_records(archive):
    selected = {}
    with gzip.open(archive, "rt") as stream:
        for line_number, line in enumerate(stream, 1):
            row = json.loads(line)
            if row["record_id"] not in RECORDS:
                continue
            if row["record_id"] in selected:
                raise ValueError("Duplicate exact source occurrence: " + row["record_id"])
            if (row["status"] != "supported" or row.get("reviewed_outcome") != "supported"
                    or row.get("validation_errors") or row.get("review_validation_errors")):
                raise ValueError("Selected record is not previously admitted: " + row["record_id"])
            candidate, review = parse_extraction(row["candidate"]), parse_review(row["review"])
            if review.candidate_sha256 != digest(candidate):
                raise ValueError("Archived review does not bind this candidate")
            row["prototype_provenance"] = {
                "archive_line": line_number, "archived_row_sha256": digest(row),
                "candidate_sha256": digest(candidate), "review_sha256": digest(review),
                "qualification": "Previously model-reviewed November 2026 archive; not a new extraction or fresh source review.",
            }
            selected[row["record_id"]] = row
    missing = set(RECORDS) - selected.keys()
    if missing:
        raise ValueError("Missing selected archive records: " + repr(sorted(missing)))
    return [selected[key] for key in RECORDS]


def prepare_relations(raw, records):
    """Check graph references; scientific claims and their status remain the author's."""
    by_id = {r["record_id"]: r for r in records}
    relations = []
    for original in raw.get("relations", []):
        relation = dict(original)
        if relation["kind"] not in HISTORY | COMPARISON:
            raise ValueError("Unknown relation kind: " + relation["kind"])
        if not relation.get("evidence") or not relation.get("status"):
            raise ValueError("Every relation requires evidence and an explicit review status")
        for side in ("source", "target"):
            endpoint = relation[side]
            resolved = ALIASES.get(endpoint, endpoint)
            relation[side + "_record_id"] = resolved if resolved in by_id else None
            if endpoint in ALIASES:
                relation[side + "_scope_note"] = "Smart-Seq branch only; source card also contains distinct nanoCAGE/CAGEscan records."
            ids = relation.get(side + "_operation_ids", [])
            if ids and resolved not in by_id:
                raise ValueError("Unextracted endpoint cannot claim operation IDs")
            available = {o["id"] for o in by_id.get(resolved, {}).get("candidate", {}).get("operations", [])}
            if set(ids) - available:
                raise ValueError("Relation references a missing Operation")
        relation["layer"] = "history" if relation["kind"] in HISTORY else "comparison"
        relations.append(relation)
    groups = raw.get("comparison_groups", [])
    for group in groups:
        if group.get("kind") not in COMPARISON or not group.get("status"):
            raise ValueError("Comparison group requires kind and status")
        for member in group["members"]:
            record_id = ALIASES.get(member["record_id"], member["record_id"])
            if record_id not in by_id:
                raise ValueError("Comparison group references an unexported source")
            available = {o["id"] for o in by_id[record_id]["candidate"]["operations"]}
            if not member.get("operation_ids") or set(member["operation_ids"]) - available:
                raise ValueError("Comparison membership requires exact existing Operations")
    ids = [r["id"] for r in relations] + [g["id"] for g in groups]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate relation/group IDs")
    return relations, groups


def load_pilot(directory):
    """Expose a separately published native pilot without replacing old occurrences."""
    if not directory.exists():
        return []
    names = ("extraction.json", "review.json", "graph.json", "card.json", "source_registry.json")
    files = {name: json.loads((directory / name).read_text()) for name in names}
    candidate, review = parse_extraction(files["extraction.json"]), parse_review(files["review.json"])
    graph, sources = files["graph.json"], files["source_registry.json"]
    if (graph["status"] != "supported" or review.candidate_sha256 != digest(candidate)
            or graph["candidate_sha256"] != digest(candidate) or graph["review_sha256"] != digest(review)
            or graph["source_registry_sha256"] != digest(sources)):
        raise ValueError("Native pilot publication and source hashes do not agree")
    return [{"record_id": candidate.record_id, "title": candidate.title, "status": "supported",
        "batch": "New native pilot", "candidate": files["extraction.json"], "review": files["review.json"],
        "card": files["card.json"], "sources": {key: {k: v for k, v in value.items() if k != "text"}
                                              for key, value in sources.items()},
        "prototype_provenance": {"kind": "native_pilot", "date": "2026-11-04",
            "candidate_sha256": digest(candidate), "review_sha256": digest(review),
            "source_registry_sha256": digest(sources), "directory": str(directory.relative_to(ROOT)),
            "file_sha256": {name: sha(directory / name) for name in names},
            "qualification": "New native extraction and fresh model review; separate from archived source occurrences. No historical or comparison membership is inferred for this new version."}}]


EXTRA_CSS = """
#landscape{margin:22px 36px;background:#151d27;border:1px solid #344354;border-radius:10px;padding:20px}
#landscape svg{max-width:100%;height:auto}.network-node{cursor:pointer}.network-node:hover rect{stroke:#fff}
.chemical-panel{max-height:420px;max-width:100%;height:auto;background:white;padding:6px;border-radius:5px}
.network-node text{fill:#dce7f3;font-family:Arial,Helvetica,sans-serif}.history-edge{cursor:pointer}
.history-edge:hover{stroke-width:6}.tabs{display:flex;gap:10px;margin:12px 0}.tabs button{background:#202d3e;border:1px solid #50647a;border-radius:6px;padding:8px 14px}
.network-inspector{border-left:3px solid #ad91e6;padding:8px 20px;margin-top:14px}
.group{border:1px solid #476877;border-radius:8px;padding:14px;margin:10px 0;background:#17252c}
.members{display:flex;flex-wrap:wrap;gap:8px}.members button{background:#183e46;border:1px solid #53a5ac;border-radius:6px;padding:8px 13px}
.legend{display:flex;gap:18px;flex-wrap:wrap;color:#acc0d0;font-size:13px}.legend b{color:#dac3ff}
"""


HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RNA technology atlas · inspectable prototype</title><style>__CSS__</style></head><body>
<header><div><span class="eyebrow">QIU LAB · RNA TECHNOLOGY ATLAS</span><h1>Workflows and documented design changes</h1>
<p>Eight selected source depictions from the November 2026 archive, plus a separately displayed new Drop-seq extraction. Original workflow versions and reviews are retained. Separate relation claims explain connections; the original graphs are not rewritten.</p></div></header>
<section id="landscape"><h2>Between methods</h2><p class="muted">Click a source card to inspect its workflow. Click a relation to see what changed and its supporting source. Absence of a link means no link was supplied for this prototype.</p>
<div class="tabs"><button id="history-tab">Documented history</button><button id="groups-tab">Shared-operation comparisons</button></div>
<div class="legend"><span><b>→</b> History: earlier → later, as qualified by the claim</span><span>Comparison membership: no ancestry or interchangeability implied</span><span>Dashed card: workflow not extracted here</span></div>
<div id="network"></div><div id="relations-detail" class="network-inspector"></div><div id="groups" hidden></div><div id="drawing-review"></div></section>
<main><aside><h3>Inspect an archived workflow</h3><nav id="records"></nav><p class="muted">Rounded State = material/data at a boundary.<br>Rectangular Operation = action.<br>Flow = the recorded input, output or ordering connection.</p>
<p class="muted">These are archived version 1.0 records, not results from the current version 1.1 pilot. Model-reviewed does not mean experimentally validated.</p><a id="download-all" download="atlas.json">Download this complete prototype</a></aside>
<article id="detail" tabindex="-1" aria-live="polite"></article></main>
<footer>No model calls or remote services. Reuses the Illumina extraction-review renderer. Provenance, exact record versions and source links are included in the download.</footer>
<script>__DATA__</script><script>__RENDERER__</script><script>__APP__</script></body></html>"""


APP = r"""
const data=ATLAS, rows=new Map([...data.records,...(data.pilot_records||[])].map(r=>[r.record_id,r]));
const baseInspect=inspect;
inspect=function(parent,item,kind,record){baseInspect(parent,item,kind,record);
 const factsAnchor=parent.children[2];
 const sections=kind==='State'?[['Components',item.components],['Typed properties',item.properties]]:kind==='Operation'?[['Input roles',item.input_roles],['Recorded effects',item.effects]]:[];
 if(kind==='State'&&item.source_label)fields(parent,[['Literal source label',item.source_label]]);
 if(kind==='Operation'&&item.resolution)fields(parent,[['Resolution',item.resolution],['Output grouping',item.output_grouping]]);
 for(const[title,items]of sections){if(!items?.length)continue;const panel=section(parent,title);panel.open=true;
   for(const entry of items){const box=el('div',null,'evidence');fields(box,Object.entries(entry).filter(([key])=>key!=='evidence').map(([key,value])=>[key,typeof value==='object'?JSON.stringify(value):value]));const support=section(box,'Supporting source');evidence(support,entry.evidence,record);panel.append(box);}
   if(factsAnchor)parent.insertBefore(panel,factsAnchor);
 }
};
const blob=value=>URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
$('download-all').href=blob(data);
function show(id,reveal=false){
  const r=rows.get(id);if(!r)return;
  selected=id;r.file=r.file||blob(r);renderRecord(r);
  const native=r.prototype_provenance.kind==='native_pilot';
  const note=el('p',(native?'New native extraction + fresh model review · ':'Archived model review · ')+r.candidate.format_version+' / '+r.review.format_version+' · '+r.prototype_provenance.candidate_sha256.slice(0,12),'muted');
  $('detail').prepend(note);
  const pattern=(data.source_patterns?.methods||[]).find(p=>p.method_id===id);
  if(pattern){const d=section($('detail'),'New source inspection: what this method changes');d.open=true;d.append(el('p',pattern.measurement_role),el('p',pattern.distinguishing_change));
    for(const t of pattern.transitions){const b=el('div',null,'evidence');fields(b,[['Before',t.before],['Change',t.change],['After',t.after]]);for(const e of t.evidence||[]){const src=(data.source_patterns.sources||[]).find(s=>s.id===e.source)||{};b.append(link(e.locator,src.url));}d.append(b);}
    for(const q of pattern.source_qualifications||[])d.append(el('p',q,'warning'));d.append(el('p',pattern.reading_extent,'muted'));raw(d,'Exact new source-pattern review',pattern);
    const illustration=data.illustrations?.[id==='clickseq'?'chemical_legend_top':id==='cp-rna-seq'?'chemical_legend_terminal':''];
    if(illustration){const image=el('img',null,'chemical-panel');image.src=illustration.data_url;image.alt=illustration.caption;d.append(image,el('p',illustration.caption,'muted'));raw(d,'Crop source and hash',Object.fromEntries(Object.entries(illustration).filter(([k])=>k!=='data_url')));}
  }
  raw($('detail'),native?'Exact native pilot provenance':'Exact archived provenance',r.prototype_provenance);
  for(const b of $('records').children)b.classList.toggle('active',b.dataset.id===id);
  if(reveal)$('detail').scrollIntoView({behavior:'smooth',block:'start'});
}
for(const r of data.records){const b=el('button',r.title);b.dataset.id=r.record_id;b.append(el('small',r.candidate.operations.length+' Operations · '+r.candidate.states.length+' States'));b.onclick=()=>show(r.record_id,true);$('records').append(b);}
if(data.pilot_records?.length)$('records').append(el('h3','Current native pilot'));
for(const r of data.pilot_records||[]){const b=el('button','New extraction · '+r.title+' (1.1)');b.dataset.id=r.record_id;b.append(el('small','Separate author + fresh reviewer · 4 November'));b.onclick=()=>show(r.record_id,true);$('records').append(b);}
function claimEvidence(parent,items){for(const e of items||[]){const box=el('div',null,'evidence');box.append(link(e.title||e.url||e.source_id||'Source',e.url),el('p',e.locator||'','muted'));if(e.quote)box.append(el('blockquote',e.quote));if(e.rationale)box.append(el('p',e.rationale));parent.append(box);}}
function showRelation(r){const p=$('relations-detail');p.replaceChildren(el('span',r.kind,'badge'),el('span',r.status,'badge'),el('h3',r.summary||r.id));
 fields(p,[['From / to',r.source+' → '+r.target],['Publication years',r.source_year&&r.target_year?r.source_year+' → '+r.target_year:null],['Scoped method',r.source_method],['Design change',r.design_change],['Earlier limitation',r.limitation_addressed],['Capability gained',r.capability_gained||r.reported_gain],['Trade-offs',r.tradeoffs],['Limitations',r.limitations],['Source scope',r.source_scope_note],['Target scope',r.target_scope_note]]);
 for(const c of r.changes||[]){const b=el('div',null,'evidence');fields(b,[['Change type',c.change_type],['Before',c.before],['After',c.after],['Exact affected Operations',c.affected_operation_ids],['Source location',c.source_locator]]);p.append(b);}
 for(const side of ['source','target']){const rid=r[side+'_record_id'];if(!rid){p.append(el('p',r[side]+': no workflow extracted in this prototype.','muted'));continue;}const b=el('button','Inspect '+r[side]+' workflow','operation');b.onclick=()=>show(rid,true);p.append(b);const rec=rows.get(rid);const names=(r[side+'_operation_ids']||[]).map(id=>rec.candidate.operations.find(o=>o.id===id)?.label+' ['+id+']');if(names.length)p.append(el('p',names.join('; '),'muted'));}
 claimEvidence(p,r.evidence);raw(p,'Exact relation claim',r);if(data.history_review)raw(p,'Independent check of these two history examples',data.history_review);
}
function overview(){const NS='http://www.w3.org/2000/svg',s=(tag,attrs,text)=>{const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!=null)n.textContent=text;return n;};
 const ns=data.records.map(r=>{const scoped=data.relations.find(e=>e.source_record_id===r.record_id&&e.source_method);return {id:r.record_id,label:scoped&&r.candidate.method_names.length>1?scoped.source_method+' (scoped branch)':r.title,record:r.record_id};}),known=new Set(ns.map(n=>n.id));
 for(const r of data.relations)for(const side of ['source','target']){const id=r[side+'_record_id']||r[side];if(!known.has(id)){known.add(id);ns.push({id,label:r[side+'_method']||r[side],record:null});}}
 const preferred=['smart-seq-nanocage-cagescan','smart-seq2','drop-seq','cel-seq','cel-seq2','cage-seq','cp-rna-seq','rasl-seq','clickseq'];ns.sort((a,b)=>preferred.indexOf(a.id)-preferred.indexOf(b.id));
 const pos=new Map(ns.map((n,i)=>[n.id,{x:25+(i%3)*300,y:25+Math.floor(i/3)*126}]));const svg=s('svg',{viewBox:`0 0 920 ${Math.ceil(ns.length/3)*126+25}`,role:'img','aria-label':'Method history prototype'});
 const defs=s('defs',{}),m=s('marker',{id:'history-arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto'});m.append(s('path',{d:'M0,0 L10,5 L0,10 z',fill:'#b69be7'}));defs.append(m);svg.append(defs);
 for(const r of data.relations.filter(r=>r.layer==='history')){const a=pos.get(r.source_record_id||r.source),b=pos.get(r.target_record_id||r.target);if(!a||!b)continue;const path=s('path',{d:`M${a.x+248},${a.y+38} C${a.x+273},${a.y+38} ${b.x-25},${b.y+38} ${b.x},${b.y+38}`,stroke:'#b69be7','stroke-width':3,fill:'none','marker-end':'url(#history-arrow)',class:'history-edge',tabindex:0,role:'button','aria-label':r.summary||r.id});path.append(s('title',{},r.kind+': '+(r.summary||r.id)));path.onclick=()=>showRelation(r);path.onkeydown=e=>{if(e.key==='Enter')path.onclick();};svg.append(path);}
 for(const n of ns){const p=pos.get(n.id),g=s('g',{class:'network-node',tabindex:0,role:'button','aria-label':n.label});g.append(s('rect',{x:p.x,y:p.y,width:248,height:78,rx:10,fill:'#202e40',stroke:'#627d99','stroke-dasharray':n.record?'':'5 4'}));
 const words=n.label.split('/');g.append(s('text',{x:p.x+14,y:p.y+29,'font-size':15},words[0]));if(words.length>1)g.append(s('text',{x:p.x+14,y:p.y+46,'font-size':12},words.slice(1).join(' / ')));g.append(s('text',{x:p.x+14,y:p.y+64,'font-size':10},n.record?'ARCHIVED SOURCE WORKFLOW':'LITERATURE NODE · NO WORKFLOW'));g.onclick=()=>n.record?show(n.record,true):showRelation(data.relations.find(r=>r.source===n.id||r.target===n.id));g.onkeydown=e=>{if(e.key==='Enter')g.onclick();};svg.append(g);}
 $('network').append(svg);const first=data.relations.find(r=>r.layer==='history');if(first)showRelation(first);else $('relations-detail').append(el('p','No reviewed history claims supplied yet.'));
}
function showGroups(){const p=$('groups');for(const g of data.comparison_groups){const box=el('section',null,'group');box.append(el('span',g.kind,'badge'),el('span',g.status,'badge'),el('h3',g.label||g.id),el('p',g.summary||''));const members=el('div',null,'members');
 for(const m of g.members){const rid=data.aliases[m.record_id]||m.record_id,r=rows.get(rid),b=el('button',r.title+' · '+m.operation_ids.length+' Operation(s)');b.onclick=()=>{show(rid,true);const op=r.candidate.operations.find(o=>o.id===m.operation_ids[0]);if(op)inspect($('detail').querySelector('.inspector'),op,'Operation',r);};members.append(b);}
 box.append(members);for(const m of g.members)if(m.explanation)box.append(el('p',m.record_id+': '+m.explanation,'muted'));if(g.limitations)box.append(el('p',g.limitations,'warning'));claimEvidence(box,g.evidence);raw(box,'Exact comparison definition and memberships',g);p.append(box);}
 for(const r of data.relations.filter(r=>r.layer==='comparison')){const b=el('button',r.summary||r.id,'operation');b.onclick=()=>showRelation(r);p.append(b);}if(!p.children.length)p.append(el('p','No reviewed comparison memberships supplied.'));
}
$('history-tab').onclick=()=>{$('network').hidden=false;$('groups').hidden=true;$('relations-detail').hidden=false;};
$('groups-tab').onclick=()=>{$('network').hidden=true;$('groups').hidden=false;$('relations-detail').hidden=false;};
overview();showGroups();show(data.pilot_records?.[0]?.record_id||'drop-seq');
const chem=section($('drawing-review'),'What the molecular drawings establish');chem.append(el('p',data.chemical_review?.review||''));
for(const e of data.chemical_review?.examples||[]){const d=section(chem,e.example);fields(d,[['Drawing role',e.role],['Observed',e.observed],['Safe to encode',e.safe_to_encode],['Not established',e.not_established]]);}
for(const s of Object.values(data.chemical_review?.sources||{}))chem.append(link(s.url,s.url));raw(chem,'Pinned source review and its limits',data.chemical_review);
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--relations", type=Path, required=True)
    parser.add_argument("--groups", type=Path, default=HERE / "rna_comparison_groups.json")
    parser.add_argument("--pilot", type=Path, default=ROOT / "provenance/experiments/transcriptomics_revision_20261104/pilot_01/drop_seq")
    parser.add_argument("--output", type=Path, default=HERE / "rna_prototype")
    args = parser.parse_args(argv)
    records = load_records(args.archive)
    raw_relations = json.loads(args.relations.read_text())
    if args.groups.exists():
        raw_relations["comparison_groups"] = raw_relations.get("comparison_groups", []) + json.loads(args.groups.read_text())["groups"]
    relations, groups = prepare_relations(raw_relations, records)
    renderer_path = ROOT / "scripts/illumina/review_site/app.js"
    renderer = renderer_path.read_text().split("async function openRecord(", 1)[0]
    if "function renderRecord(" not in renderer:
        raise ValueError("Review renderer interface changed")
    payload = {
        "format_version": "rna_atlas_inspection_prototype/1.0",
        "purpose": "Inspect archived workflows and separately authored relation claims; not fresh scientific admission.",
        "provenance": {"archive": str(args.archive.relative_to(ROOT)), "archive_sha256": sha(args.archive),
                       "relation_file": str(args.relations), "relation_file_sha256": sha(args.relations),
                       "renderer": str(renderer_path.relative_to(ROOT)), "renderer_sha256": sha(renderer_path)},
        "aliases": ALIASES, "records": records, "relations": relations, "comparison_groups": groups,
        "pilot_records": load_pilot(args.pilot),
    }
    for key, filename in (("source_patterns", "rna_source_patterns.json"), ("chemical_review", "chemical_structure_review.json"), ("history_review", "history_review.json")):
        path = HERE / filename
        payload[key] = json.loads(path.read_text()) if path.exists() else None
        if path.exists():
            payload["provenance"][key] = {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
    if args.groups.exists():
        payload["provenance"]["comparison_groups"] = {"path": str(args.groups), "sha256": sha(args.groups)}
    args.output.mkdir(parents=True, exist_ok=True)
    payload["illustrations"] = {}
    for name, caption in (
        ("chemical_legend_top", "RNA poster chemical legend: nucleotide/reagent structures, not a complete ClickSeq reaction or proof of use in every neighboring method."),
        ("chemical_legend_terminal", "RNA poster terminal-chemistry panel: contextual transformations and alternatives, not a command to apply all treatments in cP-RNA-seq."),
    ):
        original = Path("/private/tmp/atlas_rna_patterns_20261104") / (name + ".png")
        destination = args.output / "assets" / original.name
        if original.exists():
            destination.parent.mkdir(exist_ok=True)
            shutil.copy2(original, destination)
        if destination.exists():
            poster = next(s for s in payload["source_patterns"]["sources"] if s["id"] == "rna_poster")
            region = next(c for c in poster["crops"] if c["name"] == name)
            payload["illustrations"][name] = {"file": "assets/" + destination.name, "caption": caption,
                "sha256": sha(destination), "source_pdf_sha256": poster["sha256"],
                "source_url": poster["url"], "crop": region,
                "data_url": "data:image/png;base64," + base64.b64encode(destination.read_bytes()).decode()}
    (args.output / "atlas.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    css = (ROOT / "scripts/illumina/review_site/style.css").read_text() + EXTRA_CSS
    encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    html = HTML.replace("__CSS__", css).replace("__DATA__", "const ATLAS=" + encoded + ";")
    html = html.replace("__RENDERER__", renderer).replace("__APP__", APP)
    (args.output / "index.html").write_text(html)
    print(json.dumps({"output": str(args.output), "source_depictions": len(records),
                      "history_claims": sum(r["layer"] == "history" for r in relations),
                      "comparison_groups": len(groups), "separate_native_pilots": len(payload["pilot_records"]), "native_calls": 0}))


if __name__ == "__main__":
    main()

"""Follow reviewed history connections into the exact saved workflow records.

This is a deterministic inspection export. It does not infer new ancestry,
convert paper-only contributions into workflows, or change extracted records.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text())


def pointer(record, field):
    if field is None:
        return record
    if not field.startswith('/'):
        raise ValueError('Invalid record field pointer')
    value = record
    for token in field[1:].split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def follow(index, edge_id, extraction_files):
    """Resolve one directed edge from a previously reviewed, pinned index.

    extraction_files maps recorded SHA256 values to available file locations.
    The registry relocates files; it never changes scientific identity or bytes.
    """
    edges = [edge for edge in index['edges'] if edge['id'] == edge_id]
    if len(edges) != 1:
        raise ValueError('Select one exact history edge ID')
    edge = edges[0]
    if not edge.get('source_citation') or edge['review']['decision'] != 'accept':
        raise ValueError('This edge has no reviewed connection between two extracted workflows')
    required = sorted(change['id'] for change in edge['claim']['change_records'])
    findings = edge['mapping_review']['findings']
    if (not required or sorted(row['change_id'] for row in findings) != required
            or not all(row['decision'] == 'accept' for row in findings)):
        raise ValueError('Unaccepted workflow mapping')
    result = {'edge': edge, 'earlier': {}, 'later': {}, 'changes': []}
    records = {}
    for side, endpoint in [('earlier', 'source'), ('later', 'target')]:
        node = index['nodes'][edge[endpoint]]
        if node['kind'] != 'bound_workflow':
            raise ValueError('History endpoint is not an extracted workflow')
        path = Path(extraction_files[node['extraction_sha256']])
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != node['extraction_sha256']:
            raise ValueError('Workflow bytes differ from the reviewed history endpoint')
        document = json.loads(raw)
        if document['record_id'] != node['record_id']:
            raise ValueError('Workflow method identity differs from history endpoint')
        result[side] = {'node': node, 'workflow': document,
                        'extraction_sha256': node['extraction_sha256']}
        records[side] = {(kind, row['id']): row for kind, key in
                         [('state', 'states'), ('operation', 'operations'), ('flow', 'flows')]
                         for row in document[key]}
    for change in edge['claim']['change_records']:
        if change['workflow_locations']['earlier_scope'] != 'bound_workflow':
            raise ValueError('Earlier contribution has not been located in a workflow')
        row = {'report': change, 'earlier': [], 'later': []}
        for side in ['earlier', 'later']:
            for anchor in change['workflow_locations'][side]:
                record = records[side][(anchor['kind'], anchor['record_id'])]
                row[side].append({'anchor': anchor, 'record': record,
                                  'value': pointer(record, anchor['field'])})
        result['changes'].append(row)
    return result


def build(project, index, output):
    output.mkdir(parents=True, exist_ok=False)
    roots = [project / 'experiments/history_changes_20261106/completed_batch_51341499/batch_10_01/connections/bindings',
             project / 'experiments/history_changes_20261106/completed_mapping_51339109/bindings']
    files, bindings = {}, {}
    for root in roots:
        for graph in root.glob('*/graph.json'):
            bindings[sha256(graph.read_bytes()).hexdigest()] = graph.parent
            for side in ['left', 'right']:
                path = graph.parent / 'inputs' / f'{side}_extraction.json'
                files[sha256(path.read_bytes()).hexdigest()] = path
    pairs = []
    for edge in index['edges']:
        if not edge.get('source_citation'):
            continue
        pair = follow(index, edge['id'], files)
        folder = bindings[edge['artifact']['sha256']]
        registry_path = folder / 'inputs/sources.json'
        assert sha256(registry_path.read_bytes()).hexdigest() == edge['artifact']['source_registry']['sha256']
        pair['sources'] = {sid: {k:value.get(k) for k in ['title', 'url', 'kind', 'content_level']}
                           for sid, value in load(registry_path).items()}
        pair['key'] = edge['claim_id']
        for side in ['earlier', 'later']:
            prefix = 'left:' if side == 'earlier' else 'right:'
            pair[side]['sources'] = {sid[len(prefix):]: value for sid, value in pair['sources'].items()
                                     if sid.startswith(prefix)}
            path = output / f"{pair['key']}_{side}_workflow.json"
            path.write_bytes(Path(files[pair[side]['extraction_sha256']]).read_bytes())
            pair[side]['file'] = path.name
        (output / f"{pair['key']}_connection.json").write_text(json.dumps(pair, ensure_ascii=False, indent=2)+'\n')
        pairs.append(pair)
    pairs.sort(key=lambda pair: pair['earlier']['node']['record_id'])
    (output / 'data.js').write_text('window.WORKFLOW_CONNECTIONS='+json.dumps(pairs,ensure_ascii=False).replace('</','<\\/')+';\n')
    # Reuse the existing workflow renderer, including its actual Flow-based DAG
    # layout, instead of introducing a second biological graph representation.
    source = (project / 'scripts/illumina/review_site/app.js').read_text()
    renderer = source[source.index('function drawGraph('):source.index('function renderRecord(')]
    (output / 'app.js').write_text(SCRIPT_PREFIX + renderer + SCRIPT_SUFFIX)
    (output / 'index.html').write_text(HTML)
    return {'connections': len(pairs), 'source_renderer_sha256': sha256(source.encode()).hexdigest(),
            'workflow_hashes': sorted(files), 'scientific_records_changed': False}


HTML = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Follow an evolutionary connection</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f6f8;color:#18303e;font:16px/1.55 Arial,Helvetica,sans-serif}main{max-width:1600px;padding:24px;margin:auto}h1{font-size:29px}h2{font-size:23px}h3{font-size:18px}button,a{font:inherit}button{border:1px solid #9db0bd;border-radius:6px;padding:9px 14px;background:white;color:#194c64;cursor:pointer}button.active{background:#164b63;color:white}button:hover{border-color:#b8791b}nav{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:20px 0}.edge-button{background:#fff3d8;border-color:#c28a37}.columns,.before-after{display:grid;grid-template-columns:1fr 1fr;gap:20px}.workflow,.report{background:white;border:1px solid #c8d6e0;border-radius:12px;padding:20px;min-width:0}.report{margin-top:24px}.graph{overflow:auto;border:1px solid #d0dde6;border-radius:8px;margin:12px 0}.graph text{fill:#fff}.node,.flow{cursor:pointer}.selected-record rect{stroke:#d08618;stroke-width:4}.node:focus{outline:2px solid #c08a27}.inspector{border-top:2px solid #d5e0e7;margin-top:18px;padding-top:12px;scroll-margin-top:20px}.fields{display:grid;grid-template-columns:130px 1fr;gap:10px 14px;margin:14px 0}.fields span{overflow-wrap:anywhere}.fields b{color:#28546c}.anchor{display:block;text-align:left;width:100%;margin:8px 0;border-color:#c08a27}.property{border-left:4px solid #c38a29;background:#fff8e9;padding:10px 14px;margin:14px 0}.muted{color:#526a79}.before-after>div{background:#f4f7fa;padding:10px 18px;border-radius:8px}details{border-top:1px solid #d3dfe7;padding-top:12px;margin-top:14px}summary{cursor:pointer;font-weight:bold}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;background:#f3f6f8;padding:12px}blockquote{border-left:3px solid #9ab3c3;margin:10px 0;padding-left:14px}.evidence{margin:15px 0}a{color:#145a7b}.scope{background:#fff6e2;border-left:4px solid #c28a37;padding:12px 16px}.badge{display:inline-block;background:#ebf3f8;padding:3px 8px;border-radius:4px}.controls{display:flex;gap:8px}.section{margin-top:14px}@media(max-width:1000px){.columns,.before-after{grid-template-columns:1fr}}
</style><main><p><a href="../index.html">All history examples</a></p><h1>Follow an edge between extracted workflows</h1>
<p>Select a connection, inspect the earlier workflow, read its edge report, then open the recorded location in the later workflow. Orange outlines mark the records named by this edge; they do not mean every property of those records was inherited.</p>
<p class="muted">Arrows inside a workflow are its recorded material/data Flows. The directed historical connection between methods identifies a credited feature; it does not mean physical material passes from one assay into the other.</p>
<nav id="pairs"></nav><nav id="traverse"></nav><div class="columns"><section class="workflow" id="earlier"></section><section class="workflow" id="later"></section></div><section class="report" id="report"></section>
<p class="muted">These are the two completed exact workflow connections in this pilot. The Tn5 → ATAC paper contribution is not promoted here: its earlier PCR-based workflow has no reviewed mapping. The older PCR-free Tn5 extraction is a different implementation.</p>
</main><script src="data.js"></script><script src="app.js"></script></html>"""


SCRIPT_PREFIX = """'use strict';
const $=id=>document.getElementById(id);
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
function section(parent,title){const n=el('details',null,'section');n.append(el('summary',title));parent.append(n);return n;}
function raw(parent,title,value){section(parent,title).append(el('pre',JSON.stringify(value,null,2)));}
function fields(parent,rows){const n=el('div',null,'fields');for(const[k,v]of rows){if(v==null)continue;n.append(el('b',k),el('span',typeof v==='string'?v:JSON.stringify(v)));}parent.append(n);}
function button(label,action,cls){const b=el('button',label,cls);b.type='button';b.onclick=action;return b;}
function evidence(parent,items,pair){for(const e of items||[]){const box=el('div',null,'evidence'),s=pair.sources[e.source_id]||{};const a=el('a',s.title||e.source_id);if(/^https?:\\/\\//.test(s.url||'')){a.href=s.url;a.target='_blank';a.rel='noopener';}box.append(a);if(e.locator)box.append(el('p',e.locator,'muted'));if(e.quote)box.append(el('blockquote',e.quote));if(e.rationale)box.append(el('p',e.rationale));parent.append(box);}}
let current=null;const panels={};
function anchors(side){return current.changes.flatMap(c=>c[side]);}
function inspect(parent,item,kind,record){
 const side=record.side,graph=current[side].workflow,selected=anchors(side).filter(a=>a.anchor.kind===kind.toLowerCase()&&a.anchor.record_id===item.id);
 parent.replaceChildren(el('span',kind,'badge'),el('h3',item.label||item.id));
 for(const a of selected){const n=el('div',null,'property');n.append(el('b',a.anchor.field?'This edge points to '+a.anchor.field:'This edge locates its feature within this record.'));n.append(el('p','The edge report determines the adopted feature; the whole record is not automatically inherited.'));if(a.anchor.field)n.append(el('pre',typeof a.value==='string'?a.value:JSON.stringify(a.value,null,2)));parent.append(n);}
 if(kind==='Operation'){
  fields(parent,[['Description',item.description],['Mechanism',item.mechanism],['Information effect',item.information_effect]]);
  for(const[field,title]of[['inputs','Input States'],['outputs','Output States']]){parent.append(el('b',title));for(const id of item[field]||[]){const state=graph.states.find(s=>s.id===id);parent.append(button(state.label,()=>inspect(parent,state,'State',record),'anchor'));}}
 }else if(kind==='State'){
  fields(parent,[['Kind',item.kind]]);
  for(const p of item.properties||[]){const n=el('div',null,'property');fields(n,[['Property',p.property],['Subject',p.subject],['Value',p.value]]);if(p.evidence?.length)evidence(section(n,'Property support'),p.evidence,{sources:current[side].sources});parent.append(n);}
  if(item.components?.length)raw(parent,'Constituents',item.components);
 }else fields(parent,[['Flow type',item.kind],['From',item.source],['To',item.target]]);
 fields(parent,[['Exact record ID',item.id]]);
 const target=kind.toLowerCase()+':'+item.id;
 for(const u of graph.unknowns||[])if(u.affected_ids.includes(target))parent.append(el('p',(u.blocking?'Blocking unknown: ':'Recorded unknown: ')+u.question,'scope'));
 for(const c of graph.corrections||[])if(c.target_id===target)parent.append(el('p','Recorded correction: '+c.primary_statement,'scope'));
 const checks=section(parent,'Evidence for this record');evidence(checks,item.evidence,{sources:current[side].sources});raw(parent,'Unchanged JSON record',item);
}
"""


SCRIPT_SUFFIX = """
function focusGraph(document,selected){
 const operations=document.operations.filter(o=>selected.some(a=>a.anchor.kind==='operation'&&a.anchor.record_id===o.id));
 if(!operations.length)return document;
 const ids=new Set([...operations.map(o=>o.id),...operations.flatMap(o=>[...o.inputs,...o.outputs]),...selected.filter(a=>a.anchor.kind==='state').map(a=>a.anchor.record_id)]);
 return {...document,states:document.states.filter(s=>ids.has(s.id)),operations,flows:document.flows.filter(f=>ids.has(f.source)&&ids.has(f.target))};
}
function showAnchor(side,anchor){
 const p=panels[side],document=current[side].workflow,key=anchor.kind==='operation'?'operations':anchor.kind==='state'?'states':'flows';
 inspect(p.inspector,document[key].find(r=>r.id===anchor.record_id),anchor.kind[0].toUpperCase()+anchor.kind.slice(1),p.record);
 p.inspector.scrollIntoView({behavior:'smooth',block:'start'});
}
function openSide(side){const list=anchors(side),a=list.find(x=>x.anchor.kind==='operation')||list[0];showAnchor(side,a.anchor);}
function drawSide(side,full=false){
 const host=$(side),d=current[side].workflow,list=anchors(side);host.replaceChildren(el('p',side==='earlier'?'EARLIER EXTRACTED WORKFLOW':'LATER EXTRACTED WORKFLOW','muted'),el('h2',d.title));
 const controls=el('div',null,'controls');controls.append(button('Referenced region',()=>drawSide(side,false),full?'':'active'),button('Whole workflow',()=>drawSide(side,true),full?'active':''));host.append(controls);
 host.append(el('p',full?'All saved States, Operations and Flows are shown.':'Showing referenced Operations and their actual input/output States. Open Whole workflow for the remaining recorded operations.','muted'));
 const graph=el('div',null,'graph'),inspector=el('div',null,'inspector'),subset=full?d:focusGraph(d,list);
 const record={candidate:subset,side};host.append(graph);drawGraph(graph,inspector,record);
 const drawn=[...subset.states,...subset.operations];graph.querySelectorAll('g.node').forEach((g,i)=>{if(list.some(a=>a.anchor.record_id===drawn[i].id))g.classList.add('selected-record');});
 for(const a of list)host.append(button(a.anchor.kind.toUpperCase()+': '+a.record.label+(a.anchor.field?' · '+a.anchor.field:''),()=>showAnchor(side,a.anchor),'anchor'));
 const download=el('a','Download this exact extracted workflow JSON');download.href=current[side].file;host.append(download,inspector);panels[side]={inspector,record};
 const first=list.find(a=>a.anchor.kind==='operation')||list[0];inspect(inspector,first.record,first.anchor.kind[0].toUpperCase()+first.anchor.kind.slice(1),record);
}
function drawReport(){
 const host=$('report');host.replaceChildren(el('h2','Directed edge: '+current.earlier.workflow.title+' → '+current.later.workflow.title));
 host.append(el('p','Relation: '+current.edge.relation));
 for(const row of current.changes){const c=row.report;host.append(el('h3',c.compared_feature));const p=el('div',null,'before-after');
  for(const side of ['earlier','later']){const box=el('div');box.append(el('h3',side==='earlier'?'Before':'After'),el('p',c[side==='earlier'?'before':'after']),button('Show this in the '+side+' workflow',()=>openSide(side)));p.append(box);}host.append(p,el('h3','Retained'));
  const ul=el('ul');for(const v of c.retained)ul.append(el('li',v));host.append(ul,el('h3','Reported consequence and limits'));
  if(!c.consequence_and_limits.reported_effects.length)host.append(el('p','No isolated benefit is established for this reused feature.','scope'));
  for(const e of c.consequence_and_limits.reported_effects)fields(host,[['Evidence status',e.status],['Result',e.description],['Comparator',e.comparator],['Tested context',e.tested_context],['Attribution',e.attribution_scope]]);
  const limits=el('ul');for(const v of [...c.consequence_and_limits.limits,...c.consequence_and_limits.unresolved])limits.append(el('li',v));host.append(limits);
  const ev=section(host,'Paper evidence for this edge');for(const[k,v]of Object.entries(c.evidence)){ev.append(el('h3',k));evidence(ev,v,current);}
 }
 const review=section(host,'Independent scientific and workflow-location reviews');raw(review,'Scientific review',current.edge.review);raw(review,'Workflow-location review',current.edge.mapping_review);
 const a=el('a','Download the edge, both workflows, resolved locations and field values');a.href=current.key+'_connection.json';host.append(a);raw(host,'Exact directed edge JSON',current.edge);
}
function choose(pair){current=pair;const nav=$('traverse');nav.replaceChildren(button(pair.earlier.workflow.title,()=>openSide('earlier')),button('→ Read this evolutionary edge →',()=>$('report').scrollIntoView({behavior:'smooth'}),'edge-button'),button(pair.later.workflow.title,()=>openSide('later')));drawSide('earlier');drawSide('later');drawReport();document.querySelectorAll('#pairs button').forEach(b=>b.classList.toggle('active',b.dataset.key===pair.key));}
for(const p of window.WORKFLOW_CONNECTIONS){const b=button(p.earlier.workflow.title+' → '+p.later.workflow.title,()=>choose(p));b.dataset.key=p.key;$('pairs').append(b);}
choose(window.WORKFLOW_CONNECTIONS.find(p=>p.key===decodeURIComponent(location.hash.slice(1)))||window.WORKFLOW_CONNECTIONS[0]);
"""

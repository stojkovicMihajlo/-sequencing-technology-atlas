"""Render an existing reviewed pair result, without another model call."""

import argparse
import base64
import json
from pathlib import Path


def render(run, output):
    data = json.loads((run / "graph.json").read_text())
    analysis = json.loads((run / "analysis.json").read_text())
    data["operation_coverage"] = analysis["operation_coverage"]
    data["summary"] = analysis["summary"]
    registry = json.loads((run / "inputs/sources.json").read_text())
    data["source_index"] = {key: {k: v.get(k) for k in ("title", "url", "pmid", "content_level")}
                            for key, v in registry.items()}
    data["images"] = {}
    for side in ("left", "right"):
        images = sorted((run / "inputs").glob(side + "_diagram_*.png"))
        if images:
            data["images"][side] = "data:image/png;base64," + base64.b64encode(images[0].read_bytes()).decode()
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    output.write_text(HTML.replace("__DATA__", payload))


HTML = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Two-method comparison</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f6f8;color:#17212b;font:15px/1.5 Arial,Helvetica,sans-serif}
header{padding:26px 32px 18px;background:white;border-bottom:1px solid #dde2e7}h1{font-size:25px;margin:0 0 8px}
p{margin:8px 0}.muted{color:#526272;font-size:13px}.legend{display:flex;gap:20px;flex-wrap:wrap;margin-top:14px}
.dot{display:inline-block;width:12px;height:12px;margin-right:6px;background:#d5e9ee;border:1px solid #276676}
.dot.state{background:#f2f4f7;border-color:#a0aab4;border-radius:10px}.dot.link{background:#8269bb;border-color:#8269bb}
main{display:grid;grid-template-columns:minmax(0,1fr) 390px;gap:18px;padding:22px}
.panel{background:white;border:1px solid #dbe1e6;border-radius:9px;overflow:hidden}.graph{overflow:auto}
svg{display:block;font:12px Arial}svg .node{cursor:pointer}svg .node:hover rect{stroke:#122c3b;stroke-width:2}
svg .selected rect{stroke:#7651af;stroke-width:3}.edge{stroke:#81909d;stroke-width:1.5;fill:none}
.relation-line{stroke:#9776bd;stroke-dasharray:5 5;stroke-width:1.5;fill:none;opacity:.48}
.relation{cursor:pointer}.relation:hover rect{fill:#e4d7f3}.sidebar{padding:20px;max-height:900px;overflow:auto}
h2{font-size:18px;margin:0 0 12px}h3{font-size:14px;margin:20px 0 6px}.badge{font-size:12px;background:#eee7f7;padding:3px 7px;border-radius:4px}
button{display:block;width:100%;text-align:left;padding:13px 15px;background:white;border:0;border-top:1px solid #e0e5ea;cursor:pointer;font:inherit;color:inherit}
button:hover{background:#f4f0fa}button.active{background:#eee7f7}.listtitle{padding:14px 16px;font-weight:bold}
blockquote{margin:8px 0;padding:8px 12px;background:#f5f7fa;border-left:3px solid #a0aebb;font-size:13px;white-space:pre-wrap}
details{padding:14px;border-top:1px solid #e0e5ea}summary{cursor:pointer;font-weight:bold}details img{max-width:100%;margin-top:12px}
.footer{padding:0 24px 24px}.coverage{padding:14px 18px}.coverage p{font-size:13px}a{color:#1e5e8a}
@media(max-width:950px){main{grid-template-columns:1fr}.sidebar{max-height:none}}
</style><header><h1 id="title"></h1><p>Original Illumina workflow graphs, with paper-supported connections between existing operations.</p>
<p class="muted">Solid arrows show recorded material/data flows within a method. Dashed lines attach operation groups to comparison records; they are not material transfers between assays.</p>
<div class="legend"><span><i class="dot"></i>Operation</span><span><i class="dot state"></i>Material or data state</span><span><i class="dot link"></i>Reviewed comparison · click for evidence</span></div>
</header><main><section><div class="panel graph"><svg id="graph"></svg></div><div class="panel" style="margin-top:18px"><div class="listtitle">Connections and their evidence</div><div id="claims"></div></div>
<div class="panel" id="sources" style="margin-top:18px"></div><div class="panel coverage" id="coverage" style="margin-top:18px"></div></section>
<aside class="panel sidebar" id="detail"></aside></main><div class="footer muted" id="boundary"></div>
<script type="application/json" id="data">__DATA__</script><script>
const D=JSON.parse(document.getElementById('data').textContent), G=D.source_graph;
const links=[...D.correspondences,...D.historical_links], NS='http://www.w3.org/2000/svg';
const $=id=>document.getElementById(id), el=(t,text,cls)=>{const e=document.createElement(t);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
const S=(tag,attrs={},text)=>{const e=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text!==undefined)e.textContent=text;return e};
const nodes=G.nodes.filter(n=>['operation','state'].includes(n.node_type));const byId=new Map(nodes.map(n=>[n.id,n]));
const flows=G.edges.filter(e=>['material_input','material_output','data_input','data_output','precedes'].includes(e.kind));
const titles=Object.values(D.bindings).map(x=>x.title);$('title').textContent=titles.join(' ↔ ');$('boundary').textContent=D.boundary;
const pos=new Map(), rank=new Map(nodes.map(n=>[n.id,0]));
for(let i=0;i<nodes.length;i++){let change=false;for(const f of flows)if(rank.has(f.source)&&rank.has(f.target)&&rank.get(f.target)<=rank.get(f.source)){rank.set(f.target,rank.get(f.source)+1);change=true}if(!change)break}
const width=Math.max(1250,(Math.max(...rank.values())+1)*175+60,links.length*210+60);
const sides=Object.entries(D.bindings), counts=new Map();
for(const[side,b]of sides){const row=side==='left'?145:570;for(const n of nodes.filter(n=>n.record_id===b.record_id)){const key=side+':'+rank.get(n.id), count=counts.get(key)||0;counts.set(key,count+1);pos.set(n.id,{x:35+rank.get(n.id)*175,y:row+count*95})}}
const height=Math.max(740,...Array.from(pos.values()).map(p=>p.y+120));const svg=$('graph');svg.setAttribute('width',width);svg.setAttribute('height',height);
const defs=S('defs'),marker=S('marker',{id:'arrow',markerWidth:8,markerHeight:8,refX:7,refY:4,orient:'auto'});marker.append(S('path',{d:'M0,0 L8,4 L0,8',fill:'#81909d'}));defs.append(marker);svg.append(defs);
for(const[side,b]of sides)svg.append(S('text',{x:35,y:side==='left'?65:490,'font-size':20,'font-weight':600},b.title));
for(const f of flows){const a=pos.get(f.source),b=pos.get(f.target);if(!a||!b)continue;const path=S('path',{d:`M${a.x+145},${a.y+26} C${a.x+160},${a.y+26} ${b.x-15},${b.y+26} ${b.x},${b.y+26}`,class:'edge','marker-end':'url(#arrow)'});path.append(S('title',{},f.kind));svg.append(path)}
links.forEach((item,i)=>{const x=35+i*210,y=350;for(const side of ['left','right'])for(const id of item.claim[side+'_operation_ids']){const p=pos.get(id);if(!p)continue;const sy=side==='left'?p.y+52:p.y;svg.append(S('path',{d:`M${p.x+72},${sy} C${p.x+72},${y+22} ${x+90},${sy} ${x+90},${y+22}`,class:'relation-line'}))}
 const group=S('g',{class:'relation'});group.append(S('rect',{x,y,width:180,height:45,rx:7,fill:'#f1eafa',stroke:'#9776bd'}));group.append(S('text',{x:x+10,y:y+19},(i+1)+'. '+item.claim.relation.replaceAll('_',' ')));group.append(S('text',{x:x+10,y:y+35,'font-size':10},item.claim.direction==='undirected'?'correspondence · no ancestry implied':item.claim.direction.replaceAll('_',' ')));group.onclick=()=>showClaim(item,i);svg.append(group)});
const nodeElements=new Map();for(const n of nodes){const p=pos.get(n.id),g=S('g',{class:'node'});g.append(S('rect',{x:p.x,y:p.y,width:145,height:58,rx:n.node_type==='state'?19:5,fill:n.node_type==='state'?'#f2f4f7':'#e2f0f3',stroke:n.node_type==='state'?'#9ca8b3':'#367282'}));const label=n.original.label;const words=label.split(/\s+/);let lines=[''];for(const word of words){let k=lines.length-1;if((lines[k]+' '+word).length>22&&lines[k])lines.push(word);else lines[k]+=(lines[k]?' ':'')+word}lines.slice(0,3).forEach((line,i)=>g.append(S('text',{x:p.x+9,y:p.y+17+i*14,'font-size':11},line)));g.append(S('title',{},label));g.onclick=()=>showNode(n);svg.append(g);nodeElements.set(n.id,g)}
function reset(){for(const g of nodeElements.values())g.classList.remove('selected');$('detail').replaceChildren();document.querySelectorAll('#claims button').forEach(b=>b.classList.remove('active'))}
function para(title,text){$('detail').append(el('h3',title),el('p',text))}
function evidence(title,items){$('detail').append(el('h3',title));for(const e of items){const src=D.source_index[e.source_id]||{};const url=src.url|| (src.pmid?'https://pubmed.ncbi.nlm.nih.gov/'+src.pmid+'/':null);const p=el('p',undefined,'muted');if(url&&/^https?:\/\//.test(url)){const a=el('a',src.title||e.source_id);a.href=url;a.target='_blank';a.rel='noopener noreferrer';p.append(a)}else p.textContent=src.title||e.source_id;$('detail').append(p,el('blockquote',e.quote))}}
function showClaim(item,i){reset();const c=item.claim;for(const id of [...c.left_operation_ids,...c.right_operation_ids])nodeElements.get(id)?.classList.add('selected');$('claims').children[i]?.classList.add('active');$('detail').append(el('span',c.relation.replaceAll('_',' '),'badge'),el('h2','Connection '+(i+1)));para('What is shared',c.shared_description);para('What differs',c.difference);para('What follows from that difference',c.consequence);para('Why this relationship is supported',c.rationale);for(const[side,b]of sides){const labels=c[side+'_operation_ids'].map(id=>byId.get(id)?.original.label||id);para(b.title+' operations',labels.join(' → '));evidence(b.title+' evidence',c[side+'_evidence'])}if(c.unresolved.length)para('Limits retained after review',c.unresolved.join('\n\n'));para('Independent review',item.review.rationale)}
function showNode(n){reset();nodeElements.get(n.id).classList.add('selected');$('detail').append(el('span',n.node_type,'badge'),el('h2',n.original.label));for(const field of ['description','features','mechanism','information_effect'])if(n.original[field])para(field.replaceAll('_',' '),Array.isArray(n.original[field])?n.original[field].join('; '):n.original[field]);for(const dir of ['inputs','outputs'])if(n.original[dir]){const labels=n.original[dir].map(id=>nodes.find(x=>x.record_id===n.record_id&&x.original.id===id)?.original.label||id);para(dir,labels.join('; '))}const entry=D.operation_coverage.find(x=>x.operation_id===n.id);if(entry)para('Comparison coverage',entry.explanation);para('Record address',n.id)}
links.forEach((item,i)=>{const b=el('button',(i+1)+'. '+item.claim.shared_description);b.onclick=()=>showClaim(item,i);$('claims').append(b)});
for(const[side,b]of sides){const d=el('details'),s=el('summary','Original Illumina diagram · '+b.title);d.append(s);if(D.images[side]){const img=el('img');img.src=D.images[side];img.alt=b.title+' original source diagram';d.append(img)}$('sources').append(d)}
$('coverage').append(el('h2','Was the existing detail sufficient?'));for(const a of D.diagram_adequacy){const title=Object.values(D.bindings).find(b=>b.record_id===a.record_id)?.title||a.record_id;$('coverage').append(el('h3',title+' · '+a.verdict.replaceAll('_',' ')),el('p',a.explanation))}
const d=el('details');d.append(el('summary','Unmatched operations and withheld claims'));for(const row of D.operation_coverage.filter(x=>!x.claim_ids.length))d.append(el('p',(byId.get(row.operation_id)?.original.label||row.operation_id)+': '+row.explanation));for(const w of D.withheld)d.append(el('p',w.claim.shared_description+' — '+w.reason+': '+w.review.rationale));$('coverage').append(d);
if(links.length)showClaim(links[0],0);else $('detail').append(el('p','No accepted cross-method connections. Inspect the coverage and withheld findings.'));
</script></html>'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    render(args.run, args.output)

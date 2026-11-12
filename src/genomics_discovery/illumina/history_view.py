"""Portable view of reviewed method relationships, including unextracted donors.

Input is the saved history index. This view neither invents method identities nor
requires a predecessor workflow before displaying a reviewed historical claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def view_data(index):
    """Add presentation metadata without changing the scientific index."""
    nodes = {}
    for key, original in index['nodes'].items():
        node = dict(original)
        kind = node['kind']
        node['workflow_status'] = ('extracted' if kind == 'bound_workflow' else
            'not_extracted_or_not_matched' if kind in {'assay', 'general_method', 'unextracted_method'} else
            'component_or_other_contribution')
        nodes[key] = node
    edges = []
    for original in index['edges']:
        if original['source'] not in nodes or original['target'] not in nodes:
            raise ValueError('Relationship endpoint missing from the history index')
        if original['review']['decision'] != 'accept':
            raise ValueError('Only reviewed accepted relationships belong in this view')
        edge = dict(original)
        registry = original.get('artifact', {}).get('source_registry', {}).get('sources', {})
        edge['sources'] = registry
        edges.append(edge)
    return {'nodes': nodes, 'edges': edges}


def build(index_path, output):
    raw = Path(index_path).read_bytes()
    data = view_data(json.loads(raw))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'history_index.json').write_bytes(raw)
    # Escape HTML-significant characters, including any source quote containing
    # a closing script tag. Scientific records remain unchanged in the JSON file.
    encoded = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
    (output / 'index.html').write_text(HTML.replace('__DATA__', encoded))
    receipt = {'history_index_sha256': hashlib.sha256(raw).hexdigest(),
        'nodes': len(data['nodes']), 'relationships': len(data['edges']),
        'scientific_records_changed': False,
        'boundary': 'Presentation of saved review decisions; no new evidence adjudication or method-identity merging.'}
    (output / 'VIEW_RECEIPT.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


HTML = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sequencing technology history</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#203242;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}main{max-width:1320px;margin:auto;padding:36px 24px}h1{font-size:34px;letter-spacing:-.8px}h2{font-size:23px}h3{font-size:18px}select,button{font:inherit;padding:8px 12px;background:white;border:1px solid #aebfcb;border-radius:6px}button{cursor:pointer}button:hover{border-color:#158275}a{color:#1b6383}.controls{display:flex;gap:20px;flex-wrap:wrap;align-items:center;margin:24px 0}.muted{color:#526b7d;font-size:14px}.canvas{background:#142f42;border-radius:12px;overflow:auto;color:white}svg{display:block}svg text{font-family:Arial,sans-serif}.edge{cursor:pointer}.edge:hover path{stroke:#f3c876}.edge:focus{outline:2px solid #f3c876}.report{background:white;padding:28px;margin-top:24px;border:1px solid #d7e1e8;border-radius:12px}.before-after{display:grid;grid-template-columns:1fr 1fr;gap:20px}.before-after>div{padding:16px;background:#f3f7fa;border-top:3px solid #6c92ad}.before-after>div+div{background:#eff7f4;border-color:#158275}blockquote{border-left:3px solid #a9c2d2;padding-left:16px;margin-left:0;font-size:14px}details{border-top:1px solid #dde5eb;padding-top:12px;margin-top:18px}summary{cursor:pointer;font-weight:600}.status{font-size:13px;color:#426379}.note{padding:12px;background:#f2f6f9;border-left:3px solid #6d8eaa}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}button.selected{background:#dff2ee}@media(max-width:750px){.before-after{grid-template-columns:1fr}.report{padding:16px}}
</style><main><p class="muted">SEQUENCING TECHNOLOGY ATLAS</p>
<h1>What an assay builds on</h1>
<p>Choose a later method, then select an arrow to read the earlier contribution, the change and its evidence.</p>
<div class="controls"><label>Later method <select id="methods"></select></label><label><input id="context" type="checkbox"> Include comparisons and mechanistic precedents</label></div>
<p class="muted">Solid arrows show documented reuse. Other relationships appear only when enabled and do not establish ancestry. A dashed node border means its workflow is not extracted or not yet matched; it does not weaken an independently supported historical relationship.</p>
<div class="canvas" id="graph"></div><section class="report" id="report" aria-live="polite"></section>
<p class="muted">Names and claims come from the saved reviewed records. Cited contributions are not merged by name or publication alone. <a href="history_index.json">Download the unchanged graph JSON</a></p>
</main><script>
'use strict';const data=__DATA__;const $=id=>document.getElementById(id);
const el=(tag,text)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;return n;};
const types={documented_reuse:'Builds on this contribution',mechanistic_precedent:'Mechanistic precedent',comparison:'Comparison',method_development:'Method development',component_adoption:'Component adoption'};
const historical=e=>e.relationship_layer==='history';
const status=n=>n.workflow_status==='extracted'?'Extracted workflow':n.workflow_status==='not_extracted_or_not_matched'?'Workflow not extracted or not yet matched':'Experimental component or other contribution';
const visible=()=>data.edges.filter(e=>$('context').checked||historical(e));
function section(parent,title){const n=el('details');n.append(el('summary',title));parent.append(n);return n;}
function citations(parent,values,e){for(const v of values||[]){const source=e.sources[v.source_id]||{},box=el('div'),link=el('a',source.title||'Source passage');if(/^https?:\/\//.test(source.url||'')){link.href=source.url;link.target='_blank';link.rel='noopener';}box.append(link,el('blockquote',v.quote));parent.append(box);}}
function inspect(e){const r=$('report');r.replaceChildren(el('p',types[e.relation]||e.relation),el('h2',data.nodes[e.source].name+' → '+data.nodes[e.target].name));
 r.append(el('p','Contribution used: '+(e.claim.contributor||e.source_contribution?.name||'See change records below')));
 const boundary=el('p',status(data.nodes[e.source])+' → '+status(data.nodes[e.target]));boundary.className='status';r.append(boundary);
 if(!historical(e)){const p=el('p','This is contextual evidence, not a demonstrated evolutionary relationship.');p.className='note';r.append(p);}
 for(const c of e.claim.change_records||[]){r.append(el('h3',c.compared_feature));const pair=el('div');pair.className='before-after';
  for(const [heading,text]of[['Earlier implementation',c.before||'Not established in the registered sources'],['Later implementation',c.after]]){const box=el('div');box.append(el('strong',heading),el('p',text));pair.append(box);}r.append(pair);
  const outcome=c.consequence_and_limits;r.append(el('h3','What this enabled or changed'));
  if(!outcome.reported_effects.length)r.append(el('p','No separate performance benefit is established for this contribution in the saved record.'));
  for(const effect of outcome.reported_effects){r.append(el('p',effect.description));const qualifiers=[effect.reported_value,effect.comparator?'Comparator: '+effect.comparator:null,effect.tested_context?'Tested context: '+effect.tested_context:null,'Evidence: '+effect.status.replaceAll('_',' '),'Attribution: '+effect.attribution_scope.replaceAll('_',' ')].filter(Boolean);const p=el('p',qualifiers.join(' · '));p.className='muted';r.append(p);citations(section(r,'Evidence for this consequence'),effect.evidence,e);}
  for(const [title,items]of[['Supported limits',outcome.limits],['Unresolved questions',outcome.unresolved]])if(items.length){r.append(el('h3',title));for(const text of items)r.append(el('p',text));}
  for(const [key,title]of[['inheritance','Evidence of actual reuse'],['before','Evidence for the earlier implementation'],['after','Evidence for the later implementation']])citations(section(r,title),c.evidence[key],e);
  if(c.retained.length){const s=section(r,'Relevant retained features');for(const t of c.retained)s.append(el('p',t));}
 }
 if(!e.claim.change_records?.length){r.append(el('p','This older record has no structured change description. Inspect its original record below.'));}
 section(r,'Original reviewed relationship').append(el('pre',JSON.stringify(e,null,2)));
}
const svg=(tag,attrs={},text)=>{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!=null)n.textContent=text;return n;};
function node(canvas,n,x,y){const g=svg('g');g.append(svg('rect',{x,y,width:340,height:104,rx:10,fill:'#214354',stroke:'#96bdcf','stroke-dasharray':n.workflow_status==='not_extracted_or_not_matched'?'6 4':'none'}));const words=n.name.split(/\s+/),lines=[''];for(const word of words){if((lines.at(-1)+' '+word).length>37)lines.push(word);else lines[lines.length-1]+=(lines.at(-1)?' ':'')+word;}lines.slice(0,3).forEach((line,i)=>g.append(svg('text',{x:x+14,y:y+24+i*19,fill:'white','font-size':15},i===2&&lines.length>3?line+'…':line)));g.append(svg('title',{},n.name));const label=n.workflow_status==='extracted'?'EXTRACTED WORKFLOW':n.workflow_status==='not_extracted_or_not_matched'?'WORKFLOW PENDING':'COMPONENT / OTHER CONTRIBUTION';g.append(svg('text',{x:x+14,y:y+89,fill:'#a9cad8','font-size':10},label));canvas.append(g);}
function draw(){const edges=visible().filter(e=>e.target===$('methods').value),box=$('graph');box.replaceChildren();if(!edges.length){$('report').replaceChildren(el('p','No reviewed relationships in this selection.'));return;}
 const h=Math.max(220,edges.length*140+40),canvas=svg('svg',{viewBox:`0 0 1120 ${h}`,width:1120,height:h,role:'img','aria-label':'Earlier contributions connect to the selected later method'});const defs=svg('defs'),marker=svg('marker',{id:'arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:8,markerHeight:8,orient:'auto'});marker.append(svg('path',{d:'M0 0 L10 5 L0 10Z',fill:'#93cbbc'}));defs.append(marker);canvas.append(defs);
 edges.forEach((e,i)=>{const y=20+i*140;node(canvas,data.nodes[e.source],20,y);const g=svg('g',{class:'edge',role:'button',tabindex:0,'aria-label':types[e.relation]+': '+data.nodes[e.source].name+' to '+data.nodes[e.target].name});g.append(svg('path',{d:`M365 ${y+52} C540 ${y+52} 555 ${h/2} 748 ${h/2}`,fill:'none',stroke:'#93cbbc','stroke-width':3,'stroke-dasharray':historical(e)?'none':'5 4','marker-end':'url(#arrow)'}));g.append(svg('text',{x:385,y:y+24,fill:'#d5e9e8','font-size':12},types[e.relation]||e.relation));g.onclick=()=>inspect(e);g.onkeydown=event=>{if(event.key==='Enter')inspect(e);};canvas.append(g);});node(canvas,data.nodes[edges[0].target],755,h/2-52);box.append(canvas);inspect(edges[0]);}
function choices(){const previous=$('methods').value,targets=[...new Set(visible().map(e=>e.target))].sort((a,b)=>data.nodes[a].name.localeCompare(data.nodes[b].name));$('methods').replaceChildren();for(const key of targets){const option=el('option',data.nodes[key].name);option.value=key;$('methods').append(option);}if(targets.includes(previous))$('methods').value=previous;draw();}
$('methods').onchange=draw;$('context').onchange=choices;choices();
</script></html>'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.index, args.output)))

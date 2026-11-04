'use strict';
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
const labels = {supported:'Supported', research_needed:'Further research', failed:'Failed', integrity_error:'Rejected on revalidation'};
const batchLabel = b => ({initial_two:'Catalogue card', catalogue:'Catalogue card', posters:'Main poster row', footer_panels:'Footer / product / readout panel'})[b] || b;
let inventory = [], selected = '', request = 0;
function link(text, url) { const a = el('a',text); if (/^https?:\/\//.test(url || '')) { a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; } return a; }
function section(parent, title) { const d = el('details',null,'section'); d.append(el('summary',title)); parent.append(d); return d; }
function raw(parent, title, value) { const d = section(parent,title); d.append(el('pre',JSON.stringify(value,null,2))); }
function fields(parent, rows) { const d = el('div',null,'fields'); for (const [name,value] of rows) { if (value == null || value === '') continue; d.append(el('b',name),el('span',Array.isArray(value) ? value.join('; ') : String(value))); } parent.append(d); }
function evidence(parent, items, record) {
  for (const e of items || []) {
    const box = el('div',null,'evidence'), s = record.sources[e.source_id] || {};
    box.append(link(s.title || e.source_id,s.url),el('p',`${e.support} · ${s.content_level || e.basis} · ${e.locator}`,'muted'));
    if (e.quote) box.append(el('blockquote',e.quote));
    if (e.bbox) box.append(el('p','Image region [left, top, right, bottom]: '+e.bbox.join(', '),'muted'));
    if (e.rationale) box.append(el('p',e.rationale));
    parent.append(box);
  }
}
function filterRecords() {
  const q = $('search').value.toLowerCase(), status = $('status').value, batch = $('batch').value;
  const found = inventory.filter(r => (status === 'all' || r.status === status) &&
    (batch === 'all' || (batch === 'catalogue' ? ['initial_two','catalogue'].includes(r.batch) : r.batch === batch)) &&
    [r.title,r.record_id,...r.method_names,...r.category].join(' ').toLowerCase().includes(q));
  $('count').textContent = found.length+' source depictions'; $('records').replaceChildren();
  for (const r of found) {
    const b = el('button',r.title,r.record_id === selected ? 'active' : '');
    b.append(el('small',batchLabel(r.batch)+' · '+labels[r.status]));
    b.onclick = () => { location.hash = encodeURIComponent(r.record_id); }; $('records').append(b);
  }
}
function inspect(parent, item, kind, record) {
  const c = record.candidate;
  item = (kind === 'Flow' ? c.flows : kind === 'State' ? c.states : c.operations).find(x => x.id === item.id) || item;
  parent.replaceChildren(el('span',kind,'badge'),el('h3',item.label || item.id));
  const states = new Map(c.states.map(s => [s.id,s]));
  const names = ids => (ids || []).map(id => (states.get(id)?.label || id)+' ['+id+']');
  if (kind === 'Operation') fields(parent,[['Description',item.description],['Input States',names(item.inputs)],['Output States',names(item.outputs)],['Mechanism',item.mechanism || 'Not specified'],['Information effect',item.information_effect || 'Not specified']]);
  if (kind === 'State') fields(parent,[['Kind',item.kind],['Features',item.features]]);
  if (kind === 'Flow') {
    const nodes = [...c.states,...c.operations], name = id => nodes.find(n => n.id === id)?.label || id;
    fields(parent,[['Type',item.kind],['From',name(item.source)+' ['+item.source+']'],['To',name(item.target)+' ['+item.target+']']]);
  }
  fields(parent,[['Local ID',item.id]]);
  const affected = c.corrections.filter(x=>x.target_id===item.id), unknowns = c.unknowns.filter(x=>x.affected_ids.includes(item.id));
  for (const x of affected) parent.append(el('p','Correction: '+x.primary_statement,'warning'));
  for (const x of unknowns) parent.append(el('p','Unknown: '+x.question,'warning'));
  parent.append(el('h3','Evidence for this record')); evidence(parent,item.evidence,record);
  const checks = record.review?.checks?.filter(x=>x.target===item.id || x.target.endsWith(':'+item.id)) || [];
  if (checks.length) raw(parent,'Recorded review of this target',checks);
  raw(parent,'Exact JSON record',item);
}
function drawGraph(parent, inspector, record) {
  const c = record.candidate, nodes = [...c.states.map(x=>({...x,type:'State'})),...c.operations.map(x=>({...x,type:'Operation'}))];
  const ids = new Set(nodes.map(x=>x.id)), incoming = new Map(nodes.map(x=>[x.id,0])), next = new Map(nodes.map(x=>[x.id,[]]));
  for (const f of c.flows) { if (!ids.has(f.source)||!ids.has(f.target)) { parent.append(el('p','Cannot draw: a Flow endpoint is missing. Inspect the raw record.','warning')); return; } incoming.set(f.target,incoming.get(f.target)+1);next.get(f.source).push(f.target); }
  const rank = new Map(nodes.map(x=>[x.id,0])), ready = nodes.filter(x=>incoming.get(x.id)===0).map(x=>x.id); let processed=0;
  while (ready.length) { const id = ready.shift(); processed++;for (const target of next.get(id)) { rank.set(target,Math.max(rank.get(target),rank.get(id)+1)); incoming.set(target,incoming.get(target)-1);if (!incoming.get(target)) ready.push(target); } }
  if (processed !== nodes.length) { parent.append(el('p','A cycle prevents a left-to-right layout. Use the exact Flow records below.','warning'));return; }
  const NS='http://www.w3.org/2000/svg', svg=(tag,attrs,text)=>{const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!=null)n.textContent=text;return n;};
  const pos = new Map(), lanes = new Map();
  for (const n of nodes) { const col=rank.get(n.id),row=lanes.get(col)||0;lanes.set(col,row+1);pos.set(n.id,{x:25+col*224,y:25+row*112}); }
  const canvas=svg('svg',{width:Math.max(680,Math.max(...rank.values())*224+240),height:Math.max(150,Math.max(...lanes.values())*112+30),role:'img','aria-label':'Directed State and Operation workflow'});
  const defs=svg('defs',{}),marker=svg('marker',{id:'arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'});marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#7692ac'}));defs.append(marker);canvas.append(defs);
  for (const f of c.flows) { const a=pos.get(f.source),b=pos.get(f.target),p=svg('path',{d:`M${a.x+180},${a.y+38} C${a.x+205},${a.y+38} ${b.x-25},${b.y+38} ${b.x},${b.y+38}`,stroke:'#7692ac','stroke-width':2,fill:'none','marker-end':'url(#arrow)',class:'flow',tabindex:0,'aria-label':f.kind+' '+f.source+' to '+f.target});p.append(svg('title',{},f.kind+' · click for evidence'));p.onclick=()=>inspect(inspector,f,'Flow',record);p.onkeydown=e=>{if(e.key==='Enter')p.onclick();};canvas.append(p); }
  for (const n of nodes) { const p=pos.get(n.id),g=svg('g',{class:'node',tabindex:0,role:'button','aria-label':n.type+': '+n.label});g.append(svg('rect',{x:p.x,y:p.y,width:180,height:78,rx:n.type==='State'?20:5,fill:n.type==='State'?'#203044':'#183e46',stroke:n.type==='State'?'#55799a':'#53a5ac'}));
    g.append(svg('text',{x:p.x+12,y:p.y+17,'font-size':10},n.type.toUpperCase()));
    const lines=['']; for (const word of n.label.split(/\s+/)) { let i=lines.length-1;if((lines[i]+' '+word).length>25&&lines[i])lines.push(word);else lines[i]+=(lines[i]?' ':'')+word; }
    lines.slice(0,3).forEach((line,i)=>g.append(svg('text',{x:p.x+12,y:p.y+34+i*15,'font-size':12},i===2&&lines.length>3?line+'…':line)));
    g.append(svg('title',{},n.label));g.onclick=()=>inspect(inspector,n,n.type,record);g.onkeydown=e=>{if(e.key==='Enter')g.onclick();};canvas.append(g);
  }
  parent.append(canvas);
}
function renderRecord(r) {
  const root=$('detail');root.replaceChildren(el('span',labels[r.status],'badge'),el('span',batchLabel(r.batch),'badge'),el('h2',r.title));
  root.append(el('p',r.record_id,'muted'));
  if(r.status!=='supported') root.append(el('p','Diagnostic candidate only — excluded from supported retrieval. '+(r.reason||'Inspect the review and research questions for the unresolved outcome.'),'warning'));
  const links=el('div',null,'links'),download=el('a','Download exact saved candidate + review');download.href=r.file;download.download='';links.append(download);
  if(r.card.source_page_url)links.append(link('Original Illumina page',r.card.source_page_url));root.append(links);
  const c=r.candidate;if(!c){root.append(el('p','No authored candidate was recovered.'));raw(root,'Failure record',r);return;}
  root.append(el('p',c.scope),el('p',`${c.operations.length} Operations · ${c.states.length} States · ${c.flows.length} Flows · ${c.corrections.length} Corrections · ${c.unknowns.length} Unknowns`,'muted'));
  root.append(el('h3','Workflow'),el('p','Rounded nodes are States (material or data). Rectangular nodes are Operations (transformations). Arrows are recorded Flows. Click any node or arrow to inspect its fields and evidence.','muted'));
  const graph=el('div',null,'graph'),inspector=el('div',null,'inspector');root.append(graph,inspector);drawGraph(graph,inspector,r);
  root.append(el('p','Only recorded connections are drawn. A multi-input Operation does not imply that every input molecule becomes every output. Horizontal position displays dependency, not elapsed time.','muted'));
  if(c.operations.length)inspect(inspector,c.operations[0],'Operation',r);
  const ops=section(root,'All Operations — select one to inspect');for(const op of c.operations){const b=el('button',op.label,'operation');b.onclick=()=>{inspect(inspector,op,'Operation',r);inspector.scrollIntoView({behavior:'smooth',block:'start'});};ops.append(b);}
  const scope=section(root,'Which named methods or variants does this depiction cover?');for(const s of c.method_scopes){scope.append(el('h3',s.method+' · '+s.status));fields(scope,[['Applies to Operations',s.operation_ids.map(id=>c.operations.find(o=>o.id===id)?.label||id)],['Reason',s.rationale]]);evidence(scope,s.evidence,r);}
  const corrections=section(root,'Corrections and unknowns');if(!c.corrections.length&&!c.unknowns.length)corrections.append(el('p','None recorded at the schematic level. This does not mean every laboratory execution detail is known.'));
  for(const x of c.corrections){corrections.append(el('h3',x.id+' · '+x.disposition));fields(corrections,[['Target',x.target_id],['Diagram observation',x.diagram_observation],['Primary statement',x.primary_statement]]);evidence(corrections,x.evidence,r);}
  for(const x of c.unknowns){corrections.append(el('h3',(x.blocking?'Blocking':'Nonblocking')+' unknown'));fields(corrections,[['Question',x.question],['Affected records',x.affected_ids]]);}
  const papers=section(root,'Paper associations ('+c.paper_matches.length+')');for(const m of c.paper_matches){const source=r.sources[m.source_id]||{};papers.append(el('h3',m.methods.join(', ')),link(source.title||m.source_id,source.url));fields(papers,[['Assessment',m.assessment],['Relationship',m.relationship],['Source content level',source.content_level]]);evidence(papers,m.evidence,r);}
  const review=section(root,'Independent model review and later admission decision');review.append(el('p','The review below is the original model assessment. Any later rejection shown above takes precedence.'));
  if(r.revalidation_finding)raw(review,'Later rejection',r.revalidation_finding);
  for(const check of r.review?.checks||[]){const d=section(review,check.target+' · '+check.verdict);d.append(el('p',check.rationale));evidence(d,check.evidence,r);}
  raw(root,'All exact State / Operation / Flow records',c);raw(root,'Audit flags and research questions',{flags:r.flags,errors:r.errors,research_questions:r.research_questions,attempt_directory:r.attempt_directory});
}
async function openRecord(id) {
  const row=inventory.find(r=>r.record_id===id);if(!row){$('detail').replaceChildren(el('p','No source record has this ID.'));return;}
  selected=id;filterRecords();const ticket=++request;$('detail').replaceChildren(el('p','Loading '+row.title+'…'));
  try{const response=await fetch(row.file);if(!response.ok)throw Error('Record could not be loaded');const r=await response.json();if(ticket===request)renderRecord(r);}catch(e){if(ticket===request)$('detail').replaceChildren(el('p',e.message,'warning'));}
}
async function start(){
  const response=await fetch('index.json');if(!response.ok)throw Error('Inventory could not be loaded');const data=await response.json();inventory=data.records;
  const stats=[['Source depictions',data.manifest.source_occurrences],...Object.entries(data.manifest.counts).map(([k,v])=>[labels[k],v]),['Unique methods','Not consolidated']];
  for(const[label,value]of stats){const d=el('div',null,'stat');d.append(el('strong',value),el('span',label));$('summary').append(d);}
  if(data.manifest.comparison){$('pair').href=data.manifest.comparison.url;$('pair').hidden=false;}
  for(const id of ['search','status','batch'])$(id).addEventListener('input',filterRecords);
  window.addEventListener('hashchange',()=>openRecord(decodeURIComponent(location.hash.slice(1))));
  await openRecord(decodeURIComponent(location.hash.slice(1))||'drop-seq');
}
start().catch(e=>{$('detail').replaceChildren(el('p','Could not open this review export. Serve its directory with python3 -m http.server. '+e.message,'warning'));});

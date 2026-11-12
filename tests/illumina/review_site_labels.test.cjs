// Display names must not change scientific records or graph routing.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.textContent='';}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  setAttribute(k,v){this.attributes[k]=v;}
}
const document={getElementById:()=>null,createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t)};
const context=vm.createContext({document,console,fetch:()=>new Promise(()=>{})});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../../scripts/illumina/review_site/app.js'),'utf8'),context);
const record={sources:{},review:{checks:[]},candidate:{
  states:[{id:'s0',label:'Captured RNA',components:[{id:'c1',label:'RNA fragment'},{id:'c2',label:'DNA adapter'}]},
    {id:'s1',label:'Adapter-linked RNA'}],
  operations:[{id:'o1',label:'Ligate the RNA adapter',description:'Attach the adapter.',inputs:['s0'],outputs:['s1'],
    information_effect:'The linked adapter supplies a priming site.',input_roles:[],effects:[],evidence:[]}],
  flows:[{id:'f0',source:'s0',target:'o1',kind:'material_input',evidence:[]}],corrections:[],unknowns:[]}};
function text(node,technical=false){
  if(!technical&&node.tag==='details'&&node.children[0]?.textContent?.startsWith('Technical details'))return '';
  return [node.textContent,...node.children.map(n=>text(n,technical))].join(' ');
}
test('operation inspector uses scientific names and keeps IDs only in technical JSON',()=>{
  const before=JSON.stringify(record),parent=new Element('div');
  context.inspect(parent,record.candidate.operations[0],'Operation',record);
  assert.match(text(parent),/Captured RNA/);assert.match(text(parent),/Adapter-linked RNA/);
  assert.doesNotMatch(text(parent),/\b(s0|s1|o1)\b|Local ID/);
  assert.match(text(parent,true),/"inputs":/);assert.match(text(parent,true),/"s0"/);
  assert.equal(JSON.stringify(record),before);
});
test('properties resolve constituent references locally and retain chemistry',()=>{
  const item={id:'p1',property:'association',subject:'c1',partner:'c2',value:'covalently_linked_to',evidence:[]};
  const rows=context.assertionRows(item,record,record.candidate.states[0]);
  const shown=JSON.stringify(rows);
  assert.match(shown,/RNA fragment/);assert.match(shown,/DNA adapter/);assert.match(shown,/covalently linked to/);
  assert.doesNotMatch(shown,/"(p1|c1|c2)"/);
  const end=context.assertionRows({property:'terminal_group',subject:'self',end:'three_prime',value:'hydroxyl'},record,record.candidate.states[0]);
  assert.match(JSON.stringify(end),/3′ end/);assert.match(JSON.stringify(end),/hydroxyl/);
});
test('roles and effects resolve references without inferring missing identities',()=>{
  const role=context.assertionRows({state_id:'s0',subject:'c2',roles:['reagent'],explanation:'Adapter input.'},record,record.candidate.operations[0]);
  assert.match(JSON.stringify(role),/DNA adapter/);assert.doesNotMatch(JSON.stringify(role),/"s0"|"c2"/);
  const effect=context.assertionRows({input_state_ids:['s0'],output_state_ids:['s1'],statement:'Attach adapter.'},record,record.candidate.operations[0]);
  assert.match(JSON.stringify(effect),/Captured RNA/);assert.match(JSON.stringify(effect),/Adapter-linked RNA/);
  assert.equal(context.nodeName(record,'missing'),'Unresolved workflow reference');
  assert.equal(context.subjectName(record.candidate.states[1],'c2'),'Unresolved constituent reference');
});
test('flow inspection and accessible graph labels use names; clicks preserve exact endpoints',()=>{
  const graph=new Element('div'),inspector=new Element('div'),before=JSON.stringify(record);
  context.drawGraph(graph,inspector,record);
  const svg=graph.children[0],flow=svg.children.find(n=>n.attributes.class==='flow');
  assert.match(flow.attributes['aria-label'],/Captured RNA to Ligate the RNA adapter/);
  assert.doesNotMatch(flow.attributes['aria-label'],/\b(s0|o1)\b/);
  flow.onclick();assert.match(text(inspector),/Captured RNA → Ligate the RNA adapter/);
  assert.doesNotMatch(text(inspector),/\b(s0|o1|f0)\b/);
  assert.equal(JSON.stringify(record),before);
});
test('review targets use scientific names, including encoded property addresses',()=>{
  const r=JSON.parse(JSON.stringify(record));
  r.candidate.states.push({id:'s:2',label:'Purified cDNA',properties:[{id:'p:1',property:'terminal_group'}]});
  assert.equal(context.targetName(r,'state_property:s%3A2:p%3A1'),'Purified cDNA · terminal group');
  assert.equal(context.targetName(r,'state:s:2'),'State · Purified cDNA');
  assert.equal(context.targetName(r,'operation_semantics:o1'),'Ligate the RNA adapter · input roles and effects');
  assert.doesNotMatch(context.targetName(r,'flow:f0'),/f0|s0|o1/);
});

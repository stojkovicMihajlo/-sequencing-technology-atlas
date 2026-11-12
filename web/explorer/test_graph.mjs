import assert from 'node:assert/strict';
import {test} from 'node:test';
import {projectGraph} from './src/graph.js';
test('compact projection preserves joint inputs, splits and dependencies',()=>{
  const c={states:['a','b','r','c','d'].map(id=>({id,label:id})),operations:[{id:'copy',label:'Copy'},{id:'join',label:'Join'}],flows:[
    {id:'1',source:'a',target:'copy',kind:'material_input'},
    {id:'2',source:'copy',target:'b',kind:'material_output'},
    {id:'3',source:'b',target:'join',kind:'material_input'},
    {id:'4',source:'r',target:'join',kind:'material_input'},
    {id:'5',source:'join',target:'c',kind:'material_output'}]};
  const original=JSON.stringify(c),g=projectGraph(c,true);
  assert(!g.all.some(n=>n.id==='copy'));
  assert(g.all.some(n=>n.id==='join'));
  assert.deepEqual(g.connections.find(e=>e.operation).flowIds,['1','2']);
  assert.equal(g.connections.find(e=>e.operation).operation,c.operations[0]);
  assert.equal(JSON.stringify(c),original);
  assert.deepEqual(projectGraph(c,false).connections,c.flows);
  c.flows.push({id:'6',source:'copy',target:'join',kind:'precedes'});
  assert(projectGraph(c,true).all.some(n=>n.id==='copy'));
  c.flows.pop();c.flows.push({id:'7',source:'copy',target:'d',kind:'material_output'});
  assert(projectGraph(c,true).all.some(n=>n.id==='copy'));
});

import React, {useMemo,useState} from 'react';
import Inspector,{Evidence} from './Inspector';

// Reading order is derived from explicit connections, not guessed from labels.
export function readingOrder(candidate) {
  const ids=[...candidate.states,...candidate.operations].map(n=>n.id);
  const indegree=new Map(ids.map(id=>[id,0])), next=new Map(ids.map(id=>[id,[]])),rank=new Map(ids.map(id=>[id,0]));
  for(const f of candidate.flows){next.get(f.source).push(f.target);indegree.set(f.target,indegree.get(f.target)+1);}
  const queue=ids.filter(id=>indegree.get(id)===0);let visited=0;
  while(queue.length){const id=queue.shift();visited++;for(const child of next.get(id)){rank.set(child,Math.max(rank.get(child),rank.get(id)+1));indegree.set(child,indegree.get(child)-1);if(!indegree.get(child))queue.push(child);}}
  if(visited!==ids.length)return {operations:candidate.operations,cyclic:true};
  return {operations:[...candidate.operations].sort((a,b)=>rank.get(a.id)-rank.get(b.id)),cyclic:false};
}

export default function Reader({record,base}) {
  const [selection,select]=useState(null),[active,setActive]=useState(null);
  const c=record.candidate;
  const ordered=useMemo(()=>c?readingOrder(c):null,[c]);
  if(!c)return <div className="no-graph"><h2>Workflow awaiting completion</h2><p>{record.reason}</p></div>;
  const states=new Map(c.states.map(s=>[s.id,s]));
  const producers=id=>c.operations.filter(o=>o.outputs.includes(id));
  const consumers=id=>c.operations.filter(o=>o.inputs.includes(id));
  const refs=new Map();
  function jump(id){setActive(id);refs.get(id)?.scrollIntoView({behavior:'smooth',block:'start'});}
  function Materials({ids,output=false}){return <div className="reader-materials"><h3>{output?'Output States':'Input States'}</h3>{ids.map(id=>{const s=states.get(id),related=output?consumers(id):producers(id);return <div className="material-item" key={id}><button onClick={()=>select({...s,type:'State'})}>{s?.label}<span>↗</span></button>{related.length>0&&<div className="material-route"><small>{output?'Used by':'From'}</small>{related.map(o=><button key={o.id} onClick={()=>jump(o.id)}>{o.label} ↗</button>)}</div>}</div>})}</div>;}
  return <div className={`reader ${selection?'has-selection':''}`}>
    <nav className="reader-index" aria-label="Workflow Operations"><span className="eyebrow">OPERATIONS</span>{ordered.operations.map((o,i)=><button className={active===o.id?'active':''} key={o.id} onClick={()=>jump(o.id)}><span>{String(i+1).padStart(2,'0')}</span>{o.label}</button>)}</nav>
    <div className="reader-content"><div className="reader-intro"><span className="eyebrow">WORKFLOW READER</span><p>Read each transformation at a normal text size. Select a material to inspect its State; follow “From” and “Used by” links to trace branches.</p>{ordered.cyclic&&<p>These records contain a cycle; the list retains the source order.</p>}</div>
      {ordered.operations.map((o,i)=><article className="operation-card" key={o.id} ref={el=>refs.set(o.id,el)}>
        <header><span className="operation-number">{String(i+1).padStart(2,'0')}</span><div><small>OPERATION</small><h2>{o.label}</h2></div><button className="inspect-operation" aria-label={`Inspect ${o.label}`} onClick={()=>select({...o,type:'Operation'})}>Inspect ↗</button></header>
        <div className="transformation-row"><Materials ids={o.inputs}/><div className="transformation-arrow" aria-hidden="true"><svg viewBox="0 0 100 50"><path d="M2 25 C30 25 20 10 50 10 S70 25 92 25"/><path d="m84 19 8 6-8 6"/></svg></div><Materials ids={o.outputs} output/></div>
        {o.description&&<div className="operation-explanation"><h3>What happens</h3><p>{o.description}</p></div>}
        {o.mechanism&&<div className="operation-explanation mechanism"><h3>How it works</h3><p>{o.mechanism}</p></div>}
        {o.information_effect&&<div className="operation-explanation"><h3>Information effect</h3><p>{o.information_effect}</p></div>}
        <details className="reader-evidence"><summary>Supporting evidence · {o.evidence?.length || 0}</summary><Evidence items={o.evidence} record={record} base={base}/></details>
      </article>)}
    </div>{selection&&<div className="reader-inspector"><Inspector selection={selection} record={record} base={base} onClose={()=>select(null)} onSelect={select}/></div>}
  </div>;
}

import React from 'react';
export const words = text => String(text ?? '').replaceAll('_', ' ');
function Value({value}) {
  if (value === null || value === undefined) return null;
  if (Array.isArray(value)) return <div className="values">{value.map((v,i) => <Value key={i} value={v}/>)}</div>;
  if (typeof value === 'object') return <dl>{Object.entries(value).filter(([k,v]) => !['id','evidence'].includes(k) && v != null).map(([k,v]) => <div key={k}><dt>{words(k)}</dt><dd><Value value={v}/></dd></div>)}</dl>;
  return <span>{String(value)}</span>;
}
export function Evidence({items = [], record, base}) {
  return <div className="evidence">{items.map((e,i) => {
    const source = record.sources?.[e.source_id];
    return <article key={i}><small>{words(e.support)} · {words(source?.content_level || e.basis)}</small>
      {source?.url && <a href={source.url} target="_blank" rel="noreferrer">{source.title || (source.kind === 'image' ? 'Original Illumina diagram' : 'Source')} ↗</a>}
      {e.quote && <blockquote>{e.quote}</blockquote>}
      {e.locator && <p className="muted">{e.locator}</p>}
    </article>;
  })}</div>;
}
export default function Inspector({selection,record,base,onClose,onSelect}) {
  if (!selection) return <aside className="inspector empty"><span className="eyebrow">READ THE WORKFLOW</span><h2>Follow the material.</h2><p>Select a State to see what exists at that point. Select an Operation to read what changes and why it works.</p><div className="key"><span className="state-dot"/> State · material or data</div><div className="key"><span className="operation-dot"/> Operation · transformation</div><p>Arrows show the recorded input, output or dependency. Select an arrow to inspect its evidence.</p></aside>;
  const c = record.candidate;
  const nodes = [...c.states,...c.operations];
  const lookup = id => nodes.find(n => n.id === id);
  const x = selection;
  return <aside className="inspector"><header><span className="eyebrow">{x.type || 'Flow'}</span><button className="icon" aria-label="Close inspector" onClick={onClose}>×</button></header>
    <h2>{x.label || words(x.kind)}</h2>
    {x.type === 'State' && <p className="muted">{words(x.kind)}</p>}
    {['description','mechanism','information_effect'].map(k => x[k] && <section key={k}><h3>{words(k)}</h3><p>{x[k]}</p></section>)}
    {['inputs','outputs'].map(k => x[k]?.length > 0 && <section key={k}><h3>{words(k)}</h3>{x[k].map(id => <button className="related" key={id} onClick={() => onSelect({...lookup(id),type:'State'})}>{lookup(id)?.label || 'Unresolved reference'} <span>↗</span></button>)}</section>)}
    {!x.type && <section><h3>Connects</h3>{[x.source,x.target].map(id => <button className="related" key={id} onClick={() => onSelect({...lookup(id),type:c.states.some(s=>s.id===id)?'State':'Operation'})}>{lookup(id)?.label}<span>↗</span></button>)}</section>}
    {x.components?.length > 0 && <section><h3>Material</h3><Value value={x.components}/></section>}
    {x.features?.length > 0 && <section><h3>Features</h3><Value value={x.features}/></section>}
    {x.properties?.length > 0 && <section><h3>Properties</h3>{x.properties.map((p,i) => <article className="property" key={i}><strong>{words(p.property)}</strong><Value value={{...p,subject:p.subject === 'self' ? x.label : x.components?.find(v=>v.id===p.subject)?.label || p.subject}}/></article>)}</section>}
    <details><summary>Supporting evidence · {x.evidence?.length || 0}</summary><Evidence items={x.evidence} record={record} base={base}/></details>
  </aside>;
}

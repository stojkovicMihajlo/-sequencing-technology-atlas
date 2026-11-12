import React, {useEffect, useMemo, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow, ReactFlowProvider, Handle, Position, Controls, Background, MiniMap, useNodesState, useEdgesState} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {layoutGraph} from './graph';
import Inspector, {words} from './Inspector';
import './style.css';

const statusName = {supported:'Accepted',research_needed:'Needs research',needs_revision:'Needs revision',source_recovery_needed:'Needs sources',failed:'Failed'};
const baseURL = import.meta.env.BASE_URL;
async function read(url) { const r = await fetch(url, {cache:'no-cache'}); if (!r.ok) throw new Error(`Could not load ${url} (${r.status})`); return r.json(); }
function AssayNode({data}) {
  return <div className={`assay-node ${data.type.toLowerCase()}`}><Handle type="target" position={Position.Left}/><small>{data.type}</small><div>{data.label}</div><Handle type="source" position={Position.Right}/></div>;
}
const nodeTypes = {assay: AssayNode};
function Workflow({record,base}) {
  const [nodes,setNodes,onNodesChange] = useNodesState([]), [edges,setEdges,onEdgesChange] = useEdgesState([]);
  const [selection,select] = useState(null), [error,setError] = useState(null), [ready,setReady] = useState(false);
  useEffect(() => { let active = true; setReady(false); select(null);
    if (record.candidate) layoutGraph(record.candidate).then(graph => {if(active){setNodes(graph.nodes);setEdges(graph.edges);setReady(true);}}).catch(e=>setError(e.message));
    return () => {active=false;};
  },[record]);
  if(!record.candidate) return <div className="no-graph"><h2>Workflow awaiting completion</h2><p>{record.reason}</p></div>;
  return <div className="workflow"><div className="canvas">{error ? <p role="alert">{error}</p> : ready ? <ReactFlow key={record.record_id} nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} fitView fitViewOptions={{nodes:[...nodes].sort((a,b)=>a.position.x-b.position.x || a.position.y-b.position.y).slice(0,3),padding:0.25,minZoom:0.6,maxZoom:0.9}} minZoom={0.08} maxZoom={2} nodesConnectable={false} deleteKeyCode={null} onNodeClick={(_,n)=>select(n.data)} onEdgeClick={(_,e)=>select(e.data)} onPaneClick={()=>select(null)}>
      <Background color="#d4dcd6" gap={22} size={1}/><Controls showInteractive={false}/><MiniMap pannable zoomable nodeColor={n=>n.data.type==='State'?'#c8d8de':'#287266'}/>
    </ReactFlow> : <div className="loading">Arranging workflow…</div>}
    <div className="canvas-caption"><span className="state-dot"/> State <span className="operation-dot"/> Operation <span>→ Flow</span></div></div><Inspector selection={selection} record={record} base={base} onClose={()=>select(null)} onSelect={select}/></div>;
}
function Method({record,base,back}) {
  const [tab,setTab]=useState('workflow');
  const c=record.candidate;
  return <div className="method"><div className="breadcrumb"><button onClick={back}>← Method library</button><span>/</span>{record.category?.join(' / ')}</div>
    <div className="method-heading"><div><span className="eyebrow">{record.category?.[0]}</span><h1>{record.title}</h1></div><span className={`status ${record.status}`}>{statusName[record.status] || words(record.status)}</span></div>
    <div className="method-meta">{c ? `${c.operations.length} Operations · ${c.states.length} States · ${c.flows.length} Flows` : 'No completed workflow'}<span>Source-grounded extraction</span></div>
    <nav className="tabs">{['workflow','diagram','review'].map(t=><button className={tab===t?'active':''} key={t} onClick={()=>setTab(t)}>{t==='diagram'?'Illumina diagram':t==='review'?'Sources & review':'Workflow'}</button>)}</nav>
    {tab==='workflow' && <ReactFlowProvider><Workflow record={record} base={base}/></ReactFlowProvider>}
    {tab==='diagram' && <div className="document">{(record.source_comparison?.diagrams || []).map(d=><section key={d.source_id}><div className="section-heading"><h2>Original workflow diagram</h2><a href={d.url} target="_blank" rel="noreferrer">Illumina source ↗</a></div>{d.local_asset && <div className="diagram-wrap"><img src={base+d.local_asset} alt={`Original Illumina workflow for ${record.title}`}/></div>}</section>)}{!record.source_comparison?.diagrams?.length && <p>No diagram is available in this snapshot.</p>}</div>}
    {tab==='review' && <div className="document"><h2>Extraction outcome</h2><p>{record.reason}</p>{c?.scope && <details><summary>Extraction scope</summary><p>{c.scope}</p></details>}<h2>Sources</h2>{Object.values(record.sources || {}).filter(s=>s.url && s.kind!=='image').map((s,i)=><a className="source-link" key={i} href={s.url} target="_blank" rel="noreferrer">{s.title || words(s.content_level)} ↗</a>)}{['unknowns','corrections'].map(k=>c?.[k]?.length>0 && <details key={k}><summary>{words(k)} · {c[k].length}</summary><pre>{JSON.stringify(c[k],null,2)}</pre></details>)}<details><summary>Full independent review</summary><pre>{JSON.stringify(record.review,null,2)}</pre></details><a className="download" href={base+record._file} download>Download original extraction JSON ↓</a></div>}
  </div>;
}
function App() {
  const [manifest,setManifest]=useState(null),[base,setBase]=useState(''),[error,setError]=useState('');
  const [query,setQuery]=useState(''),[category,setCategory]=useState('All methods'),[status,setStatus]=useState('supported');
  const [selected,setSelected]=useState(null),[record,setRecord]=useState(null),[loading,setLoading]=useState(false);
  useEffect(()=>{read(baseURL+'data/current.json').then(async pointer=>{const url=baseURL+'data/'+pointer.manifest;setBase(url.slice(0,url.lastIndexOf('/')+1));setManifest(await read(url));}).catch(e=>setError(e.message));
    const change=()=>setSelected(decodeURIComponent(location.hash.slice(1))||null);change();addEventListener('hashchange',change);return()=>removeEventListener('hashchange',change);
  },[]);
  useEffect(()=>{if(!selected || !manifest){setRecord(null);return;}const row=manifest.records.find(r=>r.record_id===selected);if(!row){setError('Method is absent from this snapshot.');return;}let active=true;setLoading(true);setRecord(null);read(base+row.file).then(d=>{if(active)setRecord({...d,_file:row.file});}).catch(e=>setError(e.message)).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[selected,manifest,base]);
  const categories=useMemo(()=>[...new Set(manifest?.records.map(r=>r.category?.[0]) || [])].sort(),[manifest]);
  const filtered=useMemo(()=>manifest?.records.filter(r=>(category==='All methods'||r.category?.includes(category)) && (status==='all'||r.status===status) && [r.title,...(r.method_names||[]),...(r.category||[])].join(' ').toLowerCase().includes(query.toLowerCase())).sort((a,b)=>a.title.localeCompare(b.title))||[],[manifest,category,status,query]);
  return <div className="app"><aside className="sidebar"><a className="brand" href="#"><span className="brand-symbol">≋</span><span>Sequencing<br/><b>Technology Atlas</b></span></a><div className="lab">QIU LAB · RESEARCH COLLECTION</div><span className="eyebrow">LIBRARY</span><button className={category==='All methods'?'nav active':'nav'} onClick={()=>{setCategory('All methods');location.hash='';}}>All methods <span>{manifest?.records.length || '—'}</span></button>{categories.map(cat=><React.Fragment key={cat}><button className={category===cat?'nav active':'nav'} onClick={()=>{setCategory(cat);location.hash='';}}>{cat}<span>{manifest.records.filter(r=>r.category?.[0]===cat).length}</span></button>{category===cat && [...new Set(manifest.records.filter(r=>r.category?.[0]===cat).map(r=>r.category?.[1]))].filter(Boolean).map(sub=><button key={sub} className="subnav" onClick={()=>{setCategory(sub);location.hash='';}}>{sub}</button>)}</React.Fragment>)}<div className="sidebar-note"><span className="eyebrow">DATA SNAPSHOT</span><p>{manifest?.label}</p><small>Acceptance status belongs to this saved extraction batch.</small></div></aside>
    <main><header className="topbar"><span>Technology collection</span><span className="snapshot">{manifest?.counts.supported || '—'} accepted workflows <i/></span></header>{error && <div className="error" role="alert">{error}</div>}{loading ? <p className="loading">Opening method…</p> : record ? <Method key={record.record_id} record={record} base={base} back={()=>{location.hash='';}}/> : <div className="library"><div className="intro"><span className="eyebrow">EXPLORE THE METHODS</span><h1>From sample to signal.</h1><p>Read the transformations behind sequencing assays.<br/>Explore each workflow alongside its original diagram and scientific evidence.</p></div><div className="library-tools"><label className="search"><span>⌕</span><input aria-label="Search methods" placeholder="Search methods, categories, aliases…" value={query} onChange={e=>setQuery(e.target.value)}/><kbd>⌘ K</kbd></label><select aria-label="Review status" value={status} onChange={e=>setStatus(e.target.value)}><option value="all">All outcomes</option>{Object.entries(statusName).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></div><div className="results-line"><span>{category} <b>{filtered.length}</b></span><span>Alphabetical order</span></div><div className="cards">{filtered.map(row=><button key={row.record_id} className="method-card" onClick={()=>{location.hash=encodeURIComponent(row.record_id);}}><div className="card-top"><span className={`category ${row.category?.[0]?.toLowerCase()}`}>{row.category?.[0]}</span><span>↗</span></div><h2>{row.title}</h2><p>{row.category?.slice(1).join(' / ')}</p><div className="card-bottom"><span>{row.operations || 0} Operations · {row.states} States</span><span className={`status-dot ${row.status}`} title={statusName[row.status]}/></div></button>)}</div>{!filtered.length && <div className="no-graph"><h2>No matching methods</h2><p>Try a different search or review status.</p></div>}</div>}</main></div>;
}
createRoot(document.getElementById('root')).render(<App/>);

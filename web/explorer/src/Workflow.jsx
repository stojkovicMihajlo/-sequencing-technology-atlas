import React, {useEffect,useRef,useState,useMemo} from 'react';
import {ReactFlow,Handle,Position,Background,MiniMap,useNodesState,useEdgesState,useReactFlow,BaseEdge,EdgeLabelRenderer,getBezierPath,NodeToolbar,useViewport} from '@xyflow/react';
import {layoutGraph} from './graph';
import Inspector from './Inspector';

function AssayNode({data}) {
  const {zoom}=useViewport();
  const down=data.direction==='DOWN';
  return <div className={`assay-node ${data.type.toLowerCase()}`}><Handle type="target" position={down?Position.Top:Position.Left}/><NodeToolbar><button className="node-inspect nodrag" onClick={()=>data.inspect(data)}>Inspect {data.type} ↗</button></NodeToolbar><small>{data.type}{data.type==='Operation' && <span className="port-count">{data.inputs.length} in · {data.outputs.length} out</span>}</small><div title={data.label} className={zoom<0.35?'overview-label':''}>{data.label}</div><Handle type="source" position={down?Position.Bottom:Position.Right}/></div>;
}
function TransformationEdge(props) {
  const [path,x,y]=getBezierPath(props);
  return <><BaseEdge id={props.id} path={path} markerEnd={props.markerEnd} style={props.style} interactionWidth={24}/><EdgeLabelRenderer><button className="transformation-label nodrag nopan" style={{transform:`translate(-50%, -50%) translate(${x}px,${y}px)`}} onClick={()=>props.data.inspect(props.data)}><small>Operation</small>{props.data.label}</button></EdgeLabelRenderer></>;
}
const nodeTypes={assay:AssayNode},edgeTypes={transformation:TransformationEdge};

export default function Workflow({record,base}) {
  const [nodes,setNodes,onNodesChange]=useNodesState([]),[edges,setEdges,onEdgesChange]=useEdgesState([]);
  const [selection,select]=useState(null),[error,setError]=useState(null),[ready,setReady]=useState(false);
  const [compact,setCompact]=useState(false),[direction,setDirection]=useState('RIGHT'),[fullscreen,setFullscreen]=useState(false);
  const [focus,setFocus]=useState(record.candidate?.operations[0]?.id || '');
  const frame=useRef(null),rf=useReactFlow();
  const visible=useMemo(()=>{
    const c=record.candidate;if(!c || !focus)return c;
    const incident=c.flows.filter(f=>f.source===focus || f.target===focus);
    const ids=new Set([focus,...incident.flatMap(f=>[f.source,f.target])]);
    return {...c,states:c.states.filter(s=>ids.has(s.id)),operations:c.operations.filter(o=>ids.has(o.id)),flows:c.flows.filter(f=>ids.has(f.source)&&ids.has(f.target))};
  },[record,focus]);
  useEffect(()=>{let active=true;setReady(false);setError(null);select(null);
    if(record.candidate)layoutGraph(visible,{compact:focus?false:compact,direction}).then(g=>{if(active){setNodes(g.nodes.map(n=>({...n,data:{...n.data,inspect:select}})));setEdges(g.edges.map(e=>e.type==='transformation'?{...e,data:{...e.data,inspect:select}}:e));setReady(true);}}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};
  },[record,compact,direction,focus]);
  useEffect(()=>{const update=()=>setFullscreen(document.fullscreenElement===frame.current);document.addEventListener('fullscreenchange',update);return()=>document.removeEventListener('fullscreenchange',update);},[]);
  function start(){const first=[...rf.getNodes()].sort((a,b)=>direction==='DOWN'?a.position.y-b.position.y || a.position.x-b.position.x:a.position.x-b.position.x || a.position.y-b.position.y)[0];if(first)rf.setCenter(first.position.x+125,first.position.y+150,{zoom:0.9,duration:300});}
  async function toggleFullscreen(){try{if(document.fullscreenElement)await document.exitFullscreen();else await frame.current.requestFullscreen();}catch(e){setError(`Fullscreen unavailable: ${e.message}`);}}
  if(!record.candidate)return <div className="no-graph"><h2>Workflow awaiting completion</h2><p>{record.reason}</p></div>;
  return <div className="workflow-frame" ref={frame}>
    <div className="graph-toolbar"><label className="focus-picker">Show <select aria-label="Focus Operation" value={focus} onChange={e=>setFocus(e.target.value)}><option value="">Whole workflow</option>{record.candidate.operations.map(o=><option key={o.id} value={o.id}>{o.label}</option>)}</select></label><div className={`view-switch ${focus?'hidden-switch':''}`}><button className={compact?'active':''} onClick={()=>setCompact(true)}>Compact</button><button className={!compact?'active':''} onClick={()=>setCompact(false)}>All nodes</button></div>
      <select aria-label="Graph direction" value={direction} onChange={e=>setDirection(e.target.value)}><option value="DOWN">Vertical</option><option value="RIGHT">Horizontal</option></select><span className="toolbar-divider"/>
      <button aria-label="Zoom out" onClick={()=>rf.zoomOut({duration:200})}>−</button><button aria-label="Zoom in" onClick={()=>rf.zoomIn({duration:200})}>+</button><button onClick={()=>rf.fitView({padding:0.15,duration:300,minZoom:0.005})}>Fit all</button><button onClick={start}>Start at readable size</button><button className="fullscreen-button" onClick={toggleFullscreen}>{fullscreen?'Exit fullscreen':'Fullscreen'}</button>
    </div>
    <div className="graph-explanation">{focus?'Focused transformation: all recorded inputs and outputs are shown. Choose another Operation or Whole workflow above.':compact?'Simple transformations appear on clickable arrows. Operations with multiple inputs, outputs or dependencies keep a shared node.':'Every State, Operation and Flow is shown.'} Drag the canvas to move; use + / − to zoom.</div>
    <div className="workflow"><div className="canvas">{error?<p role="alert">{error}</p>:ready?<ReactFlow key={`${record.record_id}-${compact}-${direction}-${focus}`} nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} fitView fitViewOptions={{nodes:focus?nodes:[...nodes].sort((a,b)=>direction==='DOWN'?a.position.y-b.position.y:a.position.x-b.position.x).slice(0,2),padding:0.2,minZoom:focus?0.4:0.7,maxZoom:0.95}} minZoom={0.005} maxZoom={2.5} nodesConnectable={false} deleteKeyCode={null} onNodeClick={(_,n)=>select(n.data)} onEdgeClick={(_,e)=>select(e.data)} onPaneClick={()=>select(null)}><Background color="#d4dcd6" gap={22} size={1}/><MiniMap pannable zoomable nodeColor={n=>n.data.type==='State'?'#c8d8de':'#287266'}/></ReactFlow>:<div className="loading">Arranging workflow…</div>}
    </div>{selection&&<Inspector selection={selection} record={record} base={base} onClose={()=>select(null)} onSelect={select}/>}</div>
  </div>;
}

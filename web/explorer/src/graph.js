import ELK from 'elkjs/lib/elk.bundled.js';
const elk = new ELK();

export function projectGraph(candidate, compact = false) {
  const removed = new Set(), connections = [], removedFlows = new Set();
  if (compact) for (const operation of candidate.operations) {
    const incident = candidate.flows.filter(f => f.source === operation.id || f.target === operation.id);
    const incoming = incident.filter(f => f.target === operation.id && ['material_input','data_input'].includes(f.kind));
    const outgoing = incident.filter(f => f.source === operation.id && ['material_output','data_output'].includes(f.kind));
    // Collapse only an unambiguous unary transformation. A merge, split or
    // extra dependency retains its common Operation node and all original Flows.
    if (incident.length !== 2 || incoming.length !== 1 || outgoing.length !== 1) continue;
    removed.add(operation.id); incident.forEach(f => removedFlows.add(f.id));
    connections.push({id:`operation:${operation.id}`,source:incoming[0].source,target:outgoing[0].target,
      kind:'operation_projection', operation, flowIds:incident.map(f=>f.id)});
  }
  return {all:[...candidate.states.map(x=>({...x,type:'State'})), ...candidate.operations.filter(x=>!removed.has(x.id)).map(x=>({...x,type:'Operation'}))],
    connections:[...candidate.flows.filter(f=>!removedFlows.has(f.id)),...connections]};
}

export async function layoutGraph(candidate, {compact = false, direction = 'RIGHT'} = {}) {
  const {all, connections} = projectGraph(candidate,compact);
  const heights = new Map(all.map(n => [n.id, Math.max(128, 65 + Math.ceil(n.label.length / 27) * 20)]));
  const result = await elk.layout({id: 'workflow', layoutOptions: {
    'elk.algorithm': 'layered', 'elk.direction': direction,
    'elk.layered.spacing.nodeNodeBetweenLayers': compact ? '150' : '85', 'elk.spacing.nodeNode': '38',
    'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
  }, children: all.map(x => ({id: x.id, width: 250, height: heights.get(x.id)})),
  edges: connections.map(x => ({id: x.id, sources: [x.source], targets: [x.target]}))});
  const positions = new Map(result.children.map(x => [x.id, {x: x.x, y: x.y}]));
  return {nodes: all.map(x => ({id: x.id, type: 'assay', position: positions.get(x.id), style: {width:250,height:heights.get(x.id)}, data: {...x,direction}})),
    edges: connections.map(x => ({id: x.id, source: x.source, target: x.target, data: x.operation ? {...x.operation,type:'Operation'} : x,
      type: x.operation ? 'transformation' : 'default', style: {stroke: '#85958e', strokeWidth: 1.6},
      markerEnd: {type: 'arrowclosed', color: '#85958e'}, interactionWidth: 24,
      ariaLabel: `${x.kind.replaceAll('_', ' ')} connection`,
    }))};
}

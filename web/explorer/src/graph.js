import ELK from 'elkjs/lib/elk.bundled.js';
const elk = new ELK();

export async function layoutGraph(candidate) {
  const all = [...candidate.states.map(x => ({...x, type: 'State'})), ...candidate.operations.map(x => ({...x, type: 'Operation'}))];
  const heights = new Map(all.map(n => [n.id, Math.max(128, 65 + Math.ceil(n.label.length / 27) * 20)]));
  const result = await elk.layout({id: 'workflow', layoutOptions: {
    'elk.algorithm': 'layered', 'elk.direction': 'RIGHT',
    'elk.layered.spacing.nodeNodeBetweenLayers': '85', 'elk.spacing.nodeNode': '38',
    'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
  }, children: all.map(x => ({id: x.id, width: 250, height: heights.get(x.id)})),
  edges: candidate.flows.map(x => ({id: x.id, sources: [x.source], targets: [x.target]}))});
  const positions = new Map(result.children.map(x => [x.id, {x: x.x, y: x.y}]));
  return {nodes: all.map(x => ({id: x.id, type: 'assay', position: positions.get(x.id), style: {width:250,height:heights.get(x.id)}, data: x})),
    edges: candidate.flows.map(x => ({id: x.id, source: x.source, target: x.target, data: x,
      type: 'smoothstep', style: {stroke: '#85958e', strokeWidth: 1.6},
      markerEnd: {type: 'arrowclosed', color: '#85958e'}, interactionWidth: 24,
      ariaLabel: `${x.kind.replaceAll('_', ' ')} connection`,
    }))};
}

"""Deterministic proposal graph/report; no model summarization or atlas mutation."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote, unquote

from genomics_discovery.illumina.models import digest
from .models import IdeaReview, parse_candidate, validate_review_binding


def proposal_overlay(candidates, reviews, atlas_sha256):
    """Lossless proposal-only projection; atlas references remain external.

    Flow changes have explicit edge anchors in change_records. They are not
    silently turned into whole-workflow edits or additional material routes.
    """
    parsed = [parse_candidate(c) for c in candidates]
    if not parsed or len({c.id for c in parsed}) != len(parsed):
        raise ValueError('Provide distinct candidate IDs')
    parsed_reviews = [IdeaReview.model_validate(r) for r in reviews['reviews']]
    if len(parsed_reviews) != len(parsed) or {r.candidate_id for r in parsed_reviews} != {c.id for c in parsed}:
        raise ValueError('Each exported candidate needs exactly one matching review')
    for review in parsed_reviews:
        validate_review_binding(review, next(c for c in parsed if c.id == review.candidate_id))
    candidates = [c.model_dump(mode='json') for c in parsed]
    nodes, edges, changes, external, flow_references = [], [], [], {}, set()
    for idea in candidates:
        prefix = 'proposal:' + quote(idea['id'], safe='')
        graph_id = lambda local: prefix + ':' + quote(local, safe='')
        assessments = {a['target']: a for a in idea['assessments']}
        nodes.append({'id': prefix, 'node_type': 'workflow', 'label': idea['title'],
                      'admission': 'proposal_only', 'candidate_id': idea['id'],
                      'candidate_sha256': digest(idea), 'measurement': idea['measurement'],
                      'biological_question': idea['biological_question'],
                      'expected_benefit': idea['expected_benefit'],
                      **({'research': idea['research']} if 'research' in idea else {})})
        for kind, records in [('state', idea['workflow']['states']), ('operation', idea['workflow']['operations'])]:
            for record in records:
                nodes.append({'id': graph_id(record['id']), 'node_type': kind,
                    'label': record['label'], 'original': record, 'candidate_id': idea['id'],
                    'assessment': assessments[kind+':'+record['id']], 'admission': 'proposal_only',
                    'workflow_id': prefix})
        for flow in idea['workflow']['flows']:
            edges.append({'id': graph_id(flow['id']), 'source': graph_id(flow['source']),
                'target': graph_id(flow['target']), 'kind': flow['kind'], 'original': flow,
                'assessment': assessments['flow:'+flow['id']], 'admission': 'proposal_only'})
        for change in idea['changes']:
            anchors = []
            for target in change['proposed_target_ids']:
                kind, local = target.split(':', 1)
                if kind == 'state_property':
                    state, prop = (unquote(v) for v in local.split(':'))
                    anchors.append({'target': target, 'kind': kind, 'graph_element_id': graph_id(state),
                                    'property_id': prop})
                else:
                    anchors.append({'target': target, 'kind': kind, 'graph_element_id': graph_id(local)})
            change_id = prefix + ':change:' + quote(change['id'], safe='')
            changes.append({'id': change_id, 'candidate_id': idea['id'], 'original': change,
                            'target_anchors': anchors, 'admission': 'proposal_only'})
            nodes.append({'id': change_id, 'node_type': 'proposed_change',
                'label': change['changed'], 'candidate_id': idea['id'], 'workflow_id': prefix,
                'change_record_id': change_id, 'admission': 'proposal_only'})
            for prior in change['earlier_refs']:
                previous = external.get(prior['node_id'])
                if previous and previous['atlas_binding'] != prior:
                    raise ValueError('Conflicting atlas versions for the same referenced node')
                external[prior['node_id']] = {'id': prior['node_id'], 'node_type': 'atlas_reference',
                    'atlas_binding': prior, 'boundary': 'Reference only; original atlas record is not copied or modified.'}
                edges.append({'id': change_id + ':basis:' + digest(prior),
                    'source': prior['node_id'], 'target': change_id, 'kind': 'earlier_basis',
                    'atlas_binding': prior, 'change_id': change_id, 'admission': 'proposal_only'})
            # A grouped change does not assert a Cartesian product of pairwise
            # correspondences. Both sides connect through the one claimed change.
            for anchor in anchors:
                target = anchor['graph_element_id']
                if anchor['kind'] == 'flow':
                    target = prefix + ':flow_ref:' + quote(anchor['target'], safe='')
                    if target not in flow_references:
                        nodes.append({'id': target, 'node_type': 'flow_reference',
                            'label': anchor['target'], 'flow_id': anchor['graph_element_id'],
                            'candidate_id': idea['id'], 'workflow_id': prefix,
                            'admission': 'proposal_only'})
                        flow_references.add(target)
                edges.append({'id': change_id + ':effect:' + digest(anchor),
                    'source': change_id, 'target': target, 'kind': 'proposed_effect',
                    'proposed_target': anchor['target'], 'change_id': change_id,
                    'admission': 'proposal_only'})
    if len({e['id'] for e in edges}) != len(edges):
        raise ValueError('Duplicate proposal edge or repeated prior/target anchor')
    return {'format_version': 'assay_ideation_overlay/2', 'atlas_sha256': atlas_sha256,
            'nodes': nodes, 'edges': edges, 'external_atlas_nodes': list(external.values()),
            'change_records': changes, 'candidates': candidates, 'reviews': reviews,
            'boundary': 'Proposal-only overlay. No historical, equivalence or experimentally supported assay edges are admitted.'}


PAGE = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Assay design proposals</title><style>
:root{color-scheme:dark;font-family:Arial,Helvetica,sans-serif;background:#0c1420;color:#e5edf8}*{box-sizing:border-box}body{margin:0}header{padding:24px 32px;border-bottom:1px solid #2c3a4d}h1{font-size:25px;margin:0 0 10px}p{line-height:1.5;margin:8px 0}.muted{color:#aab9cb}#tabs{display:flex;gap:8px;margin-top:18px;flex-wrap:wrap}button{border:1px solid #40546c;border-radius:7px;background:#162335;color:#e5edf8;padding:10px 15px;cursor:pointer}button.active{background:#244c6b;border-color:#73b9ed}main{display:grid;grid-template-columns:minmax(0,1fr) 400px;height:640px}#diagram{overflow:auto;background:#101b29;position:relative}aside{overflow:auto;border-left:1px solid #2c3a4d;padding:22px}h2{font-size:19px;margin:5px 0 12px}h3{font-size:15px;color:#abd4ef}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 monospace;color:#c8d5e8}svg text{font:13px Arial;pointer-events:none;fill:#edf4ff}.node{cursor:pointer}.node:hover rect{stroke:#fff;stroke-width:2.5}#detail{white-space:normal;overflow-wrap:anywhere}.tag{display:inline-block;border:1px solid #52627a;border-radius:4px;padding:4px 7px;font-size:12px;color:#dec78d}section{padding:24px 32px;border-top:1px solid #2c3a4d}details{margin:12px 0}summary{cursor:pointer}a{color:#8bc7f0}.scrollhint{position:sticky;top:0;left:0;padding:10px;background:#101b29e8;color:#9eb3ca;font-size:12px;width:max-content;z-index:1}@media(max-width:850px){main{grid-template-columns:1fr;height:auto}#diagram{height:480px}aside{height:430px;border-top:1px solid #2c3a4d}}
</style><header><h1>Assay design proposals</h1><p id="request"></p><p class="muted">Untested architectures · Existing atlas evidence + explicit proposed changes · Model review is not experimental validation</p><div id="tabs"></div></header><main><div id="diagram"><div class="scrollhint">Rounded boxes: States · Rectangles: Operations · Click a node or arrow to inspect it · Scroll to follow the workflow</div><svg id="graph"></svg></div><aside><h2 id="title">Select a graph element</h2><div id="detail"></div></aside></main><section id="summary"></section><section><details><summary>Exact machine-readable outputs and run files</summary><p><a href="proposal_graph.json">Proposal graph</a> · <a href="final_review.json">Final review</a> · <a href="plan.json">Coordinator plan and roles</a> · <a href="state.json">Execution record</a> · <a href="sources.json">Captured sources</a></p><p class="muted">The proposal overlay is separate from the established atlas. Every full candidate JSON is under candidates/&lt;expert ID&gt;/.</p></details></section>
<script>const D=__DATA__;const NS='http://www.w3.org/2000/svg';let current;
function el(t,a={},text){const n=document.createElementNS(NS,t);for(const[k,v]of Object.entries(a))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;return n}
function textblock(parent,label,value){const h=document.createElement('h3');h.textContent=label;parent.append(h);const p=document.createElement('p');p.textContent=typeof value==='string'?value:JSON.stringify(value,null,2);parent.append(p)}
function encodedId(id){return encodeURIComponent(id).replace(/[!'()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase())}
function inspect(kind,record){record=(kind==='flow'?current.workflow.flows:kind==='state'?current.workflow.states:current.workflow.operations).find(x=>x.id===record.id)||record;document.querySelector('#title').textContent=record.label||record.id;const box=document.querySelector('#detail');box.replaceChildren();const all=[...current.workflow.states,...current.workflow.operations],name=id=>(all.find(x=>x.id===id)?.label||id)+' ['+id+']';textblock(box,'Record type',kind==='state'?'State — material or data at a boundary':kind==='operation'?'Operation — action on specified inputs':'Flow — declared input, output or ordering');if(kind==='operation'){textblock(box,'Action',record.description);textblock(box,'Input States',record.inputs.map(name).join('; ')||'None declared');textblock(box,'Output States',record.outputs.map(name).join('; ')||'None declared');if(record.mechanism)textblock(box,'Proposed mechanism',record.mechanism);if(record.information_effect)textblock(box,'Information effect',record.information_effect)}if(kind==='state'){textblock(box,'State kind',record.kind);for(const p of record.properties)textblock(box,p.property+' · '+p.subject,typeof p.value==='string'?p.value:JSON.stringify(p.value))}if(kind==='flow'){textblock(box,'Flow type',record.kind);textblock(box,'From',name(record.source));textblock(box,'To',name(record.target))}const a=current.assessments.find(x=>x.target===kind+':'+record.id);if(a){textblock(box,'Evidence status',a.status);textblock(box,'Required property',a.required_property);textblock(box,'Reason',a.rationale);if(a.compatibility_assumption)textblock(box,'Compatibility assumption',a.compatibility_assumption);if(a.unsupported_property)textblock(box,'Unsupported property',a.unsupported_property);if(a.decisive_test)textblock(box,'Decisive test',a.decisive_test)}const details=document.createElement('details');details.open=true;const s=document.createElement('summary');s.textContent='Exact record and supporting references';const pre=document.createElement('pre');pre.textContent=JSON.stringify({record,assessment:a,property_assessments:current.assessments.filter(x=>x.target.startsWith('state_property:'+encodedId(record.id)+':'))},null,2);details.append(s,pre);box.append(details)}
function show(i){current=D.candidates[i];document.querySelectorAll('#tabs button').forEach((b,j)=>b.classList.toggle('active',j===i));const graph=document.querySelector('#graph');graph.replaceChildren();const defs=el('defs');const marker=el('marker',{id:'arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto-start-reverse'});marker.append(el('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#71869d'}));defs.append(marker);graph.append(defs);
const w=current.workflow;const nodes=[...w.states.map(n=>({...n,type:'state'})),...w.operations.map(n=>({...n,type:'operation'}))];const levels=Object.fromEntries(nodes.map(n=>[n.id,0]));for(let k=0;k<nodes.length;k++)for(const f of w.flows)levels[f.target]=Math.max(levels[f.target],levels[f.source]+1);const used={},positions={};for(const n of nodes){const l=levels[n.id];const row=used[l]||0;used[l]=row+1;positions[n.id]={x:30+l*275,y:30+row*160}}
const width=310+Math.max(...Object.values(levels))*275,height=Math.max(480,Math.max(...Object.values(used))*160+60);graph.setAttribute('width',width);graph.setAttribute('height',height);
for(const f of w.flows){const a=positions[f.source],b=positions[f.target];const x=a.x+215,y=a.y+45;const path=el('path',{d:`M ${x} ${y} C ${x+35} ${y}, ${b.x-35} ${b.y+45}, ${b.x} ${b.y+45}`,fill:'none',stroke:'#71869d','stroke-width':2,'marker-end':'url(#arrow)'});path.style.cursor='pointer';path.addEventListener('click',()=>inspect('flow',f));graph.append(path);const label=el('text',{x:x+6,y:(y+b.y+45)/2-8,'font-size':9},f.kind.replaceAll('_',' '));label.setAttribute('style','font-size:9px;fill:#9fb0c3');graph.append(label)}
for(const n of nodes){const p=positions[n.id],group=el('g',{class:'node',transform:`translate(${p.x},${p.y})`});group.append(el('rect',{width:215,height:100,rx:n.type==='state'?20:4,fill:n.type==='state'?'#1b354a':'#283047',stroke:n.type==='state'?'#4c8eb4':'#8d8cb8'}));const a=current.assessments.find(x=>x.target===n.type+':'+n.id);group.append(el('text',{x:12,y:18,style:'font-size:10px;fill:#aabed4'},n.type.toUpperCase()+' · '+a.status));const words=n.label.split(' ');let lines=[''];for(const word of words){if((lines.at(-1)+' '+word).length>27)lines.push(word);else lines[lines.length-1]+=(lines.at(-1)?' ':'')+word}lines.slice(0,4).forEach((s,j)=>group.append(el('text',{x:12,y:39+j*16},s)));const title=el('title',{},n.label);group.append(title);group.addEventListener('click',()=>inspect(n.type,n));graph.append(group)}
const review=D.review.reviews.find(r=>r.candidate_id===current.id),summary=document.querySelector('#summary');summary.replaceChildren();textblock(summary,current.title,current.measurement);textblock(summary,'Predicted benefit',current.expected_benefit);textblock(summary,'Review',review.disposition+' · '+review.novelty_status+' — '+review.summary);for(const [area,f] of Object.entries(current.research||{})){textblock(summary,'Research: '+area+' · '+f.status,f.question+'\n'+f.conclusion+'\nAffected: '+f.targets.join(', ')+(f.remaining_uncertainty?'\nUnresolved: '+f.remaining_uncertainty:''))}for(const c of current.changes){textblock(summary,'Proposed change: '+c.id,'Before: '+c.earlier_arrangement+'\nChange: '+c.changed+'\nRetained: '+c.retained+'\nPredicted gain: '+c.expected_gain)}for(const issue of review.issues)textblock(summary,issue.severity+' · '+issue.target,issue.reason+'\nNext test: '+issue.next_test);textblock(summary,'Comparison across proposals',D.review.comparison_summary);inspect('operation',w.operations[0]);document.querySelector('#diagram').scrollTo(0,0)}
function showLiterature(parent){if(!D.literature)return;textblock(parent,'Independent literature investigation',D.literature.search_status);for(const limit of D.literature.limitations||[])textblock(parent,'Search limitation',limit);const finding=(D.literature.candidate_findings||[]).find(x=>x.candidate_id===current.id);if(!finding){textblock(parent,'Literature scope','No candidate-specific findings recorded.');return}const sameVersion=finding.candidate_sha256===D.candidate_sha256[current.id];textblock(parent,'Candidate version checked',sameVersion?'These findings address this exact candidate.':'These findings address an earlier candidate. Changes require fresh checks; this report does not certify the displayed revision.');for(const prior of finding.closest_priors||[])textblock(parent,'Closest prior found: '+prior.title,'Overlap: '+prior.overlap+'\nRemaining distinction: '+prior.residual_difference);for(const check of finding.checks||[])textblock(parent,'Literature check: '+check.target+' · '+check.verdict,check.claim+'\n'+check.reason);for(const limit of finding.coverage_limitations||[])textblock(parent,'Unresolved coverage',limit);const coverage=(D.literature_coverage?.candidates||[]).find(x=>x.candidate_id===current.id&&x.candidate_sha256===finding.candidate_sha256);if(coverage)textblock(parent,'Graph coverage at the checked version',coverage.checked_targets.length+' targets checked; '+coverage.unchecked_graph_targets.length+' graph targets not checked. Target coverage is not scientific correctness.')}
const showWithoutLiterature=show;show=function(i){showWithoutLiterature(i);showLiterature(document.querySelector('#summary'))};
document.querySelector('#request').textContent=D.request.prompt;D.candidates.forEach((c,i)=>{const b=document.createElement('button');b.textContent=c.title;b.onclick=()=>show(i);document.querySelector('#tabs').append(b)});if(D.literature){const links=document.createElement('p');const a=document.createElement('a');a.href='literature_report.json';a.textContent='Independent literature report';links.append(a);if(D.literature_coverage){links.append(document.createTextNode(' · '));const b=document.createElement('a');b.href='literature_coverage.json';b.textContent='Literature coverage';links.append(b)}document.querySelector('header').append(links)}show(0);
</script></html>'''


def export_report(directory, config, candidate_files, review):
    directory = Path(directory)
    candidates = [json.loads(Path(p).read_text()) for p in candidate_files]
    request = json.loads(Path(config['request_file']).read_text())
    graph = proposal_overlay(candidates, review, config['atlas_sha256'])
    (directory/'proposal_graph.json').write_text(json.dumps(graph, indent=2, ensure_ascii=False)+'\n')
    display = {'request': request, 'candidates': candidates, 'review': review,
               'candidate_sha256': {c['id']: digest(parse_candidate(c)) for c in candidates}}
    for name in ('literature_report', 'literature_coverage'):
        path = directory / (name + '.json')
        if path.exists():
            display['literature' if name == 'literature_report' else name] = json.loads(path.read_text())
    payload = json.dumps(display, ensure_ascii=False)
    payload = payload.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    (directory/'index.html').write_text(PAGE.replace('__DATA__', payload))
    lines = ['ASSAY IDEATION — PROPOSALS, NOT VALIDATED TECHNOLOGIES', '', request['prompt'], '']
    for idea in candidates:
        r = next(r for r in review['reviews'] if r['candidate_id'] == idea['id'])
        lines += [idea['title'], 'Measurement: '+idea['measurement'], 'Predicted benefit: '+idea['expected_benefit'],
                  'Review: '+r['disposition']+' / '+r['novelty_status'], r['summary']]
        for area, finding in idea.get('research', {}).items():
            lines += [f"Research {area} [{finding['status']}]: {finding['question']}",
                      finding['conclusion'], 'Affected targets: '+', '.join(finding['targets'])]
            if finding.get('remaining_uncertainty'):
                lines.append('Unresolved: '+finding['remaining_uncertainty'])
        for change in idea['changes']:
            lines += ['Change: '+change['changed'], 'Earlier arrangement: '+change['earlier_arrangement'],
                      'Retained: '+change['retained'], 'Predicted gain: '+change['expected_gain']]
        for issue in r['issues']:
            lines += [f"Issue {issue['id']} [{issue['severity']}] {issue['target']}: {issue['reason']}",
                      'Next test: '+issue['next_test']]
        lines += ['Candidate: '+next(p for p in candidate_files if json.loads(Path(p).read_text())['id'] == idea['id']), '']
    lines += ['Reviewer comparison: '+review['comparison_summary'], '',
              'Graph validity, retrieved precedent and model review do not establish experimental feasibility or novelty.']
    (directory/'report.txt').write_text('\n'.join(lines)+'\n')

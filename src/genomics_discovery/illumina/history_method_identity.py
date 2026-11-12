"""Join independently reviewed method identities; never merge by similar labels."""
import copy
import hashlib
import json
from pathlib import Path

from .extraction import read,sha
from .relationships import catalogue_method_pmids,catalogue_entry_sha256,is_assay_history


def catalogue_cards(inventory):
    return [{'record_id':c['record_id'],'title':c.get('source_title') or c.get('recovered_title') or c['record_id'],
             'description':c.get('description_verbatim',''),'references':copy.deepcopy(c.get('references',[])),
             'source_page_url':c.get('source_page_url'),
             'method_names':c.get('verified_method_names',[]),'implementation_scope':c.get('implementation_scope',''),
             'method_pmids':sorted(catalogue_method_pmids(c))} for c in inventory['cards']]


def method_registry(snapshot,inventory_path):
    state=read(snapshot/'state.json');methods={}
    for card in catalogue_cards(read(inventory_path)):
        mid=card['record_id'];row=state['cards'].get(mid,{})
        entry={'record_id':mid,'name':card['title'],'method_pmids':card['method_pmids'],
               'catalogue_entry_sha256':catalogue_entry_sha256(card),'extraction':None}
        if row.get('status')=='supported':
            path=Path(row['attempt_directory'])/'extraction.json';pin=row['artifact_pins']['extraction.json']
            if sha(path)!=pin or read(path)['record_id']!=mid:
                raise ValueError('Registered method extraction changed: '+mid)
            entry['extraction']={'path':str(path),'sha256':pin}
        methods[mid]=entry
    return {'format_version':'reviewed_history_method_registry/1','inventory_sha256':sha(inventory_path),'methods':methods}


def bind_reviewed_methods(nodes,edges,registry):
    """Identity resolution does not assert an exact earlier Operation mapping."""
    if registry['format_version']!='reviewed_history_method_registry/1':
        raise ValueError('Unknown history method registry')
    issues=[]
    for edge in edges:
        match=edge.get('review',{}).get('method_match')
        if not match:continue
        original=nodes[edge['source']];basis=original.get('method_identity_basis',{})
        if match['status']=='not_in_atlas':
            reference=edge['claim']['reference_id']
            paper=edge.get('artifact',{}).get('source_registry',{}).get('sources',{}).get(reference,{})
            pmid=str(paper.get('pmid') or '')
            if (not pmid or basis.get('paper_pmid')!=pmid or basis.get('method_name')!=match['method_name']
                    or basis.get('identity_status')!='identified_unextracted_method'):
                issues.append({'edge':edge['id'],'reason':'Unextracted method identity lacks matching reviewed source/basis'})
                continue
            # Only formatting-equivalent names within the SAME verified paper
            # share this placeholder. Broader aliases require later curation.
            name=' '.join(match['method_name'].split())
            key='unextracted:'+hashlib.sha256(json.dumps([pmid,name.casefold()]).encode()).hexdigest()[:24]
            nodes.setdefault(key,{'id':key,'name':name,'kind':'unextracted_method','paper_pmid':pmid,
                'identity_status':'identified_unextracted_method','needs_curation':True})
            edge['source_contribution']=copy.deepcopy(original);edge['source']=key
            edge['method_identity_resolution']={'status':'identified_not_in_atlas','paper_pmid':pmid,
                'method_name':name,'exact_operation_mapping':False}
            continue
        if match['status']!='resolved':continue
        mid=match['record_id'];method=registry['methods'].get(mid)
        if (not method or basis.get('record_id')!=mid
                or basis.get('catalogue_entry_sha256')!=method['catalogue_entry_sha256']):
            issues.append({'edge':edge['id'],'method':mid,'reason':'Method metadata absent or changed since independent review'})
            continue
        reference=edge['claim']['reference_id']
        paper=edge.get('artifact',{}).get('source_registry',{}).get('sources',{}).get(reference,{})
        if not paper.get('pmid') or str(paper['pmid']) not in method['method_pmids']:
            issues.append({'edge':edge['id'],'method':mid,'reason':'Cited publication is not assigned to the reviewed method'})
            continue
        if nodes[edge['target']].get('record_id')==mid:
            issues.append({'edge':edge['id'],'method':mid,'reason':'Same canonical method requires explicit version resolution'})
            continue
        extraction=method['extraction']
        if extraction:
            if sha(Path(extraction['path']))!=extraction['sha256']:
                raise ValueError('Earlier registered extraction bytes changed')
            key='workflow:'+mid+':'+extraction['sha256'];kind='bound_workflow'
        else:key='method:'+mid;kind='unextracted_method'
        nodes.setdefault(key,{'id':key,'record_id':mid,'name':method['name'],'kind':kind,
            **({'extraction_sha256':extraction['sha256']} if extraction else {})})
        edge['source_contribution']=copy.deepcopy(original)
        edge['source']=key
        edge['method_identity_resolution']={'status':'independently_reviewed_method_identity',
            'record_id':mid,'catalogue_entry_sha256':basis['catalogue_entry_sha256'],
            'exact_operation_mapping':False}
    return issues


def connectivity_report(index,investigated_methods=()):
    """Weak components describe coverage; never optimize them by inventing links."""
    methods={k:v for k,v in index['nodes'].items() if v.get('record_id') or v.get('kind')=='unextracted_method'}
    neighbors={k:set() for k in methods};history=[e for e in index['edges'] if is_assay_history(e)]
    unresolved=[]
    for e in history:
        if e['source'] in methods and e['target'] in methods:
            neighbors[e['source']].add(e['target']);neighbors[e['target']].add(e['source'])
        else:unresolved.append(e['id'])
    remaining=set(methods);components=[]
    while remaining:
        queue=[min(remaining)];seen=set()
        while queue:
            node=queue.pop()
            if node in seen:continue
            seen.add(node);queue.extend(neighbors[node]-seen)
        remaining-=seen;components.append(sorted(methods[n].get('record_id',n) for n in seen))
    components.sort(key=lambda c:(-len(c),c))
    studied=set(investigated_methods)
    return {'requested_methods':len(studied),'methods_without_published_history':sorted(studied-{v.get('record_id') for v in methods.values()}),'method_nodes':len(methods),'experimental_relationships':len(history),
        'resolved_method_relationships':len(history)-len(unresolved),'unresolved_contribution_edges':unresolved,
        'weak_component_sizes':[len(c) for c in components],'components':components,
        'isolated_investigated_methods':sorted(v['record_id'] for k,v in methods.items() if not neighbors[k] and v.get('record_id') in studied),
        'atlas_addition_queue':[{'node_id':k,'method_name':v['name'],'paper_pmid':v.get('paper_pmid'),
            'supporting_edges':[e['id'] for e in history if e['source']==k]}
            for k,v in methods.items() if v.get('needs_curation')],
        'boundary':'Connectivity is diagnostic, not scientific correctness or a requirement that every method have a parent.'}

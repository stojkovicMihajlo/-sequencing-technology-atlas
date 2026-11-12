"""Publish saved protocol outputs and precisely located evolutionary review cases."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
from urllib.parse import urlencode


def read(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def publish(protocols,evolution,renders,destination,snapshot,issues_from=None):
    methods=read(protocols/'manifest.json')['methods'];graph=read(evolution/'manifest.json')
    target=destination/snapshot
    target.mkdir(parents=True,exist_ok=False)
    manifest={'format_version':'atlas_review_examples/1','evolution_manifest':'/evolution/'+evolution.name+'/manifest.json',
        'evolution_manifest_sha256':sha(evolution/'manifest.json'),'evolution_counts':graph['counts'],'cases':[],'methods':[]}
    library=evolution.parent.parent/'data';library_manifest=library/read(library/'current.json')['manifest'];catalogue=read(library_manifest)
    for ident,mid,title,kind,explanation,talking,state_ids,output_ids in [
        ('extraction-rasl','rasl-seq','RASL-seq: preserve the identity of the DNA reporter','Extraction example',
         'This saved extraction distinguishes the RNA template from the ligated DNA probes that become the sequencing reporter.',
         'Point to the input State, the ligation Operation and the reporter output State. Calling that product cDNA would describe a different mechanism. The recorded inputs and outputs make the distinction inspectable.',
         ['selected','ligated','eluted','indexed'],['ligated','eluted','indexed']),
        ('extraction-strt','strt','STRT: unnecessary repetition of illustrative cells','Granularity issue',
         'An older accepted extraction expands three illustrative cells into repeated processing blocks. It preserves separate cell attribution but makes the method harder to read and compare.',
         'This is a representation problem, not evidence that the assay biology is wrong. We need a consistent way to represent repeated per-cell processing while retaining barcode attachment and pooling boundaries.',
         ['c1_cell','c1_lysate','c2_cell','c2_lysate','c3_cell','c3_lysate'],['c1_lysate','c2_lysate','c3_lysate'])]:
        row=next(r for r in catalogue['records'] if r['record_id']==mid);source=library_manifest.parent/row['file'];record=read(source)
        folder=target/'extractions';folder.mkdir(exist_ok=True);shutil.copyfile(source,folder/(mid+'.json'))
        manifest['cases'].append({'id':ident,'title':title,'kind':kind,'explanation':explanation,'talking_point':talking,
            'workflow_file':'extractions/'+mid+'.json','workflow_sha256':sha(source),'workflow_base':'/data/'+library_manifest.parent.name+'/',
            'workflow_link':'#'+mid,'state_ids':state_ids,'output_ids':output_ids,'source_snapshot':catalogue['label']})
    specifications=[
        ('eclip-library','iCLIP → eCLIP: a supported library redesign','Strong example','iclip','eclip','R6_truncated_library',
         'The record identifies the library route that changed and preserves the crosslink-associated endpoint.',
         'Direct DNA-adapter ligation replaces cDNA circularization and restriction reopening. The reported yield and usable-read gains compare whole protocols; they do not isolate this one change.'),
        ('single-cell-atac','ATAC-seq → single-cell indexing','Strong example','atac-seq','scatac-seq-cell-index','C1.1',
         'The contribution is located in the measurement architecture, including how a cell-attributed library is obtained.',
         'Show the inherited accessibility measurement alongside the later cell-indexing strategy. Reusing Tn5 alone would not establish this historical connection.'),
        ('eclip-wording','iCLIP → eCLIP: unclear comparison wording','Readability issue','iclip','eclip','R6_capture',
         'The stored paragraphs mix retained capture processing with different specimen demonstrations. The facts can be supported while the design comparison remains hard to read.',
         'HeLa/hnRNP C versus RBFOX2/K562 examples obscure that this capture strategy was retained. The harness now asks for aligned, feature-specific comparisons; that prompt change still needs a fresh outcome comparison.'),
        ('faire-predecessor','An earlier FAIRE implementation is outside the workflow collection','Coverage gap',None,'faire-seq','R3_readout',
         'The earlier implementation uses a microarray readout. Its contribution can be documented even though its workflow has not been extracted into this snapshot.',
         'A missing predecessor is not automatically a missing sequencing-technology card. Historical precursors and adopted experimental components need their own identity and scope decisions.')]
    for ident,title,kind,source,target_id,feature,explanation,talking in specifications:
        candidates=[e for e in graph['edges'] if e['historical_development_edge'] and graph['nodes'][e['target']].get('record_id')==target_id and
            (graph['nodes'][e['source']].get('record_id')==source if source else 'FAIRE with yeast DNA-microarray' in graph['nodes'][e['source']]['name'])]
        if len(candidates)!=1:raise ValueError('Ambiguous saved example: '+ident)
        edge=candidates[0];path=evolution/edge['file']
        if sha(path)!=edge['sha256']:raise ValueError('Edge bytes changed')
        if not any(c['id']==feature for c in read(path)['claim']['change_records']):raise ValueError('Feature is absent')
        manifest['cases'].append({'id':ident,'title':title,'kind':kind,'explanation':explanation,'talking_point':talking,
            'edge_file':'/evolution/'+evolution.name+'/'+edge['file'],'edge_sha256':edge['sha256'],'feature':feature,
            'explorer_link':'#evolution?'+urlencode({'snapshot':evolution.name,'edge':edge['id'],'feature':feature})})
    for ident,mid,title,explanation,talking in [
        ('spatial-inference','spatial-nt-seq','Spatial NT-seq: an incorrect kinetic dependency',
         'The original draft described synthesis estimation from new-to-total ratio and labelling duration while omitting abundance as an additional input.',
         'The reviewer identified a genuine information-dependency error: ratio and duration support half-life/degradation estimation, whereas synthesis magnitude additionally needs RNA abundance. The finding is preserved even after a later correction.'),
        ('paired-tag-cleavage','paired-tag','Paired-Tag: unsupported molecule-specific cleavage',
         'The original draft pictured NotI cutting cDNA, although its cited passage established treatment of a mixed RNA-library aliquot.',
         'A reported reagent addition does not establish exactly which molecule is cut. The reviewer requested an aliquot-level depiction or decisive site-map evidence, without inventing a removal mechanism.')]:
        manifest['cases'].append({'id':ident,'title':title,'kind':'Reviewer-caught error','protocol':mid,
                                 'explanation':explanation,'talking_point':talking})
    for method in methods:
        mid=method['id'];folder=target/'methods'/mid;folder.mkdir(parents=True)
        item={k:method[k] for k in ['id','name','status','doi','pmid','year','origin']};item['files']={}
        for key,filename in [('plan','plan.json'),('review','review.json')]:
            spec=method['files'][filename];source=protocols/spec['file']
            if sha(source)!=spec['sha256']:raise ValueError('Protocol packet changed: '+mid)
            shutil.copyfile(source,folder/filename);item['files'][key]='methods/'+mid+'/'+filename
        plan=read(folder/'plan.json');text=protocols/method['files'].get('protocol.txt',{}).get('file','absent')
        if text.is_file():shutil.copyfile(text,folder/'protocol.txt')
        else:
            outline=[plan['method_name'],'Original candidate draft; revision required.',plan['measurement_goal']]
            outline += [s['caption']+'\nInput: '+'; '.join(s['inputs'])+'\nTransformation: '+s['transformation']+'\nOutput: '+'; '.join(s['outputs']) for s in plan['stages']]
            (folder/'protocol.txt').write_text('\n\n'.join(outline)+'\n')
        item['files']['protocol']='methods/'+mid+'/protocol.txt'
        raster=renders/mid/'diagram.png'
        if raster.exists():
            receipt=read(renders/mid/'result.json');request=read(renders/mid/'request.json')
            if sha(raster)!=receipt['sha256'] or request['plan_sha256']!=method['files']['plan.json']['sha256']:raise ValueError('Raster/plan provenance mismatch')
            shutil.copyfile(raster,folder/'diagram.png');item['image']='methods/'+mid+'/diagram.png'
            item['image_review']=receipt.get('image_review_status','pending')
        manifest['methods'].append(item)
    if issues_from:
        original={m['id']:m for m in read(issues_from/'manifest.json')['methods']}
        for case in manifest['cases']:
            if not case.get('protocol'):continue
            old=original[case['protocol']];folder=target/'cases'/case['id'];folder.mkdir(parents=True)
            method={k:old[k] for k in ['id','name','status','doi','pmid','year','origin']};method['files']={}
            for key in ['plan','review']:
                spec=old['files'][key+'.json'];source=issues_from/spec['file']
                if sha(source)!=spec['sha256']:raise ValueError('Original issue packet changed')
                shutil.copyfile(source,folder/(key+'.json'));method['files'][key]='cases/'+case['id']+'/'+key+'.json'
            p=read(folder/'plan.json');(folder/'protocol.txt').write_text(p['method_name']+'\nOriginal candidate before correction\n\n'+p['measurement_goal']+'\n\n'+'\n\n'.join(s['caption']+'\n'+s['transformation'] for s in p['stages']))
            method['files']['protocol']='cases/'+case['id']+'/protocol.txt';case['method']=method
    code=Path(__file__).resolve().parents[2]/'experiments/diagram_generation10_20261112/run.py'
    constants={n.targets[0].id:ast.literal_eval(n.value) for n in ast.parse(code.read_text()).body if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ['COMMON','AUTHOR','REVIEW']}
    (target/'author_instructions.txt').write_text(constants['COMMON']+'\n'+constants['AUTHOR'])
    (target/'reviewer_instructions.txt').write_text(constants['COMMON']+'\n'+constants['REVIEW'])
    (target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    pointer=destination/'.current.tmp';pointer.write_text(json.dumps({'manifest':snapshot+'/manifest.json'}));pointer.replace(destination/'current.json')
    return {'protocols':len(methods),'cases':len(manifest['cases']),'rasters':sum(bool(m.get('image')) for m in manifest['methods'])}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['protocols','evolution','renders','destination']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--snapshot',required=True);p.add_argument('--issues-from',type=Path);a=p.parse_args()
    print(publish(a.protocols,a.evolution,a.renders,a.destination,a.snapshot,a.issues_from))

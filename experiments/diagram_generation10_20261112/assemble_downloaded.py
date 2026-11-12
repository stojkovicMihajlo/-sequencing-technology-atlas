"""Assemble verified acquired papers after a staging-directory collision.

No downloads or model calls. Read only completed per-method source receipts;
keep acquisition/registration directories separate from model-input copies.
"""
import argparse
from pathlib import Path
import os

from genomics_discovery.illumina.extraction import read,write,sha,verify_registry_files
from genomics_discovery.illumina.sources import parse_pubmed
from .acquire_inputs import stage_source


def assemble(args):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Source assembly belongs in a Qiu Slurm allocation')
    pool=read(args.candidates);args.output.mkdir(parents=True,exist_ok=False)
    audit_path=args.candidates.parent/'catalogue_absence_audit.json'
    audit=read(audit_path)
    if sha(Path(audit['catalogue']['path']))!=audit['catalogue']['sha256']:
        raise ValueError('Illumina inventory differs from the recorded target check')
    selected=set(audit['selected_methods'])
    checked={row['method_id']:row for row in audit['candidates']}
    if len(selected)!=10 or any(checked[mid]['matches'] for mid in selected):
        raise ValueError('Require ten targets without a recorded Illumina match')
    metadata=args.source_run/'acquisition/pubmed.xml'
    publications=parse_pubmed(metadata.read_bytes()) if metadata.exists() else {}
    manifest={'format_version':'assay_diagram_sources/1','methods':[],'model_calls_made':0,
        'new_downloads':0,'required_count':pool['required_ready_methods'],
        'target_illumina_diagrams_provided':False,'candidate_pool_sha256':sha(args.candidates),
        'source_acquisition_run':str(args.source_run),'selection':'Recorded candidate priority; reuse acquired identity-checked originals and staged text. No assay workflow extraction.'}
    held=[]
    for candidate in sorted(pool['methods'],key=lambda c:c['priority']):
        if len(manifest['methods'])==pool['required_ready_methods']:break
        mid=candidate['method_id'];old=args.source_run/'methods'/mid
        if mid not in selected:continue
        acquisition=old/'ACQUISITION.json';primary_receipt=old/'primary/SOURCE_RECEIPT.json'
        if not acquisition.exists() or not primary_receipt.exists():
            held.append({'method_id':mid,'reason':'Completed acquisition and registered-primary copy not available'});continue
        record=read(acquisition);primary=read(primary_receipt)['source_registration']
        pmid=str(primary.get('pmid') or '')
        assigned=record['candidate'].get('pmid')
        if not pmid or pmid!=assigned or record['candidate']['doi']!=candidate['doi']:
            raise ValueError('Acquired source changed assigned publication identity: '+mid)
        if pmid!=checked[mid]['pmid'] or sha(primary_receipt)!=checked[mid]['primary_receipt']['sha256']:
            raise ValueError('Acquired primary differs from the target-inventory audit: '+mid)
        verify_registry_files({'primary':primary})
        supplement_candidates=[];provenance=[{'path':str(acquisition),'sha256':sha(acquisition)},
            {'path':str(primary_receipt),'sha256':sha(primary_receipt)}]
        cached=record['candidate'].get('cached_registry')
        if cached:
            registry=read(Path(cached));verify_registry_files(registry)
            supplement_candidates += [s for s in registry.values() if s.get('document_role')=='supplement']
            provenance.append({'path':cached,'sha256':sha(Path(cached))})
        for path in sorted(old.glob('supplement_*/registered_source.json')):
            source=read(path)
            if source.get('document_role')=='supplement':supplement_candidates.append(source)
            provenance.append({'path':str(path),'sha256':sha(path)})
        folder=args.output/'methods'/mid;folder.mkdir(parents=True)
        primary_id='paper:'+pmid;sources={primary_id:stage_source(primary,folder,'primary','primary')}
        seen=set()
        for source in supplement_candidates:
            if str(source.get('pmid') or '')!=pmid:
                raise ValueError('Supplement changes parent publication: '+mid)
            verify_registry_files({'supplement':source})
            key=(source['sha256'],source.get('original',{}).get('sha256'))
            if key in seen:continue
            seen.add(key);sid='supplement:'+pmid+':'+source['sha256'][:16]
            sources[sid]=stage_source(source,folder,'supplement_'+str(len(seen)),'supplement')
        method={'method_id':mid,'name':candidate['name'],'publication_year':candidate['publication_year'],
            'publication_year_basis':candidate.get('publication_year_basis','Declared journal/online year; authoritative metadata dates are retained separately.'),
            'primary_source_id':primary_id,'pmid':pmid,'doi':candidate['doi'],'paper_title':primary['title'],
            'publication_dates':publications.get(pmid,{}).get('publication_dates',candidate.get('publication_dates',[])),
            'sources':sources,'missing_supplements':record.get('missing_supplements',[]),
            'target_illumina_diagrams_provided':False}
        for source in sources.values():source['doi']=candidate['doi']
        write(folder/'method.json',method);write(folder/'ASSEMBLY_RECEIPT.json',{
            'inputs':provenance,'distinct_supplements':len(seen),'model_calls':0,'downloads':0,
            'reason':'New model-input namespace avoids collision with existing source-registration folders. Original acquisition artifacts unchanged.'})
        manifest['methods'].append(method)
    manifest['ready_count']=len(manifest['methods']);manifest['held_candidates']=held
    manifest['catalogue_absence_audit']={'path':str(audit_path),'sha256':sha(audit_path)}
    write(args.output/'input_manifest.json',manifest)
    if manifest['ready_count']!=10 or len({m['pmid'] for m in manifest['methods']})!=10:
        raise ValueError('Need ten distinct verified paper inputs before content agents start')
    print({'ready_count':10,'methods':[m['method_id'] for m in manifest['methods']],
           'manifest':str(args.output/'input_manifest.json'),'new_downloads':0,'model_calls':0})
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('candidates','source-run','output'):parser.add_argument('--'+name,type=Path,required=True)
    assemble(parser.parse_args())

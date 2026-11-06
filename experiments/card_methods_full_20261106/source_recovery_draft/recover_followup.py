"""Recover bounded, identity-bound evidence before another extraction/review.

Known method assignments and actual parent-paper attachments/citations define
retrieval. No search-result corpus, new technology identity or ancestry is inferred.
"""
import argparse
import asyncio
import copy
from pathlib import Path
import os
import re
import shutil
from urllib.parse import urlencode, quote

from genomics_discovery.execution.codex_worker import CodexWorker
from genomics_discovery.illumina.extraction import read, write, sha, now, checkpoint, verify_registry_files, extract_registered_workflow
from genomics_discovery.illumina.graph import _load_card, protocol_text
from genomics_discovery.illumina.sources import fetch, parse_pubmed, acquire_pmc_fulltext
from genomics_discovery.sources.document_manifest import inspect_jats
from experiments.diagram_paper_pilot_20261106.run import prepare_method
from experiments.diagram_paper_pilot_20261106.recover_dependencies import recover_supplement
from scripts.illumina.technology_sources.recover_selected import acquire_one
from scripts.illumina.technology_sources.article_identity import normalized_title
from scripts.illumina.review_site.export_batch import export
from .audit import actions


def primary_source(cache, paper, *, contact_email):
    """One attempt per publication, shared by methods assigned that publication."""
    pid=paper['paper_id']; folder=cache/pid;folder.mkdir(parents=True,exist_ok=False)
    pmid=pid.removeprefix('pmid-')
    if not pmid.isdecimal():raise ValueError('Missing-primary recovery needs an already assigned PMID')
    metadata_path=folder/'pubmed.xml'
    fetch('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?'+urlencode({'db':'pubmed','id':pmid,'retmode':'xml'}),metadata_path,timeout=40)
    metadata=parse_pubmed(metadata_path.read_bytes())[pmid]
    if normalized_title(metadata['title'])!=normalized_title(paper['title']):
        raise ValueError('Fresh metadata title differs from curated method-paper assignment')
    assigned={'id':pid,'pmid':pmid,'title':paper['title'],'metadata':metadata}
    _,files,receipts=acquire_one(folder,assigned)
    if not files:
        result=acquire_pmc_fulltext(metadata,folder/'official_pmc')
        receipts.append({'route':'official_pmc','result':result})
        if result.get('status')=='fulltext_acquired':
            base=folder/'official_pmc'
            files=[(base/result[k],kind) for k,kind in [('path','article'),('text_path','reading-text'),('blocks_path','blocks')]]
    if not files and metadata.get('identifiers',{}).get('doi'):
        # The user explicitly approved this email/DOI lookup. Use only returned
        # public PDF locations; their bytes still need title/DOI confirmation.
        doi=metadata['identifiers']['doi'][0]
        route=folder/'unpaywall.json'
        try:
            fetch('https://api.unpaywall.org/v2/'+quote(doi,safe='')+'?'+urlencode({'email':contact_email}),route,timeout=35)
            record=read(route); locations=record.get('oa_locations',[])
            if record.get('best_oa_location'):locations=[record['best_oa_location'],*locations]
            urls=list(dict.fromkeys(loc.get('url_for_pdf') for loc in locations if loc.get('url_for_pdf')))
            for i,url in enumerate(urls):
                path=folder/f'public_copy_{i}.pdf'
                try:
                    receipt=fetch(url,path,timeout=40);receipts.append(receipt)
                    if not path.read_bytes().startswith(b'%PDF-'):continue
                    import subprocess
                    text=path.with_suffix('.txt')
                    subprocess.run(['pdftotext','-enc','UTF-8',str(path),str(text)],check=True,capture_output=True,timeout=90)
                    body=text.read_text();front='\f'.join(body.split('\f')[:2])
                    if (normalized_title(paper['title']) not in normalized_title(front)
                            or doi.lower() not in re.sub(r'\s+','',front).lower()):continue
                    files=[(path,'article'),(text,'reading-text')];break
                except Exception as exc:receipts.append({'url':url,'error':str(exc)})
        except Exception as exc:receipts.append({'route':'unpaywall','error':str(exc)})
    write(folder/'RETRIEVAL.json',receipts)
    if not files:return None
    text=next(p for p,kind in files if kind=='reading-text')
    originals=[p for p,kind in files if kind=='article']
    custody=[metadata_path,folder/'RETRIEVAL.json',*[p for p,_ in files]]
    source={'kind':'text','content_level':'fulltext','document_role':'primary_article',
        'assigned_paper_id':pid,'pmid':pmid,'doi':metadata.get('identifiers',{}).get('doi',[]),
        'title':paper['title'],'url':'https://pubmed.ncbi.nlm.nih.gov/'+pmid+'/',
        'path':str(text),'sha256':sha(text),'text':text.read_text(),
        'custody_files':[{'path':str(p),'sha256':sha(p)} for p in custody],
        'association_status':'Curated method-paper assignment; original own identifiers/title checked by acquisition. Inspect the actual implementation and source version; no scientific acceptance follows from recovery.'}
    verify_registry_files({'paper:'+pmid:source});write(folder/'registered_source.json',source)
    return source


def parent_structure(source):
    for pin in source.get('custody_files',[]):
        p=Path(pin['path'])
        if p.suffix.lower()!='.xml':continue
        try:d=inspect_jats(p.read_bytes())
        except (ValueError,Exception) as exc:
            continue
        if d['identity']['identifiers'].get('pmid')==[source.get('pmid')]:return d
    return None


def recover_evidence(registry, card, review, output, cache, *, contact_email):
    """Register actual focal-paper attachments; proposed applicability stays open."""
    output.mkdir(parents=True,exist_ok=False);added={};report=[]
    pmid=card['curation_binding']['primary_paper_id'].removeprefix('pmid-')
    sid='paper:'+pmid;source=registry.get(sid,{})
    structure=parent_structure(source)
    if not structure:
        write(output/'RECOVERY.json',[{'status':'parent_jats_unavailable','source_id':sid,
            'meaning':'Automatic attachment enumeration unavailable for this source format; not proof that no supplement exists.'}]);return added
    for item in structure['supplements']:
        key=item.get('element_id')
        request={'kind':'supplement','parent_source_id':sid,'attachment_id':key}
        if not key:
            report.append({'status':'attachment_has_no_stable_parent_id','attachment':item});continue
        try:
            row=recover_supplement(request,source,output/key)
            registered='supplement:'+pmid+':'+key
            if registered in registry:
                report.append({'status':'already_registered','source_id':registered});continue
            added[registered]=row;report.append({'status':'registered_not_scientifically_resolved','source_id':registered,'parent_attachment':item})
        except Exception as exc:report.append({'status':'retrieval_or_conversion_failed','attachment':item,'error':str(exc)})
    # Follow only a procedural citation whose own PMID/DOI is explicitly named
    # in the recorded research request AND appears in this actual parent JATS.
    requested=' '.join(c['rationale'] for c in review['checks'] if c['action']=='research').lower()
    refs={}
    for candidate in structure['procedure_citation_candidates']:
        for ref in candidate['reference_records']:
            ids=ref['identifiers']; ps=ids.get('pmid',[]);ds=ids.get('doi',[])
            if len(ps)==1 and (re.search(r'\b'+re.escape(ps[0])+r'\b',requested) or any(d.lower() in requested for d in ds)):
                refs[ps[0]]=ref
    for pid,ref in refs.items():
        if 'paper:'+pid in registry:continue
        try:
            # Fresh own metadata supplies the exact title; the parent citation
            # supplies the requested identity, never a guessed protocol title.
            meta_path=output/('dependency_'+pid+'.xml')
            fetch('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?'+urlencode({'db':'pubmed','id':pid,'retmode':'xml'}),meta_path,timeout=40)
            meta=parse_pubmed(meta_path.read_bytes())[pid]
            packet={'paper_id':'pmid-'+pid,'title':meta['title']}
            if pid not in cache:cache[pid]=primary_source(output/'dependencies',packet,contact_email=contact_email)
            if cache[pid]:
                row=copy.deepcopy(cache[pid]);row.update(document_role='cited_procedural_dependency',parent_source_id=sid,
                    association_status='Requested procedural citation present in the focal JATS; applicability and transfer still need independent scientific review.')
                added['paper:'+pid]=row;report.append({'status':'registered_dependency_not_scientifically_resolved','pmid':pid,'parent_reference':ref})
        except Exception as exc:report.append({'status':'dependency_recovery_failed','pmid':pid,'error':str(exc)})
    write(output/'RECOVERY.json',report);write(output/'registered_additions.json',added);return added


async def execute(previous, library, vault, output, workers, contact_email):
    if not os.environ.get('SLURM_JOB_ID'):raise ValueError('Source acquisition and native extraction require Qiu Slurm')
    output.mkdir(parents=True,exist_ok=False)
    state=copy.deepcopy(read(previous/'state.json'));selection=read(previous/'selection.json')
    chosen={r['method_id']:r for r in selection['methods']};cards=read(previous/'inventory.json')['cards']
    card_map={c['record_id']:c for c in cards};targets=[]
    for mid,row in state['cards'].items():
        if row['status']=='source_recovery_needed':targets.append(mid)
        elif row['status'] in {'research_needed','needs_revision'}:
            if actions(row,read(Path(row['attempt_directory'])/'review.json'))['needs_source_investigation']:targets.append(mid)
    state.update(started_at=now(),finished_at=None,slurm_job_id=os.environ['SLURM_JOB_ID'],
        followup={'previous_batch':str(previous),'selected_methods':targets,'maximum_source_recovery_passes':1,
            'maximum_correction_rounds':1,'provider_blocked_methods_excluded':True,
            'reason':'Audit-driven source recovery. Fresh calls only for a repaired source bundle or newly registered evidence; no unchanged research-loop retry.'})
    for name in ('inventory.json','selection.json'):shutil.copyfile(previous/name,output/name)
    state['policy'].update(inventory_sha256=sha(output/'inventory.json'),workers=workers)
    checkpoint(output,state,cards)
    cache={};dependency_cache={};gate=asyncio.Semaphore(workers);tasks=[]

    async def model(mid,card,registry,images,prior):
        async with gate:
            destination=output/'cards'/mid/'attempt_001'
            row={'record_id':mid,'title':card['source_title'],'status':'running','attempt_directory':str(destination),'previous_outcome':prior}
            state['cards'][mid]=row;checkpoint(output,state,cards)
            worker=CodexWorker(model='gpt-6.1-sol',effort='xhigh',timeout_seconds=None,web_search=False,network_access=False,pin_shell_environment=True)
            try:
                result=await extract_registered_workflow(card,registry,images,destination,worker,max_revisions=1,
                    source_revision_seed=prior if prior['status'] in {'research_needed','needs_revision'} else None)
                row.update(result,finished_at=now());graph,_=_load_card(row,card)
                (destination/'workflow.txt').write_text(protocol_text(graph,sources=registry))
            except Exception as exc:row.update(status='failed',reason=f'{type(exc).__name__}: {exc}',finished_at=now())
            checkpoint(output,state,cards)

    for mid in targets:
        prior=copy.deepcopy(state['cards'][mid]);source_dir=output/'source_recovery'/mid
        state['active_source_recovery']=mid;checkpoint(output,state,cards)
        try:
            if prior['status']=='source_recovery_needed':
                package=library/chosen[mid]['canonical_directory']/'source_package.json'
                overrides={}
                try:card,registry,images=prepare_method(package,vault,chosen[mid])
                except ValueError:
                    p=next(p for p in read(package)['method_papers'] if p['paper_id']==chosen[mid]['primary_paper_id'])
                    if p['availability']=='fulltext_acquired':raise
                    pid=p['paper_id']
                    if pid not in cache:
                        try:cache[pid]=await asyncio.to_thread(primary_source,output/'primary_cache',p,contact_email=contact_email)
                        except Exception as exc:cache[pid]=None;write(output/('recovery_'+pid+'.json'),{'error':str(exc)})
                    if not cache[pid]:raise ValueError('Assigned primary remains inaccessible through recorded public routes')
                    overrides[pid]=cache[pid];card,registry,images=prepare_method(package,vault,chosen[mid],primary_overrides=overrides)
                card_map[mid]=card
            else:
                directory=Path(prior['attempt_directory']);card=read(directory/'card.json');_load_card(prior,card)
                registry=read(directory/'source_registry.json');review=read(directory/'review.json')
                added=await asyncio.to_thread(recover_evidence,registry,card,review,source_dir,dependency_cache,contact_email=contact_email)
                if not added:
                    state['cards'][mid]['source_recovery_attempt']=str(source_dir);continue
                registry={**registry,**added};card=copy.deepcopy(card)
                for sid,s in added.items():
                    if s.get('document_role')=='cited_procedural_dependency':
                        card['references'].append({'reference_id':mid+':dependency:'+s['pmid'],'role':'recovered','pmid':s['pmid'],'source_id':sid})
                images=[(sid,Path(s['path'])) for sid,s in registry.items() if s['kind']=='image'];card_map[mid]=card
            write(output/'inventory.json',{'cards':[card_map[c['record_id']] for c in cards]})
            state['policy']['inventory_sha256']=sha(output/'inventory.json');checkpoint(output,state,cards)
            tasks.append(asyncio.create_task(model(mid,card,registry,images,prior)))
        except Exception as exc:
            state['cards'][mid]['source_recovery_error']=f'{type(exc).__name__}: {exc}';checkpoint(output,state,cards)
    await asyncio.gather(*tasks)
    state.pop('active_source_recovery',None);state['finished_at']=now();checkpoint(output,state,cards)
    export(output,output/'inspection_final');shutil.make_archive(str(output/'inspection_final'),'zip',output/'inspection_final')
    write(output/'RESULT.json',{'counts':state['counts'],'source_investigations':targets,
        'native_retries_only_after_source_change':True,'separate_revision_batch_not_merged':True})
    return state


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('previous','library','vault','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--workers',type=int,default=10);p.add_argument('--contact-email',required=True);a=p.parse_args()
    asyncio.run(execute(a.previous.resolve(),a.library.resolve(),a.vault.resolve(),a.output.resolve(),a.workers,a.contact_email))

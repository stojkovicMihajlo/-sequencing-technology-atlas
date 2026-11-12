"""Replay failed source registrations and authoritative PMC routes, without new finders.

Only the frozen remaining-method manifest is eligible. Existing extraction tasks
are excluded. Newly ready methods each receive an independent extraction slot.
"""
import argparse
import asyncio
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode
import os

from genomics_discovery.illumina.extraction import read,write,sha,verify_registry_files
from genomics_discovery.illumina.sources import fetch,parse_pubmed,acquire_pmc_fulltext
from genomics_discovery.sources.acquisition.models import Document,Identity
from genomics_discovery.sources.acquisition.downloads import Download,download
from genomics_discovery.sources.document_manifest import inspect_jats
from experiments.card_methods_full_20261112.located_documents import register,_article_identity
from experiments.diagram_paper_pilot_20261106 import run as extractor


def cached_fetch(original, receipt):
    """Reuse only original bytes bound to an actual previous host receipt."""
    if sha(original)!=receipt['sha256']:
        raise ValueError('Cached source differs from its retrieval receipt')
    def get(url):
        if url in {receipt['requested_url'],receipt['final_url']}:
            return Download(original.read_bytes(),receipt['final_url'],receipt.get('content_type',''))
        return download(url)
    return get


def validate_remaining_scope(manifest, scheduled):
    """No provider stop or already dispatched method can become a fresh retry."""
    rows = manifest['rows']
    ids = [row['method'] for row in rows]
    if (not ids or len(ids) != len(set(ids)) or set(ids) != set(manifest['methods'])
            or len(ids) != manifest.get('case_count', len(ids))):
        raise ValueError('Remaining-method scope is inconsistent')
    if set(ids).intersection(scheduled):
        raise ValueError('Remaining manifest overlaps existing extraction tasks')
    for row in rows:
        if row['case']['hold']['failure_category'] == 'provider_policy_block':
            raise ValueError('Provider-blocked method cannot enter source preparation')
        if row['case']['selection']['method_id'] != row['method']:
            raise ValueError('Remaining row changes its method identity')
        if row['case']['selection']['primary_paper_id'] != row['paper']['paper_id']:
            raise ValueError('Remaining row changes its assigned publication')
    return rows, ids


def role_correction_candidate(correction, pid, repository=None):
    """Reopen the exact source-preparer report and cached original before use."""
    proposed = correction['proposed_document']
    if proposed['identity'].get('pmid') != pid.removeprefix('pmid-'):
        return None
    repository = repository or Path(__file__).resolve().parents[2]
    report = Path(correction['inspection_report']['path'])
    if not report.is_absolute():
        relative = str(report).removeprefix('sequencing-technology-atlas/')
        report = repository / relative
        if not report.resolve().is_relative_to(repository.resolve()):
            raise ValueError('Role-correction report escapes its repository')
    original = Path(correction['original']['path'])
    if (sha(report) != correction['inspection_report']['sha256']
            or sha(original) != correction['original']['sha256']):
        raise ValueError('Inspected role correction bytes changed')
    return Document.model_validate(proposed), original.parent, report


def new_primary_rendition(source, parents, pid):
    """Different readable evidence may inform an unresolved request; same text cannot."""
    previous = [p for p in parents.values() if p.get('content_level') == 'fulltext'
                and p.get('document_role') != 'supplement'
                and str(p.get('pmid') or '') == pid.removeprefix('pmid-')]
    return bool(previous) and all(p.get('text') != source['text'] for p in previous)


def api_source(publication,paper,output):
    result=acquire_pmc_fulltext(publication,output)
    write(output/'oai_outcome.json',result)
    if result['status']!='fulltext_acquired':
        raise ValueError('Official PMC source unavailable: '+str(result))
    original=output/result['path'];text=output/result['text_path']
    expected=Identity(pmid=paper['paper_id'].removeprefix('pmid-'),doi=next(iter(paper.get('doi',[])),None),title=paper['title'])
    identity=_article_identity(inspect_jats(original.read_bytes())['identity'],expected)
    paths=[original,text,output/result['blocks_path'],output/result['original_response']['path'],output/'oai_outcome.json']
    return {'kind':'text','content_level':'fulltext','document_role':'primary_article',
        'pmid':expected.pmid,'doi':paper.get('doi',[]),'title':paper['title'],
        'url':result['original_response']['request_url'],'path':str(text),'text':text.read_text(),'sha256':sha(text),
        'original':{'path':str(original),'sha256':sha(original)},
        'custody_files':[{'path':str(p),'sha256':sha(p)} for p in paths],
        'identity_check':identity,'association_status':'Exact assigned publication; article subtree derived from preserved official PMC OAI response. Scientific scope and sufficiency require fresh review.'}


async def execute(args):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Run source preparation and models on Qiu Slurm')
    if not 1 <= args.workers <= 50:
        raise ValueError('Select 1–50 bounded independent method workers')
    manifest=read(args.manifest)
    # Exclude any method already dispatched since the manifest was frozen.
    scheduled=set()
    for p in (Path(manifest['prior_run'])/'retries').glob('*/extraction/state.json'):
        scheduled.update(read(p)['cards'])
    rows, ids = validate_remaining_scope(manifest, scheduled)
    args.output.mkdir(parents=True,exist_ok=False)
    state={'status':'preparing','methods':{},'scope':ids,'method_workers':args.workers,
           'new_finder_calls':0,'manifest_sha256':sha(args.manifest)}
    def checkpoint():write(args.output/'state.json',state)
    checkpoint()
    metadata=args.output/'pubmed.xml'
    pubids=sorted({r['paper']['paper_id'].removeprefix('pmid-') for r in rows})
    try:
        receipt=await asyncio.to_thread(fetch,'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?'+urlencode({'db':'pubmed','id':','.join(pubids),'retmode':'xml'}),metadata)
        write(args.output/'pubmed_retrieval.json',receipt);publications=parse_pubmed(metadata.read_bytes())
    except Exception as exc:
        publications={};write(args.output/'metadata_failure.json',{'reason':str(exc)})
    groups={}
    for row in rows:groups.setdefault(row['paper']['paper_id'],[]).append(row)
    role_corrections=read(args.role_corrections) if args.role_corrections else []
    if not isinstance(role_corrections, list):
        raise ValueError('Role corrections must be an explicit list of inspected candidates')
    source_release=Path(manifest['source_release'])
    existing=read(source_release/'recovery_02/registered_additions.json')
    semaphore=asyncio.Semaphore(args.workers)
    async def one(row,additions,overrides):
        async with semaphore:
            mid=row['method'];folder=args.output/'extractions'/mid;folder.mkdir(parents=True)
            write(folder/'selected.json',{'methods':[row['case']['selection']],
                'reason_for_fresh_extraction':'Targeted remaining-case preparation with new verified evidence; earlier outcomes unchanged.'})
            write(folder/'additions.json',{mid:additions})
            state['methods'][mid]={'status':'extracting','path':str(folder/'extraction')};checkpoint()
            try:
                result=await extractor.execute(args.library,args.vault,folder/'selected.json',folder/'extraction',1,
                    additions_path=folder/'additions.json',primary_overrides={mid:overrides},
                    max_revisions=2,isolate_source_failures=True,stop_on_first_issue=False)
                state['methods'][mid].update(status='completed',result=result)
            except Exception as exc:state['methods'][mid].update(status='failed',reason=str(exc))
            checkpoint()
    tasks=[]
    for pid,group in groups.items():
        try:
            folder=args.output/'publications'/pid;folder.mkdir(parents=True)
            first=group[0];registry_path=Path(first['publication_directory'])/'finder/source_registry.json'
            parents=read(registry_path);verify_registry_files(parents)
            extra={};primary=None;outcomes=[];paper=first['paper']
            publication=publications.get(pid.removeprefix('pmid-'))
            if publication and publication.get('identifiers',{}).get('pmc'):
                try:
                    primary=await asyncio.to_thread(api_source,publication,paper,folder/'oai')
                    if new_primary_rendition(primary, parents, pid):
                        extra['paper:'+pid+':official-recovered:'+primary['sha256'][:16]]=primary
                    parents['recovered-primary']=primary
                    outcomes.append({'route':'official_oai','status':'registered'})
                except Exception as exc:outcomes.append({'route':'official_oai','status':'held','reason':str(exc)})
            documents=(first.get('findings') or {}).get('documents',[])
            candidates=[(Document.model_validate(d),Path(first['publication_directory'])/'documents'/d['id'],None) for d in documents]
            for correction in role_corrections:
                candidate=role_correction_candidate(correction,pid)
                if candidate:candidates.append(candidate)
            candidates.sort(key=lambda item:item[0].role!='primary_paper')
            for doc,old,inspection in candidates:
                receipts=old/'retrieval.json'
                if not receipts.exists():continue # No new request to an unchanged failed download.
                transport=read(receipts)
                originals=[p for p in old.glob('original.*') if p.is_file() and sha(p)==transport['sha256']]
                if len(originals)!=1:
                    outcomes.append({'document':doc.id,'status':'held','reason':'Need one cached original matching the preserved retrieval receipt'})
                    continue
                original=originals[0]
                try:
                    source=await asyncio.to_thread(register,doc,paper,folder/'documents'/doc.id,
                        downloader=cached_fetch(original,transport),parent_sources=parents)
                    source['custody_files'] += [{'path':str(original),'sha256':sha(original)},{'path':str(receipts),'sha256':sha(receipts)}]
                    if inspection:
                        source['custody_files'].append({'path':str(inspection),'sha256':sha(inspection)})
                        source['source_preparer_qualification']=doc.rationale
                    if doc.role=='primary_paper':
                        if not inspection and new_primary_rendition(source,parents,pid):
                            extra['paper:'+pid+':recovered:'+source['sha256'][:16]]=source
                        parents['registered-primary:'+doc.id]=source
                        if primary is None or inspection:primary=source
                        if inspection:extra['paper:'+pid+':inspected-composite']=source
                    else:extra[doc.role+':'+pid+':'+sha(Path(source['path']))[:16]]=source
                    verify_registry_files({'registered':source})
                    write(folder/'documents'/doc.id/'registered_source.json',source)
                    outcomes.append({'document':doc.id,'status':'registered'})
                except Exception as exc:outcomes.append({'document':doc.id,'status':'held','reason':str(exc)})
            write(folder/'preparation.json',outcomes)
            for row in group:
                mid=row['method'];missing=row['case']['hold']['failure_category']=='evidence_recovery_failed'
                if missing and not primary:
                    state['methods'][mid]={'status':'held','reason':'Assigned primary still unavailable; inspect preparation receipts.'}
                elif not extra and not (missing and primary):
                    state['methods'][mid]={'status':'held','reason':'No new registered evidence for the outstanding scientific request.'}
                else:
                    tasks.append(asyncio.create_task(one(row,{**existing.get(mid,{}),**extra},{pid:primary} if missing else {})))
                    state['methods'][mid]={'status':'ready_for_extraction'}
            checkpoint()
        except Exception as exc:
            for row in group:
                state['methods'][row['method']]={'status':'preparation_failed','reason':f'{type(exc).__name__}: {exc}'}
            checkpoint()
    await asyncio.gather(*tasks)
    state['status']='finished';state['counts']=dict(Counter(r['status'] for r in state['methods'].values()));checkpoint()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','library','vault','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--workers',type=int,choices=range(1,51),default=50)
    p.add_argument('--role-corrections',type=Path)
    asyncio.run(execute(p.parse_args()))

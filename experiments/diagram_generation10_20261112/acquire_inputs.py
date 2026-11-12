"""Acquire ten exact post-2016 assay papers for a diagram-generation experiment.

No model calls or atlas/workflow extraction. Original bytes and readable text are
registered first; unavailable candidates remain explicit and are not substituted.
"""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
from urllib.parse import urlencode

from genomics_discovery.illumina.extraction import read, write, sha, verify_registry_files
from genomics_discovery.illumina.sources import fetch, parse_pubmed
from genomics_discovery.sources.acquisition.models import Document
from genomics_discovery.sources.acquisition.downloads import Download
from genomics_discovery.sources.acquisition.runtime import _links
from experiments.card_methods_full_20261112.located_documents import register
from experiments.evidence_recovery_20261112.prepare_remaining import api_source


def curl_download(url, directory):
    path=directory/('download_'+str(len(list(directory.glob('download_*')))))
    receipt=fetch(url,path,timeout=60)
    write(path.with_suffix('.json'),receipt)
    return Download(path.read_bytes(),receipt['final_url'],'')


def stage_source(source, folder, label, role):
    verify_registry_files({'source':source})
    original=source.get('original')
    if not original:
        original=next((p for p in source['custody_files'] if Path(p['path']).suffix.lower() in {'.xml','.nxml','.pdf','.html'}),None)
    if not original or sha(Path(original['path']))!=original['sha256']:
        raise ValueError('No unchanged original document accompanies the readable source')
    dest=folder/'inputs'/label;dest.mkdir(parents=True)
    text=dest/'reading.txt';shutil.copyfile(source['path'],text)
    orig=dest/('original'+Path(original['path']).suffix);shutil.copyfile(original['path'],orig)
    write(dest/'SOURCE_RECEIPT.json',{'source_registration':source,'copied_readable_sha256':sha(text),
        'copied_original_sha256':sha(orig),'model_calls':0,
        'boundary':'Source acquisition and rendition only; no scientific extraction or diagram assessment.'})
    return {'kind':'text','content_level':'fulltext','document_role':role,'path':str(text),'sha256':sha(text),
        'title':source['title'],'url':source.get('url'),'pmid':source.get('pmid'),'doi':source.get('doi'),
        'original':{'path':str(orig),'sha256':sha(orig)},
        'custody_files':[{'path':str(p),'sha256':sha(p)} for p in [text,orig,dest/'SOURCE_RECEIPT.json']]}


def prepare(args):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Source acquisition belongs in a Qiu Slurm allocation')
    pool=read(args.candidates);args.output.mkdir(parents=True,exist_ok=False)
    manifest={'format_version':'assay_diagram_sources/1','methods':[],'model_calls_made':0,
        'candidate_pool_sha256':sha(args.candidates),'selection':'First ten verified ready original papers in the recorded priority order. This is a demonstration set, not a novelty benchmark.',
        'target_illumina_diagrams_provided':False,'unavailable_candidates':[]}
    work=args.output/'acquisition';work.mkdir()
    candidates=pool['methods']
    if pool['required_ready_methods']!=10 or len({c['method_id'] for c in candidates})!=len(candidates) or len({c['doi'] for c in candidates})!=len(candidates):
        raise ValueError('Require ten ready targets drawn from distinct explicit method/paper candidates')
    if any(c['publication_year']<2017 for c in candidates):
        raise ValueError('Every candidate must be post-2016')
    for candidate in candidates:
        if not candidate.get('pmid'):
            try:
                path=work/(candidate['method_id']+'_doi_lookup.json')
                fetch('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?'+urlencode({'db':'pubmed','term':candidate['doi']+'[DOI]','retmode':'json','retmax':5}),path,timeout=45)
                ids=read(path)['esearchresult']['idlist']
                if len(ids)!=1:raise ValueError('DOI lookup must identify exactly one PubMed paper')
                candidate['pmid']=ids[0]
            except Exception as exc:candidate['metadata_error']=str(exc)
    known=[c['pmid'] for c in candidates if c.get('pmid')]
    metadata=work/'pubmed.xml';publications={}
    try:
        receipt=fetch('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?'+urlencode({'db':'pubmed','id':','.join(known),'retmode':'xml'}),metadata,timeout=75)
        write(work/'pubmed_retrieval.json',receipt);publications=parse_pubmed(metadata.read_bytes())
    except Exception as exc:write(work/'metadata_error.json',{'error':str(exc)})
    for candidate in candidates:
        if len(manifest['methods'])==pool['required_ready_methods']:break
        mid=candidate['method_id'];folder=args.output/'methods'/mid;folder.mkdir(parents=True)
        outcomes=[];missing=[];registry={};primary=None;pub=publications.get(candidate.get('pmid'))
        expected={'paper_id':'pmid-'+str(candidate.get('pmid')),'title':pub['title'] if pub else '', 'doi':[candidate['doi']]}
        try:
            if candidate.get('cached_registry'):
                cached=read(Path(candidate['cached_registry']));verify_registry_files(cached)
                primary=next(s for s in cached.values() if s.get('document_role')!='supplement' and str(s.get('pmid'))==candidate['pmid'])
                if candidate['doi'] not in re.sub(r'\s+','',primary['text']):raise ValueError('Cached target text does not contain its declared DOI')
                registry={k:v for k,v in cached.items() if str(v.get('pmid'))==candidate['pmid']}
                outcomes.append({'route':'cached_registration','status':'ready','registry_sha256':sha(Path(candidate['cached_registry']))})
            else:
                if not pub:raise ValueError('Authoritative PubMed metadata unavailable')
                if set(pub.get('identifiers',{}).get('doi',[]))!={candidate['doi']}:
                    raise ValueError('PubMed DOI differs from the explicit candidate identity')
                try:
                    primary=api_source(pub,expected,folder/'oai')
                    outcomes.append({'route':'official_pmc_oai','status':'ready'})
                except Exception as exc:outcomes.append({'route':'official_pmc_oai','status':'held','reason':str(exc)})
                alternatives=[]
                pmc=next(iter(pub.get('identifiers',{}).get('pmc',[])),None)
                if pmc:alternatives.append(('jats','https://www.ebi.ac.uk/europepmc/webservices/rest/'+pmc+'/fullTextXML'))
                alternatives.extend((d['format'],d['url']) for d in candidate.get('observed_primary_urls',[]))
                alternatives.append(('html','https://doi.org/'+candidate['doi']))
                for number,(fmt,url) in enumerate(alternatives):
                    if primary:break
                    try:
                        doc=Document.model_validate({'id':'primary-'+str(number),'url':url,'role':'primary_paper','format':fmt,
                            'identity':{'pmid':candidate['pmid'],'doi':candidate['doi'],'title':pub['title']},
                            'rationale':'Exact assigned assay paper; original identity and article body must pass host checks.'})
                        downloads=folder/('downloads_'+str(number));downloads.mkdir()
                        primary=register(doc,expected,folder/('primary_'+str(number)),downloader=lambda u:curl_download(u,downloads))
                        outcomes.append({'route':url,'status':'ready'})
                    except Exception as exc:outcomes.append({'route':url,'status':'held','reason':str(exc)})
                if not primary:raise ValueError('No registered original primary document is available')
                registry={'paper:'+candidate['pmid']:primary}
            expected['title']=primary['title']
            # Inspect the paper's actual publisher links; no supplement filename
            # is invented. Any unavailable attachment remains explicit.
            try:
                landing=folder/'publisher.html';receipt=fetch('https://doi.org/'+candidate['doi'],landing,timeout=45)
                write(folder/'publisher_retrieval.json',receipt)
                links=sorted(u for u in _links(landing.read_bytes(),receipt['final_url']) if ('/MediaObjects/' in u or '/suppl_file/' in u or '.DC1/' in u) and u.lower().split('?')[0].endswith(('.pdf','.docx','.doc')))
                if not links:missing.append({'reason':'No procedural attachment URL recovered from publisher response; this does not establish that no supplement exists.'})
                for number,url in enumerate(links[:8]):
                    try:
                        fmt=Path(url.split('?')[0]).suffix.lstrip('.')
                        doc=Document.model_validate({'id':'supp-'+str(number),'url':url,'role':'supplement','format':fmt,
                            'identity':{'pmid':candidate['pmid'],'doi':candidate['doi'],'title':primary['title']},
                            'linked_from':receipt['final_url'],'rationale':'Original procedural attachment linked by the assigned publisher page.'})
                        downloads=folder/('supp_downloads_'+str(number));downloads.mkdir()
                        registry['supplement:'+str(number)]=register(doc,expected,folder/('supplement_'+str(number)),
                            downloader=lambda u:curl_download(u,downloads),parent_sources={'primary':primary})
                    except Exception as exc:missing.append({'url':url,'reason':str(exc)})
                if len(links)>8:missing.append({'reason':'Additional attachment URLs retained in publisher page but not downloaded in this bounded source preparation.','urls':links[8:]})
            except Exception as exc:missing.append({'reason':'Publisher supplement inventory unavailable','detail':str(exc)})
            primary_id='paper:'+candidate['pmid'];sources={};sources[primary_id]=stage_source(primary,folder,'primary','primary')
            for sid,source in registry.items():
                if source is primary or source.get('document_role')!='supplement':continue
                sources[sid]=stage_source(source,folder,'supplement_'+str(len(sources)),'supplement')
            method={'method_id':mid,'name':candidate['name'],'publication_year':candidate['publication_year'],
                'publication_year_basis':candidate.get('publication_year_basis','Journal/online year from the declared original-paper identity; raw authoritative dates retained separately.'),
                'primary_source_id':primary_id,'pmid':candidate['pmid'],'doi':candidate['doi'],'paper_title':primary['title'],
                'publication_dates':pub.get('publication_dates',[]) if pub else candidate.get('publication_dates',[]),
                'sources':sources,'missing_supplements':missing,'target_illumina_diagrams_provided':False}
            write(folder/'method.json',method);manifest['methods'].append(method)
        except Exception as exc:manifest['unavailable_candidates'].append({'method_id':mid,'reason':str(exc),'outcomes':outcomes})
        write(folder/'ACQUISITION.json',{'candidate':candidate,'outcomes':outcomes,'missing_supplements':missing,'model_calls':0})
        write(args.output/'input_manifest.json',manifest)
        print(mid+': '+('ready' if any(m['method_id']==mid for m in manifest['methods']) else 'held'),flush=True)
    manifest['ready_count']=len(manifest['methods']);manifest['required_count']=pool['required_ready_methods']
    write(args.output/'input_manifest.json',manifest)
    if manifest['ready_count']!=manifest['required_count']:
        raise ValueError('Fewer than ten verified papers are ready; no content-model job should start')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidates',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();prepare(args)

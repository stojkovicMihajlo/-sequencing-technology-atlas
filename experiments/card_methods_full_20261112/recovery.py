"""Bounded source recovery; no model calls or scientific admission.

Promoted from the earlier recovery draft's acquisition functions. The draft's
native execution loop is deliberately not used: recovery and extraction remain
separate stages with an inspectable manifest between them.
"""
import copy
import re
from pathlib import Path
from urllib.parse import urlencode, quote
from genomics_discovery.illumina.extraction import read, write, sha, verify_registry_files
from genomics_discovery.illumina.sources import fetch, parse_pubmed, acquire_pmc_fulltext
from genomics_discovery.sources.document_manifest import inspect_jats
from experiments.diagram_paper_pilot_20261106.recover_dependencies import recover_supplement
from scripts.illumina.technology_sources.recover_selected import acquire_one
from scripts.illumina.technology_sources.article_identity import normalized_title

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
        except (ValueError, SyntaxError):
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
        if not any(str(h).lower().endswith('.pdf') for h in item.get('hrefs', [])):
            report.append({'status':'unsupported_attachment_format', 'attachment':item});continue
        if 'supplement:'+pmid+':'+key in registry:
            report.append({'status':'already_registered','attachment':item});continue
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

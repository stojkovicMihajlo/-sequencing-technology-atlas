"""Deterministically stage existing native judgments; no models or acquisition.

Preserves both screens and their disagreements. Identity lookup is an exact
name/own-publication comparison against a pinned existing inventory, never an
automatic new-method admission or a semantic alias/version decision.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from genomics_discovery.illumina.sources import parse_pubmed
from intake import run
from live_pilot import validate_decisions


SCOPES = {
    '28079019': ('cut-and-run','CUT&RUN',['Epigenome'],'DNA-Protein Interactions', 'Original targeted nuclease-release sequencing workflow'),
    '33098772': ('share-seq','SHARE-seq',['Epigenome','Transcriptome'],'Chromatin Accessibility','Original same-cell accessibility and RNA profiling workflow'),
    '28095828': ('tn5-059-library-preparation','Tn5-059 library preparation',['Genome'],'Library Preparation','Engineered-Tn5-supported genome/exome library performance; distinct assay identity remains under review'),
    '25079858': ('picelli-2014-low-input-tagmentation','Low-input tagmentation (Picelli 2014)',['Genome','Transcriptome'],'Library Preparation','Tagmentation reaction conditions and low-input library preparation; older predecessor identity/need remains unverified'),
    '29472610': ('scnmt-seq','scNMT-seq',['Epigenome','Transcriptome'],'Chromatin Accessibility','Original 2018 joint accessibility/methylation/transcription assay'),
}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,data):
    with Path(path).open('x') as stream:json.dump(data,stream,indent=2,ensure_ascii=False);stream.write('\n')
def normal(text):return ''.join(c for c in text.casefold() if c.isalnum())
def normalize_doi(text):return re.sub(r'^https?://(?:dx\.)?doi\.org/','',text or '',flags=re.I).casefold()


def exact_inventory_lookup(inventory, pmid, doi, names):
    wanted={normal(n) for n in names};matches=[]
    for key,method in inventory['methods'].items():
        labels=[method.get('name',''),key,*method.get('aliases',[])]
        name_hits=sorted({label for label in labels if normal(label) in wanted})
        references=[]
        for ref in method.get('method_references',[]):
            ds=ref.get('doi') or []
            if isinstance(ds,str):ds=[ds]
            if str(ref.get('pmid') or '')==pmid or ref.get('paper_id')=='pmid-'+pmid or normalize_doi(doi) in {normalize_doi(d) for d in ds}:
                references.append({k:ref.get(k) for k in ('reference_id','pmid','paper_id','role','label')})
        if name_hits or references:
            matches.append({'method_id':key,'name':method['name'],'entry_type':method['entry_type'],
                            'matching_names':name_hits,'matching_publication_references':references})
    return matches


def import_judgments(observed, inventory_path, inventory_sha256, output):
    observed=Path(observed).resolve();inventory_path=Path(inventory_path).resolve();output=Path(output).resolve()
    if sha(inventory_path)!=inventory_sha256:raise ValueError('Pinned canonical inventory changed')
    inventory=json.loads(inventory_path.read_text());transfer=json.loads((observed/'OBSERVATION_MANIFEST.json').read_text())
    for item in transfer['files']:
        if sha(observed/item['relative_path'])!=item['sha256']:raise ValueError('Observed original changed: '+item['relative_path'])
    raw_path=observed/'metadata/pubmed.xml';raw=parse_pubmed(raw_path.read_bytes())
    journal_years={record.findtext('./MedlineCitation/PMID'):record.findtext('./MedlineCitation/Article/Journal/JournalIssue/PubDate/Year')
                   for record in ET.fromstring(raw_path.read_bytes())}
    papers=json.loads((observed/'metadata/papers.json').read_text())['papers']
    if {p['pmid'] for p in papers}!=set(raw):raise ValueError('Raw/presented paper sets differ')
    for paper in papers:
        if paper['title']!=raw[paper['pmid']]['title'] or paper['abstract']!=raw[paper['pmid']]['abstract']:
            raise ValueError('Presented abstract differs from raw PubMed source')
    output.mkdir(parents=True,exist_ok=False);(output/'metadata').mkdir()
    metadata={};lookups={}
    for pmid,record in raw.items():
        own_dois=record['identifiers'].get('doi',[])
        if len(own_dois)!=1:raise ValueError('Own paper DOI unresolved: '+pmid)
        year=journal_years.get(pmid)
        if year is None or not year.isdigit():raise ValueError('No authoritative journal Year element: '+pmid)
        year=int(year)
        identity={'pmid':pmid,'doi':own_dois[0],'title':record['title'],'publication_year':year,'date_basis':'journal_issue'}
        sections={f'Abstract:{i+1}'+(':'+a['label'] if a['label'] else ''):a['text'] for i,a in enumerate(record['abstract'])}
        path=output/'metadata'/('pmid-'+pmid+'.json')
        write(path,{'record_kind':'raw_metadata','source_url':'https://pubmed.ncbi.nlm.nih.gov/'+pmid+'/',
                    'identity':identity,'observed_passages':sections,
                    'original_metadata':{'path':str(raw_path),'sha256':sha(raw_path),'format':'pubmed_xml'},
                    'original_retrieval_receipt':{'path':str(observed/'metadata/retrieval.json'),'sha256':sha(observed/'metadata/retrieval.json')},
                    'limits':'Controller-derived view of pinned original PubMed metadata. No fulltext or scientific admission.'})
        metadata[pmid]=(identity,sections,{'path':'metadata/'+path.name,'sha256':sha(path)})
        names=SCOPES[pmid][0:2] if pmid in SCOPES else [record['title']]
        lookups[pmid]=exact_inventory_lookup(inventory,pmid,own_dois[0],names)
    inventory_pin={'path':str(inventory_path),'sha256':inventory_sha256}
    summaries={}
    for role in ('author','independent_reviewer'):
        artifact=observed/'screens'/role/'artifact.json';decisions=validate_decisions(json.loads(artifact.read_text()),papers)
        rows=[]
        for native in decisions['papers']:
            pmid=native['pmid'];identity,sections,pin=metadata[pmid];evidence=[];criteria={}
            for i,(criterion,judgment) in enumerate(native['criteria'].items()):
                source_section=next(key for key,text in sections.items() if judgment['abstract_quote'] in text)
                evidence.append({'id':'e'+str(i+1),'section':source_section,'passage':judgment['abstract_quote'],
                                 'source_url':'https://pubmed.ncbi.nlm.nih.gov/'+pmid+'/',
                                 'locator':'PubMed '+source_section})
                criteria[criterion]={'status':judgment['status'],'rationale':judgment['rationale'],'evidence_ids':[evidence[-1]['id']]}
            screen={'classification':native['classification'],'publication_role':native['publication_role'],
                    'reader':'Native '+role+'; gpt-6.1-sol/xhigh; job52176596',
                    'reviewed_at':'2026-11-13','inspection_level':'primary_abstract_only',
                    'rationale':native['rationale'],'criteria':criteria}
            if native['focused_question']:
                screen.update(focused_review_question=native['focused_question'],focused_review_targets=['Only the Methods/figures needed for the recorded question; acquisition not authorized by import'])
            proposals=[];associations=[]
            if native['classification']=='assay':
                candidate_id,name,domains,sub,scope=SCOPES[pmid]
                exact=[m for m in lookups[pmid] if m['matching_names'] and m['matching_publication_references'] and m['entry_type']=='named_method']
                status='existing_version' if len(exact)==1 else 'pending'
                match={'status':status,'basis':'Exact name and own-publication comparisons against pinned canonical_inventory; semantic aliases and distinct versions remain a separate review.',
                       'existing_method_ids':[m['method_id'] for m in exact],'inventory_pin':inventory_pin,
                       'checked_names':[candidate_id,name],'checked_identifiers':[{'pmid':pmid,'doi':identity['doi']}],
                       'observed_matches':lookups[pmid]}
                placement={'category':domains[0],'subclassification':sub,'classification_origin':'curator_proposal',
                           'basis':'Administrative proposal from measured subject, not an original Illumina placement.',
                           'new_category_candidate':({'name':'Library Preparation','rationale':'General library construction does not fit the observed Genome leaf categories; preserve as a proposed new subclass pending taxonomy review.'} if sub=='Library Preparation' else None)}
                proposals.append({'candidate_id':candidate_id,'name':name,'implementation_scope':scope,
                    'scope_track':'missing_predecessor_candidate' if identity['publication_year']<2017 else 'post_2016',
                    'domain_tags':domains,'placement':placement,'identity_review':match})
                associations.append({'target':exact[0]['method_id'] if len(exact)==1 else candidate_id,
                                     'role':'assay_primary','basis':'Provisional source association for the native assay screen; does not establish a distinct atlas entry.'})
            if pmid=='28095828':
                associations.append({'target':'nextera-library-preparation','role':'component_dependency',
                    'basis':'Separate curator interpretation: earlier targeted Methods observation reports standard Nextera library preparation using the engineered enzyme. Native assay-improvement judgment remains unchanged; a distinct assay version is unestablished.'})
            if native['classification']=='software_only':
                associations.append({'target':'chromVAR analysis context','role':'component_dependency',
                    'basis':'Original computational methods publication; potential downstream analysis source, not an experimental assay entry.'})
            rows.append({'publication_id':'pmid-'+pmid,'identity':identity,'metadata_receipt':pin,'evidence':evidence,
                         'screening':screen,'method_proposals':proposals,'source_associations':associations,
                         'native_artifact':{'path':str(artifact),'sha256':sha(artifact)},
                         'native_judgment_unchanged':copy.deepcopy(native)})
        path=output/(role+'.json');write(path,{'format_version':'method_intake/2','publications':rows,
            'boundary':'Deterministic import of existing blinded judgments. No expected answer correction, fulltext acquisition or new method admission.'})
        summaries[role]=run(path);write(output/(role+'_staging.json'),summaries[role])
    write(output/'IDENTITY_RECONCILIATION.json',{'inventory':inventory_pin,'method_and_resource_entries_checked':len(inventory['methods']),
        'publication_matches':lookups,'boundary':'Exact name/own-publication lookup only; absence is not proof of novelty. Only a concordant exact scNMT name+publication match is routed as an existing identity; other assay proposals stay pending.'})
    return summaries


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observed',type=Path,required=True);parser.add_argument('--inventory',type=Path,required=True)
    parser.add_argument('--inventory-sha256',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=import_judgments(args.observed,args.inventory,args.inventory_sha256,args.output)
    print(json.dumps({k:{'publications_screened':v['publications_screened'],'extraction_authorizations':0} for k,v in result.items()}))

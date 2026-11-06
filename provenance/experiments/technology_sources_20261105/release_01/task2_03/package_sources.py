"""Write inspectable per-method source packages, not extracted protocol graphs."""
import argparse
import csv
from html import escape
import json
import os
from pathlib import Path
import subprocess

from scripts.illumina.technology_sources.inventory import read, sha
from scripts.illumina.technology_sources.materialize import link, write


def table(path, rows):
    with path.open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n')
        w.writeheader();w.writerows(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--source-root',type=Path,required=True)
    a=parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Batch source preparation belongs in Slurm')
    index=read(a.root/'canonical_inventory.json')
    state=read(a.source_root/'collection.json')
    reconciliation=read(a.root/'SOURCE_RECONCILIATION.json')
    rows=[]; previews={}; missing={}
    for mid,m in index['methods'].items():
        if m['entry_type']!='named_method':continue
        folder=a.root/m['directory']
        comparison=reconciliation['methods'][mid]
        papers=[]
        for ref in m['method_references']:
            if ref['role']!='method_reference':continue
            pid=ref.get('paper_id')
            if pid and any(p['paper_id']==pid for p in papers):continue
            p=state['papers'].get(pid,{})
            item={'paper_id':pid,'title':ref.get('resolved_title') or ref.get('citation'),
                  'association_basis':ref['association_basis'],'availability':ref['availability'],
                  'url':ref.get('url') or p.get('url'),'doi':p.get('metadata',{}).get('identifiers',{}).get('doi',[]),
                  'publication_type':ref.get('publication_type') or p.get('publication_type'),
                  'publication_role':ref.get('publication_role') or p.get('publication_role'),
                  'scope_note':ref.get('scope_note') or p.get('scope_note'),
                  'paper_scope_review':ref.get('paper_scope_review') or p.get('paper_scope_review'),
                  'files':ref['cached_files'],'held_files':ref['held_files']}
            papers.append(item)
            if pid and item['availability']!='fulltext_acquired':
                missing.setdefault(pid,{'paper_id':pid,'title':item['title'],'doi':'; '.join(item['doi']),
                    'url':item['url'],'methods':[],'status':item['availability'],
                    'public_retrieval_result':p.get('repository_recovery_status') or p.get('cloud_recovery_status') or p.get('selected_recovery_status','not_recorded')})['methods'].append(mid)
        contexts=[]
        d=m['description']
        if d.get('source_kind')=='illumina_methods_guide_excerpt' and d.get('scope_review'):
            page=int(d['locator'].split()[-1])
            matches=[g for g in m['guide_sources'] if g['pdf_page']==page and g['pdf_sha256']==d['source_pdf_sha256']]
            if not matches:raise ValueError('No registered guide context for '+mid)
            g=matches[0];key=(g['pdf_sha256'],page)
            if key not in previews:
                pdf=a.source_root/g['pdf_path']
                if sha(pdf)!=g['pdf_sha256']:raise ValueError('Guide source changed')
                prefix=a.root/'source_previews'/(Path(g['guide']).stem+'-p'+str(page))
                prefix.parent.mkdir(exist_ok=True)
                cmd=['pdftoppm','-f',str(page),'-l',str(page),'-singlefile','-r','144','-png',str(pdf),str(prefix)]
                subprocess.run(cmd,check=True,capture_output=True,timeout=90)
                image=prefix.with_suffix('.png')
                previews[key]={'path':str(image.relative_to(a.root)),'sha256':sha(image),
                    'pdf_sha256':g['pdf_sha256'],'pdf_page':page,'command':cmd,
                    'role':'Complete source page; not an isolated or scientifically verified method diagram.'}
            record=previews[key]
            target=folder/'guides'/Path(record['path']).name
            link(a.root/record['path'],target)
            contexts.append({**record,'method_link':str(target.relative_to(folder))})
        native=[d for d in comparison['depictions'] if d['kind']=='website_card']
        diagram_status=('original_card_asset_available; interpretation_unreviewed' if native else
                        'complete_guide_context_and_original_poster_available; diagram_scope_unreviewed' if contexts else
                        'original_poster_available; derived_crop_not_approved')
        package={'method_id':mid,'name':m['name'],'source_coverage':comparison['source_coverage'],
                 'directory':m['directory'],'description':d,'placements':m['placements'],
                 'original_classifications':m['classifications'],'source_depictions':comparison['depictions'],
                 'complete_guide_contexts':contexts,'diagram_status':diagram_status,
                 'method_papers':papers,'source_scope_note':m.get('source_curation_review',{}).get('scope_note'),
                 'workflow_extraction_authorized':False,
                 'paper_confirmation_required':'Operations, order, material routing and method-specific changes must be confirmed from the paper/protocol during extraction.'}
        write(folder/'source_package.json',package)
        body='<h2>Source package</h2><p><a href="source_package.json">Description, classification, diagrams and exact paper availability</a></p>'
        for context in contexts:
            body+='<p>Complete Illumina guide page; it may include related methods. It is not an approved isolated workflow.</p><a href="'+escape(context['method_link'])+'"><img loading="lazy" src="'+escape(context['method_link'])+'"></a>'
        html=(folder/'index.html').read_text()
        (folder/'index.html').write_text(html.replace('</body>',body+'</body>'))
        fulltexts=sum(p['availability']=='fulltext_acquired' for p in papers)
        rows.append({'method_id':mid,'name':m['name'],'directory':m['directory'],
            'source_coverage':comparison['source_coverage'],'categories':'; '.join(p['category']+' > '+p['subclassification'] for p in m['placements']),
            'description_source':d.get('source_kind',d['status']),
            'assigned_paper_ids':'; '.join(p['paper_id'] or p['url'] or '' for p in papers),
            'downloaded_method_papers':fulltexts,'missing_assigned_papers':sum(p['availability']!='fulltext_acquired' for p in papers),
            'guide_context_pages':len(contexts),'diagram_status':diagram_status,
            'source_package_status':'method_text_available' if fulltexts else 'method_fulltext_still_needed',
            'workflow_review':'not_performed'})
    table(a.root/'SOURCE_PACKAGE_CHECKLIST.csv',rows)
    poster=[r for r in rows if r['source_coverage']=='poster_only']
    table(a.root/'POSTER_ONLY_SOURCE_PACKAGES.csv',poster)
    if missing:table(a.root/'MANUAL_PAPER_RECOVERY.csv',[{**r,'methods':'; '.join(sorted(set(r['methods'])))} for r in missing.values()])
    summary={'named_method_packages':len(rows),'poster_only_packages':len(poster),
        'methods_with_downloaded_method_paper':sum(r['downloaded_method_papers']>0 for r in rows),
        'methods_still_without_downloaded_method_paper':sum(r['downloaded_method_papers']==0 for r in rows),
        'poster_only_with_downloaded_method_paper':sum(r['downloaded_method_papers']>0 for r in poster),
        'poster_only_without_downloaded_method_paper':sum(r['downloaded_method_papers']==0 for r in poster),
        'full_uncropped_guide_pages_rendered':len(previews),'source_extraction_performed':False,
        'remaining':'Undownloaded papers have exact records in MANUAL_PAPER_RECOVERY.csv. Original poster diagrams remain available but have not all been interpreted/verified.'}
    write(a.root/'TASK2_SUMMARY.json',summary)
    write(a.root/'GUIDE_CONTEXT_PROVENANCE.json',list(previews.values()))
    text=json.dumps(summary,indent=2)
    (a.root/'READ_FIRST.txt').write_text('ILLUMINA METHOD SOURCE LIBRARY\n\n'+text+'\n\nOpen index.html. SOURCE_PACKAGE_CHECKLIST.csv has one row per named method.\nPOSTER_ONLY_SOURCE_PACKAGES.csv is the99-method subset without cards.\nEach method folder has source_package.json and a readable index.html.\nThese are curated source packages, not extracted workflows or evolutionary edges.\n')
    root_html=(a.root/'index.html').read_text()
    notice='<h2>Task2 source packages</h2><pre>'+escape(text)+'</pre><p><a href="SOURCE_PACKAGE_CHECKLIST.csv">Every method</a> · <a href="POSTER_ONLY_SOURCE_PACKAGES.csv">Poster-only methods</a> · <a href="MANUAL_PAPER_RECOVERY.csv">Missing paper files</a></p>'
    (a.root/'index.html').write_text(root_html.replace('</body>',notice+'</body>'))
    print(text,flush=True)


if __name__=='__main__':main()

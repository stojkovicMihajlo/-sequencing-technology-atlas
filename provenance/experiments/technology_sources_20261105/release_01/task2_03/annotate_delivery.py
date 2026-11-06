"""Attach exact recovery diagnostics and recorded visual scope checks to a view.

Original source bytes and collection hashes remain unchanged. The publication
archive preserves the prior view; this metadata overlay has its own receipt.
"""
import argparse
import csv
import hashlib
from html import escape
import json
import os
from pathlib import Path
import shutil


def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['root','source-root','visual-review','backup']:
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    reviews=read(a.visual_review)
    inventory=read(a.root/'canonical_inventory.json')
    collection=read(a.source_root/'collection.json')
    paper_receipts=read(a.source_root/'REPOSITORY_RECOVERY_RECEIPTS.json')
    before={}
    def save(path,value):
        before[str(path.relative_to(a.root))]=sha(path) if path.exists() else None
        path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    source_reviews=read(a.root/'POSTER_ONLY_REVIEW.json')
    for review in reviews:
        mid=review['method_id'];m=inventory['methods'][mid];folder=a.root/m['directory']
        package=read(folder/'source_package.json')
        contexts=[c for c in package['complete_guide_contexts'] if Path(c['path']).name==review['image_name']]
        if len(contexts)!=1 or sha(a.root/contexts[0]['path'])!=review['image_sha256']:
            raise ValueError('Visual review no longer matches its source image: '+mid)
        contexts[0]['visual_scope_review']=review
        package.setdefault('visual_scope_reviews',[]).append(review)
        save(folder/'source_package.json',package)
        method=read(folder/'method.json')
        for record in [method,m,next(r for r in source_reviews if r['method_id']==mid)]:
            record.setdefault('visual_context_reviews',[]).append(review)
        save(folder/'method.json',method)
        html=folder/'index.html';before[str(html.relative_to(a.root))]=sha(html)
        note='<p><strong>Visual source scope:</strong> '+escape(review['scope_note'])+'</p>'
        html.write_text(html.read_text().replace('</body>',note+'</body>'))
    save(a.root/'POSTER_ONLY_REVIEW.json',source_reviews)
    save(a.root/'canonical_inventory.json',inventory)
    save(a.root/'VISUAL_CONTEXT_REVIEW.json',reviews)
    access={}
    for pid,attempts in paper_receipts.items():
        if collection['papers'].get(pid,{}).get('status')=='fulltext_acquired' or any(r.get('admitted') for r in attempts):continue
        access[pid]={'failed_attempts':[r for r in attempts if r.get('error')],
            'candidate_article_urls':[r['url'] for r in attempts if r.get('stage')=='article_pdf'],
            'meaning':'Acquisition failed through the recorded routes; not evidence that no public copy exists or that the method is scientifically unspecified.'}
    save(a.root/'PAPER_ACCESS_REPORT.json',access)
    path=a.root/'MANUAL_PAPER_RECOVERY.csv'
    rows=list(csv.DictReader(path.open()))
    for r in rows:
        diagnostic=access.get(r['paper_id'],{})
        r['failed_attempt_details']=' | '.join(x.get('stage','')+': '+x['error'].replace('\n',' ') for x in diagnostic.get('failed_attempts',[]))
        if not r['failed_attempt_details'] and not diagnostic.get('candidate_article_urls'):
            r['failed_attempt_details']='No candidate article PDF URL returned by the recorded public metadata routes.'
        r['candidate_article_urls']=' | '.join(diagnostic.get('candidate_article_urls',[]))
        r['recovery_receipts']=os.path.relpath(a.source_root/'REPOSITORY_RECOVERY_RECEIPTS.json',a.root)
    before[path.name]=sha(path)
    with path.open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)
    summary=read(a.root/'TASK2_SUMMARY.json')
    summary['full_guide_pages_visually_inspected']=len({r['image_sha256'] for r in reviews})
    summary['method_workflows_scientifically_verified']=0
    save(a.root/'TASK2_SUMMARY.json',summary)
    receipt={'scope':'Derived metadata only; no source-collection bytes changed.',
             'prior_sha256':before,'visual_review_sha256':sha(a.visual_review),
             'paper_recovery_receipts_sha256':sha(a.source_root/'REPOSITORY_RECOVERY_RECEIPTS.json')}
    save(a.root/'TASK2_ANNOTATION_RECEIPT.json',receipt)
    a.backup.mkdir(exist_ok=False)
    hashes={}
    for name in before:
        source=a.root/name;dest=a.backup/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest);hashes[name]=sha(dest)
    (a.backup/'FILES.json').write_text(json.dumps(hashes,indent=2)+'\n')
    print('Metadata overlay backed up:',len(hashes),'files')


if __name__=='__main__':main()

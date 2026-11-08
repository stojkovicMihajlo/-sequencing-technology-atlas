"""Package ten saved Illumina examples for a manual paper-to-diagram test.
No model calls; original scientific extractions and source image bytes are kept.
Run from the repository with a Python environment containing PyMuPDF.
"""
from pathlib import Path
import csv
import hashlib
import html
import json
import shutil
import struct
import zipfile

import fitz

PROJECT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
OUT = PROJECT/'deliverables/diagram_fewshot_20261108'
VIEWER = PROJECT/'deliverables/illumina_extractions_20261106'
METHODS = ['atac-seq','drop-seq','g-and-t-seq','clickseq','cp-rna-seq',
           'chipmentation','malbac','tif-seq','icshape','bless']
FONT = '/System/Library/Fonts/Supplemental/Arial.ttf'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v): p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def compact(v):
    if isinstance(v,dict):
        return {k:compact(x) for k,x in v.items() if k not in {'evidence','inspected_sources','paper_matches'}}
    if isinstance(v,list): return [compact(x) for x in v]
    return v


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    for name in ['spatial_nt_seq_paper.pdf','spatial_nt_seq_supplement_1.pdf','spatial_nt_seq_supplement_2.pdf']:
        if not (OUT/'target'/name).is_file():
            raise FileNotFoundError('Stage the exact target PDF before packaging: '+name)
    for name in ['examples','prompts','upload','results']:(OUT/name).mkdir(exist_ok=True)
    index=json.loads((VIEWER/'index.json').read_text());byid={r['record_id']:r for r in index['records']}
    manifest={'format_version':'diagram_fewshot_packet/1','example_count':10,
              'model_calls_made':0,'selection':'Ten distinct named methods with a saved supported extraction and original card image; varied explanatory workflows. A development set, not random sampling or certified ground truth.',
              'source_viewer':str(VIEWER.relative_to(PROJECT)),'examples':[]}
    pdf=fitz.open();descriptions=[];workflows=[];cards=[]
    for n,mid in enumerate(METHODS,1):
        row=byid[mid];r=json.loads((VIEWER/row['file']).read_text())
        if r['status']!='supported':raise ValueError('Unsupported selected example: '+mid)
        images=[(sid,s) for sid,s in r['sources'].items() if s.get('kind')=='image']
        if len(images)!=1:raise ValueError('Select the actual method source image explicitly')
        sid,image=images[0];folder=OUT/'examples'/f'{n:02d}_{mid}';folder.mkdir(exist_ok=True)
        original=VIEWER/image['local_asset'];shutil.copyfile(original,folder/'illumina_original.png')
        if sha(original)!=image['sha256']:raise ValueError('Original diagram hash changed')
        # A PDF-backed white page flattens source transparency for upload, preserving
        # the entire original image and its native pixel dimensions. No redraw/crop.
        w,h=struct.unpack('>II',original.read_bytes()[16:24])
        with fitz.open() as white:
            page=white.new_page(width=w,height=h)
            page.draw_rect(page.rect,color=None,fill=(1,1,1));page.insert_image(page.rect,filename=str(original))
            page.get_pixmap(alpha=False).save(folder/'diagram_on_white.png')
        raw=r['candidate'];write(folder/'workflow_original.json',raw)
        simple={'projection':'Portable few-shot view; evidence quotations omitted. See workflow_original.json for the full extraction.',
                'source_extraction_sha256':sha(folder/'workflow_original.json'),'workflow':compact(raw)}
        write(folder/'workflow_compact.json',simple)
        if r.get('workflow_figure'):
            for ext in ['png','svg']:shutil.copyfile(VIEWER/r['workflow_figure'][ext],folder/f'workflow.{ext}')
        catalogue=[s for s in r['sources'].values() if s.get('content_level')=='catalogue']
        description='\n\n'.join(s.get('display_text','') for s in catalogue)
        (folder/'illumina_description.txt').write_text(description+'\n')
        (folder/'explanation.txt').write_text(raw['scope']+'\n')
        pages=r.get('source_comparison',{}).get('pages',[])
        links={'illumina_cards':pages,'original_diagram':image['url'],
               'papers':[{'title':s.get('title'),'url':s.get('url')} for s in r['sources'].values()
                         if s.get('content_level')=='fulltext']}
        write(folder/'source_links.json',links)
        record={'number':n,'record_id':mid,'title':r['title'],'directory':str(folder.relative_to(OUT)),
                'category':r.get('category',[]),'original_diagram_url':image['url'],
                'original_diagram_sha256':sha(original),'on_white_sha256':sha(folder/'diagram_on_white.png'),
                'source_record_sha256':sha(VIEWER/row['file']),'workflow_original_sha256':sha(folder/'workflow_original.json'),
                'source_dimensions':[int(w),int(h)],'source_status':r['status'],
                'preparation':'Original PNG copied byte-for-byte. Upload copy composites transparency onto white at native dimensions; no content crop, redraw or detail recovery.'}
        manifest['examples'].append(record)
        descriptions.append(f'EXAMPLE {n:02d}: {r["title"]}\nILLUMINA DESCRIPTION\n{description}\n\nATLAS EXPLANATION\n{raw["scope"]}\n\nSOURCE\n{image["url"]}\n')
        workflows.append(f'EXAMPLE {n:02d}: {r["title"]}\n'+json.dumps(simple,ensure_ascii=False,separators=(',',':')))
        page=pdf.new_page(width=1200,height=720)
        page.insert_font(fontname='Arial',fontfile=FONT)
        page.insert_text((36,45),f'{n:02d}  {r["title"]}',fontname='Arial',fontsize=25,color=(.09,.16,.21))
        page.insert_text((36,76),'Illumina source workflow | explanatory granularity and visual reference',fontname='Arial',fontsize=13,color=(.3,.36,.41))
        rect=fitz.Rect(36,115,1164,320);page.insert_image(rect,filename=str(folder/'diagram_on_white.png'))
        page.insert_text((36,355),'Assay explanation from the saved workflow',fontname='Arial',fontsize=15,color=(.09,.16,.21))
        for size in [12,11,10,9]:
            free=page.insert_textbox(fitz.Rect(36,375,1164,625),raw['scope'],fontname='Arial',fontsize=size,lineheight=1.35,color=(.15,.2,.24))
            if free>=0:break
        if free<0:raise ValueError('Example explanation does not fit: '+mid)
        page.insert_text((36,655),image['url'],fontname='Arial',fontsize=8,color=(.2,.32,.42))
        page.insert_link({'kind':fitz.LINK_URI,'from':fitz.Rect(36,640,1164,664),'uri':image['url']})
        page.insert_text((36,688),'Original image content retained; white background for transparency. Full descriptions, exact workflow and evidence are separate files.',fontname='Arial',fontsize=10,color=(.35,.4,.45))
        cards.append('<section><h2>'+html.escape(f'{n:02d} {r["title"]}')+'</h2><p><a href="'+html.escape(image['url'])+'">Original source image</a> · <a href="'+str(folder.relative_to(OUT))+'/workflow_original.json">Full extracted workflow JSON</a></p><img src="'+str(folder.relative_to(OUT))+'/diagram_on_white.png"><p>'+html.escape(raw['scope'])+'</p><details><summary>Our extracted workflow figure</summary><a href="'+str(folder.relative_to(OUT))+'/workflow.png"><img src="'+str(folder.relative_to(OUT))+'/workflow.png"></a></details></section>')
    pdf.save(OUT/'upload/illumina_examples.pdf',garbage=4,deflate=True);pdf.close()
    (OUT/'upload/examples_descriptions.txt').write_text('\n\n'.join(descriptions))
    (OUT/'upload/examples_workflows.txt').write_text('\n\n'.join(workflows))
    for p in (HERE/'prompts').glob('*.txt'):shutil.copyfile(p,OUT/'prompts'/p.name)
    target_source=PROJECT/'experiments/paper_fewshot_20261106/target.json'
    target=json.loads(target_source.read_text())
    target.pop('example_methods',None)  # Old two-example run settings are not target identity.
    write(OUT/'target/paper_identity.json',target)
    target['source_inventory']=[{'file':p.name,'sha256':sha(p)} for p in sorted((OUT/'target').glob('*.pdf'))]
    manifest['target']=target
    manifest['input_modes']={'A':'Target PDFs + generate.txt only',
        'B':'Same target PDFs + same generate.txt + illumina_examples.pdf + examples_descriptions.txt',
        'C_optional':'Condition B + examples_workflows.txt + add_workflows.txt'}
    write(OUT/'manifest.json',manifest)
    (OUT/'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Diagram few-shot examples</title><style>body{font:16px/1.55 Arial,Helvetica,sans-serif;color:#192e3b;background:#f5f7f9;margin:0}main{max-width:1250px;margin:auto;padding:30px}section{background:white;padding:24px;margin:22px 0;border:1px solid #d0d9df;border-radius:8px}img{display:block;max-width:100%;height:auto;margin:20px 0;background:white}a{color:#175d80}summary{cursor:pointer}</style><main><h1>Ten source diagrams and their extracted workflows</h1><p>Use START_HERE.txt for the matched Claude/Gemini experiment. These are existing model-reviewed extractions; the reference drawings are original Illumina sources.</p><p><a href="upload/illumina_examples.pdf">Ten-page upload PDF</a> · <a href="prompts/generate.txt">Generation prompt</a> · <a href="START_HERE.txt">Instructions</a></p>'+''.join(cards)+'</main></html>')
    if not (OUT/'results/run_log.csv').exists():
        with (OUT/'results/run_log.csv').open('w') as f:
            csv.writer(f).writerow(['run_id','provider','exact_model','interface','date_utc','condition','replicate','target_pdf_sha256','prompt_sha256','example_order','web_search','thinking_setting','temperature_if_available','artifact_paths','elapsed_seconds','input_tokens_if_available','output_tokens_if_available','notes'])
    if not (OUT/'results/review_log.csv').exists():
        with (OUT/'results/review_log.csv').open('w') as f:
            csv.writer(f).writerow(['run_id','reviewer','claims_checked','supported','contradicted','unsupported','unresolved','consequential_omissions','critical_interface_errors','presentation_issues','target_figure_consulted','notes'])
    print(json.dumps({'examples':len(manifest['examples']),'packet':str(OUT),'example_pdf_pages':10,'target_files':target['source_inventory']},indent=2))


if __name__=='__main__':main()

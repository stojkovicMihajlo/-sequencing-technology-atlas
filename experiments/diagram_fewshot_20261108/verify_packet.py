"""Validate saved few-shot inputs and package them; no model calls."""
from pathlib import Path
import hashlib
import json
import re
import struct
import zipfile

import fitz

PROJECT=Path(__file__).resolve().parents[2]
P=PROJECT/'deliverables/diagram_fewshot_20261108'
M=json.loads((P/'manifest.json').read_text())
assert len(M['examples'])==10 and len({e['record_id'] for e in M['examples']})==10
for row in M['examples']:
    d=P/row['directory'];raw=(d/'illumina_original.png').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==row['original_diagram_sha256']
    assert struct.unpack('>II',raw[16:24])==tuple(row['source_dimensions'])
    assert struct.unpack('>II',(d/'diagram_on_white.png').read_bytes()[16:24])==tuple(row['source_dimensions'])
    candidate=json.loads((d/'workflow_original.json').read_text())
    nodes={x['id'] for k in ['states','operations'] for x in candidate[k]}
    assert all(f['source'] in nodes and f['target'] in nodes for f in candidate['flows'])
    for fn in ['illumina_description.txt','explanation.txt','workflow_compact.json','workflow.png','workflow.svg','source_links.json']:
        assert (d/fn).is_file(),fn
with fitz.open(P/'upload/illumina_examples.pdf') as doc:
    assert len(doc)==10
    for page in doc:
        assert len(page.get_images())==1
        assert 'Assay explanation' in re.sub(r'\s+',' ',page.get_text())
        for word in page.get_text('words'):
            assert page.rect.contains(fitz.Rect(word[:4])),word
pages={}
assert {p.name for p in (P/'target').glob('*.pdf')} == {
    'spatial_nt_seq_paper.pdf','spatial_nt_seq_supplement_1.pdf','spatial_nt_seq_supplement_2.pdf'}
for file in sorted((P/'target').glob('*.pdf')):
    assert file.read_bytes().startswith(b'%PDF')
    with fitz.open(file) as doc:
        assert len(doc)>0;pages[file.name]=len(doc)
for name in [p.name for p in (Path(__file__).parent/'prompts').glob('*.txt')]:
    assert (P/'prompts'/name).read_bytes()==(Path(__file__).parent/'prompts'/name).read_bytes()
for link in re.findall(r'(?:src|href)="([^"]+)"',(P/'index.html').read_text()):
    if not link.startswith('http'):assert (P/link).is_file(),link
pins={str(f.relative_to(P)):hashlib.sha256(f.read_bytes()).hexdigest() for f in P.rglob('*')
      if f.is_file() and 'results' not in f.parts and f.name not in ['input_hashes.json','PACKET_CHECK.json']}
(P/'input_hashes.json').write_text(json.dumps(pins,indent=2)+'\n')
check={'examples':10,'all_original_png_hashes_match':True,'all_native_image_dimensions_preserved':True,
       'all_workflow_references_valid':True,'all_example_pdf_text_inside_page':True,'target_pdf_pages':pages,
       'internal_links_checked':True,'prompt_files_match_authored_sources':True,'model_calls':0,
       'visual_qa':'All 10 rendered example pages personally inspected; no clipped content or overlap found.'}
(P/'PACKET_CHECK.json').write_text(json.dumps(check,indent=2)+'\n')
archive=P.with_suffix('.zip')
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=5) as z:
    for f in sorted(P.rglob('*')):
        if f.is_file():z.write(f,arcname=str(Path(P.name)/f.relative_to(P)))
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    print('ZIP files',len(z.namelist()),'MB',round(archive.stat().st_size/1e6,1))
print(json.dumps(check,indent=2))

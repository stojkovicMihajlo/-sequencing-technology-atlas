"""Export a completed method batch as a portable, offline inspection app."""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import shutil

from genomics_discovery.illumina.extraction import read, sha, write
from genomics_discovery.illumina.graph import _load_card


def export(batch, output):
    output.mkdir(parents=True, exist_ok=False)
    for name in ('records', 'assets'):
        (output / name).mkdir()
    state = read(batch / 'state.json'); inventory = read(batch / 'inventory.json')
    if sha(batch / 'inventory.json') != state['policy']['inventory_sha256']:
        raise ValueError('Batch inventory differs from recorded input')
    rows, categories, copied = [], {}, set()
    selection = read(batch / 'selection.json')
    for m in selection['methods']:
        categories[m['method_id']] = Path(m.get('canonical_directory','')).parts[1:-1]
    for card in inventory['cards']:
        ident = card['record_id']; row = state['cards'][ident]
        item = {'record_id': ident, 'title': card['source_title'], 'batch': 'catalogue',
            'status': row['status'], 'reason': row.get('reason',''), 'method_names': card.get('verified_method_names',[]),
            'category': list(categories.get(ident,())), 'operations': row.get('operations',0)}
        candidate, review, sources, flags = None, None, {}, []
        directory = Path(row['attempt_directory']) if row.get('attempt_directory') else None
        if directory:
            for name, key in [('extraction.json','candidate'), ('review.json','review'), ('source_registry.json','sources')]:
                p = directory / name
                if not p.exists():continue
                value = read(p)
                if key == 'candidate':candidate=value
                elif key == 'review':review=value
                else:sources=value
            if candidate and review:
                try:
                    _load_card(row, card)
                except Exception as exc:
                    item.update(status='integrity_error', reason=str(exc));flags.append(str(exc))
        displayed = {}
        for sid, s in sources.items():
            shown = {k: v for k,v in s.items() if k != 'text'}
            if s.get('content_level') == 'catalogue':shown['display_text']=s.get('text','')
            if s.get('kind') == 'image':
                p = Path(s['path']); expected = s['sha256']
                if sha(p) != expected:raise ValueError('Original diagram changed: '+sid)
                asset = 'assets/' + expected + p.suffix.lower()
                if asset not in copied:shutil.copyfile(p,output/asset);copied.add(asset)
                shown['local_asset'] = asset
            displayed[sid]=shown
        filename = hashlib.sha256(ident.encode()).hexdigest()[:24]
        item.update(file='records/'+filename+'.json', script='records/'+filename+'.js')
        detail = {**item, 'candidate':candidate, 'review':review, 'sources':displayed, 'card':card,
            'research_questions': row.get('research_questions',[]), 'flags':flags, 'errors':[],
            'attempt_directory':str(directory) if directory else None, 'revalidation_finding':None}
        raw = json.dumps(detail,ensure_ascii=False,separators=(',',':'))
        (output/item['file']).write_text(raw)
        (output/item['script']).write_text('window.ATLAS_RECORDS=window.ATLAS_RECORDS||{};window.ATLAS_RECORDS['+json.dumps(ident)+']='+raw+';\n')
        rows.append(item)
    manifest = {'format_version':'method_batch_inspection/1', 'method_count':len(rows),
        'inventory_unit':'Named methods', 'counts':dict(Counter(r['status'] for r in rows)),
        'input_pins':{n:sha(batch/n) for n in ('state.json','inventory.json','selection.json')},
        'original_batch':str(batch), 'native_model_calls':0,
        'boundary':'Read-only display. Original extractions and review verdicts are preserved; failed admission gates are shown. No new scientific review or historical edge is created.'}
    document = {'manifest':manifest,'records':rows}
    write(output/'index.json',document);write(output/'manifest.json',manifest)
    here = Path(__file__).parent
    page=(here/'index.html').read_text().replace('<body>','<body data-inventory-unit=" named methods">')
    page=page.replace('Each entry is one source depiction; several entries may describe the same method.',
        'Each entry is one curated named method. Inspect its original diagram, extracted workflow and independent review.')
    page=page.replace('Find a source depiction','Find a method').replace('Supported only','Accepted only')
    page=page.replace('<option value="research_needed">','<option value="needs_revision">Needs revision</option><option value="source_recovery_needed">Source holds</option><option value="research_needed">')
    page=page.replace('<script src="app.js?v=3"></script>',
        '<script id="index-data" type="application/json">'+json.dumps(document,ensure_ascii=False).replace('<','\\u003c')+'</script><script src="app.js"></script>')
    (output/'index.html').write_text(page)
    for name in ('app.js','style.css'):shutil.copyfile(here/name,output/name)
    return manifest


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(export(a.batch.resolve(),a.output.resolve()),indent=2))

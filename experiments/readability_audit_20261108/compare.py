"""Render exact saved before/after records; no model calls or scientific rewriting."""
import argparse
import copy
import hashlib
import html
import json
from pathlib import Path
import shutil

from genomics_discovery.illumina.models import parse_extraction, parse_review, review_outcome
from scripts.illumina.review_site.build import standalone


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_record(directory, template_path, template_root, output, name):
    candidate = parse_extraction((directory / 'extraction.json').read_bytes())
    review = parse_review((directory / 'review.json').read_bytes())
    sources = read(directory / 'source_registry.json')
    status = review_outcome(review, candidate, sources)
    record = copy.deepcopy(read(template_path))
    # Verify each copied original image, retaining its registered scientific source ID.
    for sid, source in sources.items():
        if source.get('kind') != 'image':
            continue
        displayed = record['sources'][sid]
        image = template_root / displayed['local_asset']
        if sha(image) != source['sha256']:
            raise ValueError('Original diagram mismatch: ' + sid)
        relative = 'assets/' + image.name
        shutil.copyfile(image, output / relative)
        displayed['local_asset'] = relative
    record.update(candidate=candidate.model_dump(mode='json'), review=review.model_dump(mode='json'),
                  status=status, reason='', flags=[], errors=[], research_questions=[],
                  revalidation_finding=None, attempt_directory=str(directory),
                  file=name+'.json', download_label='Download this saved candidate and review')
    # Old display metadata must not point to the baseline workflow PNG.
    record.pop('workflow_figure', None)
    record.pop('source_comparison', None)
    (output / (name+'.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
    standalone(record, output / (name+'.html'))
    page = output / (name+'.html')
    page.write_text(page.read_text().replace('href="../../index.html">Technology atlas',
                                             'href="index.html">Comparison'))
    return {'name':name, 'status':status, 'operations':len(candidate.operations),
            'states':len(candidate.states), 'flows':len(candidate.flows),
            'review_targets':len(review.checks),
            'source_pins':{f:sha(directory/f) for f in
                           ('card.json','source_registry.json','extraction.json','review.json')},
            'directory':str(directory)}


def build(plan_path, output):
    plan=read(plan_path)
    output.mkdir(parents=True, exist_ok=False)
    (output/'assets').mkdir()
    rows=[]
    for item in plan['records']:
        row=export_record(Path(item['directory']), Path(item['template']),
                          Path(plan['template_root']), output, item['name'])
        rows.append({**row, 'label':item['label']})
    repo=Path(__file__).resolve().parents[2]
    receipt={'purpose':'Read-only comparison of saved extractions and reviews',
             'plan_sha256':sha(plan_path),'model_calls':0,'records':rows,
             'renderer_sha256':{name:sha(repo/name) for name in (
                 'experiments/readability_audit_20261108/compare.py',
                 'scripts/illumina/review_site/build.py',
                 'scripts/illumina/review_site/app.js',
                 'scripts/illumina/review_site/style.css')},
             'limits':plan['limits']}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    esc=html.escape
    table=''.join(f'<tr><td><a href="{esc(r["name"])}.html">{esc(r["label"])}</a></td>'
                  f'<td>{esc(r["status"])}</td><td>{r["operations"]}</td>'
                  f'<td>{r["states"]}</td><td>{r["flows"]}</td></tr>' for r in rows)
    findings=''.join(f'<section><h2>{esc(f["title"])}</h2><p>{esc(f["finding"])}</p>'
                      f'<p class="basis">{esc(f["basis"])}</p></section>' for f in plan['findings'])
    page=f'''<!doctype html><html lang="en"><meta charset="utf-8">
<title>State clarity — fixed-source comparison</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font:17px/1.6 system-ui,sans-serif;max-width:1050px;margin:40px auto;padding:0 24px;color:#172737;background:#fff}}
h1{{font-size:30px}}h2{{font-size:21px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #ddd}}
a{{color:#145c8b}}section{{margin:28px 0}}.basis{{font-size:15px;color:#4e5e69}}</style>
<h1>State clarity — fixed-source comparison</h1><p>{esc(plan['summary'])}</p>
<p>Open a saved workflow below. Each contains its original diagram, clickable States and Operations, evidence and independent review.</p>
<table><thead><tr><th>Saved workflow</th><th>Review outcome</th><th>Operations</th><th>States</th><th>Flows</th></tr></thead><tbody>{table}</tbody></table>
{findings}<p>{esc(plan['limits'])}</p><p><a href="receipt.json">Exact input hashes and export receipt</a></p></html>'''
    (output/'index.html').write_text(page)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.plan.resolve(),args.output.resolve()),indent=2))

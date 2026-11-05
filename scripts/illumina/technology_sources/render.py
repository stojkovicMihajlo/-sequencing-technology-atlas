"""Plain readable views over the same source manifest; no model summaries."""
import csv
from collections import Counter
from html import escape as e
from pathlib import Path

from .materialize import write


STYLE = '<style>body{font:16px/1.55 Arial,sans-serif;margin:40px auto;max-width:1200px;padding:0 24px;color:#202b36}a{color:#155899}td,th{border-bottom:1px solid #ddd;padding:10px;text-align:left}table{border-collapse:collapse;width:100%}pre{white-space:pre-wrap;background:#f4f6f8;padding:20px}img{max-width:100%;height:auto}small{color:#59636d}input{padding:10px;width:70%;margin:16px 0}</style>'


def page(title, body):
    return '<!doctype html><meta charset="utf-8"><title>' + e(title) + '</title>' + STYLE + body


def render(root, state):
    root = Path(root)
    rows = []
    for mid, method in sorted(state['inventory']['methods'].items(), key=lambda x: x[1]['name'].casefold()):
        directory = root / method['directory']
        collection = method['collection']
        classifications = sorted({' > '.join(c['path']) for c in method['classifications'] if c['path']})
        headings = '<h1>' + e(method['name']) + '</h1><p>' + e('; '.join(classifications) or 'Classification not recovered') + '</p>'
        body = headings + '<p><a href="../../index.html">All technologies</a> · <a href="method.json">Source manifest</a></p>'
        body += '<p>Source collection only. Shared depictions do not establish identical workflows. Original source text follows; it has not been rewritten by a model.</p>'
        text = [method['name'], 'Illumina classification: ' + '; '.join(classifications), '',
                'catalogue/: source descriptions and reference lists',
                'diagrams/: source images and recorded PDF crops',
                'posters/ and guides/: complete shared source PDFs',
                'papers/: explicitly listed references, with acquisition status',
                'paper_candidates/: retrieved leads whose method association needs review',
                'related_papers/: the card’s separately labelled related literature', '',
                'No method graph has been newly extracted in this folder.']
        for rid in method['source_records']:
            source = state['sources'].get(rid)
            occ = state['inventory']['occurrences'][rid]
            body += '<h2>' + e(occ['title']) + '</h2><p><small>' + e(occ['batch'] + ' · ' + rid) + '</small></p>'
            if occ['shared_source']:
                body += '<p><strong>Shared depiction:</strong> ' + e(', '.join(occ['card'].get('source_method_labels') or occ['members'])) + '</p>'
            if not source:
                body += '<p>Source registry unavailable; see collection errors.</p>'
                continue
            for c in source['catalogue']:
                path = root / c['path']
                body += '<details' + (' open' if c['source_id'].startswith('card:') else '') + '><summary>' + e(c.get('title') or c['source_id']) + '</summary><pre>' + e(path.read_text()) + '</pre></details>'
            for image in source['diagrams']:
                relative = '../../' + image['path']
                body += '<p><a href="' + e(relative) + '">Open original image / crop</a></p><a href="' + e(relative) + '"><img loading="lazy" src="' + e(relative) + '"></a>'
            for doc in source['documents']:
                body += '<p><a href="../../' + e(doc['path']) + '">Full PDF: ' + e(Path(doc['path']).name) + '</a></p>'
        body += '<h2>Papers</h2><table><tr><th>Paper</th><th>Relationship to source</th><th>Available files</th></tr>'
        for binding in collection['papers']:
            p = state['papers'][binding['paper_id']]
            roles = ', '.join(sorted({b['role'] for b in binding['bindings']}))
            links = ' '.join('<a href="../../' + e(f['path']) + '">' + e(f['kind'] + Path(f['path']).suffix) + '</a>' for f in p['files']) or e(p.get('retrieval_status', p['status']))
            body += '<tr><td>' + e(p.get('title') or p['id']) + '</td><td>' + e(roles) + '</td><td>' + links + '</td></tr>'
        body += '</table>'
        body += '<h2>References exactly as listed</h2><p>These include unresolved citations without an established paper identifier.</p><pre>'
        import json
        body += e(json.dumps(collection['listed_references'], indent=2, ensure_ascii=False)) + '</pre>'
        (directory / 'index.html').write_text(page(method['name'], body))
        (directory / 'README.txt').write_text('\n'.join(text) + '\n')
        primary = [x for x in collection['papers'] if any(b['role'] == 'references' for b in x['bindings'])]
        row = {'technology': method['name'], 'directory': method['directory'], 'kind': method['kind'],
               'catalogue_group': ' / '.join(method['catalogue_group']),
               'classifications': '; '.join(classifications), 'depictions': len(method['source_records']),
               'diagrams': len(collection['diagrams']), 'listed_papers': len(primary),
               'listed_fulltexts': sum(state['papers'][p['paper_id']]['status'] == 'fulltext_acquired' for p in primary),
               'candidate_papers': sum(any(b['role'].startswith('candidate') for b in x['bindings']) for x in collection['papers']),
               'identity_review': 'pending',
               'paper_association_review': 'pending',
               'classification_review': 'pending',
               'diagram_quality_review': 'pending',
               'graph_extraction_status': 'not_run_in_this_collection'}
        rows.append(row)
    with (root / 'CHECKLIST.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    summary = {**state['inventory']['counts'], 'source_registries_collected': len(state['sources']),
        'unique_stored_files': len(state['files']), 'cached_bytes': sum(x['bytes'] for x in state['files'].values()),
        'papers_by_acquisition_status': dict(Counter(p['status'] for p in state['papers'].values())),
        'unavailable_cached_files_or_hash_errors': len(state['errors']),
        'technology_directories_without_diagrams': sum(r['kind'] == 'technology' and not r['diagrams'] for r in rows),
        'scientific_review': False, 'new_model_extractions': 0}
    write(root / 'SUMMARY.json', summary)
    table = ''.join('<tr><td><a href="' + e(r['directory']) + '/index.html">' + e(r['technology']) + '</a></td><td>' + e(r['classifications']) + '</td><td>' + str(r['depictions']) + '</td><td>' + str(r['diagrams']) + '</td><td>' + str(r['listed_fulltexts']) + '/' + str(r['listed_papers']) + '</td></tr>' for r in rows)
    body = '<h1>Illumina technology source collection</h1><p>Original descriptions, diagrams and papers organized by source name. This directory count is not a validated count of distinct assays; versions and unresolved aliases remain visible. This stage collects sources only; it does not define or extract States, Operations, protocols or historical edges.</p><p><a href="CHECKLIST.csv">Coverage checklist</a> · <a href="SUMMARY.json">Acquisition results</a></p><input id="q" placeholder="Filter technologies or classifications"><table id="methods"><thead><tr><th>Technology</th><th>Illumina classification</th><th>Depictions</th><th>Diagrams</th><th>Listed fulltexts / papers</th></tr></thead><tbody>' + table + '</tbody></table><script>document.getElementById("q").oninput=function(){for(const r of document.querySelectorAll("tbody tr")){r.hidden=!r.innerText.toLowerCase().includes(this.value.toLowerCase())}}</script>'
    (root / 'index.html').write_text(page('Illumina sources', body))
    return summary

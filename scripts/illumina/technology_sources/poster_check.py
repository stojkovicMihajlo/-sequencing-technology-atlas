"""Create traceable row views and guide-page matches from original Illumina PDFs.

Text geometry limits the proposed crop; it cannot certify molecular completeness.
The resulting row images still require visual inspection alongside the full PDF.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from html import escape
import json
import math
import os
from pathlib import Path
import re
import subprocess

from .curation import compile_inventory
from .inventory import read, sha
from .materialize import write
from .render import page


def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def row_windows(inventory):
    rows = [(rid, o['poster_row']) for rid, o in inventory['occurrences'].items() if o['batch'] == 'posters']
    groups = defaultdict(list)
    for rid, row in rows:
        column = row['row_id'].split(':c')[1].split(':')[0]
        groups[(row['resource_id'], row['page'], column)].append((rid, row))
    results = []
    for members in groups.values():
        members.sort(key=lambda x: min(b[1] for b in x[1]['exact_label_bboxes']))
        for n, (rid, row) in enumerate(members):
            own_top = min(b[1] for b in row['exact_label_bboxes'])
            own_bottom = max(b[3] for b in row['exact_label_bboxes'])
            box = list(row.get('prior_tight_region_proposal') or row['diagram_region_proposal'])
            if n:
                prev_bottom = max(b[3] for b in members[n-1][1]['exact_label_bboxes'])
                if prev_bottom >= own_top:
                    raise ValueError('Overlapping method labels require manual row resolution: ' + rid)
                box[1] = max(box[1], (prev_bottom + own_top) / 2)
            if n + 1 < len(members):
                next_top = min(b[1] for b in members[n+1][1]['exact_label_bboxes'])
                if next_top <= own_bottom:
                    raise ValueError('Overlapping method labels require manual row resolution: ' + rid)
                box[3] = min(box[3], (own_bottom + next_top) / 2)
            if any(not (box[0] <= b[0] and box[1] <= b[1] and box[2] >= b[2] and box[3] >= b[3]) for b in row['exact_label_bboxes']):
                raise ValueError('Crop would exclude its own label: ' + rid)
            neighbor_labels = [other['source_row_caption'] for oid, other in members if oid != rid
                               and any(overlap(box, b) for b in other['exact_label_bboxes'])]
            if neighbor_labels:
                raise ValueError('Refined crop includes another method label: ' + rid)
            results.append({'source_id': rid, 'row': row, 'pdf_point_box': box,
                'own_method_labels_contained': True, 'neighbor_method_labels_intersect': False,
                'workflow_extent_review': 'pending_visual_review'})
    return results


def check(source_root, decisions, bindings, output):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('PDF rendering must execute within a Slurm allocation')
    root, output = Path(source_root), Path(output)
    state = read(root / 'collection.json')
    inventory = state['inventory']
    curated = compile_inventory(inventory, decisions, bindings, state['papers'])
    output.mkdir(parents=True, exist_ok=False)
    rows, html, verified = row_windows(inventory), [], set()
    for n, record in enumerate(rows, 1):
        row, rid = record['row'], record['source_id']
        pdf = root / 'shared/documents' / Path(row['source_url']).name
        if row['source_pdf_sha256'] not in verified:
            if sha(pdf) != row['source_pdf_sha256']:
                raise ValueError('Original poster checksum differs: ' + str(pdf))
            verified.add(row['source_pdf_sha256'])
        box = record['pdf_point_box']
        pixels = [math.floor(box[0] * 2), math.floor(box[1] * 2), math.ceil(box[2] * 2), math.ceil(box[3] * 2)]
        prefix = output / 'rows' / rid
        prefix.parent.mkdir(exist_ok=True)
        command = ['pdftoppm', '-f', str(row['page']), '-l', str(row['page']), '-singlefile',
            '-r', '144', '-x', str(pixels[0]), '-y', str(pixels[1]), '-W', str(pixels[2]-pixels[0]),
            '-H', str(pixels[3]-pixels[1]), '-png', str(pdf), str(prefix)]
        subprocess.run(command, check=True, capture_output=True, timeout=75)
        image = prefix.with_suffix('.png')
        record.update(image_path=str(image), image_sha256=sha(image), command=command,
                      original_pdf=str(pdf), original_pdf_sha256=row['source_pdf_sha256'])
        html.append('<h2>' + escape(row['source_row_caption']) + '</h2><p>' + escape(rid) +
            ' · <a href="' + escape(row['source_url']) + '">Full original poster</a></p><img src="rows/' + rid + '.png">')
        if n % 25 == 0:
            print(f'Poster row views {n}/{len(rows)}', flush=True)
    write(output / 'ROW_VIEWS.json', rows)
    # Original guides are independent secondary sources. Matching a name is a
    # page lead, not proof that a generic heading describes the exact variant.
    guide_pages = {}
    for name in ['dna-sequencing-methods-review-web.pdf', 'rna-sequencing-methods-review-web.pdf']:
        pdf = root / 'shared/documents' / name
        if not pdf.is_file():
            continue
        textfile = output / (name + '.txt')
        subprocess.run(['pdftotext', '-layout', str(pdf), str(textfile)], check=True, capture_output=True, timeout=90)
        guide_pages[name] = {'pdf': str(pdf), 'pdf_sha256': sha(pdf), 'pages': textfile.read_text().split('\f')}
    poster_only = {}
    for mid, method in curated['methods'].items():
        if method['entry_type'] != 'named_method' or any(inventory['occurrences'][r]['batch'] in {'catalogue','initial_two'} for r in method['source_records']):
            continue
        matches = []
        for name, guide in guide_pages.items():
            for i, text in enumerate(guide['pages'], 1):
                labels = [a for a in method['aliases'] if re.search(r'(?<!\w)' + re.escape(a) + r'(?!\w)', text, re.I)]
                if labels:
                    matches.append({'guide': name, 'pdf_page': i, 'matched_labels': labels,
                        'page_text': text, 'pdf_sha256': guide['pdf_sha256'],
                        'status': 'literal_page_match_requires_scope_check'})
        poster_only[mid] = {'name': method['name'], 'aliases': method['aliases'],
            'original_classifications': method['classifications'], 'method_references': method['method_references'],
            'guide_page_matches': matches,
            'website_description_status': 'not_in_captured_154_cards; supplementary_site_search_not_yet_completed',
            'classification_review': 'source_heading_crosswalk_available; scientific_category_scope_requires_confirmation'}
    write(output / 'POSTER_ONLY_SOURCES.json', poster_only)
    (output / 'index.html').write_text(page('Poster row inspection', '<h1>Original poster row views</h1><p>Geometry checks pass for method labels. Molecular drawing boundaries still require visual review.</p>' + ''.join(html)))
    return {'row_views': len(rows), 'poster_only_methods': len(poster_only),
            'poster_only_with_guide_text_matches': sum(bool(x['guide_page_matches']) for x in poster_only.values()),
            'visual_reviews_completed': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ['source-root','decisions','bindings','output']:
        p.add_argument('--'+key, type=Path, required=True)
    a=p.parse_args()
    summary=check(a.source_root, read(a.decisions), read(a.bindings), a.output)
    write(a.output/'SUMMARY.json', summary)
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()

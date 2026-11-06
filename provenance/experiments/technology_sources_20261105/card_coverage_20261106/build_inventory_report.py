"""Build a human-readable inventory report from pinned source-accounting records."""
from collections import Counter, defaultdict
import csv
from html import escape
import gzip
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / 'provenance/experiments/technology_sources_20261105'
HERE = Path(__file__).resolve().parent
OUT = ROOT / 'docs/reports/illumina_inventory_numbers_20261106.html'


def read_csv(path):
    return list(csv.DictReader(path.open()))


def link(path, label):
    return '<a href="' + escape(os.path.relpath(path, OUT.parent)) + '">' + escape(label) + '</a>'


def table(headers, rows):
    return '<table><thead><tr>' + ''.join('<th>' + escape(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + str(c) + '</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table>'


def main():
    inventory = json.loads(gzip.decompress((BASE / 'inventory.json.gz').read_bytes()))
    entries = read_csv(BASE / 'release_01/IDENTITY_AND_CLASSIFICATION.csv')
    delivery = BASE / 'release_01/task2_07/delivered'
    checks = {r['method_id']: r for r in read_csv(delivery / 'SOURCE_PACKAGE_CHECKLIST.csv')}
    missing = {r['paper_id'] for r in read_csv(delivery / 'MANUAL_PAPER_RECOVERY.csv')}
    audit = json.loads((HERE / 'delivered/SUMMARY.json').read_text())
    details = json.loads((HERE / 'delivered/CARD_DETAILS.json').read_text())
    cards = {k: v for k, v in inventory['occurrences'].items() if v['batch'] in {'catalogue', 'initial_two'}}
    posters = {k: v for k, v in inventory['occurrences'].items() if v['batch'] == 'posters'}
    owners = defaultdict(list)
    for entry in entries:
        for sid in entry['source_ids'].split('; '):
            owners[sid].append(entry)
    named = {r['method_id']: r for r in entries if r['entry_type'] == 'named_method'}
    C = {m['method_id'] for sid in cards for m in owners[sid] if m['entry_type'] == 'named_method'}
    P = {m['method_id'] for sid in posters for m in owners[sid] if m['entry_type'] == 'named_method'}
    distribution = Counter(len(owners[sid]) for sid in cards)
    assigned = sum(len(owners[sid]) for sid in cards)
    non_method = sum(sum(m['entry_type'] != 'named_method' for m in owners[sid]) for sid in cards)
    appearances = assigned - non_method
    repeated = appearances - len(C)
    card_papers = {pid for mid in C for pid in checks[mid]['assigned_paper_ids'].split('; ')}
    all_papers = {pid for r in checks.values() for pid in r['assigned_paper_ids'].split('; ')}
    poster_counts, poster_sets = [], {}
    titles = {'dna_poster': 'DNA poster', 'rna_poster': 'RNA poster', 'single_cell_poster': 'Single-cell poster'}
    for kind in titles:
        ids = {sid for sid, o in posters.items() if o['poster_row']['resource_kind'] == kind}
        mids = {m['method_id'] for sid in ids for m in owners[sid] if m['entry_type'] == 'named_method'}
        poster_sets[kind] = mids
        poster_counts.append([titles[kind], len(ids), len(mids)])
    image_groups = {}
    for card in details:
        if not card['saved_card_record']:
            continue
        mids = {m['method_id'] for m in card['methods'] if m['entry_type'] == 'named_method'}
        for image in card['diagrams']:
            item = image_groups.setdefault(image['sha256'], {'cards': set(), 'methods': set()})
            item['cards'].add(card['card_id'])
            item['methods'].update(mids)
    shared = {h: v for h, v in image_groups.items() if len(v['methods']) > 1}
    shared_methods = set().union(*(g['methods'] for g in shared.values()))
    card_methods_with_text = sum(int(checks[mid]['downloaded_method_papers']) > 0 for mid in C)
    grouped = [dict(card_id=sid, title=c['title'], entries=owners[sid])
               for sid, c in cards.items() if len(c['members']) > 1]
    primary_categories = Counter(r['canonical_directory'].split('/')[1] for r in named.values())
    assert len(cards) == 154 and len(C) == 174 and len(P) == 271 and len(C & P) == 172
    assert len(C | P) == 273 and len(cards) + (assigned - len(cards)) - non_method - repeated == len(C)
    assert len(grouped) == 24 and len(shared) == 21 and len(shared_methods) == 53
    assert audit['current_catalogue_cards'] == 154 and audit['failed_file_hash_checks'] == 0
    assert set(C | P) == set(named)
    summary = {
        'as_of': '2026-11-06', 'published_snapshot': 'illumina_library_20261105_07',
        'source_counts': {'website_cards': len(cards), 'poster_rows': len(posters),
            'per_poster': poster_counts, 'footer_panels': 39, 'source_occurrences': 478},
        'method_counts': {'cards': len(C), 'posters': len(P), 'both': len(C & P),
            'card_only': len(C - P), 'poster_only': len(P - C), 'union': len(C | P)},
        'card_arithmetic': {'card_entry_assignments': assigned, 'non_method_assignments': non_method,
            'named_method_appearances': appearances, 'repeated_appearances': repeated,
            'cards_by_entry_count': dict(distribution)},
        'shared_diagrams': {'distinct_card_image_hashes': len(image_groups),
            'shared_image_groups': len(shared), 'named_methods_in_shared_image_groups': len(shared_methods),
            'verified_identical_experimental_workflows': 'not_established'},
        'file_availability': {'card_methods_with_text': card_methods_with_text,
            'card_methods_without_text': len(C) - card_methods_with_text,
            'card_publications': len(card_papers), 'card_publications_downloaded': len(card_papers - missing),
            'card_publications_missing': len(card_papers & missing),
            'all_methods_with_text': 236, 'all_methods_without_text': 37,
            'all_publications': len(all_papers), 'all_publications_downloaded': len(all_papers - missing),
            'all_publications_missing': len(all_papers & missing)},
        'other_entries': dict(Counter(r['entry_type'] for r in entries if r['entry_type'] != 'named_method')),
        'primary_folder_categories': dict(primary_categories), 'fresh_card_audit': audit,
        'reviewed_exceptions': {
            'mda-ims-mda-midas': 'Empty original CMS card; do not invent an Illumina description. Image and independently assigned papers remain available.',
            'scbs-seq': 'Category-tree reference exists, but All Methods does not list this card and the destination returns404. Method already has a poster-derived entry.',
            'mpe-seq': 'One citation URL is malformed in the saved source record. The printed Hertzberg-Dervan1982 citation is already preserved; this is not a newly absent method paper.'},
        'input_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [BASE / 'inventory.json.gz', BASE / 'release_01/IDENTITY_AND_CLASSIFICATION.csv',
                delivery / 'SOURCE_PACKAGE_CHECKLIST.csv', delivery / 'MANUAL_PAPER_RECOVERY.csv',
                HERE / 'delivered/SUMMARY.json', HERE / 'delivered/CARD_DETAILS.json']}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.with_suffix('.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    body = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Illumina atlas: inventory numbers</title><style>
    @page{size:Letter;margin:1in}*{box-sizing:border-box}body{font:11pt/1.35 Arial,Helvetica,sans-serif;color:#161616;background:white;margin:0 auto;padding:40px 28px;max-width:980px}h1{font-size:25pt;line-height:1.12;margin:0 0 8px;font-weight:600}h2{font-size:16pt;margin:28px 0 10px}h3{font-size:12pt;margin:18px 0 6px}p{margin:0 0 10px}a{color:#195b9b}small,.muted{color:#555;font-size:9.5pt}table{border-collapse:collapse;width:100%;margin:12px 0 16px;font-size:10.5pt}th,td{text-align:left;vertical-align:top;border:1px solid #d7dce0;padding:9px 11px}th{background:#f2f4f7;font-weight:600}tr{break-inside:avoid}thead{display:table-header-group}.equation{font-size:17pt;line-height:1.35;margin:14px 0;padding:12px 16px;background:#f5f7f9;border-left:3px solid #326087}.partition{display:flex;gap:10px;margin:14px 0 12px}.partition div{flex:1;border:1px solid #ccd3da;padding:14px;background:#fafbfc}.partition strong{font-size:26pt;display:block}.note{padding:12px 14px;border:1px solid #dadada;background:#fcfcfc;margin:12px 0}.compact{font-size:10pt}.appendix td{padding:8px 10px}li{margin-bottom:6px}@media print{body{padding:0;max-width:none}h1,h2,h3{break-after:avoid}a{color:inherit;text-decoration:none}.appendix{break-before:page}.no-print{display:none}}
    </style></head><body><h1>Illumina atlas: inventory numbers</h1><p class="muted">6 November 2026 · Published source library07 · Source coverage, method identity and diagram sharing</p>
    <p><strong>The current library has273 curated method/implementation entries.</strong> Of these,174 are represented by website cards,271 by poster rows, and172 appear in both. These are source-backed catalogue entries; this number does not count completed protocol graphs, historical edges or independently validated inventions.</p>
    <h2>1. What we counted</h2>'''
    body += table(['Source', 'Source items', 'What an item means'], [
        ['Website cards', 154, 'One All Methods page, sometimes naming several methods.'],
        ['DNA poster', 105, 'One main labelled diagram row.'],
        ['RNA poster', 102, 'One main labelled diagram row.'],
        ['Single-cell poster', 78, 'One main labelled diagram row.'],
        ['All main poster rows', 285, '105 +102 +78; a row can group methods.'],
        ['Footer panels', 39, 'Additional source panels; not39 extra named methods.'],
        ['Total source occurrences', 478, '154 cards +285 main rows +39 footer panels.']])
    body += '<p>A source occurrence is a place where something is depicted or described. A method can occur on a standalone card, a grouped card and more than one poster. Those occurrences retain their provenance within the same method entry.</p>'
    body += '<h2>2. How cards and posters give273 methods</h2><div class="partition"><div>Cards only<strong>2</strong></div><div>Cards and posters<strong>172</strong></div><div>Posters only<strong>99</strong></div></div>'
    body += '<div class="equation">174 +271 −172 =273</div><p>Adding the card and poster method counts counts the172 overlapping methods twice, so we subtract them once. Equivalently,2 +172 +99 =273.</p>'
    body += '<p>The two card-only named entries are<strong> PTB-Seq and RAP-RNA</strong>. The99 poster-only entries supplement the website cards; they are not duplicate extractions.</p>'
    body += table(['Poster', 'Main diagram rows', 'Distinct named entries in that poster'], poster_counts)
    body += '<p>The per-poster method counts total323, while their union is271:223 methods occur in one poster,44 in two, and4 in all three. The four appearing in all three are DR-Seq,G&amp;T-Seq,scM&amp;T-Seq and scTrio-Seq. A shared row can also represent more than one method.</p>'
    body += '<h2>3. Why154 cards represent174 methods</h2><p>We use the reviewed source-to-method assignments rather than treating every slash-separated name as a new technology. There are132 cards mapping to one curated entry,12 mapping to two,8 mapping to three,1 mapping to four and1 mapping to five.</p>'
    body += '<div class="equation">132 ×1 +12 ×2 +8 ×3 +1 ×4 +1 ×5 =189<br>189 −7 −8 =174</div>'
    body += '<p>The189 are card-to-entry assignments. Seven assignments refer to family/component records rather than named-method entries. The remaining182 named-method appearances include eight repeated appearances across cards, leaving174 distinct named-method entries.</p>'
    body += '<p>For example, Hi-C/3C-Seq/Capture-C is one card connected to three separate entries and their assigned papers. Drop-ChIP/scChIP-Seq is one entry with aliases. Bru-Seq appears both on its own card and on the grouped GRO/BRIC/Bru/BruChase card, so it is counted once.</p>'
    body += '<h2>4. What shares a diagram?</h2>'
    body += table(['Quantity', 'Count'], [
        ['Card titles containing multiple source method names', 24],
        ['Cards mapped to multiple curated entries, including families/components', 22],
        ['Distinct card-image files referenced by the154 cards', len(image_groups)],
        ['Image groups assigned to at least two named methods', len(shared)],
        ['Named methods belonging to those shared-image groups', len(shared_methods)]])
    body += '<p>“Shared” here means the entries reference the same stored image bytes. It is useful for starting comparisons. It does not prove identical workflows, reusable/interchangeable operations, or a historical ancestor–descendant relationship. The number of experimentally equivalent workflows has not been established.</p>'
    body += '<h2>5. Descriptions, diagrams and papers: actual availability</h2>'
    body += table(['Coverage', 'Cards / card methods', 'Full named-method library'], [
        ['Saved source cards', '154 of154 on the current All Methods list', 'Includes99 additional poster-only entries'],
        ['Saved Illumina card descriptions', '153 of154', 'Poster-only descriptions use separately attributed sources'],
        ['Card diagram files present and hash-checked', '154 of154 cards', 'Visual/scientific interpretation is a separate check'],
        ['Methods with at least one associated article/protocol file', f'{card_methods_with_text} of174', '236 of273'],
        ['Methods without any associated article/protocol full text', f'{174-card_methods_with_text}', '37'],
        ['Distinct assigned method publications', f'{len(card_papers)}', f'{len(all_papers)}'],
        ['Assigned method publications downloaded', f'{len(card_papers-missing)}', f'{len(all_papers-missing)}'],
        ['Assigned method publications still missing', f'{len(card_papers&missing)}', f'{len(all_papers&missing)}']])
    body += '<p>Some papers describe more than one method; some methods have several associated publications. Therefore “methods with a paper” and “downloaded papers” are different counts. The card-subset figures exclude papers assigned only to family/component resources.</p>'
    body += '<div class="note"><strong>Recorded exceptions.</strong> The MDA/IMS-MDA/MIDAS CMS card is empty: its image and assigned papers are retained, but we do not invent Illumina prose for it. A scBS-seq category-tree reference points to a404 page; scBS-seq already has a poster-derived method entry. The MPE-seq card has one malformed stored citation URL; the printed1982 chemistry citation itself is preserved.</div>'
    body += '<p>The fresh audit retrieved the current All Methods index, three category trees and card content;616 registered source/article file checks passed. All174 named card-method entries have paper assignments. This establishes source accounting and file integrity, not full scientific paper review or protocol correctness.</p>'
    body += '<p>The library also retains25 separately typed resources:15 commercial kits,5 family overviews,2 reusable components,1 readout platform,1 application overview and1 unresolved source label. They are excluded from the273 named-method total. The primary method folders are Genome61,Transcriptome135 and Epigenome77; secondary category links do not create duplicate methods.</p>'
    body += '<h2>6. A short update you can use</h2><div class="note">We’ve organized Illumina’s catalogue into273 method/implementation entries. The154 website cards represent174 named methods, and the three posters add99 methods not represented by those cards. Each entry keeps its original sources and paper assignments; grouped methods can reference the same diagram. We’ve identified21 shared-diagram groups involving53 methods, which gives us a focused starting set for comparing the specific changes between assays. Paper acquisition remains incomplete, and diagram sharing is kept separate from evidence of historical development.</div>'
    body += '<h2>Records behind these numbers</h2><p>' + link(BASE / 'release_01/IDENTITY_AND_CLASSIFICATION.csv', 'Method identities, source assignments and folder paths') + '<br>' + link(delivery / 'SOURCE_PACKAGE_CHECKLIST.csv', 'One source-package record per named method') + '<br>' + link(delivery / 'MANUAL_PAPER_RECOVERY.csv', 'The48 missing assigned publication files') + '<br>' + link(HERE / 'delivered/CARD_CHECKLIST.csv', 'Fresh card-by-card audit') + '<br>' + link(OUT.with_suffix('.json'), 'Exact counts, source hashes and audit qualifications') + '</p>'
    body += '<p class="muted">Source library on Qiu Sherlock:/scratch/groups/xiaojie/mstojkov/agents_snRNA/technology_sources/library/. This report records the current curated inventory; the earlier201-entry atlas used a different inventory and is not the current denominator.</p>'
    body += '<section class="appendix"><h2>Appendix: the24 grouped-name cards</h2><p>The named-entry column excludes families and components. These are source groupings, not evolutionary edges.</p>'
    group_rows = []
    for group in sorted(grouped, key=lambda r: r['title'].casefold()):
        members = group['entries']
        n = sum(m['entry_type'] == 'named_method' for m in members)
        label = '<a href="https://www.illumina.com/science/sequencing-method-explorer/kits-and-arrays/' + escape(group['card_id']) + '.html">' + escape(group['title']) + '</a>'
        text = '; '.join(escape(m['method']) + ('' if m['entry_type']=='named_method' else ' [' + escape(m['entry_type'].replace('_',' ')) + ']') for m in members)
        group_rows.append([label, n, text])
    body += table(['Illumina card', 'Named entries', 'Curated entries linked to that card'], group_rows)
    body += '<p class="muted">Numbers are computed from pinned inventory and review records. Source classifications and method identity can be revised when justified, with the changes recorded; these are not claims of273 independent inventions.</p></section></body></html>'
    body = body.replace('<h2>6. A short update you can use</h2>',
        '<p>The earlier provisional318 folders became298 curated entries after21 alias-folder consolidations and one homonym split:318 −21 +1 =298 =273 named methods +25 other resources. This is a separate identity-cleanup calculation.</p><h2>6. A short update you can use</h2>')
    # Normalize compact numeric labels in this dated report, without altering method names or links.
    for old, new in [('has273','has 273'),('these,174','these, 174'),('by poster rows, and172','by poster rows, and 172'),('are132','are 132'),('entries,12','entries, 12'),('two,8','two, 8'),('three,1','three, 1'),('and1','and 1'),('The189','The 189'),('remaining182','remaining 182'),('leaving174','leaving 174'),('by the154','by the 154'),('Includes99','Includes 99'),('are Genome61,Transcriptome135 and Epigenome77','are Genome 61, Transcriptome 135 and Epigenome 77'),('with273','with 273'),('into273','into 273'),('The154','The 154'),('represent174','represent 174'),('add99','add 99'),('identified21','identified 21'),('involving53','involving 53'),('the273','the 273'),('The48','The 48'),('the24','the 24'),('three are DR-Seq,G&amp;T-Seq,scM&amp;T-Seq','three are DR-Seq, G&amp;T-Seq, scM&amp;T-Seq'),('total323','total 323'),('is271:223','is 271: 223'),('poster,44','poster, 44'),('and4','and 4'),('printed1982','printed 1982'),('to a404','to a 404'),('content;616','content; 616'),('All174','All 174'),('retains25','retains 25'),('resources:15','resources: 15'),('kits,5','kits, 5'),('overviews,2','overviews, 2'),('components,1','components, 1'),('platform,1','platform, 1'),('overview and1','overview and 1'),('earlier201','earlier 201'),('of273','of 273'),('273method','273 method')]:
        body = body.replace(old, new)
    for old, new in [('cards,271','cards, 271'), ('not39','not 39'), ('give273','give 273'),
                     ('the172','the 172'), ('The99','The 99'), ('Why154','Why 154'),
                     ('entry,12','entry, 12'), ('of154','of 154'), ('of174','of 174'),
                     ('provisional318','provisional 318'), ('became298','became 298'),
                     ('after21','after 21'), ('split:318','split: 318'), ('=298','= 298'),
                     ('=273','= 273'), ('+25','+ 25'), ('+285','+ 285'), ('+39','+ 39'),
                     ('+102','+ 102'), ('+78','+ 78'), ('+271','+ 271'), ('−172','− 172'),
                     ('+172','+ 172'), ('+99','+ 99'), ('×1','× 1'), ('×2','× 2'),
                     ('×3','× 3'), ('×4','× 4'), ('×5','× 5'), ('+12','+ 12'), ('+8','+ 8'),
                     ('+1','+ 1'), ('−7','− 7'), ('−8','− 8'), ('=189','= 189'), ('=174','= 174'),
                     ('−21','− 21'), ('Sherlock:/','Sherlock: /'), ('library07','library 07')]:
        body = body.replace(old, new)
    OUT.write_text(body)
    print(json.dumps({'document': str(OUT), 'counts': summary['method_counts'],
                      'shared_image_groups': len(shared), 'math_checks': 'passed'}, indent=2))


if __name__ == '__main__':
    main()

"""Read-only card coverage audit against Illumina's public catalogue and taxonomy.

No method reclassification, source publication, model calls or paper downloads.
File integrity, bibliography coverage and scientific review are distinct checks.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from html import escape
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import zlib

from genomics_discovery.illumina.sources import fetch, https_url
from scripts.illumina.technology_sources.inventory import read, sha
from scripts.illumina.technology_sources.materialize import write


PREFIX = 'https://www.illumina.com/content/illumina-marketing/amr/en_US/science/sequencing-method-explorer'


def same_text(a, b):
    return ' '.join((a or '').split()) == ' '.join((b or '').split())


def reference_key(ref):
    if ref.get('pmid'):
        return 'pmid:' + str(ref['pmid'])
    raw = ref.get('source_reference', ref)
    return raw.get('canonical_url') or raw.get('url') or ref.get('reference_id')


def image_header(path):
    """Inspect file structure, not scientific diagram meaning or raster fidelity."""
    data = path.read_bytes()
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        offset = 8
        kinds = []
        width = height = 0
        while offset < len(data):
            if offset + 12 > len(data):
                raise ValueError('Truncated PNG chunk header')
            size = struct.unpack('>I', data[offset:offset + 4])[0]
            chunk = data[offset + 4:offset + 8]
            end = offset + 8 + size
            if end + 4 > len(data):
                raise ValueError('Truncated PNG chunk payload')
            if zlib.crc32(data[offset + 4:end]) != struct.unpack('>I', data[end:end + 4])[0]:
                raise ValueError('PNG chunk CRC differs')
            if chunk == b'IHDR':
                width, height = struct.unpack('>II', data[offset + 8:offset + 16])
            kinds.append(chunk)
            offset = end + 4
            if chunk == b'IEND':
                break
        if not width or not height or b'IDAT' not in kinds or kinds[-1] != b'IEND':
            raise ValueError('PNG lacks required chunks or dimensions')
        return {'format': 'PNG', 'width': width, 'height': height, 'png_chunk_crc_verified': True,
                'image_file_signature_recognized': True, 'full_raster_decoding_performed': False}
    if data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'):
        return {'format': 'JPEG', 'image_file_signature_recognized': True,
                'full_raster_decoding_performed': False}
    raise ValueError('File has no recognized complete PNG/JPEG signature')


def csv_file(path, rows):
    if not rows:
        path.write_text('')
        return
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['source-root', 'view', 'output', 'parser-module']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        raise SystemExit('The complete audit executes inside a Slurm allocation.')
    args.output.mkdir(parents=True, exist_ok=False)
    state = read(args.source_root / 'collection.json')
    curated = read(args.view / 'canonical_inventory.json')['methods']
    inventory = state['inventory']
    cards = {sid: value for sid, value in inventory['occurrences'].items()
             if value['batch'] in {'catalogue', 'initial_two'}}
    spec = importlib.util.spec_from_file_location('frozen_original_card_parser', args.parser_module)
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    accessed = datetime.now(timezone.utc).isoformat()
    receipts = []
    checks = {}

    def verified(item):
        path = args.source_root / item['path']
        key = (str(path), item.get('sha256'))
        if key not in checks:
            checks[key] = bool(path.is_file() and item.get('sha256') and sha(path) == item['sha256'])
        return checks[key]

    def live_json(url, destination):
        result = {'url': url}
        try:
            result['receipt'] = fetch(url, destination, timeout=30)
            value = read(destination)
            if not isinstance(value, dict) or value.get('jcr:primaryType') != 'cq:Page':
                raise ValueError('Expected a public Illumina CMS page, not an error/challenge response')
            result['status'] = 'page_received'
        except Exception as exc:
            result.update(status='not_verified', error=str(exc))
            value = None
        receipts.append(result)
        write(args.output / 'LIVE_REQUESTS.json', receipts)
        return value

    catalogue = live_json(PREFIX + '/kits-and-arrays.1.2.3..json?getLatest=true',
                          args.output / 'live/catalogue.json')
    listed = set(original.children(catalogue)) if catalogue else set()
    classified = set()
    taxonomy_ok = True
    for domain in ['rna', 'dna', 'epigenome']:
        tree = live_json(PREFIX + '/' + domain + '.1.2.3.4.5..json?getLatest=true',
                         args.output / ('live/taxonomy-' + domain + '.json'))
        if tree is None:
            taxonomy_ok = False
            continue
        for section in original.children(tree).values():
            classified.update(original.children(section))
    live_ids = listed | classified
    owners = defaultdict(list)
    for mid, method in curated.items():
        for sid in method['source_records']:
            owners[sid].append(mid)
    rows, method_rows, details = [], [], []
    missing_papers = {}
    for number, sid in enumerate(sorted(set(cards) | live_ids), 1):
        occurrence = cards.get(sid)
        live = live_json(PREFIX + '/kits-and-arrays/' + sid + '.1.2.3.4.5..json?getLatest=true',
                         args.output / ('live/cards/' + sid + '.json'))
        live_card = (original.extract(sid, live, receipts[-1]['url'], accessed)
                     if live and 'jcr:content' in live else None)
        source = state['sources'].get(sid, {})
        saved_cms = (occurrence or {}).get('original_cms', {}).get('content')
        saved_card = (original.extract(sid, saved_cms, (occurrence or {})['card'].get('source_page_url'), accessed)
                      if saved_cms and 'jcr:content' in saved_cms else None)
        references = (occurrence or {}).get('card', {}).get('references', [])
        main_refs = [r for r in references if r.get('role') == 'references']
        expected_refs = (live_card or {}).get('references', [])
        recorded_refs = {reference_key(r) for r in main_refs}
        unrecorded = [r for r in expected_refs if reference_key(r) not in recorded_refs]
        image_rows = []
        for image in source.get('diagrams', []):
            status = {'path': image['path'], 'url': image.get('url'), 'sha256': image.get('sha256'),
                      'file_hash_verified': verified(image), 'visual_interpretation_reviewed': False}
            if status['file_hash_verified']:
                try:
                    status.update(image_header(args.source_root / image['path']))
                except Exception as exc:
                    status.update(image_file_signature_recognized=False, error=str(exc))
            image_rows.append(status)
        valid_images = [r for r in image_rows if r['file_hash_verified'] and r.get('image_file_signature_recognized')]
        source_text_files = [dict(item, file_exists=(args.source_root / item['path']).is_file())
                             for item in source.get('catalogue', [])]
        cms_file = args.source_root / 'shared/depictions' / sid / 'original_cms.json'
        original_cms_saved = bool(cms_file.is_file() and read(cms_file) == saved_cms)
        papers, methods = {}, []
        for mid in sorted(owners[sid]):
            method = curated[mid]
            folder = args.view / method['directory']
            description = read(folder / 'description.json')
            assigned = [r for r in method['method_references'] if r['role'] == 'method_reference']
            paper_ids = set()
            local_missing = set()
            image_links_ok = all((folder / 'diagrams' / sid / Path(i['path']).name).is_file()
                                 for i in source.get('diagrams', [])) and bool(valid_images)
            for ref in assigned:
                pid = ref.get('paper_id') or ref['reference_id']
                paper_ids.add(pid)
                article = [f for f in ref.get('cached_files', []) if f['kind'] == 'article']
                downloaded = ref['availability'] == 'fulltext_acquired' and any(verified(f) for f in article)
                item = {'paper_id': pid, 'title': ref.get('resolved_title') or ref.get('citation'),
                        'url': ref.get('url') or state['papers'].get(pid, {}).get('url'),
                        'reported_availability': ref['availability'], 'downloaded_article_verified': downloaded,
                        'paper_record_in_method_folder': (folder / 'papers' / pid / 'paper.json').is_file(),
                        'all_linked_files_verified': all(verified(f) and (folder / 'papers' / pid / Path(f['path']).name).is_file()
                                                       for f in ref.get('cached_files', [])),
                        'scientific_content_reviewed_by_this_audit': False}
                papers[pid] = item
                if not downloaded:
                    local_missing.add(pid)
                    missing_papers.setdefault(pid, dict(item, method_ids=set(), card_ids=set()))['method_ids'].add(mid)
                    missing_papers[pid]['card_ids'].add(sid)
            row = {'card_id': sid, 'method_id': mid, 'method': method['name'], 'entry_type': method['entry_type'],
                   'directory': method['directory'], 'separate_method_record_exists': (folder / 'method.json').is_file(),
                   'card_link_exists': (folder / 'catalogue' / sid / 'source.json').is_file(),
                   'diagram_links_valid': image_links_ok, 'published_description_source': description.get('source_kind'),
                   'published_description_present': bool(description.get('text')),
                   'published_description_is_full_card_text': same_text(description.get('text'), (saved_card or {}).get('description_verbatim')),
                   'assigned_method_paper_ids': '; '.join(sorted(paper_ids)),
                   'missing_method_paper_ids': '; '.join(sorted(local_missing)),
                   'method_specific_paper_assignment_present': bool(paper_ids)}
            methods.append(row)
            method_rows.append(row)
        record = {'card_id': sid, 'title': (live_card or {}).get('source_title') or (occurrence or {}).get('title') or sid,
                  'card_url': PREFIX + '/kits-and-arrays/' + sid + '.html',
                  'in_current_catalogue': sid in listed, 'in_current_taxonomy': sid in classified,
                  'saved_card_record': occurrence is not None, 'live_content_received': live is not None,
                  'live_content_empty': bool(live is not None and 'jcr:content' not in live),
                  'original_cms_saved': original_cms_saved,
                  'saved_illumina_description_present': bool((saved_card or {}).get('description_verbatim')),
                  'live_illumina_description_present': bool((live_card or {}).get('description_verbatim')),
                  'description_unchanged': bool(saved_card and live_card and same_text(saved_card['description_verbatim'], live_card['description_verbatim'])),
                  'original_diagram_files_verified': len(valid_images),
                  'original_reference_count': len(main_refs), 'live_reference_count': len(expected_refs),
                  'live_references_missing_from_saved_record': len(unrecorded),
                  'mapped_entry_ids': '; '.join(sorted(owners[sid])),
                  'named_methods': sum(m['entry_type'] == 'named_method' for m in methods),
                  'unique_assigned_papers': len(papers),
                  'downloaded_assigned_papers_verified': sum(p['downloaded_article_verified'] for p in papers.values()),
                  'all_assigned_papers_downloaded': bool(papers) and all(p['downloaded_article_verified'] for p in papers.values())}
        rows.append(record)
        details.append(dict(record, saved_card=saved_card, live_card=live_card, diagrams=image_rows,
                            existing_catalogue_text_files=source_text_files, methods=methods,
                            assigned_papers=list(papers.values()), unrecorded_live_references=unrecorded))
        write(args.output / 'CARD_DETAILS.json', details)
        print(number, sid, 'images', len(valid_images), 'papers', record['downloaded_assigned_papers_verified'], '/', len(papers), flush=True)
    csv_file(args.output / 'CARD_CHECKLIST.csv', rows)
    csv_file(args.output / 'CARD_METHODS.csv', method_rows)
    csv_file(args.output / 'MISSING_CARD_PAPERS.csv', [dict(r, method_ids='; '.join(sorted(r['method_ids'])),
              card_ids='; '.join(sorted(r['card_ids']))) for r in missing_papers.values()])
    summary = {'as_of_utc': accessed, 'slurm_job_id': os.environ['SLURM_JOB_ID'],
               'published_library': str(args.view.resolve()), 'source_vault': str(args.source_root.resolve()),
               'source_collection_sha256': sha(args.source_root / 'collection.json'),
               'current_catalogue_verified': catalogue is not None, 'current_taxonomy_verified': taxonomy_ok,
               'current_catalogue_cards': len(listed), 'current_taxonomy_cards': len(classified),
               'saved_website_cards': len(cards), 'current_cards_missing_from_library': sorted(live_ids - set(cards)),
               'saved_cards_not_currently_listed': sorted(set(cards) - live_ids) if catalogue and taxonomy_ok else None,
               'cards_with_saved_illumina_description': sum(r['saved_illumina_description_present'] for r in rows),
               'cards_with_verified_original_diagram': sum(r['original_diagram_files_verified'] > 0 for r in rows),
               'cards_with_all_assigned_papers_downloaded': sum(r['all_assigned_papers_downloaded'] for r in rows),
               'unique_assigned_publications_not_downloaded': len(missing_papers),
               'distinct_named_methods_on_cards': len({r['method_id'] for r in method_rows if r['entry_type'] == 'named_method'}),
               'named_methods_missing_paper_assignments': sorted({r['method_id'] for r in method_rows if r['entry_type']=='named_method' and not r['method_specific_paper_assignment_present']}),
               'file_hash_checks': len(checks), 'failed_file_hash_checks': sum(not v for v in checks.values()),
               'live_request_statuses': dict(Counter(r['status'] for r in receipts)),
               'new_model_calls': 0, 'workflow_extractions': 0, 'scientific_paper_reviews': 0,
               'limits': 'Bibliography, source-description and file-availability audit; no scientific interpretation or workflow verification.'}
    write(args.output / 'SUMMARY.json', summary)
    body = '<!doctype html><meta charset="utf-8"><title>Illumina card coverage</title><style>body{font:16px Arial,sans-serif;margin:32px;color:#222}table{border-collapse:collapse;width:100%}td,th{padding:10px;text-align:left;border-bottom:1px solid #ddd}pre{white-space:pre-wrap}</style><h1>Illumina card coverage</h1><p>One row per website card. Shared cards link to separate method entries. Paper listing and full-text acquisition are reported separately.</p><pre>' + escape(json.dumps(summary, indent=2)) + '</pre><table><tr><th>Card</th><th>Description saved</th><th>Diagram verified</th><th>Downloaded / assigned papers</th><th>Method entries</th></tr>'
    for r in rows:
        body += '<tr><td><a href="' + escape(r['card_url']) + '">' + escape(r['title']) + '</a></td><td>' + str(r['saved_illumina_description_present']) + '</td><td>' + str(r['original_diagram_files_verified']) + '</td><td>' + str(r['downloaded_assigned_papers_verified']) + ' / ' + str(r['unique_assigned_papers']) + '</td><td>' + escape(r['mapped_entry_ids']) + '</td></tr>'
    (args.output / 'index.html').write_text(body + '</table>')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()

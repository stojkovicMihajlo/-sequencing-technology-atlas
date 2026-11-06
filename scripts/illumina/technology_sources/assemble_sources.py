"""Assemble a fresh source vault from reviewed identities and checked PDF views.

No old source vault is edited. New assets retain their acquisition or rendering
receipts. Added publications describe an existing method and do not add methods.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlencode

from genomics_discovery.illumina.sources import fetch, parse_pubmed
from scripts.illumina.external_sources import PMCArticleHTML
from .article_identity import normalized_title
from .inventory import read, sha
from .materialize import write
from .review_papers import prepare


def store_asset(root, state, source, relative, kind, origin):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    item = {'path': relative, 'sha256': sha(target), 'kind': kind}
    state['files'][relative] = {**item, 'bytes': target.stat().st_size, 'origins': [origin]}
    return item


def reuse_registered_html(root, state, cache_index, project_root):
    index_path = Path(cache_index)
    registered = read(index_path)['files']
    write(root / 'CACHE_REGISTRATION_INDEX.json', read(index_path))
    results = []
    for path, digest in registered.items():
        match = re.search(r'/papers/(\d+)/article\.html$', path)
        if not match:
            continue
        pid = 'pmid-' + match.group(1)
        paper = state['papers'].get(pid)
        if not paper or paper['status'] == 'fulltext_acquired':
            continue
        source = Path(path)
        result = {'paper_id': pid, 'cached_original': path, 'registered_sha256': digest,
                  'registration_index': str(index_path), 'registration_index_sha256': sha(index_path)}
        try:
            if not source.resolve().is_relative_to(Path(project_root).resolve()) or sha(source) != digest:
                raise ValueError('Cached path or bytes differ from project registration')
            parser = PMCArticleHTML()
            parser.feed(source.read_text(errors='replace'))
            title = paper.get('metadata', {}).get('title') or paper.get('title') or ''
            if parser.citations.get('citation_pmid') != [paper['pmid']]:
                raise ValueError('HTML own PMID does not match assigned paper')
            titles = parser.citations.get('citation_title', [])
            if len(titles) != 1 or normalized_title(titles[0]) != normalized_title(title):
                raise ValueError('HTML own title requires identity review')
            expected_dois = {d.lower() for d in paper.get('metadata', {}).get('identifiers', {}).get('doi', [])}
            actual_dois = {d.lower() for d in parser.citations.get('citation_doi', [])}
            if expected_dois and actual_dois and not expected_dois & actual_dois:
                raise ValueError('HTML own DOI differs from assigned paper')
            text = parser.text()
            if len(text) < 1000:
                raise ValueError('No substantive main-article-body section')
            item = store_asset(root, state, source, 'new_assets/cached_papers/' + paper['pmid'] + '/article.html', 'article', result)
            paper['files'].append(item)
            tp = root / 'new_assets/cached_papers' / paper['pmid'] / 'reading-text.txt'
            tp.write_text('Title: ' + titles[0] + '\nPMID: ' + paper['pmid'] + '\n\n' + text)
            item = {'path': str(tp.relative_to(root)), 'sha256': sha(tp), 'kind': 'reading-text'}
            state['files'][item['path']] = {**item, 'bytes': tp.stat().st_size, 'origins': [result]}
            paper['files'].append(item)
            paper['status'] = 'fulltext_acquired'
            paper['selected_recovery_status'] = 'reused_hash_registered_html; own_pmid_title_and_main_body_checked'
            write(root / 'shared/papers' / pid / 'paper.json', paper)
            result.update(status='reused', inspected='Citation metadata and substantive main-article-body; not full scientific protocol review')
        except Exception as exc:
            result.update(status='not_admitted', reason=str(exc))
        results.append(result)
    write(root / 'CACHE_REUSE_RECEIPTS.json', results)
    return results


def assemble(source_root, output, decisions, bindings, inspection, additions, cache_index=None, project_root=None):
    root = Path(output)
    prepare(source_root, root, decisions, bindings)
    state = read(root / 'collection.json')
    cache_receipts = reuse_registered_html(root, state, cache_index, project_root) if cache_index else []
    receipts = []
    for addition in additions:
        pid, pmid = addition['paper_id'], addition['pmid']
        directory = root / 'acquisition_additions' / pid
        directory.mkdir(parents=True)
        try:
            meta_path = directory / 'pubmed.xml'
            meta_receipt = fetch('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?' + urlencode({
                'db': 'pubmed', 'id': pmid, 'retmode': 'xml'}), meta_path, timeout=45)
            meta = parse_pubmed(meta_path.read_bytes())[pmid]
            if addition['doi'] not in meta.get('identifiers', {}).get('doi', []):
                raise ValueError('Added publication DOI mismatch')
            if normalized_title(meta['title']) != normalized_title(addition['title']):
                raise ValueError('Added publication title mismatch')
            pdf = directory / 'article.pdf'
            receipt = fetch(addition['public_pdf_url'], pdf, timeout=65)
            if not pdf.read_bytes().startswith(b'%PDF-'):
                raise ValueError('Repository response is not a PDF')
            front = subprocess.run(['pdftotext', '-f', '1', '-l', '2', str(pdf), '-'],
                check=True, capture_output=True, timeout=35).stdout.decode(errors='replace')
            if normalized_title(meta['title']) not in normalized_title(front) or addition['doi'] not in front:
                raise ValueError('Repository PDF front matter does not establish expected title and DOI')
            text = directory / 'article.txt'
            subprocess.run(['pdftotext', str(pdf), str(text)], check=True, capture_output=True, timeout=60)
            paper = state['papers'][pid]
            paper.update(title=meta['title'], metadata=meta, url='https://pubmed.ncbi.nlm.nih.gov/' + pmid + '/',
                         status='fulltext_acquired', publication_role=addition['publication_role'])
            for source, kind in [(pdf, 'article'), (text, 'reading-text')]:
                paper['files'].append(store_asset(root, state, source,
                    'shared/papers/' + pid + '/repository-' + source.name, kind, receipt))
            write(root / 'shared/papers' / pid / 'paper.json', paper)
            receipts.append({'paper_id': pid, 'status': 'acquired', 'metadata': meta_receipt,
                'article': receipt, 'identity_check': 'Own PubMed DOI/title plus repository PDF cover title and DOI',
                'version': addition['version'], 'publication_role': addition['publication_role']})
        except Exception as exc:
            receipts.append({'paper_id': pid, 'status': 'not_acquired', 'error': str(exc),
                             'public_pdf_url': addition['public_pdf_url']})
    inspection = Path(inspection)
    views = read(inspection / 'ROW_VIEWS.json')
    if len(views) != 285 or len({v['source_id'] for v in views}) != 285:
        raise ValueError('Complete original-poster row inventory required')
    for view in views:
        image = Path(view['image_path'])
        if sha(image) != view['image_sha256']:
            raise ValueError('Row image differs from inspected geometry record')
        rid = view['source_id']
        if rid not in state['sources']:
            raise ValueError('Unknown poster source occurrence')
        item = store_asset(root, state, image, 'new_assets/poster_rows/' + rid + '.png',
            'image', {'original_pdf_sha256': view['original_pdf_sha256'], 'pdf_point_box': view['pdf_point_box'],
                      'inspection_manifest_sha256': sha(inspection / 'ROW_VIEWS.json')})
        for old in state['sources'][rid]['diagrams']:
            old['prior_quality'] = old.get('quality')
            old['source_kind'] = 'legacy_broad_context_crop'
            old['quality'] = 'broad_context; may_include_neighboring_rows_or_legends; not_an_isolated_workflow'
        state['sources'][rid]['diagrams'].insert(0, {**item, 'source_id': 'row-view:' + rid,
            'source_kind': 'poster_row_view', 'url': view['row']['source_url'],
            'quality': 'own_label_contained; neighbor_labels_excluded; visual_review_pending',
            'pdf_point_box': view['pdf_point_box'], 'original_pdf_sha256': view['original_pdf_sha256']})
    state['method_descriptions'], state['method_guide_sources'] = {}, {}
    poster_only = read(inspection / 'POSTER_ONLY_SOURCES.json')
    for mid, method in poster_only.items():
        leads = []
        for match in method['guide_page_matches']:
            guide = root / 'shared/documents' / match['guide']
            if sha(guide) != match['pdf_sha256']:
                raise ValueError('Methods guide differs from recorded original')
            lead = {k: v for k, v in match.items() if k != 'page_text'}
            page_text = match['page_text']
            target = root / 'new_assets/guide_pages' / mid / (match['guide'] + '-p' + str(match['pdf_page']) + '.txt')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(page_text)
            item = {'path': str(target.relative_to(root)), 'sha256': sha(target), 'kind': 'guide-page-text'}
            state['files'][item['path']] = {**item, 'bytes': target.stat().st_size,
                'origins': [{'guide_sha256': match['pdf_sha256'], 'pdf_page': match['pdf_page']}]}
            lead.update(text_file=item, pdf_path=str(guide.relative_to(root)))
            leads.append(lead)
            for name in method['aliases']:
                header = re.search(r'(?mi)^\s*' + re.escape(name) + r'\s*:\s*[^\n]+\n', page_text)
                if header and 'TABLE OF CONTENTS' not in page_text[:200].upper() and mid not in state['method_descriptions']:
                    following = page_text[header.end():].strip().split('\n\n')[0]
                    excerpt = ' '.join(following.split())
                    if len(excerpt) > 100:
                        state['method_descriptions'][mid] = {'text': excerpt,
                            'source_kind': 'illumina_methods_guide_excerpt', 'locator': 'PDF page ' + str(match['pdf_page']),
                            'url': 'https://assets.illumina.com/content/dam/illumina-marketing/documents/products/research_reviews/' + match['guide'] + '#page=' + str(match['pdf_page']),
                            'source_pdf_sha256': match['pdf_sha256'], 'status': 'Method heading located; source excerpt with normalized whitespace.'}
        state['method_guide_sources'][mid] = leads
    write(root / 'SOURCE_ADDITION_RECEIPTS.json', receipts)
    write(root / 'POSTER_ROW_PROVENANCE.json', views)
    write(root / 'collection.json', state)
    return {'poster_row_views': len(views), 'added_publications': receipts,
            'cached_html_papers_reused': sum(r['status'] == 'reused' for r in cache_receipts),
            'poster_only_with_guide_page_leads': sum(bool(v) for v in state['method_guide_sources'].values()),
            'poster_only_with_guide_description': len(state['method_descriptions'])}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ['source-root', 'output', 'decisions', 'bindings', 'inspection', 'additions']:
        p.add_argument('--'+key, type=Path, required=True)
    p.add_argument('--cache-index', type=Path)
    p.add_argument('--project-root', type=Path)
    a=p.parse_args()
    result=assemble(a.source_root, a.output, read(a.decisions), read(a.bindings), a.inspection, read(a.additions), a.cache_index, a.project_root)
    write(a.output/'ASSEMBLY_SUMMARY.json',result)
    print(json.dumps(result),flush=True)


if __name__ == '__main__':
    main()

"""Publish reviewed method folders from a preserved source vault.

python -m scripts.illumina.technology_sources.publish_curated --help
"""
from __future__ import annotations

import argparse
import csv
from html import escape
import json
import os
from pathlib import Path

from .curation import compile_inventory
from .classification import placements, method_directory
from .descriptions import describe, apply_description_reviews
from .inventory import read, sha
from .materialize import link, write
from .render import page


def table_file(path, rows):
    with path.open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def apply_diagram_rejections(state, review):
    """Bind negative visual findings to exact images, without editing source bytes.

    This does not approve any image or workflow. A method-title geometry check
    and successful file acquisition are not scientific workflow verification.
    """
    matched = []
    seen = set()
    for finding in review.get('poster_previews', {}).get('reviewed_examples', []):
        rid, digest = finding['source_id'], finding['image_sha256']
        if (rid, digest) in seen:
            raise ValueError('Duplicate image-quality finding: ' + rid)
        seen.add((rid, digest))
        if finding['status'] != 'rejected_as_complete_workflow_view':
            raise ValueError('Source-quality holds cannot approve a workflow: ' + rid)
        if not finding.get('finding') or not finding.get('reviewer'):
            raise ValueError('Image-quality rejection requires a finding and reviewer: ' + rid)
        images = [item for item in state['sources'].get(rid, {}).get('diagrams', [])
                  if item['sha256'] == digest]
        if not images:
            raise ValueError('Image-quality finding does not match source and bytes: ' + rid)
        matched.extend((item, finding) for item in images)
    # Validate the whole review before changing the in-memory publication view.
    for item, finding in matched:
        item['quality_before_review'] = item.get('quality')
        item['quality'] = finding['status']
        item['visual_review'] = finding


def publish(source_root, output, decisions, bindings, source_holds=None, category_assignments=None,
            source_quality_review=None, description_reviews=None, poster_only_reviews=None):
    source_root, output = Path(source_root).resolve(), Path(output).resolve()
    state = read(source_root / 'collection.json')
    source_quality_review = source_quality_review or {}
    apply_diagram_rejections(state, source_quality_review)
    result = compile_inventory(state['inventory'], decisions, bindings, state['papers'])
    from .source_report import validate_poster_only_reviews
    reviewed_sources = (validate_poster_only_reviews(state['inventory'], result, state['papers'], poster_only_reviews)
                        if poster_only_reviews is not None else {})
    apply_description_reviews(source_root, state, result['methods'], description_reviews or {})
    source_holds = source_holds or {'holds': []}
    category_assignments = category_assignments or {}
    held = {f['sha256'] for h in source_holds['holds'] for f in h['files']}
    held_papers = {h['paper_id'] for h in source_holds['holds']}
    for method in result['methods'].values():
        for ref in method['method_references']:
            blocked = ref.get('paper_id') in held_papers
            ref['held_files'] = [f for f in ref['cached_files'] if blocked or f['sha256'] in held]
            ref['cached_files'] = [f for f in ref['cached_files'] if not blocked and f['sha256'] not in held]
            if ref['held_files'] and not any(f['kind'] == 'article' for f in ref['cached_files']):
                ref['availability'] = 'source_identity_hold'
    output.mkdir(parents=True, exist_ok=False)
    # Original bytes remain in the preserved sibling vault. The two directories
    # must be archived/restored together; the new view has no duplicate PDFs.
    link(source_root / 'shared', output / 'shared')
    write(output / 'IDENTITY_DECISIONS.json', decisions)
    write(output / 'REFERENCE_BINDINGS.json', bindings)
    write(output / 'EXCLUDED_REFERENCES.json', result['excluded_references'])
    write(output / 'SOURCE_HOLDS.json', source_holds)
    write(output / 'CATEGORY_ASSIGNMENTS.json', category_assignments)
    write(output / 'SOURCE_QUALITY_REVIEW.json', source_quality_review)
    write(output / 'DESCRIPTION_REVIEWS.json', description_reviews or {})
    write(output / 'POSTER_ONLY_REVIEW.json', poster_only_reviews or [])
    workflow_validation = {'status': 'not_performed', 'paper_confirmation_required': True,
                          'poster_crop_geometry_is_insufficient': True}
    rows = []
    for mid, m in sorted(result['methods'].items()):
        m['category_assignments'] = category_assignments.get(mid, [])
        if mid in reviewed_sources:
            m['source_curation_review'] = reviewed_sources[mid]
        m['extraction_authorized'] = False
        m['workflow_validation'] = dict(workflow_validation)
        m['diagram_quality_holds'] = [finding
            for finding in source_quality_review.get('poster_previews', {}).get('reviewed_examples', [])
            if finding['source_id'] in m['source_records']]
        m['placements'] = placements(m, m['category_assignments'])
        directory = output / method_directory(m, m['placements'])
        directory.mkdir(parents=True)
        m['directory'] = str(directory.relative_to(output))
        m['groups'] = sorted({p['category'] for p in m['placements']})
        if m['entry_type'] == 'named_method':
            for p in m['placements'][1:]:
                link(directory, output / 'methods' / p['category'] / p['subclassification'] / mid)
        index_path = os.path.relpath(output / 'index.html', directory)
        body = '<h1>' + escape(m['name']) + '</h1><p><a href="' + escape(index_path) + '">Index</a> · <a href="method.json">Method record</a> · <a href="references.json">Selected references</a></p>'
        body += '<p>' + escape(m['entry_type']) + ' · ' + escape(m['identity_status']) + '</p>'
        body += '<p><b>Source collection, not a verified workflow.</b> The method paper or published protocol must confirm operations, order and material routing. Diagrams guide scope and level of detail.</p>'
        description = describe(state, m)
        m['description'] = description
        write(directory / 'description.json', description)
        (directory / 'description.txt').write_text(m['name'] + '\n\n' +
            (description['text'] or 'Description unavailable in the collected sources.') +
            '\n\nSource: ' + str(description.get('url') or description['status']) + '\n')
        if description['text']:
            body += '<h2>What the method measures</h2><p>' + escape(description['text']).replace('\n', '<br>') + '</p><p>Source: ' + escape(description['source_kind']) + '</p>'
        if mid in reviewed_sources:
            body += '<h2>Source scope</h2><p>' + escape(reviewed_sources[mid]['scope_note']) + '</p>'
        body += '<h2>Identity and scope</h2>' + ''.join('<p>' + escape(d['reason']) + '</p>' for d in m['identity_decisions'])
        body += '<p>' + escape(m['review_limits']) + '</p>'
        body += '<p>Groups: ' + escape(', '.join(m['groups'])) + '</p><p><a href="source_comparison.json">Card/poster names, references and recorded differences</a></p>'
        body += '<h2>Original depictions</h2><p>Shared drawings and their full bibliographies remain visible. They are not interchangeable workflows.</p>'
        diagrams = set()
        for rid in m['source_records']:
            src = state['sources'].get(rid, {})
            occurrence = state['inventory']['occurrences'][rid]
            link(source_root / 'shared/depictions' / rid, directory / 'catalogue' / rid)
            body += '<h3>' + escape(occurrence['title']) + '</h3><p><a href="catalogue/' + escape(rid) + '/source.json">Original source record</a></p>'
            for item in src.get('catalogue', []):
                body += '<details><summary>' + escape(item.get('title') or item['source_id']) + '</summary><pre>' + escape((source_root / item['path']).read_text()) + '</pre></details>'
            for item in src.get('diagrams', []):
                dest = directory / 'diagrams' / rid / Path(item['path']).name
                link(source_root / item['path'], dest)
                rel = dest.relative_to(directory).as_posix()
                label = ('Row view from original poster: label geometry checked; visual review pending'
                         if item.get('source_kind') == 'poster_row_view' else 'Original source image')
                img = '<p><a href="' + escape(rel) + '">' + label + '</a></p><img loading="lazy" src="' + escape(rel) + '">'
                if item.get('visual_review'):
                    body += '<details><summary>Rejected preview: ' + escape(item['visual_review']['finding']) + '</summary>' + img + '</details>'
                elif item.get('source_kind') == 'poster_row_view':
                    body += '<details><summary>Unreviewed poster preview; complete workflow capture has not been checked</summary>' + img + '</details>'
                elif item.get('source_kind') == 'legacy_broad_context_crop':
                    body += '<details><summary>Original broad context crop; neighboring rows may appear</summary>' + img + '</details>'
                else:
                    body += img
                diagrams.add(item['sha256'])
            for item in src.get('documents', []):
                folder = 'posters' if 'for-all-you-seq' in item['path'] else 'guides'
                dest = directory / folder / Path(item['path']).name
                link(source_root / item['path'], dest)
                body += '<p><a href="' + escape(dest.relative_to(directory).as_posix()) + '">Complete PDF: ' + escape(dest.name) + '</a></p>'
        m['guide_sources'] = state.get('method_guide_sources', {}).get(mid, [])
        if m['guide_sources']:
            write(directory / 'guide_source_matches.json', m['guide_sources'])
            body += '<h2>Illumina methods-guide pages</h2><p>Literal name matches are source leads; the page may describe a broader or related implementation.</p>'
            for guide in m['guide_sources']:
                target = directory / 'guides' / guide['guide']
                link(source_root / guide['pdf_path'], target)
                text_item = guide['text_file']
                text_target = directory / 'guides' / Path(text_item['path']).name
                link(source_root / text_item['path'], text_target)
                body += '<p><a href="' + escape(target.relative_to(directory).as_posix()) + '#page=' + str(guide['pdf_page']) + '">' + escape(guide['guide']) + ', PDF page ' + str(guide['pdf_page']) + '</a> · <a href="' + escape(text_target.relative_to(directory).as_posix()) + '">Page text</a></p>'
        body += '<h2>Method-specific references</h2><p>Roles below distinguish method descriptions, foundations, reviews and other supporting work. Related references from shared sources are kept in source_references.json.</p>'
        seen = set()
        paper_rows = []
        for b in m['method_references']:
            pid = b.get('paper_id')
            key = (pid or b['reference_id'], b['role'])
            if key in seen:
                continue
            seen.add(key)
            title = b.get('resolved_title') or b.get('citation') or b['reference_id']
            body += '<p><b>' + escape(b.get('publication_role') or b['role']) + '</b>: ' + escape(title) + '<br>' + escape(b['availability'])
            if b.get('scope_note'):
                body += '<br><b>What this publication supports:</b> ' + escape(b['scope_note'])
            if pid in state['papers']:
                paper_directory = directory / 'papers' / pid
                paper_directory.mkdir(parents=True, exist_ok=True)
                write(paper_directory / 'paper.json', {**state['papers'][pid],
                    'files': b['cached_files'], 'status': b['availability'], 'held_files': b['held_files']})
                for item in b['cached_files']:
                    link(source_root / item['path'], paper_directory / Path(item['path']).name)
                    rel = 'papers/' + pid + '/' + Path(item['path']).name
                    body += ' · <a href="' + escape(rel) + '">' + escape(item['kind']) + '</a>'
            if b['held_files']:
                body += '<br>Cached article identity conflict: affected files are excluded. The correct citation is retained.'
            paper_rows.append({'paper_id': pid, 'title': title, 'role': b['role'],
                'availability': b['availability'], 'files': b['cached_files'],
                'source_reference_ids': sorted({r['reference_id'] for r in m['method_references']
                    if r.get('paper_id') == pid and r['role'] == b['role']})})
            if b.get('url'):
                body += ' · <a href="' + escape(b['url']) + '">Publication/source</a>'
            body += '</p>'
        if not m['method_references']:
            body += '<p>No method-specific paper assigned. This entry cannot be treated as paper-ready.</p>'
        (directory / 'index.html').write_text(page(m['name'], body))
        write(directory / 'method.json', m)
        write(directory / 'references.json', m['method_references'])
        write(directory / 'papers.json', paper_rows)
        write(directory / 'source_references.json', m['source_references'])
        (directory / 'README.txt').write_text(m['name'] + '\n\n' + '\n'.join(d['reason'] for d in m['identity_decisions']) +
            '\n\ndescription.txt and description.json: attributed assay description; its source is explicit.\n'
            'method.json: identity, scope, original classifications and source membership.\n'
            'references.json: reviewed method-specific associations and file availability.\n'
            'source_references.json: original source bibliographies, including sibling methods.\n'
            'catalogue/, diagrams/, posters/, guides/: original sources, unchanged.\n'
            'papers/: only assigned papers; metadata-only records are labelled.\n'
            'index.html: readable view of these same records. No new extraction run.\n')
        method_refs = [b for b in m['method_references'] if b['role'] == 'method_reference']
        has_card = any(state['inventory']['occurrences'][r]['batch'] in {'catalogue','initial_two'} for r in m['source_records'])
        has_poster = any(state['inventory']['occurrences'][r]['batch'] == 'posters' for r in m['source_records'])
        source_coverage = 'card_and_poster' if has_card and has_poster else 'card_only' if has_card else 'poster_only' if has_poster else 'footer_only'
        rows.append({'id': mid, 'name': m['name'], 'directory': m['directory'], 'entry_type': m['entry_type'],
                     'source_coverage': source_coverage,
                     'previous_folders': '; '.join(m['previous_ids']), 'depictions': len(m['source_records']),
                     'unique_diagram_files': len(diagrams), 'identity_status': m['identity_status'],
                     'method_papers': len({b.get('paper_id') or b['reference_id'] for b in method_refs}),
            'method_fulltexts': len({b['paper_id'] for b in method_refs if b['availability'] == 'fulltext_acquired'}),
            'paper_identity_holds': len({b['paper_id'] for b in method_refs if b['held_files']}),
            'groups': '; '.join(m['groups']),
            'subclassifications': '; '.join(p['category'] + ' > ' + p['subclassification'] for p in m['placements']),
            'description_source': description.get('source_kind', description['status']),
                     'diagram_review': 'not_approved_by_identity_review', 'extraction_status': 'not_scheduled'})
    # One integrity pass covers all original bytes, then every generated link.
    errors = []
    for item in state['files'].values():
        path = source_root / item['path']
        if not path.is_file() or sha(path) != item['sha256']:
            errors.append('Source missing or changed: ' + item['path'])
    allowed_roots = [output, source_root]
    ancestor = state
    while ancestor.get('upstream_source_vault'):
        upstream = Path(ancestor['upstream_source_vault']).resolve()
        if upstream in allowed_roots:
            raise ValueError('Cyclic source vault ancestry')
        if sha(upstream / 'collection.json') != ancestor['upstream_collection_sha256']:
            raise ValueError('Upstream source vault changed')
        allowed_roots.append(upstream)
        ancestor = read(upstream / 'collection.json')
    for current, dirs, files in os.walk(output, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            if path.is_symlink() and (not path.exists() or not any(path.resolve().is_relative_to(p) for p in allowed_roots)):
                errors.append('Broken/out-of-vault link: ' + str(path))
    result['source_vault'] = os.path.relpath(source_root, output)
    result['source_collection_sha256'] = sha(source_root / 'collection.json')
    result['validation'] = {'passed': not errors, 'errors': errors, 'original_files_checked': len(state['files'])}
    table_file(output / 'CHECKLIST.csv', rows)
    poster_rows = [r for r in rows if r['entry_type'] == 'named_method' and r['source_coverage'] == 'poster_only']
    if poster_rows:
        table_file(output / 'POSTER_ONLY_CHECKLIST.csv', poster_rows)
    missing = []
    for mid, method in result['methods'].items():
        if method['entry_type'] != 'named_method':
            continue
        for ref in method['method_references']:
            if ref['role'] == 'method_reference' and ref['availability'] != 'fulltext_acquired':
                paper = state['papers'].get(ref.get('paper_id'), {})
                item = {'method_id': mid, 'method': method['name'], 'directory': method['directory'],
                    'paper_id': ref.get('paper_id'), 'paper_title': ref.get('resolved_title') or paper.get('title'),
                    'availability': ref['availability'], 'publication_url': ref.get('url') or paper.get('url'),
                    'retrieval_status': paper.get('selected_recovery_status') or paper.get('retrieval_status', 'not_recorded')}
                if item not in missing:
                    missing.append(item)
    if missing:
        table_file(output / 'MISSING_METHOD_PAPER_FILES.csv', missing)
    table_file(output / 'FOLDER_DECISIONS.csv', [{**r, 'source_ids': '; '.join(r['source_ids'])} for r in result['folder_decisions']])
    write(output / 'canonical_inventory.json', result)
    methods = [r for r in rows if r['entry_type'] == 'named_method']
    result['counts']['named_methods_with_diagram'] = sum(r['unique_diagram_files'] > 0 for r in methods)
    result['counts']['named_methods_with_downloaded_method_paper'] = sum(r['method_fulltexts'] > 0 for r in methods)
    result['counts']['named_methods_without_downloaded_method_paper'] = sum(r['method_fulltexts'] == 0 for r in methods)
    from .source_report import publish_report
    report = publish_report(output, state['inventory'], result, state['sources'])
    result['counts'] = report['counts']
    write(output / 'canonical_inventory.json', result)
    write(output / 'SUMMARY.json', {'counts': result['counts'], 'validation': result['validation'],
                                  'extraction_authorized': False, 'workflow_validation': workflow_validation})
    table = ''.join('<tr><td><a href="' + escape(r['directory']) + '/index.html">' + escape(r['name']) + '</a></td><td>' + escape(r['entry_type']) + '</td><td>' + str(r['depictions']) + '</td><td>' + str(r['method_fulltexts']) + '/' + str(r['method_papers']) + '</td></tr>' for r in rows)
    (output / 'index.html').write_text(page('Curated Illumina sources', '<h1>Curated Illumina source inventory</h1><p>One entry per reviewed catalogue method or scoped lineage. Resources and unresolved labels are explicitly separated. This does not count independent inventions or approve protocol extraction.</p><p><a href="FOLDER_DECISIONS.csv">Every original folder and its disposition</a> · <a href="CHECKLIST.csv">Coverage and availability</a></p><input id="q" placeholder="Find a method"><table><thead><tr><th>Entry</th><th>Type</th><th>Depictions</th><th>Method fulltexts / papers</th></tr></thead><tbody>' + table + '</tbody></table><script>document.getElementById("q").oninput=function(){for(const r of document.querySelectorAll("tbody tr")){r.hidden=!r.innerText.toLowerCase().includes(this.value.toLowerCase())}}</script>'))
    (output / 'README.txt').write_text('CURATED SOURCES — NOT EXTRACTED GRAPHS\n\nOpen index.html; FOLDER_DECISIONS.csv accounts for every original folder.\n'
        'methods/<Category>/<Subclassification>/<method>/ contains one canonical folder per named assay.\n'
        'Methods with several placements have relative links to that same folder; links do not add to the count.\n'
        'resources/<type>/ contains families, kits, components and unresolved labels.\n'
        'method.json preserves original categories and the evidence for curator placements.\n'
        'Source files link to the preserved sibling vault: ' + result['source_vault'] + '\n'
        'Archive or restore both directories together. Identity review is not experimental validation.\n')
    if errors:
        raise ValueError('Publication integrity check failed; inspect SUMMARY.json')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--decisions', required=True, type=Path)
    p.add_argument('--bindings', required=True, type=Path)
    p.add_argument('--source-holds', type=Path)
    p.add_argument('--category-assignments', type=Path)
    p.add_argument('--source-quality-review', type=Path,
                   help='Recorded visual rejections, matched to exact source IDs and image hashes')
    p.add_argument('--description-reviews', type=Path)
    p.add_argument('--poster-only-reviews', type=Path)
    args = p.parse_args()
    result = publish(args.source_root, args.output, read(args.decisions), read(args.bindings),
        read(args.source_holds) if args.source_holds else None,
        read(args.category_assignments) if args.category_assignments else None,
        read(args.source_quality_review) if args.source_quality_review else None,
        read(args.description_reviews) if args.description_reviews else None,
        read(args.poster_only_reviews) if args.poster_only_reviews else None)
    print(json.dumps({'counts': result['counts'], 'validation': result['validation']}, indent=2))


if __name__ == '__main__':
    main()

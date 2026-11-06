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
from .inventory import catalogue_group, read, sha, slug
from .materialize import link, write
from .render import page


def table_file(path, rows):
    with path.open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def publish(source_root, output, decisions, bindings):
    source_root, output = Path(source_root).resolve(), Path(output).resolve()
    state = read(source_root / 'collection.json')
    result = compile_inventory(state['inventory'], decisions, bindings, state['papers'])
    output.mkdir(parents=True, exist_ok=False)
    # Original bytes remain in the preserved sibling vault. The two directories
    # must be archived/restored together; the new view has no duplicate PDFs.
    link(source_root / 'shared', output / 'shared')
    write(output / 'IDENTITY_DECISIONS.json', decisions)
    write(output / 'REFERENCE_BINDINGS.json', bindings)
    write(output / 'EXCLUDED_REFERENCES.json', result['excluded_references'])
    rows = []
    for mid, m in sorted(result['methods'].items()):
        group = 'methods' if m['entry_type'] == 'named_method' else 'resources'
        directory = output / group / mid
        directory.mkdir(parents=True)
        m['directory'] = str(directory.relative_to(output))
        m['catalogue_group'] = catalogue_group(m)
        for cat in m['classifications']:
            path = [slug(str(x)) for x in cat['path'] if str(x).strip()]
            if path:
                link(directory, output / 'classification' / Path(*path) / mid)
        link(directory, output / 'groups' / Path(*m['catalogue_group']) / mid)
        body = '<h1>' + escape(m['name']) + '</h1><p><a href="../../index.html">Index</a> · <a href="method.json">Method record</a> · <a href="references.json">Selected references</a></p>'
        body += '<p>' + escape(m['entry_type']) + ' · ' + escape(m['identity_status']) + '</p>'
        body += '<h2>Identity and scope</h2>' + ''.join('<p>' + escape(d['reason']) + '</p>' for d in m['identity_decisions'])
        body += '<p>' + escape(m['review_limits']) + '</p>'
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
                body += '<p><a href="' + escape(rel) + '">Open source image</a></p><img loading="lazy" src="' + escape(rel) + '">'
                diagrams.add(item['sha256'])
            for item in src.get('documents', []):
                folder = 'posters' if 'for-all-you-seq' in item['path'] else 'guides'
                dest = directory / folder / Path(item['path']).name
                link(source_root / item['path'], dest)
                body += '<p><a href="' + escape(dest.relative_to(directory).as_posix()) + '">Complete PDF: ' + escape(dest.name) + '</a></p>'
        body += '<h2>Method-specific references</h2><p>Roles below distinguish method descriptions, foundations, reviews and other supporting work. Related references from shared sources are kept in source_references.json.</p>'
        seen = set()
        for b in m['method_references']:
            pid = b.get('paper_id')
            key = (pid or b['reference_id'], b['role'])
            if key in seen:
                continue
            seen.add(key)
            title = b.get('resolved_title') or b.get('citation') or b['reference_id']
            body += '<p><b>' + escape(b['role']) + '</b>: ' + escape(title) + '<br>' + escape(b['availability'])
            if pid in state['papers']:
                link(source_root / 'shared/papers' / pid, directory / 'papers' / pid)
                for item in b['cached_files']:
                    rel = 'papers/' + pid + '/' + Path(item['path']).name
                    body += ' · <a href="' + escape(rel) + '">' + escape(item['kind']) + '</a>'
            if b.get('url'):
                body += ' · <a href="' + escape(b['url']) + '">Publication/source</a>'
            body += '</p>'
        if not m['method_references']:
            body += '<p>No method-specific paper assigned. This entry cannot be treated as paper-ready.</p>'
        (directory / 'index.html').write_text(page(m['name'], body))
        write(directory / 'method.json', m)
        write(directory / 'references.json', m['method_references'])
        write(directory / 'source_references.json', m['source_references'])
        (directory / 'README.txt').write_text(m['name'] + '\n\n' + '\n'.join(d['reason'] for d in m['identity_decisions']) +
            '\n\nmethod.json: identity, scope, original classifications and source membership.\n'
            'references.json: reviewed method-specific associations and file availability.\n'
            'source_references.json: original source bibliographies, including sibling methods.\n'
            'catalogue/, diagrams/, posters/, guides/: original sources, unchanged.\n'
            'papers/: only assigned papers; metadata-only records are labelled.\n'
            'index.html: readable view of these same records. No new extraction run.\n')
        method_refs = [b for b in m['method_references'] if b['role'] == 'method_reference']
        rows.append({'id': mid, 'name': m['name'], 'directory': m['directory'], 'entry_type': m['entry_type'],
                     'previous_folders': '; '.join(m['previous_ids']), 'depictions': len(m['source_records']),
                     'unique_diagram_files': len(diagrams), 'identity_status': m['identity_status'],
                     'method_papers': len({b.get('paper_id') or b['reference_id'] for b in method_refs}),
                     'method_fulltexts': len({b['paper_id'] for b in method_refs if b['availability'] == 'fulltext_acquired'}),
                     'diagram_review': 'not_approved_by_identity_review', 'extraction_status': 'not_scheduled'})
    # One integrity pass covers all original bytes, then every generated link.
    errors = []
    for item in state['files'].values():
        path = source_root / item['path']
        if not path.is_file() or sha(path) != item['sha256']:
            errors.append('Source missing or changed: ' + item['path'])
    allowed_roots = [output, source_root]
    if state.get('upstream_source_vault'):
        upstream = Path(state['upstream_source_vault']).resolve()
        if sha(upstream / 'collection.json') != state['upstream_collection_sha256']:
            raise ValueError('Upstream source vault changed')
        allowed_roots.append(upstream)
    for current, dirs, files in os.walk(output, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            if path.is_symlink() and (not path.exists() or not any(path.resolve().is_relative_to(p) for p in allowed_roots)):
                errors.append('Broken/out-of-vault link: ' + str(path))
    result['source_vault'] = os.path.relpath(source_root, output)
    result['source_collection_sha256'] = sha(source_root / 'collection.json')
    result['validation'] = {'passed': not errors, 'errors': errors, 'original_files_checked': len(state['files'])}
    table_file(output / 'CHECKLIST.csv', rows)
    table_file(output / 'FOLDER_DECISIONS.csv', [{**r, 'source_ids': '; '.join(r['source_ids'])} for r in result['folder_decisions']])
    write(output / 'canonical_inventory.json', result)
    write(output / 'SUMMARY.json', {'counts': result['counts'], 'validation': result['validation'], 'extraction_authorized': False})
    table = ''.join('<tr><td><a href="' + escape(r['directory']) + '/index.html">' + escape(r['name']) + '</a></td><td>' + escape(r['entry_type']) + '</td><td>' + str(r['depictions']) + '</td><td>' + str(r['method_fulltexts']) + '/' + str(r['method_papers']) + '</td></tr>' for r in rows)
    (output / 'index.html').write_text(page('Curated Illumina sources', '<h1>Curated Illumina source inventory</h1><p>One entry per reviewed catalogue method or scoped lineage. Resources and unresolved labels are explicitly separated. This does not count independent inventions or approve protocol extraction.</p><p><a href="FOLDER_DECISIONS.csv">Every original folder and its disposition</a> · <a href="CHECKLIST.csv">Coverage and availability</a></p><input id="q" placeholder="Find a method"><table><thead><tr><th>Entry</th><th>Type</th><th>Depictions</th><th>Method fulltexts / papers</th></tr></thead><tbody>' + table + '</tbody></table><script>document.getElementById("q").oninput=function(){for(const r of document.querySelectorAll("tbody tr")){r.hidden=!r.innerText.toLowerCase().includes(this.value.toLowerCase())}}</script>'))
    (output / 'README.txt').write_text('CURATED SOURCES — NOT EXTRACTED GRAPHS\n\nOpen index.html; FOLDER_DECISIONS.csv accounts for every original folder.\n'
        'methods/ contains named, publication-scoped assays; resources/ contains families, kits, components and unresolved labels.\n'
        'classification/ preserves exact source categories. groups/ combines direct web categories; poster-only entries retain their section links.\n'
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
    args = p.parse_args()
    result = publish(args.source_root, args.output, read(args.decisions), read(args.bindings))
    print(json.dumps({'counts': result['counts'], 'validation': result['validation']}, indent=2))


if __name__ == '__main__':
    main()

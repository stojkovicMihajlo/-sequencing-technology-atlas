"""Build one technology inspection library from complementary source snapshots.

This is an inspection/export tool. It never dispatches model jobs, merges
workflow instances, or establishes historical relationships from a label match.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import tempfile
import webbrowser

from scripts.illumina.method_crosswalk import normalize
from scripts.illumina.review_site.build import build as build_source_views, standalone
from genomics_discovery.illumina.models import digest, parse_extraction


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value, *, pretty=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2 if pretty else None,
                               separators=None if pretty else (',', ':')) + '\n')


def table(path, rows, fields):
    with Path(path).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(row[k], ensure_ascii=False) if isinstance(row.get(k), (list, dict))
                             else row.get(k) for k in fields})


def seed_entries(legacy):
    entries = {}
    for item in legacy['technologies']:
        ident = item['id']
        if ident in entries:
            raise ValueError('Duplicate seed technology ID')
        entries[ident] = {'id': ident, 'name': item['name'], 'aliases': item.get('aliases', []),
            'categories': item.get('assigned_categories', item.get('original_categories', [])),
            'entry_basis': 'existing_curated_catalogue' if not item.get('outside_catalogue') else 'existing_foundation_entry',
            'source_occurrences': [{'record_id': r, 'basis': 'existing_catalogue_binding'}
                                   for r in item.get('record_ids', [])],
            'origin_paper_ids': item.get('origin_paper_ids', []), 'workflow_versions': []}
    return entries


def owner(method, entries):
    """Choose a display parent, preserving every scoped workflow/version intact.

    Exact labels and one existing card parent can group the display. This does
    not admit assay equivalence; source/implementation scope stays on the version.
    Multiple lexical/card candidates must remain unresolved instead of guessing.
    """
    target = normalize(method['name'])
    exact = [key for key, entry in entries.items()
             if target in {normalize(s) for s in [entry['name'], *entry['aliases']]}]
    if len(exact) == 1:
        return exact[0], 'exact_display_name; workflow_scope_retained'
    cards = [key for key, entry in entries.items() if any(
        s['record_id'] == method['primary_record_id'] and s['basis'] == 'existing_catalogue_binding'
        for s in entry['source_occurrences'])]
    if not exact and len(cards) == 1:
        return cards[0], 'single_existing_card_parent; distinct_version_not_equated'
    return None, 'no_unambiguous_display_parent'


def assemble(legacy, rows, selection, snapshot):
    entries = seed_entries(legacy)
    row_index = {r['record_id']: r for r in rows}
    if len(row_index) != len(rows):
        raise ValueError('Duplicate source depiction IDs')
    methods = selection['methods']
    if len({m['method_id'] for m in methods}) != len(methods):
        raise ValueError('Duplicate scoped workflow IDs')
    states = snapshot['records']['extractions/state.json']['data']['cards']
    if set(states) != {m['method_id'] for m in methods}:
        raise ValueError('Method selection and extraction snapshot differ')
    paper_levels = {'not_registered': 0, 'unavailable': 1, 'metadata': 2, 'abstract': 3, 'fulltext': 4}
    papers = {}

    def paper(source, *, depiction=None, workflow=None, role=None):
        pmid = str(source.get('pmid') or '')
        if not pmid.isdigit():
            return None  # Non-PMID resources remain in original source registries.
        key = 'pmid:' + pmid
        p = papers.setdefault(key, {'id': key, 'pmid': pmid, 'title': source.get('title'),
            'url': 'https://pubmed.ncbi.nlm.nih.gov/' + pmid + '/',
            'highest_registered_level': 'not_registered', 'source_occurrences': [],
            'workflow_ids': [], 'recorded_roles': []})
        level = source.get('content_level', 'not_registered')
        if paper_levels.get(level, 0) > paper_levels.get(p['highest_registered_level'], 0):
            p['highest_registered_level'] = level
            p['title'] = source.get('title') or p['title']
        for field, value in [('source_occurrences', depiction), ('workflow_ids', workflow), ('recorded_roles', role)]:
            if value and value not in p[field]:
                p[field].append(value)
        return key

    for row in rows:
        for source in row.get('sources', {}).values():
            paper(source, depiction=row['record_id'], role='registered_with_source_depiction')
    baseline_pmids = set(papers)
    workflows = {}
    for method in methods:
        mid = method['method_id']
        missing = set(method['source_record_ids']) - row_index.keys()
        if missing:
            raise ValueError('Method names missing source depictions: ' + repr(sorted(missing)))
        parent, basis = owner(method, entries)
        if parent is None:
            # Explicit scoped work units can extend the catalogue even when the
            # website seed lacks them. Do not extend it from a paper count.
            parent = mid
            if parent in entries:
                raise ValueError('New workflow display parent collides with a seed ID')
            entries[parent] = {'id': parent, 'name': method['name'], 'aliases': [],
                'categories': [], 'entry_basis': 'explicit_scoped_work_unit',
                'source_occurrences': [], 'origin_paper_ids': [], 'workflow_versions': []}
        entry = entries[parent]
        for rid in method['source_record_ids']:
            if not any(s['record_id'] == rid and s['basis'] == 'explicit_work_unit_sources'
                       for s in entry['source_occurrences']):
                entry['source_occurrences'].append({'record_id': rid, 'basis': 'explicit_work_unit_sources',
                                                   'workflow_id': mid})
        prefix = f'extractions/cards/{mid}/attempt_001/'
        extraction = snapshot['records'].get(prefix + 'extraction.json')
        review = snapshot['records'].get(prefix + 'review.json')
        card = snapshot['records'][prefix + 'card.json']['data']
        registry = snapshot['source_inventories'][prefix + 'source_registry.json']['entries']
        candidate = extraction['data'] if extraction else None
        row = states[mid]
        pins = row.get('artifact_pins', {})
        if candidate:
            if (candidate['record_id'] != mid or not review
                    or review['data']['record_id'] != mid
                    or review['data']['candidate_sha256'] != digest(parse_extraction(candidate))):
                raise ValueError('Workflow inspection copy is not bound to its native review')
            if pins and any(pin != actual for pin, actual in [
                (pins.get('extraction.json'), extraction['sha256']),
                (pins.get('review.json'), review['sha256']),
                (pins.get('source_registry.json'), snapshot['source_inventories'][prefix + 'source_registry.json']['sha256'])]):
                raise ValueError('Workflow artifacts disagree with the completed batch pins')
            if not pins and row['status'] != 'failed':
                raise ValueError('Nonfailed workflow lacks completed batch pins')
        elif row['status'] == 'supported':
            raise ValueError('Supported workflow has no saved extraction')
        metadata = {'workflow_id': mid, 'technology_id': parent, 'name': method['name'],
            'scope': method['scope'], 'primary_pmid': method['primary_pmid'],
            'status': row['status'], 'reason': row.get('reason', ''),
            'source_record_ids': method['source_record_ids'], 'display_grouping_basis': basis,
            'scientific_identity_admitted_by_export': False,
            'custody_binding': 'completed_batch_pins' if pins else 'archived_diagnostic_only; not_admitted',
            'native_extraction': {k: extraction[k] for k in ('path', 'sha256')} if extraction else None,
            'native_review': {k: review[k] for k in ('path', 'sha256')} if review else None,
            'copy_boundary': 'JSON inspection copy; native byte hashes refer to archived original files, not this serialization.'}
        workflows[mid] = {'metadata': metadata, 'candidate': candidate,
            'review': review['data'] if review else None, 'sources': registry, 'card': card}
        entry['workflow_versions'].append(mid)
        for source in registry.values():
            paper(source, workflow=mid, role='registered_with_scoped_workflow')
        primary = paper({'pmid': method['primary_pmid']}, workflow=mid, role='declared_primary_for_scoped_workflow')
        if primary not in entry['origin_paper_ids']:
            entry['origin_paper_ids'].append(primary)

    # Every source stays visible. Label matches nominate additional associations,
    # never permission to combine the associated workflow graphs or versions.
    names = defaultdict(set)
    for key, entry in entries.items():
        for name in [entry['name'], *entry['aliases']]:
            names[normalize(name)].add(key)
    leads, depictions = [], []
    for row in rows:
        labels = row['card'].get('source_method_labels') or (row.get('candidate') or {}).get('method_names', [])
        matches = set()
        for label in labels:
            hits = names.get(normalize(label), set())
            matches |= hits
            if not hits:
                leads.append({'label': label, 'source_record_id': row['record_id'],
                    'status': 'resolve_missing_technology_or_alias',
                    'boundary': 'Source label is a lead, not an automatically admitted new technology.'})
        explicit = {key for key, entry in entries.items()
                    if any(s['record_id'] == row['record_id'] for s in entry['source_occurrences'])}
        if not labels and not explicit:
            leads.append({'label': row['title'], 'source_record_id': row['record_id'],
                'status': 'unassigned_depiction_requires_scope_review',
                'boundary': 'No reliable extracted target label; source title retained for coverage.'})
        for key in matches - explicit:
            entries[key]['source_occurrences'].append({'record_id': row['record_id'],
                'basis': 'exact_source_label_candidate; applicability_not_admitted'})
        depictions.append({'record_id': row['record_id'], 'title': row['title'], 'batch': row['batch'],
            'status': row['status'], 'source_url': row['card'].get('source_page_url'),
            'method_labels': labels, 'explicit_entry_ids': sorted(explicit),
            'additional_label_candidate_ids': sorted(matches - explicit)})
    for entry in entries.values():
        grouped = {}
        for source in entry['source_occurrences']:
            rid = source['record_id']
            if rid not in row_index:
                continue
            association = grouped.setdefault(rid, {'record_id': rid, 'bases': [], 'workflow_ids': []})
            if source['basis'] not in association['bases']:
                association['bases'].append(source['basis'])
            if source.get('workflow_id') and source['workflow_id'] not in association['workflow_ids']:
                association['workflow_ids'].append(source['workflow_id'])
        entry['source_occurrences'] = list(grouped.values())
    # Include the old atlas's publication index as an explicitly older source.
    # Its review claims do not upgrade availability in the current source bundle.
    for publication in legacy.get('papers', []):
        ident = publication.get('paper_id', '')
        if ident.startswith('pmid:') and ident[5:].isdigit():
            paper({'pmid': ident[5:], 'title': publication.get('title')}, role='legacy_atlas_reference')
    counts = {'existing_seed_entries': len(legacy['technologies']),
        'seed_catalogue_entries': sum(not t.get('outside_catalogue') for t in legacy['technologies']),
        'seed_foundation_entries': sum(bool(t.get('outside_catalogue')) for t in legacy['technologies']),
        'working_technology_entries': len(entries), 'scoped_workflow_versions': len(workflows),
        'workflow_statuses': dict(Counter(w['metadata']['status'] for w in workflows.values())),
        'source_depictions': len(rows), 'source_classes': dict(Counter(
            'website_cards' if r['batch'] in {'initial_two','catalogue'} else
            'main_poster_rows' if r['batch']=='posters' else r['batch'] for r in rows)),
        'baseline_registered_pmids': len(baseline_pmids),
        'combined_registered_pmids': sum(p['highest_registered_level'] != 'not_registered' for p in papers.values()),
        'all_indexed_pmids_including_legacy_mentions': len(papers),
        'papers_by_highest_registered_level': dict(Counter(p['highest_registered_level'] for p in papers.values())),
        'unmatched_source_label_occurrences': len(leads),
        'distinct_unmatched_source_labels': len({normalize(x['label']) for x in leads}),
        'validated_full_catalogue_unique_method_count': None,
        'boundary': 'A working inspection index, not fresh scientific identity consolidation. Workflow versions and source depictions are not added to get a technology count.'}
    return {'technologies': list(entries.values()), 'workflows': workflows,
            'papers': list(papers.values()), 'depictions': depictions, 'identity_queue': leads, 'counts': counts}


def index_page(library):
    escape = html.escape
    cards = []
    workflows = library['workflows']
    depictions = {r['record_id']: r for r in library['depictions']}
    for entry in sorted(library['technologies'], key=lambda t: t['name'].casefold()):
        versions = []
        for mid in entry['workflow_versions']:
            m = workflows[mid]['metadata']
            versions.append(f'<li><a href="methods/{escape(mid)}/graph.html">{escape(m["name"])}</a>'
                            f' · {escape(m["status"])} · <code>{escape(mid)}</code><p>{escape(m["scope"])}</p></li>')
        sources = []
        for s in entry['source_occurrences']:
            depiction = depictions[s['record_id']]
            source_type = {'initial_two':'website card','catalogue':'website card',
                           'posters':'poster row','footer_panels':'footer panel'}.get(depiction['batch'],depiction['batch'])
            caption = depiction['title'] + ' — ' + source_type
            sources.append(f'<li><a href="source_views/index.html#{escape(s["record_id"])}">{escape(caption)}</a>'
                           f' · {escape("; ".join(s["bases"]))}</li>')
        papers = ' · '.join(f'<a href="https://pubmed.ncbi.nlm.nih.gov/{p[5:]}/">{escape(p)}</a>'
                            for p in entry['origin_paper_ids'] if p.startswith('pmid:') and p[5:].isdigit())
        cards.append(f'<details class="entry"><summary>{escape(entry["name"])}</summary>'
                     f'<p>{escape(entry["entry_basis"])} · {escape(", ".join(entry["categories"]))}</p>'
                     f'<p>{papers}</p><h3>Scoped workflow versions</h3><ul>{"".join(versions) or "No new scoped workflow yet. Inspect its source diagrams below."}</ul>'
                     f'<details><summary>{len(sources)} source associations</summary><ul>{"".join(sources)}</ul></details></details>')
    counts = escape(json.dumps(library['counts'], indent=2))
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sequencing technology atlas</title><style>body{{font:16px/1.6 Arial,Helvetica,sans-serif;max-width:1120px;margin:40px auto;padding:0 24px;background:#111821;color:#dfe8f2}}a{{color:#8ccaff}}h1{{font-size:30px}}h3{{font-size:16px}}p{{color:#b4c4d4}}input{{width:100%;padding:14px;box-sizing:border-box;font:inherit;background:#1c2938;color:white;border:1px solid #62798e;border-radius:8px}}.entry{{margin:12px 0;padding:16px 20px;background:#1a2532;border:1px solid #35485e;border-radius:8px}}summary{{cursor:pointer}}li{{margin:8px 0}}pre{{white-space:pre-wrap;font-size:13px}}</style>
<h1>Sequencing technology atlas</h1><p>One entry per working technology, with scoped workflow versions, complementary source depictions and papers beneath it.</p>
<p><a href="technologies.csv">Technology table</a> · <a href="papers.csv">Paper table</a> · <a href="depictions.csv">Source coverage</a> · <a href="identity_queue.csv">Missing technologies / aliases to resolve</a> · <a href="history.html">Historical changes</a></p>
<p><strong>{library['counts']['working_technology_entries']} working entries · {library['counts']['scoped_workflow_versions']} scoped workflow versions · {library['counts'].get('historical_edges',0)} historical links</strong></p>
<details><summary>Counts and their definitions</summary><pre>{counts}</pre></details>
<p>The seed is an earlier curated catalogue. New workflow versions retain their exact scope and model-review status. Label-only source associations are candidates. They establish neither assay equivalence nor evolutionary edges.</p>
<input id="search" type="search" placeholder="Find a technology, PMID or workflow ID…" aria-label="Find a technology">
<section>{''.join(cards)}</section><script>document.getElementById('search').oninput=function(){{const q=this.value.toLowerCase();document.querySelectorAll('.entry').forEach(e=>e.hidden=!e.textContent.toLowerCase().includes(q));}};</script></html>'''


def bind_history(library, document, snapshot):
    """Attach old reviewed links only to their exact extraction versions."""
    pairs = {r['path']: r for k, r in snapshot['records'].items()
             if k.startswith('history/pairs/') and k.endswith('/graph.json')}
    seen = set()
    for edge in document['edges']:
        if edge['id'] in seen or edge['kind'] not in {'method_development','component_adoption'}:
            raise ValueError('Duplicate or nonhistorical link in history view')
        seen.add(edge['id'])
        pair = pairs.get(edge['pair_graph'])
        if not pair or pair['sha256'] != edge['pair_graph_sha256']:
            raise ValueError('History view is not bound to an archived pair')
        data = pair['data']
        accepted = [x for x in data['historical_links'] if x['claim'] == edge['claim'] and x['review'] == edge['review']]
        if len(accepted) != 1 or edge['review']['decision'] != 'accept':
            raise ValueError('History view differs from the archived accepted claim')
        if any(a['verdict']=='needs_finer_extraction' for a in data['diagram_adequacy']):
            raise ValueError('Held pair cannot be attached as a published historical link')
        sides = data['bindings']
        ids = [sides[s]['record_id'] for s in ['left','right']]
        if edge['claim']['direction']=='right_to_left':
            ids.reverse()
        if [edge['source'],edge['target']] != ['illumina:'+i for i in ids]:
            raise ValueError('Historical link direction or endpoints changed')
        if edge['kind'] != edge['claim']['relation']:
            raise ValueError('Historical link type differs from its claim')
        for side in ['left','right']:
            binding=sides[side]
            workflow=library['workflows'].get(binding['record_id'])
            if not workflow or workflow['metadata']['native_extraction']['sha256'] != binding['extraction_sha256']:
                raise ValueError('Historical endpoint is a different extraction version')
    library['history'] = document
    library['counts']['historical_edges'] = len(document['edges'])


def history_page(library):
    rows=[]
    for edge in library.get('history',{}).get('edges',[]):
        ids=[edge[k].removeprefix('illumina:') for k in ['source','target']]
        links=' → '.join(f'<a href="methods/{html.escape(i)}/graph.html">{html.escape(library["workflows"][i]["metadata"]["name"])}</a>' for i in ids)
        claim=edge['claim']
        details=''.join(f'<h3>{html.escape(label)}</h3><p style="white-space:pre-wrap">{html.escape(claim[field])}</p>'
            for field,label in [('shared_description','Retained / shared arrangement'),('difference','What changed'),('consequence','Reported consequence and limits')])
        rows.append(f'<details><summary>{links} · {html.escape(edge["kind"])}</summary>{details}<h3>Reviewer reasoning</h3><p>{html.escape(edge["review"]["rationale"])}</p><details><summary>Exact claim, evidence and provenance</summary><pre>{html.escape(json.dumps(edge,indent=2,ensure_ascii=False))}</pre></details></details>')
    return '<!doctype html><html lang="en"><meta charset="utf-8"><title>Historical changes</title><style>body{font:16px/1.6 Arial,sans-serif;max-width:1080px;margin:40px auto;padding:20px;background:#111821;color:#dfe8f2}a{color:#8ccaff}details{padding:16px;border:1px solid #35485e;border-radius:8px;margin:14px 0}summary{cursor:pointer}pre{white-space:pre-wrap;font-size:12px}</style><a href="index.html">Technology atlas</a><h1>Historical changes</h1><p>Existing paired model-reviewed claims, bound to the exact workflow versions. Similarity and component adoption do not imply whole-method descent or interchangeability.</p>'+''.join(rows)+'</html>'


def build(audit, snapshot_path, selection_path, legacy_path, output, history_path=None):
    audit, snapshot_path, selection_path, legacy_path, output = map(Path, (audit, snapshot_path, selection_path, legacy_path, output))
    if output.exists():
        raise FileExistsError('Choose a new export directory; archived inputs are never reorganized in place')
    archive = audit/'records.jsonl.gz'
    receipt = read(audit/'AUDIT_STATUS.json')['raw_audit_receipts'][archive.name]
    if sha(archive) != receipt['sha256'] or archive.stat().st_size != receipt['bytes']:
        raise ValueError('Source audit archive differs from its receipt')
    with gzip.open(archive, 'rt') as stream:
        rows = [json.loads(line) for line in stream]
    snapshot = json.loads(gzip.decompress(snapshot_path.read_bytes()))
    snapshot_receipt = read(snapshot_path.with_name('audit.json'))
    if sha(snapshot_path) != snapshot_receipt['snapshot_sha256']:
        raise ValueError('Method snapshot differs from its audit receipt')
    overrides = read(audit/'GRAPH_PUBLICATION.json')['new_admission_failures']
    for finding in overrides:
        for row in rows:
            if row['record_id'] == finding['record_id']:
                row['status'] = finding['current_status']
    library = assemble(read(legacy_path), rows, read(selection_path), snapshot)
    if history_path:
        bind_history(library, read(history_path), snapshot)
    # All files below are derived views. Publish only a complete tree, and never
    # remove an existing export or source archive when a write fails.
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.' + output.name + '-building-', dir=output.parent))
    try:
        result = export_library(audit, snapshot_path, selection_path, legacy_path, library, temporary, history_path)
        temporary.rename(output)
        return result
    except BaseException:
        shutil.rmtree(temporary)
        raise


def export_library(audit, snapshot_path, selection_path, legacy_path, library, output, history_path=None):
    archive = audit/'records.jsonl.gz'
    source_manifest = build_source_views(audit, output/'source_views')
    for mid, workflow in library['workflows'].items():
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', mid):
            raise ValueError('Unsafe workflow path')
        folder = output/'methods'/mid
        candidate = workflow['candidate']
        if candidate:
            write(folder/'graph.json', candidate, pretty=True)
        write(folder/'record.json', {k: v for k, v in workflow.items() if k != 'candidate'})
        record = {'record_id': mid, 'title': workflow['metadata']['name'],
            'batch': 'scoped_method', 'status': workflow['metadata']['status'],
            'reason': workflow['metadata']['reason'], 'candidate': candidate,
            'review': workflow['review'], 'sources': workflow['sources'], 'card': workflow['card'],
            'file': 'graph.json' if candidate else 'record.json', 'download_label': 'Download saved workflow JSON',
            'attempt_directory': workflow['metadata']['native_extraction']['path'] if candidate else None}
        standalone(record, folder/'graph.html')
    write(output/'technologies.json', library['technologies'])
    write(output/'papers.json', library['papers'])
    write(output/'counts.json', library['counts'], pretty=True)
    write(output/'identity_queue.json', library['identity_queue'])
    write(output/'historical_changes.json', library.get('history',{}))
    (output/'history.html').write_text(history_page(library))
    table(output/'technologies.csv', library['technologies'], ['id','name','entry_basis','categories','workflow_versions','origin_paper_ids','source_occurrences'])
    table(output/'papers.csv', library['papers'], ['id','title','url','highest_registered_level','recorded_roles','workflow_ids','source_occurrences'])
    table(output/'depictions.csv', library['depictions'], ['record_id','title','batch','status','source_url','method_labels','explicit_entry_ids','additional_label_candidate_ids'])
    table(output/'identity_queue.csv', library['identity_queue'], ['label','source_record_id','status'])
    write(output/'manifest.json', {'format_version': 'technology_inspection_library/1',
        'input_sha256': {str(p.resolve()): sha(p) for p in [archive, snapshot_path, selection_path, legacy_path,
            *([Path(history_path)] if history_path else [])]},
        'source_view_manifest': source_manifest, 'counts': library['counts'], 'native_model_calls': 0})
    (output/'index.html').write_text(index_page(library))
    (output/'README.txt').write_text('Open index.html for the unified technology index.\n'
        'methods/<workflow_id>/graph.json contains the extracted States, Operations and Flows.\n'
        'graph.html renders that saved workflow; record.json contains scope, review, sources and native provenance.\n'
        'Source depictions supplement entries; their lexical candidate associations are explicitly qualified.\n'
        'identity_queue.csv lists source labels needing a missing-technology/alias decision.\n'
        'Publication IDs count papers, not technologies. Non-PMID documents remain in source registries.\n'
        'Source views use fetch; to inspect those, serve this folder with python3 -m http.server 8765.\n'
        'Per-method graph.html pages work directly from disk. No model calls occur during rendering.\n')
    return library['counts']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    for name in ['audit', 'snapshot', 'selection', 'legacy', 'output']:
        b.add_argument('--'+name, type=Path, required=True)
    b.add_argument('--history',type=Path)
    s = sub.add_parser('show')
    s.add_argument('workflow')
    s.add_argument('--library', type=Path, default=Path('atlas'))
    s.add_argument('--open', action='store_true')
    a = p.parse_args()
    if a.command == 'build':
        print(json.dumps(build(a.audit,a.snapshot,a.selection,a.legacy,a.output,a.history), indent=2))
    else:
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', a.workflow):
            p.error('Use an exact workflow ID from the index')
        folder = a.library/'methods'/a.workflow
        info = read(folder/'record.json')
        record = {'record_id': a.workflow, 'title': info['metadata']['name'], 'batch': 'scoped_method',
            'status': info['metadata']['status'], 'reason': info['metadata']['reason'],
            'candidate': read(folder/'graph.json') if (folder/'graph.json').exists() else None,
            'review': info['review'], 'sources': info['sources'], 'card': info['card'],
            'file': 'graph.json', 'download_label': 'Download saved workflow JSON'}
        standalone(record, folder/'graph.html')
        print(folder/'graph.html')
        if a.open: webbrowser.open((folder/'graph.html').resolve().as_uri())


if __name__ == '__main__':
    main()

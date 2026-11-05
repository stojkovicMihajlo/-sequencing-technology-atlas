"""Copy hash-verified sources once and expose them through technology folders.

No graph extraction, literature interpretation or model call happens here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .inventory import read, sha, slug


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    path.write_text(text)


def link(target, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        if destination.resolve() != Path(target).resolve():
            raise ValueError('Conflicting existing link: ' + str(destination))
        return
    if destination.exists():
        raise ValueError('Refusing to replace a file: ' + str(destination))
    destination.symlink_to(os.path.relpath(target, destination.parent))


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.verified = {}
        self.files = {}

    def blob(self, data, suffix, origin):
        digest = hashlib.sha256(data).hexdigest()
        suffix = suffix if suffix in {'.xml', '.pdf', '.png', '.jpg', '.jpeg', '.json', '.txt', '.html', '.svg'} else '.bin'
        target = self.root / 'shared/files' / digest[:2] / (digest + suffix)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        elif sha(target) != digest:
            raise ValueError('Corrupt collection object: ' + str(target))
        rel = str(target.relative_to(self.root))
        receipt = self.files.setdefault(rel, {'path': rel, 'sha256': digest, 'bytes': len(data), 'origins': []})
        if origin not in receipt['origins']:
            receipt['origins'].append(origin)
        return target

    def file(self, path, expected, origin):
        if not expected:
            raise ValueError('Missing expected hash for cached source: ' + str(path))
        cache_key = (str(path), expected)
        if cache_key not in self.verified:
            raw = Path(path).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('Cached source hash mismatch: ' + str(path))
            self.verified[cache_key] = self.blob(raw, Path(path).suffix.lower(), origin)
        target = self.verified[cache_key]
        receipt = self.files[str(target.relative_to(self.root))]
        if origin not in receipt['origins']:
            receipt['origins'].append(origin)
        return target


def materialize(inventory, output, manifests):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    store = Store(root)
    errors, papers, sources, posters = [], {}, {}, {}
    publications = {}
    for path in manifests:
        manifest = read(path)
        publications.update(manifest.get('publications', {}))
        for asset in manifest.get('assets', []):
            if not asset.get('path', '').lower().endswith('.pdf') or asset.get('status') != 'downloaded':
                continue
            source = Path(asset['path'])
            if not source.is_absolute():
                source = Path(path).parent / source
            try:
                target = store.file(source, asset['sha256'], {'manifest': str(path), 'url': asset.get('url')})
                name = Path(asset.get('url', '').split('?')[0]).name or asset['asset_id'] + '.pdf'
                link(target, root / 'shared/documents' / name)
                posters[asset.get('url')] = {'path': str((root / 'shared/documents' / name).relative_to(root)),
                    'sha256': asset['sha256'], 'url': asset.get('url'), 'kind': asset.get('kind')}
            except (OSError, ValueError) as exc:
                errors.append({'source': asset.get('url'), 'error': str(exc)})

    def paper(pid, record, sid):
        # A PMID is a paper identifier, never a technology identifier.
        ident = 'pmid-' + pid if str(pid).isdigit() else 'external-' + hashlib.sha256(sid.encode()).hexdigest()[:16]
        p = papers.setdefault(ident, {'id': ident, 'pmid': pid, 'title': record.get('title'),
            'url': record.get('url'), 'status': 'metadata_only', 'files': [],
            'metadata': publications.get(str(pid), {}), 'source_ids': []})
        if sid not in p['source_ids']:
            p['source_ids'].append(sid)
        d = root / 'shared/papers' / ident
        d.mkdir(parents=True, exist_ok=True)
        if record.get('content_level') != 'fulltext':
            return ident
        original = record.get('original', {})
        paths = [(original.get('path'), original.get('sha256'), 'article'),
                 (record.get('path'), record.get('sha256'), 'reading-text'),
                 (original.get('blocks_path'), original.get('blocks_sha256'), 'blocks')]
        # Some older manifests omit the blocks digest but the registry custody
        # list pins it. Use that pin, not a newly invented expected hash.
        pins = {r['path']: r['sha256'] for r in record.get('custody_files', [])}
        for path, expected, kind in paths:
            if not path:
                continue
            try:
                target = store.file(path, expected or pins.get(path), {'source_id': sid, 'kind': kind})
                name = kind + '-' + target.stem[:12] + target.suffix
                link(target, d / name)
                row = {'path': str((d / name).relative_to(root)), 'sha256': target.stem, 'kind': kind}
                if row not in p['files']:
                    p['files'].append(row)
            except (OSError, ValueError) as exc:
                errors.append({'source': sid, 'path': path, 'error': str(exc)})
        if any(r['kind'] == 'article' for r in p['files']):
            p['status'] = 'fulltext_acquired'
        elif p['files']:
            p['status'] = 'reading_text_only'
        return ident

    for rid, occurrence in inventory['occurrences'].items():
        try:
            registry = read(occurrence['registry'])
        except (OSError, ValueError) as exc:
            errors.append({'source': rid, 'error': str(exc)})
            occurrence['collection_status'] = 'registry_unavailable'
            continue
        od = root / 'shared/depictions' / rid
        od.mkdir(parents=True)
        write(od / 'source_registry.json', registry)
        write(od / 'source.json', occurrence)
        if occurrence.get('original_cms'):
            write(od / 'original_cms.json', occurrence['original_cms']['content'])
        paper_roles = {str(r['pmid']): r['role'] for r in occurrence['card'].get('references', []) if r.get('pmid')}
        # A paper can occur in both lists; the method-reference role takes priority.
        paper_roles.update({str(r['pmid']): 'references' for r in occurrence['card'].get('references', [])
                            if r.get('pmid') and r['role'] == 'references'})
        result = {'record_id': rid, 'catalogue': [], 'diagrams': [], 'papers': [], 'documents': [], 'source_registry': str((od / 'source_registry.json').relative_to(root))}
        for sid, record in registry.items():
            level = record.get('content_level')
            if record.get('kind') == 'image':
                try:
                    target = store.file(record['path'], record.get('sha256'), {'source_id': sid, 'url': record.get('url')})
                    dest = od / (slug(sid) + target.suffix)
                    link(target, dest)
                    result['diagrams'].append({'path': str(dest.relative_to(root)), 'source_id': sid,
                        'url': record.get('url'), 'sha256': target.stem, 'quality': 'original_source_or_recorded_crop; visual_review_required'})
                except (OSError, ValueError) as exc:
                    errors.append({'source': sid, 'error': str(exc)})
            elif level == 'catalogue' and record.get('text'):
                target = store.blob(record['text'].encode(), '.txt', {'source_id': sid, 'url': record.get('url'), 'basis': 'recorded_catalogue_text'})
                dest = od / (slug(sid) + '.txt')
                link(target, dest)
                result['catalogue'].append({'path': str(dest.relative_to(root)), 'source_id': sid, 'title': record.get('title'), 'url': record.get('url')})
            elif sid.startswith(('paper:', 'external:')) or level == 'fulltext':
                pid = str(record.get('pmid') or '')
                ident = paper(pid, record, sid)
                role = paper_roles.get(pid, 'candidate_for_source; association_unverified')
                result['papers'].append({'paper_id': ident, 'role': role,
                    'association_status': record.get('association_status', 'listed_reference_not_origin_verification'),
                    'shared_depiction': occurrence['shared_source']})
            # The original PDF is retained even if a crop omits detail. Match by
            # published URL, never by a method-name keyword.
            for url in [record.get('url'), occurrence['card'].get('source_page_url')]:
                if url in posters and posters[url] not in result['documents']:
                    result['documents'].append(posters[url])
        for ref in occurrence['card'].get('references', []):
            pid = str(ref.get('pmid') or '')
            if pid and not any(r['paper_id'] == 'pmid-' + pid for r in result['papers']):
                ident = paper(pid, {'title': publications.get(pid, {}).get('title'), 'url': 'https://pubmed.ncbi.nlm.nih.gov/' + pid + '/'}, 'paper:' + pid)
                result['papers'].append({'paper_id': ident, 'role': ref['role'], 'association_status': 'listed_reference_not_origin_verification', 'shared_depiction': occurrence['shared_source']})
        sources[rid] = result
        occurrence['collection_status'] = 'sources_collected'

    # Do not omit cached papers simply because a particular previous model
    # attempt did not register them. Import only papers referenced by this union.
    for manifest_path in manifests:
        manifest = read(manifest_path)
        for pmid, full in manifest.get('fulltexts', {}).items():
            if 'pmid-' + str(pmid) not in papers or full.get('status') != 'fulltext_acquired':
                continue
            if papers['pmid-' + str(pmid)]['status'] == 'fulltext_acquired':
                continue
            def absolute(path):
                return str(Path(manifest_path).parent / path) if path and not Path(path).is_absolute() else path
            record = {'content_level': 'fulltext', 'pmid': str(pmid), 'title': publications.get(str(pmid), {}).get('title'),
                'path': absolute(full.get('text_path')), 'sha256': full.get('text_sha256'),
                'original': {**full, 'path': absolute(full.get('path')), 'blocks_path': absolute(full.get('blocks_path'))}}
            paper(str(pmid), record, 'paper:' + str(pmid))

    state = {'inventory': inventory, 'sources': sources, 'papers': papers, 'documents': posters,
             'files': store.files, 'errors': errors,
             'source_manifests': [{'path': str(p), 'sha256': sha(p)} for p in manifests]}
    write(root / 'collection.json', state)
    return state


def publish(root, state):
    """Make per-technology views; download status never means scientific approval."""
    root = Path(root)
    from .inventory import catalogue_group
    for ident, p in state['papers'].items():
        write(root / 'shared/papers' / ident / 'paper.json', p)
    for mid, m in state['inventory']['methods'].items():
        group = 'technologies' if m['kind'] == 'technology' else 'supporting_resources'
        directory = root / group / mid
        directory.mkdir(parents=True, exist_ok=True)
        documents, ps, diagrams, cat = {}, {}, [], []
        for rid in m['source_records']:
            source = state['sources'].get(rid)
            if source is None:
                continue
            link(root / 'shared/depictions' / rid, directory / 'catalogue' / rid)
            for image in source['diagrams']:
                dest = directory / 'diagrams' / Path(image['path']).name
                link(root / image['path'], dest)
                diagrams.append({**image, 'local_path': str(dest.relative_to(directory)), 'depiction': rid})
            for p in source['papers']:
                ps.setdefault(p['paper_id'], []).append({**p, 'depiction': rid})
            cat.extend(source['catalogue'])
            for doc in source['documents']:
                documents[doc['path']] = doc
        for p, bindings in ps.items():
            primary = any(b['role'] == 'references' for b in bindings)
            related = all(b['role'] == 'related_publications' for b in bindings)
            folder = 'papers' if primary else ('related_papers' if related else 'paper_candidates')
            link(root / 'shared/papers' / p, directory / folder / p)
        for path, doc in documents.items():
            folder = 'posters' if 'for-all-you-seq' in path else 'guides'
            link(root / path, directory / folder / Path(path).name)
        for c in m['classifications']:
            labels = [slug(str(x)) for x in c['path'] if str(x).strip()]
            if labels:
                link(directory, root / 'classification' / Path(*labels) / mid)
        m['catalogue_group'] = catalogue_group(m)
        if m['kind'] == 'technology':
            link(directory, root / 'groups' / Path(*m['catalogue_group']) / mid)
        m['directory'] = str(directory.relative_to(root))
        m['collection'] = {'diagrams': diagrams, 'catalogue': cat, 'documents': list(documents.values()),
            'listed_references': [{**ref, 'depiction': rid} for rid in m['source_records']
                for ref in state['inventory']['occurrences'][rid]['card'].get('references', [])
                if ref.get('role') == 'references'],
            'papers': [{'paper_id': p, 'status': state['papers'][p]['status'], 'bindings': bs} for p, bs in ps.items()]}
        m['source_readiness'] = 'collected_for_review' if diagrams else 'missing_diagram'
        write(directory / 'method.json', m)
    write(root / 'collection.json', state)
    (root / 'groups/README.txt').parent.mkdir(parents=True, exist_ok=True)
    (root / 'groups/README.txt').write_text(
        'These are links to the single technology source folder, not copied methods.\n'
        'Genome, Transcriptome and Epigenome preserve recovered Illumina catalogue placements.\n'
        'Combined catalogue categories preserves methods listed in more than one of those categories.\n'
        'These placements do not establish which quantities an assay jointly measures.\n'
        'Category review pending retains poster-only/unclassified entries; original poster sections\n'
        'remain available in classification/ and method.json. A DNA poster is not a Genome label.\n'
        'New literature methods need a documented curator assignment, not an invented Illumina placement.\n')

"""Publish a review-site export as an immutable, validated explorer snapshot.

No model calls, scientific rewriting, or identity matching occur here.
Usage: python3 import_data.py SOURCE_EXPORT --snapshot NAME [--label LABEL]
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def within(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path outside source export: {relative}")
    return path


def validate_graph(candidate):
    if not candidate:
        return
    nodes = candidate.get('states', []) + candidate.get('operations', [])
    ids = [n['id'] for n in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate State/Operation IDs')
    states = {n['id'] for n in candidate.get('states', [])}
    for operation in candidate.get('operations', []):
        for ref in operation.get('inputs', []) + operation.get('outputs', []):
            if ref not in states:
                raise ValueError(f'Unknown State: {ref}')
    flow_ids = set()
    for flow in candidate.get('flows', []):
        if flow['id'] in flow_ids:
            raise ValueError('Duplicate Flow ID')
        flow_ids.add(flow['id'])
        if flow['source'] not in ids or flow['target'] not in ids:
            raise ValueError(f"Unknown Flow endpoint: {flow['id']}")


def publish(source, destination, snapshot, label):
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', snapshot):
        raise ValueError('Snapshot name must contain letters, numbers, underscores or hyphens')
    index_path = source / 'index.json'
    index = json.loads(index_path.read_text())
    # Files in macOS Documents may be offloaded by iCloud. Read independent
    # records concurrently; validation and publication remain deterministic.
    paths = [within(source, row['file']) for row in index['records']]
    with ThreadPoolExecutor(max_workers=8) as readers:
        payloads = list(readers.map(Path.read_bytes, paths))
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / snapshot
    if target.exists():
        raise ValueError('Snapshot exists; use a new name to preserve the earlier version')
    with tempfile.TemporaryDirectory(dir=destination, prefix='.import-') as temporary:
        staging = Path(temporary)
        (staging / 'records').mkdir()
        rows, seen = [], set()
        for row, payload in zip(index['records'], payloads):
            identity = row['record_id']
            if identity in seen:
                raise ValueError(f'Duplicate method identity: {identity}')
            seen.add(identity)
            original = within(source, row['file'])
            record = json.loads(payload)
            if record['record_id'] != identity:
                raise ValueError(f'Index/record identity mismatch: {identity}')
            validate_graph(record.get('candidate'))
            for diagram in (record.get('source_comparison') or {}).get('diagrams', []):
                if diagram.get('local_asset'):
                    asset = within(source, diagram['local_asset'])
                    if diagram.get('sha256') and digest(asset) != diagram['sha256']:
                        raise ValueError(f'Image hash mismatch: {identity}')
                    output = staging / diagram['local_asset']
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(asset, output)
            filename = hashlib.sha256(identity.encode()).hexdigest()[:24] + '.json'
            (staging / 'records' / filename).write_bytes(payload)
            rows.append({**{k: row.get(k) for k in ['record_id', 'title', 'status', 'category', 'operations', 'method_names']},
                         'file': f'records/{filename}', 'sha256': hashlib.sha256(payload).hexdigest(),
                         'states': len((record.get('candidate') or {}).get('states', []))})
        manifest = {'format_version': 'atlas-explorer/1', 'snapshot': snapshot, 'label': label,
                    'source_index_sha256': digest(index_path),
                    'counts': dict(Counter(r['status'] for r in rows)), 'records': rows}
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        staging.rename(target)
    # The pointer changes only after every record and asset has passed validation.
    pointer = destination / '.current.tmp'
    pointer.write_text(json.dumps({'manifest': f'{snapshot}/manifest.json'}))
    pointer.replace(destination / 'current.json')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--label', default='Extraction snapshot')
    parser.add_argument('--destination', type=Path, default=Path(__file__).parent / 'public' / 'data')
    args = parser.parse_args()
    result = publish(args.source, args.destination, args.snapshot, args.label)
    print(json.dumps({'methods': len(result['records']), 'counts': result['counts']}))

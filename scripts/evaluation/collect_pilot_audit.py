"""Copy a bounded completed pilot's records for inspection, never alter verdicts.

This is an audit view, not source material for future extraction. Full native
traces and paper bodies remain at their original locations. Every copied record
gets its original path and byte hash; source metadata inventories omit body text.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


def collect(root: Path, output: Path):
    root = root.resolve()
    records, sources, missing = {}, {}, []

    def add(path, required=False):
        path = Path(path)
        if not path.is_file():
            if required:
                missing.append(str(path))
            return None
        raw = path.read_bytes()
        value = json.loads(raw)
        records[str(path.relative_to(root))] = {
            'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'data': value}
        return value

    def registry(path):
        if not path.exists():
            return
        raw = path.read_bytes()
        data = json.loads(raw)
        sources[str(path.relative_to(root))] = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
            'entries': {sid: {**{k: v for k, v in row.items() if k != 'text'},
                'text_characters': len(row.get('text', '')),
                'text_sha256': hashlib.sha256(row.get('text', '').encode()).hexdigest()}
                for sid, row in data.items()}}

    def attempt(folder):
        for name in ('artifact.json', 'result.json', 'execution.json', 'completion.json'):
            add(folder/name)
        for name in ('final.txt', 'stderr.txt', 'stderr.log'):
            p = folder/name
            if p.is_file():
                raw = p.read_bytes()
                records[str(p.relative_to(root))] = {'path': str(p),
                    'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                    'tail_text': raw[-8000:].decode(errors='replace'), 'truncated': len(raw) > 8000}

    def stages(folder):
        for name in ('state.json', 'stages.json'):
            add(folder/name)
        for path in sorted((folder/'attempts').glob('*')):
            if path.is_dir():
                attempt(path)

    add(root/'RESULT.json', True)
    add(root/'state.json', True)
    for name in ('primary_recovery.json', 'primary_selection.json', 'manifest.json', 'baseline_diagnostics.json'):
        add(root/'sources'/name, True)
    state = add(root/'extractions/state.json', True)
    for row in state['cards'].values():
        folder = Path(row['attempt_directory'])
        for name in ('card.json', 'extraction.json', 'review.json'):
            add(folder/name, name == 'card.json')
        registry(folder/'source_registry.json')
        stages(folder)
    for name in ('methods.json', 'pair_candidates.json', 'refinement_requests.json', 'RESULT.json', 'history_inputs.json'):
        add(root/'history'/name, True)
    history = add(root/'history/state.json', True)
    for kind in ('citation_runs', 'pair_runs'):
        for row in history[kind].values():
            folder = Path(row['path'])
            for name in ('nominations.json', 'analysis.json', 'review.json'):
                add(folder/name)
            # Pair graph contains duplicate source workflow payload; keep its
            # relationships and exact original hash without repeating that graph.
            p = folder/'graph.json'
            if p.exists():
                raw = p.read_bytes(); graph = json.loads(raw)
                records[str(p.relative_to(root))] = {'path': str(p), 'sha256': hashlib.sha256(raw).hexdigest(),
                    'omitted_fields': ['source_graph'], 'data': {k: v for k, v in graph.items() if k != 'source_graph'}}
            registry(folder/'inputs/sources.json')
            add(folder/'inputs/bibliography.json')
            stages(folder)
    add(root/'evaluation/state.json', True)
    add(root/'evaluation/diagnostics.json', True)
    for folder in sorted((root/'evaluation/methods').glob('*')):
        for name in ('result.json', 'private_assignment.json', 'state.json'):
            add(folder/name)
        attempt(folder/'attempt')
    result = {'format_version': 'pilot_audit_snapshot/2', 'pilot': str(root),
        'boundary': 'Inspection-only view, not a scientific verdict or source packet. Source bodies and full native traces not copied.',
        'records': records, 'source_inventories': sources, 'missing_required': missing}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        with gzip.GzipFile(fileobj=stream, mode='wb') as compressed:
            compressed.write(json.dumps(result).encode())
    return {'records': len(records), 'source_inventories': len(sources), 'missing_required': missing,
        'archive_sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'bytes': output.stat().st_size}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.pilot, args.output), indent=2))

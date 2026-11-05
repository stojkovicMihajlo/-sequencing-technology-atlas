"""Read completed pilot artifacts into an immutable ideation input snapshot.

This never waits for, changes, or reruns extraction/history jobs. Incomplete and
held comparisons remain absent, with that limitation recorded in the receipt.
"""
import argparse
import json
from pathlib import Path
import shutil

from genomics_discovery.illumina.extraction import read, sha, write
from genomics_discovery.illumina.relationships import load_reviewed_pair


def snapshot(pilot, output):
    pilot, output = Path(pilot).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    base = pilot/'extractions'/'atlas.json'
    pin = sha(base)
    graph = read(base)
    if sha(base) != pin:
        raise ValueError('Atlas changed while snapshotting')
    shutil.copyfile(base, output/'atlas.json')
    if sha(output/'atlas.json') != pin:
        raise ValueError('Copied atlas differs')
    history_path = pilot/'history'/'state.json'
    history = read(history_path) if history_path.exists() else {'pair_runs': {}}
    manifest, held = {'format_version': 'illumina_history_inputs/1', 'overlays': []}, []
    for ident, row in history.get('pair_runs', {}).items():
        if row.get('status') != 'completed':
            held.append({'pair_id': ident, 'reason': 'not_completed_at_snapshot'}); continue
        pair = Path(row['path']); path = pair/'graph.json'; pair_pin = sha(path)
        overlay = load_reviewed_pair(path, pair_pin)
        if any(x['verdict'] == 'needs_finer_extraction' for x in overlay['diagram_adequacy']):
            held.append({'pair_id': ident, 'reason': 'needs_finer_extraction'}); continue
        for binding in overlay['bindings'].values():
            card = graph['cards'].get(binding['record_id'], {})
            if card.get('admission') != 'supported' or card.get('artifact_pins', {}).get('extraction.json') != binding['extraction_sha256']:
                raise ValueError('Historical overlay belongs to a different workflow version')
        dest = output/'pairs'/ident
        dest.mkdir(parents=True)
        shutil.copyfile(path, dest/'graph.json')
        entry = {'path': str((dest/'graph.json').relative_to(output)), 'sha256': pair_pin}
        source = pair/'inputs'/'sources.json'
        if source.exists():
            shutil.copyfile(source, dest/'sources.json')
            entry['source_registry'] = {'path': str((dest/'sources.json').relative_to(output)), 'sha256': sha(dest/'sources.json')}
        manifest['overlays'].append(entry)
    write(output/'history_inputs.json', manifest)
    receipt = {'source_atlas': str(base), 'atlas_sha256': pin,
        'supported_workflows': sum(x.get('admission') == 'supported' for x in graph['cards'].values()),
        'reviewed_history_overlays': len(manifest['overlays']), 'not_loaded': held,
        'boundary': 'Snapshot of completed model-reviewed artifacts only. Missing history is not absence of prior art.'}
    write(output/'receipt.json', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.pilot, args.output), indent=2))

"""Freeze completed pilot inputs or prepare a run with exact reference-run inputs.

This never waits for, changes, or reruns extraction/history jobs. Incomplete and
held comparisons remain absent from pilot snapshots. Reference preparation uses
only the prior config's pinned inputs, without refreshing that pilot or memory.
"""
import argparse
import json
from pathlib import Path
import shutil

from genomics_discovery.illumina.extraction import read, sha, write
from genomics_discovery.illumina.history import build_history_index
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
    # Exercise the actual consumer before spending any native model calls.
    # Checking pair-file hashes alone misses registry namespace/handoff errors.
    history_index = build_history_index(output/'history_inputs.json')
    receipt = {'source_atlas': str(base), 'atlas_sha256': pin,
        'supported_workflows': sum(x.get('admission') == 'supported' for x in graph['cards'].values()),
        'reviewed_history_overlays': len(manifest['overlays']), 'not_loaded': held,
        'history_preflight': {'status': 'passed', 'indexed_relationships': len(history_index['edges'])},
        'boundary': 'Snapshot of completed model-reviewed artifacts only. Missing history is not absence of prior art.'}
    write(output/'receipt.json', receipt)
    return receipt


def prepare_from_reference(reference_run, output):
    """Prepare current harness against the prior run's exact recorded inputs.

    This does not refresh the pilot, resume a model conversation, or carry over
    previous proposals, reviews, retrieved literature or tool memory.
    """
    from genomics_discovery.ideation.context import AtlasContext
    from genomics_discovery.ideation import run as ideation_run

    reference_run, output = Path(reference_run).resolve(), Path(output).resolve()
    if output == reference_run or output.is_relative_to(reference_run):
        raise ValueError('Matched preparation must not write inside the reference run')
    if output.exists():
        raise FileExistsError('Matched preparation requires a fresh output directory')
    config_path = reference_run/'config.json'
    config_pin = sha(config_path)
    previous = read(config_path)
    ideation_run.schemas_for(previous)  # Reject an unknown run contract.

    def pinned_path(file_key, hash_key):
        if not previous.get(file_key) or not previous.get(hash_key):
            raise ValueError('Reference config lacks input path/hash: ' + file_key)
        path = Path(previous[file_key])
        if not path.is_absolute():
            path = reference_run/path
        path = path.resolve()
        if sha(path) != previous[hash_key]:
            raise ValueError('Reference input changed: ' + file_key)
        return path

    atlas = pinned_path('atlas_file', 'atlas_sha256')
    request = pinned_path('request_file', 'request_sha256')
    if bool(previous.get('history_manifest')) != bool(previous.get('history_sha256')):
        raise ValueError('Reference history path/hash must both be present or absent')
    history = pinned_path('history_manifest', 'history_sha256') if previous.get('history_manifest') else None
    context = AtlasContext(atlas, expected_sha256=previous['atlas_sha256'],
        history_manifest=history, history_sha256=previous.get('history_sha256'))
    preflight = context.validate_history_inputs()
    # The reader above verifies every linked overlay and source registry. Check
    # input pins again before prepare, which computes its own fresh hashes.
    for file_key, hash_key in (('atlas_file', 'atlas_sha256'), ('request_file', 'request_sha256')):
        pinned_path(file_key, hash_key)
    if history:
        pinned_path('history_manifest', 'history_sha256')
    if sha(config_path) != config_pin:
        raise ValueError('Reference config changed during preparation')

    current = ideation_run.prepare(request, atlas, output, history_manifest=history)
    if current['atlas_sha256'] != previous['atlas_sha256'] or current.get('history_sha256') != previous.get('history_sha256'):
        raise ValueError('Prepared inputs differ from reference pins')
    # prepare validates the current Request schema and serializes it. Preserve
    # the original valid request bytes too, so formatting/default serialization
    # cannot masquerade as a change in the experimental input.
    if sha(request) != previous['request_sha256']:
        raise ValueError('Reference request changed during preparation')
    shutil.copyfile(request, current['request_file'])
    if sha(current['request_file']) != previous['request_sha256']:
        raise ValueError('Copied request differs from reference pin')
    current['request_sha256'] = previous['request_sha256']
    write(output/'config.json', current)

    code_root = Path(ideation_run.__file__).resolve().parent
    receipt = {'format_version': 'ideation_matched_inputs/1',
        'reference_run': str(reference_run), 'reference_config_sha256': config_pin,
        'reference_run_format': previous.get('format_version', 'ideation_run/1'),
        'prepared_run_format': current['format_version'],
        'atlas_sha256': current['atlas_sha256'], 'request_sha256': current['request_sha256'],
        'history_manifest': current.get('history_manifest'), 'history_sha256': current.get('history_sha256'),
        'history_preflight': preflight, 'input_match': 'exact atlas, request and history-manifest bytes',
        'current_harness_sources': {path.name: sha(path) for path in sorted(code_root.glob('*.py'))},
        'prior_run_memory': 'none; no candidates, reviews, retrieved literature, tool logs or model sessions copied',
        'boundary': 'Current harness with fixed prior atlas/request/history inputs; the pilot was not refreshed. '
                    'Baseline harness code hashes are not inferred from run format. Future web results and model-service behavior are not frozen; this alone is not a causal harness comparison.'}
    write(output/'matched_inputs.json', receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--pilot', type=Path)
    source.add_argument('--reference-run', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    result = prepare_from_reference(args.reference_run, args.output) if args.reference_run else snapshot(args.pilot, args.output)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

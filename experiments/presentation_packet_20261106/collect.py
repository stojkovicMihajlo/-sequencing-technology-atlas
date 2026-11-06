"""Copy a small, exact, read-only presentation snapshot from authorized results."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from datetime import datetime, timezone


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(project, output):
    output.mkdir(parents=True, exist_ok=False)
    work = project / 'technology_atlas/v2/work'
    batch = work / 'card_methods_full_20261106/run_02/extraction'
    runtime = work / 'card_methods_full_20261106/runtime_02'
    state = read(batch / 'state.json')
    receipts = []

    def copy(source, target, expected=None):
        before = sha(source)
        if expected and before != expected:
            raise ValueError('Source result changed: ' + str(source))
        dest = output / target; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        if sha(dest) != before:
            raise ValueError('Copy differs: ' + str(source))
        receipts.append({'path': str(target), 'source': str(source), 'sha256': before})

    for mid in ('atac-seq', 'clickseq', 'chipmentation'):
        row = state['cards'][mid]
        if row['status'] != 'supported':
            raise ValueError('Example needs a supported extraction: ' + mid)
        attempt = Path(row['attempt_directory']); dest = Path('examples') / mid
        for name in ('extraction.json', 'review.json', 'graph.json', 'card.json', 'source_registry.json', 'stages.json'):
            copy(attempt / name, dest / name, row['artifact_pins'].get(name))
        if (attempt / 'workflow.txt').exists():
            copy(attempt / 'workflow.txt', dest / 'workflow.txt')
        stages = read(attempt / 'stages.json')
        for role in ('author', 'reviewer'):
            stage = [s for s in stages if s['role'] == role][-1]
            path = Path(stage['path'])
            copy(path / 'stage_prompt.txt', dest / (role + '_prompt.txt'), stage.get('prompt_sha256'))
            copy(path / 'schema.json', dest / (role + '_schema.json'))
        registry = read(attempt / 'source_registry.json')
        for n, (sid, source) in enumerate((sid, s) for sid, s in registry.items() if s.get('kind') == 'image'):
            copy(Path(source['path']), dest / ('illumina_diagram' + ('' if n == 0 else '_' + str(n)) + '.png'), source['sha256'])
        (output / dest / 'admission_snapshot.json').write_text(json.dumps(row, indent=2) + '\n')
    # The paper-guided historical results are separate from workflow extraction.
    history = work / 'history_changes_20261106/run_01'
    for mid in ('atac-seq', 'chipmentation'):
        root = history / 'citations' / mid
        if read(root / 'state.json')['status'] != 'completed':
            raise ValueError('Incomplete history result: ' + mid)
        for name in ('analysis.json', 'review.json', 'graph.json', 'state.json'):
            copy(root / name, Path('history') / mid / name)
        stages = read(root / 'state.json')['stages']
        for role in ('nominate', 'analyse', 'review'):
            stage = [s for s in stages if s['role'] == role][-1]
            path = Path(stage['path'])
            copy(path / 'stage_prompt.txt', Path('history') / mid / (role + '_prompt.txt'))
            copy(path / 'schema.json', Path('history') / mid / (role + '_schema.json'))
        registry = read(root / 'inputs/sources.json')
        metadata = {sid: {k: v for k, v in source.items() if k in ('title', 'url', 'pmid', 'content_level')}
                    for sid, source in registry.items()}
        (output / 'history' / mid / 'source_metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    for relative in ('src/genomics_discovery/illumina/prompts.py',
                     'src/genomics_discovery/illumina/representation.py',
                     'src/genomics_discovery/illumina/models.py',
                     'src/genomics_discovery/illumina/extraction.py',
                     'src/genomics_discovery/illumina/graph.py',
                     'experiments/diagram_paper_pilot_20261106/run.py',
                     'experiments/card_methods_full_20261106/run.py'):
        copy(runtime / relative, Path('harness/extraction_runtime') / relative)
    lib = project / 'technology_sources/illumina_library_20261105_07'
    canonical = read(lib / 'canonical_inventory.json')
    for name in ('canonical_inventory.json', 'SUMMARY.json', 'COUNTS.txt', 'ALIASES_AND_SPLITS.csv'):
        copy(lib / name, Path('inventory') / name)
    cool = canonical['methods'].get('sccool-seq')
    if cool:
        target = output / 'source_only/sccool-seq'; target.mkdir(parents=True)
        (target / 'canonical_entry.json').write_text(json.dumps(cool, indent=2) + '\n')
    result = {'captured_at_utc': datetime.now(timezone.utc).isoformat(), 'batch': str(batch),
        'batch_updated_at': state.get('updated_at'), 'batch_counts': state['counts'],
        'extraction_job_id': '51324067', 'history_job_id': '51329874',
        'copied_source_files': receipts, 'boundary': 'Read-only result snapshot; no extraction or model calls.'}
    (output / 'COLLECTION_RECEIPT.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); collect(a.project, a.output)

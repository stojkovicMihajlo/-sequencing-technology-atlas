"""Reproduce this bounded, read-only audit of saved history examples.

This script creates audit artifacts only. It does not acquire papers, run models,
edit reviewed records, or establish fresh scientific acceptance.
"""
from pathlib import Path
import hashlib
import json
import subprocess


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
DATA = ROOT / 'web/explorer/public/evolution/reviewed-final-52094468'
SELECTED = {
    'eclip_iclip': '3f47bdd76f6f34ad2b68ba00',
    'scatac_combinatorial_atac': '8662835f938f8be6cd916a20',
    'scatac_microfluidics_atac': 'c0910b8e9eae5b51521a6c2e',
    'scatac_bulk_comparator': '56f9dc544e787e0302319e3e',
    'scatac_nextera_component': '2dff44e9cb8e2ab32ec18a64',
    'faire_array_to_seq': 'e5dbab18fb20c4f08429840b',
    'gro_concurrent_comparison': '8b35f18462646040989855c3',
    'nome_absent_mspa': '73882190a8e69fa7f70771ac',
}
CODE = [
    'src/genomics_discovery/illumina/change_records.py',
    'src/genomics_discovery/illumina/history_coverage.py',
    'src/genomics_discovery/illumina/paper_reads.py',
    'experiments/citation_lineage_pilot/prompts.py',
    'experiments/citation_lineage_pilot/run.py',
    'experiments/citation_lineage_pilot/change_models.py',
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main():
    manifest = json.loads((DATA / 'manifest.json').read_text())
    indexed = {e['file']: e for e in manifest['edges']}
    examples = {}
    for name, short_id in SELECTED.items():
        path = DATA / 'edges' / (short_id + '.json')
        entry = indexed['edges/' + short_id + '.json']
        assert sha(path) == entry['sha256'], path
        doc = json.loads(path.read_text())
        examples[name] = {
            'path': str(path.relative_to(ROOT)), 'sha256': sha(path),
            'id': doc['id'], 'target': doc['target'],
            'historical_development_edge': doc['historical_development_edge'],
            'relationship_layer': doc['relationship_layer'],
            'claim': doc['claim'], 'review': doc['review'],
            'original_graph': {
                k: doc['artifact'][k] for k in ['path', 'sha256', 'format_version']
            },
            'source_registry': {
                k: doc['artifact']['source_registry'][k]
                for k in ['path', 'sha256', 'sources']
            },
        }
    write('saved_examples.json', examples)
    write('snapshot_pins.json', {
        'observed_git_head': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'boundary': 'Saved-record semantic audit; not fresh source re-review or model validation.',
        'snapshot': manifest['snapshot'], 'counts': manifest['counts'],
        'source_index_sha256': manifest['source_index_sha256'],
        'manifest_sha256': sha(DATA / 'manifest.json'),
        'local_files': {p: sha(ROOT / p) for p in CODE},
        'examples': {name: {'path': e['path'], 'sha256': e['sha256']}
                     for name, e in examples.items()},
    })
    print(f'Verified {len(examples)} manifest-pinned examples; saved {len(CODE)} code pins.')


if __name__ == '__main__':
    main()

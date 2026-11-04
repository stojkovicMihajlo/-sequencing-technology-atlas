"""Read-only view of the selected archived representations; no source re-review."""
import argparse
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / 'provenance/experiments/illumina_extraction_20261102/audit_01/records.jsonl.gz'


def records():
    with gzip.open(ARCHIVE, 'rt') as stream:
        return {r['record_id']: r for r in map(json.loads, stream)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', action='append', default=[])
    parser.add_argument('--record', action='append', default=[])
    parser.add_argument('--overview', action='store_true')
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--compact', action='store_true', help='Short navigation excerpts; full records remain in the pinned archive')
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name('cases.json').read_text())
    rows = records()
    selected = args.record + [rid for group in args.group for rid in cases['groups'][group]]
    for rid in selected:
        row = rows[rid]
        c = row['candidate']
        print('\nMETHOD', rid, '|', row['status'])
        for state in c['states']:
            print('STATE', state['id'], state['kind'], state['label'])
            if not args.overview:
                print('  FEATURES', ' | '.join(s[:100] if args.compact else s for s in (state['features'][:2] if args.compact else state['features'])))
        for op in c['operations']:
            print('OP', op['id'], op['label'], op['inputs'], '->', op['outputs'])
            print('  EFFECT', op['description'][:160] if args.compact else op['description'])
            if args.full:
                print('  MECHANISM', op['mechanism'])
                print('  INFORMATION', op['information_effect'])
        if args.full:
            print('FLOWS', [(f['source'], f['target'], f['kind']) for f in c['flows']])
        print('SCOPES', [(s['method'], s['status']) for s in c['method_scopes']])
        print('UNKNOWNS', [(u['question'], u['blocking']) for u in c['unknowns']])


if __name__ == '__main__':
    main()

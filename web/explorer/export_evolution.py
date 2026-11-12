"""Publish a rebuilt reviewed history index for the interactive explorer.

This is a deterministic display export. It does not infer or review relationships.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import copy


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def export(index_path, receipt_path, destination, snapshot, label, method_index=None):
    raw = index_path.read_bytes()
    original = json.loads(raw)
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('scientific_records_changed') is not False:
        raise ValueError('Need the actual model-free rebuild receipt')
    if receipt.get('view', {}).get('history_index_sha256') != sha(raw):
        raise ValueError('Rebuild receipt does not bind this index')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', snapshot):
        raise ValueError('Unsafe snapshot name')
    nodes = copy.deepcopy(original['nodes'])
    display_source = None
    if method_index:
        inventory_raw = method_index.read_bytes()
        inventory = json.loads(inventory_raw)
        records = {r['record_id']: r for r in inventory['records']}
        display_source = {'path': str(method_index), 'sha256': sha(inventory_raw),
                          'basis': 'Exact canonical record ID; display title only, no scientific-record replacement.'}
        for node in nodes.values():
            record = records.get(node.get('record_id'))
            if record and record.get('title'):
                node['display_name'] = record['title']

    edges = original['edges']
    if len({e['id'] for e in edges}) != len(edges):
        raise ValueError('Duplicate relationship identity')
    for edge in edges:
        if edge['source'] not in nodes or edge['target'] not in nodes:
            raise ValueError('Unknown relationship endpoint')
        if edge['review']['decision'] != 'accept':
            raise ValueError('An unaccepted relationship cannot enter this display')
        if type(edge.get('historical_development_edge')) is not bool:
            raise ValueError('History/context classification must come from the compiled index')
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / snapshot
    if target.exists():
        raise ValueError('Snapshot already exists; preserve the previous display version')
    with tempfile.TemporaryDirectory(dir=destination, prefix='.export-') as temp:
        staging = Path(temp)
        (staging / 'edges').mkdir()
        summaries = []
        for edge in edges:
            encoded = json.dumps(edge, ensure_ascii=False, indent=2).encode()
            filename = sha(edge['id'].encode())[:24] + '.json'
            (staging / 'edges' / filename).write_bytes(encoded)
            summaries.append({k: edge.get(k) for k in ['id','source','target','relation','historical_development_edge','relationship_layer','status']})
            summaries[-1].update(file='edges/'+filename, sha256=sha(encoded),
                contribution=edge.get('claim', {}).get('contributor'))
        main = sum(e['historical_development_edge'] for e in edges)
        manifest = {'format_version':'atlas-evolution-explorer/1','snapshot':snapshot,'label':label,
                    'source_index_sha256':sha(raw),'source_rebuild_receipt_sha256':sha(receipt_path.read_bytes()),
                    'counts':{'experimental_relationships':main,'supporting_relationships':len(edges)-main,
                              'nodes':len(nodes),'reviewed_overlays':receipt.get('reviewed_overlays_recompiled',receipt.get('reviewed_method_histories'))},
                    'identity_issue_count':len(original.get('method_identity_issues',[])),
                    'display_name_source':display_source,
                    'nodes':nodes,'edges':summaries,
                    'boundary':'Accepted saved relationship records only. Layout and selection do not establish ancestry or experimental validity.'}
        (staging/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
        (staging/'REBUILD_RECEIPT.json').write_bytes(receipt_path.read_bytes())
        staging.rename(target)
    pointer = destination/'.current.tmp'
    pointer.write_text(json.dumps({'manifest':snapshot+'/manifest.json'}))
    pointer.replace(destination/'current.json')
    return manifest['counts']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--snapshot',required=True)
    parser.add_argument('--method-index',type=Path,help='Optional exact-ID catalogue display titles; original names are preserved.')
    parser.add_argument('--label',default='Reviewed technology histories')
    parser.add_argument('--destination',type=Path,default=Path(__file__).parent/'public/evolution')
    args = parser.parse_args()
    print(export(args.index,args.receipt,args.destination,args.snapshot,args.label,args.method_index))

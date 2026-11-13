"""Copy a pinned old runtime to a NEW release; apply only the focused policy patch.

Never submits jobs or calls models. The shared byte-copy helper deliberately
rejects unsupported links rather than copying ownership/mode/time attributes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_inputs(plan):
    for item in plan['reuse'].values():
        for rel,pin in item['pins'].items():
            if sha(Path(item['previous_run'])/rel)!=pin:
                raise ValueError('Original pilot input changed: '+rel)


def build(base,destination,payload_root):
    from experiments.method_intake_audit_20261113.preserve_bytes import preserve_tree
    base=Path(base).resolve();destination=Path(destination).resolve();payload_root=Path(payload_root).resolve()
    if destination.exists() or base==destination or base in destination.parents:
        raise ValueError('Release must be a new directory outside the frozen base')
    baseline=json.loads((HERE/'native_base_receipt.json').read_text())
    for rel,pin in baseline['files'].items():
        if sha(base/rel)!=pin:
            raise ValueError('Not the reviewed runtime_52073185 base: '+rel)
    plan=json.loads((HERE/'pilot_plan.json').read_text());verify_inputs(plan)
    copied=preserve_tree(base,destination,manifest_path=destination.parent/(destination.name+'.base-copy.json'))
    # Payload contains only reviewed experiment files and the SAME shared helper.
    for experiment in ('history_contract_audit_20261113','method_intake_audit_20261113'):
        preserve_tree(payload_root/'experiments'/experiment,
                      destination/'experiments'/experiment,
                      manifest_path=destination.parent/(destination.name+'.'+experiment+'.copy.json'))
    patch=destination/'experiments/history_contract_audit_20261113/native_integration.patch'
    subprocess.run(['git','apply','--check',str(patch)],cwd=destination,check=True)
    subprocess.run(['git','apply',str(patch)],cwd=destination,check=True)
    pins=json.loads((HERE/'native_integration_expected_pins.json').read_text())
    for rel,pin in pins.items():
        if sha(destination/rel)!=pin:
            raise ValueError('New release differs from reviewed focused patch: '+rel)
    changed=[row['path'] for row in copied['files'] if sha(destination/row['path'])!=row['sha256']]
    if set(changed)!={'experiments/citation_lineage_pilot/run.py','experiments/citation_lineage_pilot/prompts.py'}:
        raise ValueError('Unexpected baseline files changed: '+repr(changed))
    for rel,pin in baseline['files'].items():
        if sha(base/rel)!=pin:
            raise ValueError('Frozen base unexpectedly changed')
    verify_inputs(plan)
    receipt={'status':'software_release_prepared_no_models','base_runtime':str(base),
        'release':str(destination),'baseline_pins':baseline['files'],'candidate_pins':pins,
        'unchanged_baseline_files_verified':len(copied['files'])-len(changed),
        'changed_baseline_code_files':['experiments/citation_lineage_pilot/run.py',
                                       'experiments/citation_lineage_pilot/prompts.py'],
        'input_pins':plan['reuse'],'default_reading_policy':'full','pilot_reading_policy':'focused',
        'boundary':'Byte verification only. Full software/native custody preflight still required before models.'}
    content=json.dumps(receipt,indent=2)+'\n'
    (destination/'FOCUSED_RELEASE.json').write_text(content)
    (destination/'experiments/history_contract_audit_20261113/FOCUSED_RELEASE.json').write_text(content)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--payload-root',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.base,args.destination,args.payload_root),indent=2))


if __name__=='__main__':
    main()

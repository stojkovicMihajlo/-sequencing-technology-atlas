"""Preserve new pilot outcomes and the small reviewed code payload; never delete."""
import argparse
from pathlib import Path

from experiments.method_intake_audit_20261113.preserve_bytes import preserve_tree


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for arg in ('output','payload','release','destination'):
        parser.add_argument('--'+arg,type=Path,required=True)
    args=parser.parse_args();args.destination.mkdir(parents=True,exist_ok=True)
    for name,source in (
        ('native_outputs',args.output),
        ('reviewed_payload',args.payload),
        ('release_sidecar',args.release/'experiments/history_contract_audit_20261113')):
        if source.is_dir():
            preserve_tree(source,args.destination/name,
                          manifest_path=args.destination/(name+'.verification.json'))


if __name__=='__main__':
    main()

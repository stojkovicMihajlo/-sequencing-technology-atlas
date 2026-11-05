"""Run once: python -m scripts.illumina.technology_sources --help."""
import argparse
from pathlib import Path

from .inventory import build, read
from .materialize import materialize, publish, write
from .render import render


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory', type=Path, required=True, help='Source-only inventory produced by inventory.build')
    p.add_argument('--manifest', type=Path, action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--recover', action='store_true', help='Try public fulltext endpoints for missing explicitly listed papers')
    args = p.parse_args()
    state = materialize(read(args.inventory), args.output, args.manifest)
    if args.recover:
        from .recover import recover
        recover(args.output, state)
    publish(args.output, state)
    result = render(args.output, state)
    # Integrity is checked after links and public views are made.
    failures = []
    for path in args.output.rglob('*'):
        if path.is_symlink() and (not path.exists() or not path.resolve().is_relative_to(args.output.resolve())):
            failures.append(str(path))
    write(args.output / 'LINK_CHECK.json', {'broken_or_external_links': failures, 'passed': not failures})
    print(result, flush=True)
    if failures:
        raise SystemExit('Collection has broken/nonportable links')


if __name__ == '__main__':
    main()

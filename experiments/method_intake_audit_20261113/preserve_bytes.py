"""Copy an explicitly scoped tree's bytes and verify hashes without copying attrs.

Suitable for filesystems that reject chmod/chown/utime/xattrs. No symlinks,
special files, deletion, overwrite of differing bytes, or log compression.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def inventory(source):
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Source must be a real directory, not a symlink')
    records, directories = [], []
    for parent, names, files in os.walk(source, followlinks=False):
        for name in names:
            path = Path(parent) / name
            if path.is_symlink() or not path.is_dir():
                raise ValueError('Symlink/non-directory in source tree: ' + str(path))
            directories.append(path.relative_to(source).as_posix())
        for name in files:
            path = Path(parent) / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError('Symlink/special file in source tree: ' + str(path))
            records.append({'path':path.relative_to(source).as_posix(),
                            'bytes':path.stat().st_size, 'sha256':sha(path)})
    return sorted(records, key=lambda r:r['path']), sorted(directories)


def preserve_tree(source, destination, *, manifest_path=None):
    """Copy bytes, resume matching files, verify every hash, write a new receipt.

    manifest_path can be outside destination when preserving a runtime already
    containing PRESERVATION_MANIFEST.json. An existing identical receipt is reused;
    conflicting receipt bytes fail. Caller chooses an isolated destination.
    """
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if source.is_symlink() or destination.is_symlink():
        raise ValueError('Source/destination root cannot be a symlink')
    source = source.resolve(strict=True); destination = destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('Source and destination trees must be separate')
    records, directories = inventory(source)
    receipt_path = (Path(manifest_path).absolute() if manifest_path is not None else
                    destination / 'PRESERVATION_MANIFEST.json')
    if receipt_path.is_symlink():
        raise ValueError('Receipt cannot be a symlink')
    receipt_path = receipt_path.resolve()
    if receipt_path.is_relative_to(source):
        raise ValueError('Receipt must not modify the original source tree')
    if receipt_path.is_relative_to(destination):
        relative = receipt_path.relative_to(destination).as_posix()
        if relative in {r['path'] for r in records} or relative in directories:
            raise ValueError('Receipt would replace original source bytes; choose manifest_path outside destination')
    destination.mkdir(parents=True, exist_ok=True)
    for name in directories:
        path = destination / name
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError('Destination directory conflict: ' + str(path))
        path.mkdir(parents=True, exist_ok=True)
    for record in records:
        original, copied = source / record['path'], destination / record['path']
        copied.parent.mkdir(parents=True, exist_ok=True)
        if copied.is_symlink():
            raise ValueError('Destination file cannot be a symlink: ' + str(copied))
        if copied.exists():
            if not copied.is_file() or sha(copied) != record['sha256']:
                raise ValueError('Differing destination bytes; retain both trees and choose a new destination: ' + str(copied))
        else:
            # Copy only file contents. Never call copy2/copytree/cp-a or request
            # ownership, permissions, timestamps, ACLs, or extended attributes.
            with original.open('rb') as src, copied.open('xb') as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
        if sha(original) != record['sha256'] or sha(copied) != record['sha256']:
            raise ValueError('Source changed or destination verification failed: ' + record['path'])
    # Re-enumerate source to detect additions/removals while copying.
    after, after_dirs = inventory(source)
    if after != records or after_dirs != directories:
        raise ValueError('Source tree changed during preservation')
    manifest = {'format_version':'byte_verified_tree_copy/1','source':str(source),
                'destination':str(destination),'files':records,'directories':directories,
                'file_count':len(records),'total_bytes':sum(r['bytes'] for r in records),
                'all_destination_hashes_verified':True,
                'metadata_policy':'File contents only; no ownership/permission/timestamp/ACL/xattr preservation requested.',
                'limits':'No scientific reinterpretation, compression, deletion or symlink dereference. Source originals remain unchanged.'}
    content = json.dumps(manifest, indent=2) + '\n'
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    if receipt_path.exists():
        if receipt_path.read_text() != content:
            raise ValueError('Existing preservation receipt differs; choose a fresh manifest_path')
    else:
        with receipt_path.open('x') as handle:
            handle.write(content)
    return manifest


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--manifest',type=Path)
    args=parser.parse_args()
    result=preserve_tree(args.source,args.destination,manifest_path=args.manifest)
    print(json.dumps({k:result[k] for k in ('file_count','total_bytes','all_destination_hashes_verified')}))

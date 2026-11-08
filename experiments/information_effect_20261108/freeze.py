"""Freeze the earlier aligned runtime plus an explicit, small experimental overlay."""
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = ROOT / 'experiments/readability_audit_20261108/deployment_02'
OVERLAY = [
    'src/genomics_discovery/illumina/prompts.py',
    'src/genomics_discovery/illumina/extraction.py',
    'experiments/readability_audit_20261108/rerun.py',
    'tests/illumina/test_information_effect_experiment.py',
] + [f'experiments/information_effect_20261108/{name}' for name in
     ('run.py', 'plan.json', 'definition.txt', 'launch.sbatch', 'freeze.py')]


def sha(data):
    return hashlib.sha256(data).hexdigest()


if __name__ == '__main__':
    base = json.loads((BASE / 'runtime_02_manifest.json').read_text())
    archive = BASE / 'runtime_02.tar.gz'
    assert sha(archive.read_bytes()) == base['archive_sha256']
    with tarfile.open(archive) as tar:
        contents = {name: tar.extractfile(name).read() for name in base['files']}
    assert {k: sha(v) for k, v in contents.items()} == base['files']
    changes = {}
    for name in OVERLAY:
        content = (ROOT / name).read_bytes()
        changes[name] = {'before': base['files'].get(name), 'after': sha(content)}
        contents[name] = content
    output = HERE / 'deployment_01'
    output.mkdir(exist_ok=False)
    target = output / 'runtime_01.tar.gz'
    with tarfile.open(target, 'w:gz') as tar:
        for name, content in sorted(contents.items()):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))
    manifest = {'base_runtime_sha256': base['archive_sha256'],
                'code_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'files': {k: sha(v) for k, v in sorted(contents.items())},
                'overlay': changes, 'archive_sha256': sha(target.read_bytes())}
    (output / 'runtime_01_manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'archive': str(target), 'sha256': manifest['archive_sha256'], 'files': len(contents)}))

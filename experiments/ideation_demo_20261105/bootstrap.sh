#!/usr/bin/env bash
# Submitted to Slurm once; unpack and verify one frozen runtime in its allocation.
set -euo pipefail
: "${SLURM_JOB_ID:?Run inside a Slurm allocation}"
: "${ATLAS_IDEATION_ROOT:?Set the experiment directory}"
: "${ATLAS_IDEATION_RUNTIME:?Set the frozen runtime name}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
module load devel python/3.12.1
runtime="$ATLAS_IDEATION_ROOT/$ATLAS_IDEATION_RUNTIME"
mkdir "$runtime"
python3 - <<'PY'
import hashlib,json,os,tarfile
from pathlib import Path
root=Path(os.environ['ATLAS_IDEATION_ROOT'])
base=Path(os.environ['ATLAS_IDEATION_PILOT']).parent
runtime=root/os.environ['ATLAS_IDEATION_RUNTIME']
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(base/'runtime_01.tar.gz')=='e1ce60b61764a0a3866dff4baf5712a09ea5951cac4ce17e5a2238ea2004bcd7'
assert sha(base/'runtime_01_manifest.json')=='b111b6f76bfd1fcc017f2abc8781f9a0fd851bd419c4a74dbcfded66a6f1a747'
original=json.loads((base/'runtime_01_manifest.json').read_text())
overlay=json.loads((root/'ideation_01_manifest.json').read_text())
assert sha(root/'ideation_01.tar.gz')==overlay['archive_sha256']
for archive in (base/'runtime_01.tar.gz',root/'ideation_01.tar.gz'):
    with tarfile.open(archive) as tf:
        tf.extractall(runtime,filter='data')
combined={'format_version':'frozen_runtime/1','base_git_commit':original['git_commit'],
          'ideation_git_commit':overlay['commit'],'files':{**original['files'],**overlay['files']}}
combined['file_count']=len(combined['files'])
for name,pin in combined['files'].items():
    assert sha(runtime/name)==pin,name
(root/(runtime.name+'_manifest.json')).write_text(json.dumps(combined,indent=2)+'\n')
print(json.dumps({'runtime':str(runtime),'files':combined['file_count'],
                  'base_commit':original['git_commit'],'ideation_commit':overlay['commit']}))
PY
exec bash "$runtime/experiments/ideation_demo_20261105/launch.sbatch"

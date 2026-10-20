#!/bin/bash
#SBATCH --job-name=atlas-qiu-migration
#SBATCH --partition=service
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm after the old writers finish}"
: "${1:?Pass the separate migration control directory}"
ATLAS_CONTROL=$(realpath "$1")
export ATLAS_SOURCE_GROUP_HOME=/home/groups/rbaltman
export ATLAS_SOURCE_GROUP_SCRATCH=/scratch/groups/rbaltman
export GROUP_HOME=/home/groups/xiaojie
export GROUP_SCRATCH=/scratch/groups/xiaojie
export ATLAS_SOURCE_ROOT="$ATLAS_SOURCE_GROUP_SCRATCH/mstojkov/agents_snRNA"
export ATLAS_PROJECT_ROOT="$GROUP_SCRATCH/mstojkov/agents_snRNA"
export ATLAS_SOFTWARE_ROOT="$GROUP_HOME/mstojkov/software"
export ATLAS_ENV_DIR="$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312"
export ATLAS_CODEX_BIN_DIR="$ATLAS_SOFTWARE_ROOT/codex/0.163.0/bin"
export ATLAS_REPOSITORY="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/deployment_20261019/implementation_release_08"
export UV_PROJECT_ENVIRONMENT="$ATLAS_ENV_DIR"
export UV_CACHE_DIR="$SCRATCH/atlas-package-cache/uv"
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR="$L_SCRATCH_JOB/atlas-migration-tmp"
umask 027
mkdir -p "$TMPDIR"
hostname
id
test -d "$ATLAS_SOURCE_ROOT"
test ! -e "$ATLAS_PROJECT_ROOT"
test ! -e "$ATLAS_SOFTWARE_ROOT/codex/0.163.0"
test ! -e "$ATLAS_SOFTWARE_ROOT/atlas/20261019-01"
test ! -e "$ATLAS_ENV_DIR"
# All destination directories are owned by this user. Do not change lab roots.
mkdir -p "$GROUP_SCRATCH/mstojkov" "$GROUP_HOME/mstojkov"
chgrp xiaojie "$GROUP_SCRATCH/mstojkov" "$GROUP_HOME/mstojkov"
mkdir -p "$ATLAS_PROJECT_ROOT" "$ATLAS_SOFTWARE_ROOT/codex/0.163.0" \
    "$ATLAS_SOFTWARE_ROOT/atlas/20261019-01"
chgrp xiaojie "$ATLAS_PROJECT_ROOT" "$ATLAS_SOFTWARE_ROOT" \
    "$ATLAS_SOFTWARE_ROOT/codex" "$ATLAS_SOFTWARE_ROOT/codex/0.163.0" \
    "$ATLAS_SOFTWARE_ROOT/atlas" "$ATLAS_SOFTWARE_ROOT/atlas/20261019-01"
chmod g+s "$ATLAS_PROJECT_ROOT" "$ATLAS_SOFTWARE_ROOT" \
    "$ATLAS_SOFTWARE_ROOT/codex" "$ATLAS_SOFTWARE_ROOT/atlas"
ATLAS_COPY_ARGS=(-a --no-owner --chown=:xiaojie --omit-dir-times
    --exclude=.venv --exclude=node_modules --exclude=__pycache__
    --exclude=.pytest_cache --exclude=.DS_Store
    --exclude=.codex/ --exclude=.ssh/ --exclude=.aws/
    --exclude=.env --exclude='.env.*' --exclude=.git-credentials)
# No --delete, no moving source data, no rewriting historical paths or hashes.
rsync "${ATLAS_COPY_ARGS[@]}" --stats "$ATLAS_SOURCE_ROOT/" "$ATLAS_PROJECT_ROOT/" \
    > "$ATLAS_CONTROL/project-copy.log"
rsync "${ATLAS_COPY_ARGS[@]}" --dry-run --checksum --itemize-changes \
    "$ATLAS_SOURCE_ROOT/" "$ATLAS_PROJECT_ROOT/" > "$ATLAS_CONTROL/project-differences.txt"
test ! -s "$ATLAS_CONTROL/project-differences.txt"
for ATLAS_COMPONENT in codex/0.163.0 atlas/20261019-01; do
    rsync -a --no-owner --chown=:xiaojie --omit-dir-times \
        "$ATLAS_SOURCE_GROUP_HOME/mstojkov/software/$ATLAS_COMPONENT/" \
        "$ATLAS_SOFTWARE_ROOT/$ATLAS_COMPONENT/"
done
module load devel python/3.12.1 uv/0.10.8
module load system poppler/0.47.0
export PATH="$ATLAS_CODEX_BIN_DIR:$PATH"
# Python virtualenvs contain installation paths: rebuild; never copy the old one.
uv sync --project "$ATLAS_REPOSITORY" --frozen --extra dev \
    --python "$(command -v python3)" --no-python-downloads
"$ATLAS_ENV_DIR/bin/python" - "$ATLAS_CONTROL" <<'PY'
import hashlib,json,os,pathlib,datetime
control=pathlib.Path(__import__('sys').argv[1])
source=pathlib.Path(os.environ['ATLAS_SOURCE_ROOT'])
destination=pathlib.Path(os.environ['ATLAS_PROJECT_ROOT'])
old_home=pathlib.Path(os.environ['ATLAS_SOURCE_GROUP_HOME'])/'mstojkov/software'
new_home=pathlib.Path(os.environ['ATLAS_SOFTWARE_ROOT'])
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
pins=[]
for relative in ['codex/0.163.0/bin/codex','atlas/20261019-01/atlas.sif']:
    old,new=old_home/relative,new_home/relative
    a,b=sha(old),sha(new)
    if a!=b:raise RuntimeError('Software copy differs: '+relative)
    pins.append({'source':str(old),'destination':str(new),'sha256':a})
runtime=pathlib.Path(os.environ['ATLAS_REPOSITORY'])
release=json.loads((runtime/'release.json').read_bytes())
drift=[p for p,h in release['base_files'].items() if sha(runtime/p)!=h]
if drift:raise RuntimeError('Frozen runtime differs after copy: '+repr(drift))
receipt={'status':'copy_verified_validation_pending','recorded_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'slurm_job_id':os.environ['SLURM_JOB_ID'],'source_root':str(source),'destination_root':str(destination),
    'project_comparison':'rsync checksum dry run had zero changed source entries',
    'software':pins,'runtime_manifest_sha256':sha(runtime/'release.json'),'frozen_code_drift':drift,
    'boundary':'Historical accepted artifacts retain original paths. New source registration follows; no acceptance transfer.'}
(control/'migration.json').write_text(json.dumps(receipt,indent=2)+'\n')
PY
ATLAS_VALIDATION="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/migration_to_xiaojie_20261019/validation_$SLURM_JOB_ID"
cd "$ATLAS_REPOSITORY"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$ATLAS_REPOSITORY"
# Fresh packets/plans reproduce source bytes and passages under the new root.
"$ATLAS_ENV_DIR/bin/python" scripts/validate_release.py \
    --project-root "$ATLAS_PROJECT_ROOT" \
    --old-root /Users/mihajlostojkovic/Documents/qiu_lab/agents_snRNA \
    --output "$ATLAS_VALIDATION" > "$ATLAS_CONTROL/validation.log" 2>&1
export ATLAS_VALIDATION
"$ATLAS_ENV_DIR/bin/python" - "$ATLAS_CONTROL" <<'PY'
import json,os,pathlib,shlex,sys,datetime
control=pathlib.Path(sys.argv[1]); receipt=json.loads((control/'migration.json').read_bytes())
validation=pathlib.Path(os.environ['ATLAS_VALIDATION'])
result=json.loads((validation/'validation.json').read_bytes())
if not result['offline_checks_passed']:raise RuntimeError('New-root validation failed')
activation=pathlib.Path(os.environ['GROUP_HOME'])/'mstojkov/atlas-qiu-environment.sh'
if activation.exists():raise FileExistsError(activation)
keys=['GROUP_HOME','GROUP_SCRATCH','ATLAS_PROJECT_ROOT','ATLAS_REPOSITORY','ATLAS_ENV_DIR','ATLAS_CODEX_BIN_DIR']
activation.write_text('# Source explicitly for this project; do not add to .bashrc.\n'+
    '\n'.join('export '+k+'='+shlex.quote(os.environ[k]) for k in keys)+'\n')
receipt.update(status='copy_and_new_root_validation_passed',validation=str(validation/'validation.json'),
    activation=str(activation),new_plan=str(validation/'smoke_batch'),
    finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    native_extraction_on_new_root_started=False)
(control/'migration.json').write_text(json.dumps(receipt,indent=2)+'\n')
archive=validation.parent/'migration_receipt.json'
archive.write_text(json.dumps(receipt,indent=2)+'\n')
# Small durable receipt; papers and bulk job I/O stay on scratch.
(activation.parent/'atlas-qiu-migration.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt,indent=2))
PY

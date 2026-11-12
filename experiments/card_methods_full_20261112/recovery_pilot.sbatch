#!/usr/bin/env bash
#SBATCH --job-name=atlas-evidence-repair
#SBATCH --partition=xiaojie,normal,service
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=02:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
work="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work"
stage="$work/card_methods_full_20261112/evidence_repair_01"
export ATLAS_REPOSITORY="$stage/runtime"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
export ATLAS_WORK_ROOT="$work"
export ATLAS_REPAIR_STAGE="$stage"
backup_results() {
    code=$?; trap - EXIT
    dest="$GROUP_HOME/mstojkov/atlas-results/card_methods_full_20261112"
    mkdir -p "$dest"
    tar --exclude=__pycache__ --exclude=.pytest_cache -czf "$dest/evidence_repair_${SLURM_JOB_ID}.tar.gz" -C "$stage" . || code=1
    exit "$code"
}
trap backup_results EXIT
python3 - <<'PY'
import hashlib,json,os,shutil
from pathlib import Path
s=Path(os.environ['ATLAS_REPAIR_STAGE']); parent=s.parent/'release_03'
runtime=Path(os.environ['ATLAS_REPOSITORY']);runtime.mkdir(exist_ok=False)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
files=json.loads((parent/'runtime_manifest.json').read_text())['files']
for name,pin in files.items():
    source=parent/'runtime_03'/name; assert sha(source)==pin,name
    target=runtime/name; target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
patch=json.loads((s/'patch_pins.json').read_text())
for name,pin in patch['files'].items():
    source=s/'patch'/name; assert sha(source)==pin,name
    target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);files[name]=pin
(s/'runtime_manifest.json').write_text(json.dumps({'parent_sha256':sha(parent/'runtime_manifest.json'),'files':files,'patch':patch},indent=2))
PY
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
    experiments/card_methods_full_20261112/test_located_documents.py \
    experiments/card_methods_full_20261112/test_recovery.py \
    experiments/diagram_paper_pilot_20261106/test_dependencies.py \
    --junitxml="$stage/software_checks.xml"
"$ATLAS_CODEX_BIN_DIR/codex" login status
"$ATLAS_ENV_DIR/bin/python" -m experiments.card_methods_full_20261112.investigate_holds \
    --snapshot "$stage/held_cases_snapshot.json" \
    --library "$ATLAS_PROJECT_ROOT/technology_sources/illumina_library_20261105_07" \
    --vault "$ATLAS_PROJECT_ROOT/technology_sources/illumina_task2_sources_20261105_08" \
    --output "$stage/pilot" --workers 3 --method hi-c --method smart-seq2 --method 2b-rad

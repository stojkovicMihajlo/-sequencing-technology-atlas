#!/usr/bin/env bash
#SBATCH --job-name=atlas-remaining33-prepare
#SBATCH --partition=xiaojie,normal
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=08:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
work="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work"
stage="$work/evidence_remaining33_20261112"
export ATLAS_REMAINING_STAGE="$stage"
export ATLAS_REPOSITORY="$stage/runtime_${SLURM_JOB_ID}"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
backup_results() {
    code=$?; trap - EXIT
    dest="$GROUP_HOME/mstojkov/atlas-results/evidence_remaining33_20261112"
    mkdir -p "$dest"
    items=("patch_pins.json" "job.sbatch" "manifest.json")
    for item in "run_${SLURM_JOB_ID}" "runtime_${SLURM_JOB_ID}_manifest.json" "software_${SLURM_JOB_ID}.xml"; do
        [[ ! -e "$stage/$item" ]] || items+=("$item")
    done
    tar --exclude=__pycache__ --exclude=.pytest_cache -czf "$dest/job_${SLURM_JOB_ID}.tar.gz" -C "$stage" "${items[@]}" || code=1
    exit "$code"
}
trap backup_results EXIT
python3 - <<'PY'
import hashlib,json,os,shutil
from pathlib import Path
s=Path(os.environ['ATLAS_REMAINING_STAGE']);work=s.parent
prior=work/'evidence_recovery_20261112'
base=prior/'runtime_52065945'
manifest_path=prior/'runtime_52065945_manifest.json'
manifest=json.loads(manifest_path.read_text())
runtime=Path(os.environ['ATLAS_REPOSITORY']);runtime.mkdir(exist_ok=False)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
files={}
def copy(source,name,pin):
    assert sha(source)==pin,name
    target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target);files[name]=pin
for name,pin in manifest['files'].items():copy(base/name,name,pin)
updates=json.loads((s/'patch_pins.json').read_text())
assert sha(s/'manifest.json')==updates['remaining_manifest_sha256'],'Remaining-method manifest changed'
for name,pin in updates['files'].items():copy(s/'patch'/name,name,pin)
for name in ['src/genomics_discovery/illumina/prompts.py','src/genomics_discovery/illumina/representation.py']:
    assert sha(runtime/name)==manifest['files'][name],name
(s/('runtime_'+os.environ['SLURM_JOB_ID']+'_manifest.json')).write_text(json.dumps({
    'base_manifest_sha256':sha(manifest_path),'files':files,'patch':updates},indent=2))
print('Runtime pinned; scientific author/reviewer instructions unchanged.',flush=True)
PY
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-remaining-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-remaining-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
    experiments/evidence_recovery_20261112/test_prepare_remaining.py \
    experiments/evidence_recovery_20261112/test_recovery_dispatch.py \
    experiments/card_methods_full_20261112/test_located_documents.py \
    tests/illumina/test_illumina_sources.py \
    experiments/diagram_paper_pilot_20261106/test_dependencies.py \
    --junitxml="$stage/software_${SLURM_JOB_ID}.xml"
"$ATLAS_CODEX_BIN_DIR/codex" login status
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(preflight_pinned_shell_environment())
PY
"$ATLAS_ENV_DIR/bin/python" -m experiments.evidence_recovery_20261112.prepare_remaining \
    --manifest "$stage/manifest.json" \
    --role-corrections "$ATLAS_REPOSITORY/experiments/evidence_recovery_20261112/remaining_33/role_corrections.json" \
    --library "$ATLAS_PROJECT_ROOT/technology_sources/illumina_library_20261105_07" \
    --vault "$ATLAS_PROJECT_ROOT/technology_sources/illumina_task2_sources_20261105_08" \
    --output "$stage/run_${SLURM_JOB_ID}" --workers 50

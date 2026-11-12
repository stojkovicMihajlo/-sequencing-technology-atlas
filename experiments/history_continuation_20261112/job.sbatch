#!/usr/bin/env bash
#SBATCH --job-name=atlas-history-rebuild-retry
#SBATCH --partition=normal,xiaojie
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=06:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
work="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work"
stage="$work/history_continuation_20261112"
prior="$work/history_parallel_20261112"
export ATLAS_CONTINUATION_STAGE="$stage"
export ATLAS_REPOSITORY="$stage/runtime_${SLURM_JOB_ID}"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
backup_results() {
    code=$?; trap - EXIT
    dest="$GROUP_HOME/mstojkov/atlas-results/history_continuation_20261112"
    mkdir -p "$dest"
    items=("job.sbatch" "continuation_plan.json" "patch_pins.json")
    for item in "rebuild_${SLURM_JOB_ID}" "cip_retry_${SLURM_JOB_ID}" "runtime_${SLURM_JOB_ID}_manifest.json" "software_${SLURM_JOB_ID}.xml"; do
        [[ ! -e "$stage/$item" ]] || items+=("$item")
    done
    tar --exclude=__pycache__ --exclude=.pytest_cache -czf "$dest/job_${SLURM_JOB_ID}.tar.gz" -C "$stage" "${items[@]}" || code=1
    exit "$code"
}
trap backup_results EXIT
python3 - <<'PY'
import hashlib,json,os,shutil
from pathlib import Path
s=Path(os.environ['ATLAS_CONTINUATION_STAGE']);prior=s.parent/'history_parallel_20261112'
base=prior/'runtime_52073185';base_manifest=prior/'runtime_52073185_manifest.json'
manifest=json.loads(base_manifest.read_text());updates=json.loads((s/'patch_pins.json').read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(s/'continuation_plan.json')==updates['continuation_plan_sha256']
runtime=Path(os.environ['ATLAS_REPOSITORY']);runtime.mkdir(exist_ok=False);files={}
def copy(source,name,pin):
    assert sha(source)==pin,name
    target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target);files[name]=pin
for name,pin in manifest['files'].items():copy(base/name,name,pin)
for name,pin in updates['files'].items():copy(s/'patch'/name,name,pin)
for name in ('experiments/citation_lineage_pilot/prompts.py','experiments/citation_lineage_pilot/models.py','experiments/citation_lineage_pilot/change_models.py','src/genomics_discovery/illumina/change_records.py','src/genomics_discovery/illumina/history_method_identity.py'):
    assert sha(runtime/name)==manifest['files'][name],name
(s/('runtime_'+os.environ['SLURM_JOB_ID']+'_manifest.json')).write_text(json.dumps({'base_manifest_sha256':sha(base_manifest),'files':files,'patch':updates},indent=2))
PY
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-history-continuation-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-history-continuation-cache"
export ATLAS_REFERENCE_LOCK_FILE="$L_SCRATCH_JOB/atlas-reference-api.lock"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
    tests/illumina/test_history_method_identity.py tests/illumina/test_illumina_history.py \
    --junitxml="$stage/software_${SLURM_JOB_ID}.xml"
# Publish the 49 existing reviewed histories before spending on a fresh native call.
"$ATLAS_ENV_DIR/bin/python" -m experiments.history_continuation_20261112.rebuild \
    --plan "$stage/continuation_plan.json" --output "$stage/rebuild_${SLURM_JOB_ID}"
"$ATLAS_CODEX_BIN_DIR/codex" login status
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(preflight_pinned_shell_environment())
PY
# Exactly one failed seed is selected. Existing 49 histories are not rerun.
"$ATLAS_ENV_DIR/bin/python" -m experiments.history_changes_20261106.run \
    --batch "$prior/run_52073185/accepted_snapshot" \
    --method-inventory "$prior/preflight_52073185/method_inventory.json" \
    --library "$ATLAS_PROJECT_ROOT/technology_sources/illumina_library_20261105_07" \
    --vault "$ATLAS_PROJECT_ROOT/technology_sources/illumina_task2_sources_20261105_08" \
    --runtime "$ATLAS_REPOSITORY" --output "$stage/cip_retry_${SLURM_JOB_ID}" \
    --seed cip-tap --agents 1 --citation-only
# set -e stops on another failure; there is no resubmission or rephrasing loop.

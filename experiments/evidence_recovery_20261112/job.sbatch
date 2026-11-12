#!/usr/bin/env bash
#SBATCH --job-name=atlas-held50-recovery
#SBATCH --partition=xiaojie,normal
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=08:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
work="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work"
stage="$work/evidence_recovery_20261112"
export ATLAS_RECOVERY_STAGE="$stage"
export ATLAS_REPOSITORY="$stage/runtime_${SLURM_JOB_ID}"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
backup_results() {
    code=$?; trap - EXIT
    dest="$GROUP_HOME/mstojkov/atlas-results/evidence_recovery_20261112"
    mkdir -p "$dest"
    items=("patch_pins.json" "job.sbatch")
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
s=Path(os.environ['ATLAS_RECOVERY_STAGE']);work=s.parent
base=work/'card_methods_full_20261112/release_03'
prior=work/'card_methods_full_20261112/evidence_repair_01'
runtime=Path(os.environ['ATLAS_REPOSITORY']);runtime.mkdir(exist_ok=False)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
manifest=json.loads((base/'runtime_manifest.json').read_text());files={}
def copy(source,name,pin):
    assert sha(source)==pin,name
    target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target);files[name]=pin
for name,pin in manifest['files'].items():copy(base/'runtime_03'/name,name,pin)
for name,pin in json.loads((prior/'patch_pins.json').read_text())['files'].items():copy(prior/'patch'/name,name,pin)
updates=json.loads((s/'patch_pins.json').read_text())
for name,pin in updates['files'].items():copy(s/'patch'/name,name,pin)
(s/('runtime_'+os.environ['SLURM_JOB_ID']+'_manifest.json')).write_text(json.dumps({
    'base_manifest_sha256':sha(base/'runtime_manifest.json'),'files':files,'patch':updates},indent=2))
# The scientific author/reviewer prompts remain those of the active extraction.
for name in ['src/genomics_discovery/illumina/prompts.py','src/genomics_discovery/illumina/representation.py']:
    assert sha(runtime/name)==manifest['files'][name],name
print('Runtime pinned; extraction scientific instructions unchanged.',flush=True)
PY
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-recovery-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-recovery-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
  tests/illumina/test_illumina_source_queries.py tests/illumina/test_provider_block.py \
  experiments/card_methods_full_20261112/test_located_documents.py \
  experiments/card_methods_full_20261112/test_recovery.py \
  experiments/evidence_recovery_20261112/test_recovery_dispatch.py \
  experiments/card_methods_full_20261106/test_run.py \
  experiments/diagram_paper_pilot_20261106/test_sources.py \
  experiments/diagram_paper_pilot_20261106/test_dependencies.py \
  --junitxml="$stage/software_${SLURM_JOB_ID}.xml"
"$ATLAS_CODEX_BIN_DIR/codex" login status
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(preflight_pinned_shell_environment())
PY
"$ATLAS_ENV_DIR/bin/python" -m experiments.evidence_recovery_20261112.run \
  --snapshot "$work/card_methods_full_20261112/evidence_repair_01/held_cases_snapshot.json" \
  --methods "$ATLAS_REPOSITORY/experiments/evidence_recovery_20261112/held_method_ids.json" \
  --hints "$ATLAS_REPOSITORY/experiments/evidence_recovery_20261112/group_b/observed_source_hints.json" \
  --current "$work/card_methods_full_20261112/release_03/run_02/extraction" \
  --library "$ATLAS_PROJECT_ROOT/technology_sources/illumina_library_20261105_07" \
  --vault "$ATLAS_PROJECT_ROOT/technology_sources/illumina_task2_sources_20261105_08" \
  --output "$stage/run_${SLURM_JOB_ID}" --workers 47

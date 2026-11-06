#!/usr/bin/env bash
#SBATCH --job-name=atlas-reviewed-revisions
#SBATCH --partition=normal,xiaojie
#SBATCH --cpus-per-task=4
#SBATCH --mem=12G
#SBATCH --time=04:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_FOLLOWUP_ROOT:?}"
: "${ATLAS_FOLLOWUP_RUNTIME:?}"
: "${ATLAS_FOLLOWUP_OUTPUT:?}"
: "${ATLAS_FOLLOWUP_PREVIOUS:?}"
export ATLAS_REPOSITORY="$ATLAS_FOLLOWUP_ROOT/$ATLAS_FOLLOWUP_RUNTIME"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-revisions-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
backup_results() {
    result_code=$?
    trap - EXIT
    backup_root="$GROUP_HOME/mstojkov/atlas-results/card_methods_full_20261106"
    items=("$ATLAS_FOLLOWUP_RUNTIME.tar.gz" "${ATLAS_FOLLOWUP_RUNTIME}_manifest.json")
    for item in "$ATLAS_FOLLOWUP_OUTPUT" "software_${SLURM_JOB_ID}.xml"; do
        [[ ! -e "$ATLAS_FOLLOWUP_ROOT/$item" ]] || items+=("$item")
    done
    mkdir -p "$backup_root"
    if tar -czf "$backup_root/job_${SLURM_JOB_ID}.tar.gz" -C "$ATLAS_FOLLOWUP_ROOT" --exclude='__pycache__' "${items[@]}"; then
        sha256sum "$backup_root/job_${SLURM_JOB_ID}.tar.gz" > "$ATLAS_FOLLOWUP_ROOT/backup_${SLURM_JOB_ID}.sha256"
    else
        printf 'Durable backup failed; retain scratch results.\n' >&2
        [[ "$result_code" -ne 0 ]] || result_code=1
    fi
    exit "$result_code"
}
trap backup_results EXIT
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib,json,os
from pathlib import Path
root=Path(os.environ['ATLAS_REPOSITORY'])
manifest=json.loads((root.parent/(root.name+'_manifest.json')).read_text())
for name,expected in manifest['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
assert os.environ['CODEX_HOME'].endswith('/private/codex-atlas-runs')
assert not any(os.environ.get(k) for k in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN','OPENAI_IDENTITY_TOKEN_FILE','OPENAI_AUTH_PROVIDER_ID'))
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(json.dumps(preflight_pinned_shell_environment()))
PY
"$ATLAS_CODEX_BIN_DIR/codex" --version
"$ATLAS_CODEX_BIN_DIR/codex" login status
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
    tests/illumina/test_illumina_diagrams.py tests/illumina/test_representation_contract.py \
    tests/illumina/test_representation_pipeline.py tests/illumina/test_illumina_batch_controls.py \
    experiments/diagram_paper_pilot_20261106/test_sources.py \
    experiments/card_methods_full_20261106/test_run.py \
    experiments/card_methods_full_20261106/test_followup.py \
    --junitxml="$ATLAS_FOLLOWUP_ROOT/software_${SLURM_JOB_ID}.xml"
"$ATLAS_ENV_DIR/bin/python" -m experiments.card_methods_full_20261106.revise \
    --previous "$ATLAS_FOLLOWUP_PREVIOUS" \
    --output "$ATLAS_FOLLOWUP_ROOT/$ATLAS_FOLLOWUP_OUTPUT" --workers 10

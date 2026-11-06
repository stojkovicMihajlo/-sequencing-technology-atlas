#!/usr/bin/env bash
#SBATCH --job-name=atlas-history-bind
#SBATCH --partition=normal,xiaojie
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=03:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_HISTORY_ROOT:?Scratch experiment root}"
: "${ATLAS_HISTORY_RUNTIME:?New frozen runtime name}"
: "${ATLAS_HISTORY_SOURCE:?Completed citation run directory}"
: "${ATLAS_HISTORY_OUTPUT:?Unused connection output name}"
export ATLAS_REPOSITORY="$ATLAS_HISTORY_ROOT/$ATLAS_HISTORY_RUNTIME"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-history-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
backup_results() {
    result_code=$?
    trap - EXIT
    backup_root="$GROUP_HOME/mstojkov/atlas-results/history_changes_20261106"
    items=("$ATLAS_HISTORY_RUNTIME.tar.gz" "${ATLAS_HISTORY_RUNTIME}_manifest.json")
    for item in "$ATLAS_HISTORY_OUTPUT" "software_${SLURM_JOB_ID}.xml"; do
        [[ ! -e "$ATLAS_HISTORY_ROOT/$item" ]] || items+=("$item")
    done
    mkdir -p "$backup_root"
    if tar -czf "$backup_root/job_${SLURM_JOB_ID}.tar.gz" -C "$ATLAS_HISTORY_ROOT" --exclude='__pycache__' "${items[@]}"; then
        sha256sum "$backup_root/job_${SLURM_JOB_ID}.tar.gz" > "$ATLAS_HISTORY_ROOT/backup_${SLURM_JOB_ID}.sha256"
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
    tests/illumina/test_history_bindings.py tests/illumina/test_illumina_history.py \
    tests/ideation/test_context.py \
    experiments/citation_lineage_pilot/test_contract.py \
    experiments/citation_lineage_pilot/test_pair.py \
    experiments/citation_lineage_pilot/test_change_records.py \
    experiments/citation_lineage_pilot/test_binding_run.py \
    experiments/history_changes_20261106/test_connections.py \
    --junitxml="$ATLAS_HISTORY_ROOT/software_${SLURM_JOB_ID}.xml"
"$ATLAS_ENV_DIR/bin/python" -m experiments.history_changes_20261106.connect \
    --history-run "$ATLAS_HISTORY_SOURCE" --output "$ATLAS_HISTORY_ROOT/$ATLAS_HISTORY_OUTPUT" \
    --runtime "$ATLAS_REPOSITORY" --workers 2

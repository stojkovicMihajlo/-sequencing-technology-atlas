#!/usr/bin/env bash
#SBATCH --job-name=atlas-history-changes
#SBATCH --partition=normal,xiaojie
#SBATCH --cpus-per-task=4
#SBATCH --mem=12G
#SBATCH --time=06:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_HISTORY_ROOT:?Scratch experiment root}"
: "${ATLAS_HISTORY_RUNTIME:?Frozen runtime name}"
: "${ATLAS_HISTORY_OUTPUT:?Unused output name}"
: "${ATLAS_HISTORY_BATCH:?Existing full extraction batch}"
: "${ATLAS_HISTORY_SELECTION:?Pinned JSON method/review selection}"
export ATLAS_REPOSITORY="$ATLAS_HISTORY_ROOT/$ATLAS_HISTORY_RUNTIME"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-history-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-cache"
export OMP_NUM_THREADS=1
export ATLAS_REFERENCE_LOCK_FILE="$L_SCRATCH_JOB/atlas-reference-api.lock"
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
backup_results() {
    result_code=$?
    trap - EXIT
    backup_root="${ATLAS_HISTORY_BACKUP_ROOT:-$GROUP_HOME/mstojkov/atlas-results/history_changes_20261106}"
    items=("$ATLAS_HISTORY_RUNTIME.tar.gz" "${ATLAS_HISTORY_RUNTIME}_manifest.json")
    for item in "$ATLAS_HISTORY_OUTPUT" "software_${SLURM_JOB_ID}.xml" \
                "${ATLAS_HISTORY_PREFLIGHT:-}" "additional_software_${SLURM_JOB_ID}.xml"; do
        [[ -z "$item" || ! -e "$ATLAS_HISTORY_ROOT/$item" ]] || items+=("$item")
    done
    mkdir -p "$backup_root"
    if tar -czf "$backup_root/job_${SLURM_JOB_ID}.tar.gz" -C "$ATLAS_HISTORY_ROOT" \
        --exclude='__pycache__' "${items[@]}"; then
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
from experiments.history_changes_20261106.run import MAX_HISTORY_WORKERS
agents = os.environ.get('ATLAS_HISTORY_AGENTS')
legacy_workers = os.environ.get('ATLAS_HISTORY_WORKERS')
assert not (agents and legacy_workers and agents != legacy_workers), 'Conflicting history agent-count overrides'
override = agents or legacy_workers
if override:
    assert 1 <= int(override) <= MAX_HISTORY_WORKERS, 'History agent count is outside the supported range'
manifest=json.loads((root.parent/(root.name+'_manifest.json')).read_text())
for name,expected in manifest['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
assert os.environ['CODEX_HOME'].endswith('/private/codex-atlas-runs')
assert not any(os.environ.get(k) for k in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN',
    'OPENAI_IDENTITY_TOKEN_FILE','OPENAI_AUTH_PROVIDER_ID'))
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(json.dumps(preflight_pinned_shell_environment()))
PY
"$ATLAS_CODEX_BIN_DIR/codex" --version
"$ATLAS_CODEX_BIN_DIR/codex" login status
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
    tests/illumina/test_history_inputs.py \
    experiments/citation_lineage_pilot/test_contract.py \
    experiments/citation_lineage_pilot/test_pair.py \
    experiments/citation_lineage_pilot/test_change_records.py \
    experiments/citation_lineage_pilot/test_review_coverage.py \
    experiments/citation_lineage_pilot/test_binding_run.py \
    tests/illumina/test_history_bindings.py tests/illumina/test_illumina_history.py \
    experiments/pilot50_20261104/test_history_stage.py \
    experiments/history_changes_20261106/test_history_changes.py \
    --junitxml="$ATLAS_HISTORY_ROOT/software_${SLURM_JOB_ID}.xml"
history_options=()
if [[ -n "${ATLAS_HISTORY_METHOD_INVENTORY:-}" ]]; then
    history_options+=(--method-inventory "$ATLAS_HISTORY_METHOD_INVENTORY")
fi
history_agent_count="${ATLAS_HISTORY_AGENTS:-${ATLAS_HISTORY_WORKERS:-}}"
if [[ -n "$history_agent_count" ]]; then
    history_options+=(--agents "$history_agent_count")
fi
if [[ "${ATLAS_HISTORY_CITATION_ONLY:-0}" == "1" ]]; then
    history_options+=(--citation-only)
fi
"$ATLAS_ENV_DIR/bin/python" -m experiments.history_changes_20261106.run \
    --batch "$ATLAS_HISTORY_BATCH" \
    --library "$ATLAS_PROJECT_ROOT/technology_sources/illumina_library_20261105_07" \
    --vault "$ATLAS_PROJECT_ROOT/technology_sources/illumina_task2_sources_20261105_08" \
    --runtime "$ATLAS_REPOSITORY" --output "$ATLAS_HISTORY_ROOT/$ATLAS_HISTORY_OUTPUT" \
    --selection "$ATLAS_HISTORY_SELECTION" \
    "${history_options[@]}"

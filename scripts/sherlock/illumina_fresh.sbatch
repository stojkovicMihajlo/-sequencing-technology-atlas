#!/usr/bin/env bash
#SBATCH --job-name=illumina-fresh
#SBATCH --partition=xiaojie,normal
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=24:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_FRESH_RUN_ROOT:?Set the new run root explicitly}"
: "${ATLAS_RAW_SOURCE_ROOT:?Set the raw source snapshot root explicitly}"
: "${ATLAS_RUNTIME_NAME:=runtime_01}"
: "${ATLAS_SOURCE_NAME:=sources_01}"
: "${ATLAS_OUTPUT_NAME:=extractions_01}"
export ATLAS_REPOSITORY="$ATLAS_FRESH_RUN_ROOT/$ATLAS_RUNTIME_NAME"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export OMP_NUM_THREADS=1
[[ "$ATLAS_CODEX_ACCOUNT_LABEL" == atlas-agent-runs ]]
[[ "$CODEX_HOME" == "$GROUP_HOME/mstojkov/private/codex-atlas-runs" ]]
export CODEX_SQLITE_HOME="$SCRATCH/atlas-codex-secondary-20261102/sqlite"
mkdir -p "$CODEX_SQLITE_HOME"
chmod 700 "$CODEX_SQLITE_HOME"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-cache"
cd "$ATLAS_REPOSITORY"
backup_results() {
    result_code=$?
    trap - EXIT
    backup_root="$GROUP_HOME/mstojkov/atlas-results/$(basename "$ATLAS_FRESH_RUN_ROOT")"
    mkdir -p "$backup_root"
    tar -czf "$backup_root/job_${SLURM_JOB_ID}.tar.gz" -C "$ATLAS_FRESH_RUN_ROOT" \
        --exclude='*.tar.gz' --exclude='*.log' .
    sha256sum "$backup_root/job_${SLURM_JOB_ID}.tar.gz" > "$ATLAS_FRESH_RUN_ROOT/backup_${SLURM_JOB_ID}.sha256"
    exit "$result_code"
}
trap backup_results EXIT
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib,json,os
from pathlib import Path
root=Path(os.environ['ATLAS_REPOSITORY'])
receipt=json.loads((root.parent/(root.name+'_manifest.json')).read_text())
for name,expected in receipt['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
assert not any(os.environ.get(k) for k in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN','OPENAI_IDENTITY_TOKEN_FILE','OPENAI_AUTH_PROVIDER_ID')), 'Unexpected authentication override'
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(json.dumps(preflight_pinned_shell_environment()))
PY
"$ATLAS_CODEX_BIN_DIR/codex" --version
"$ATLAS_CODEX_BIN_DIR/codex" login status
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -q tests/illumina \
    --junitxml="$ATLAS_FRESH_RUN_ROOT/software_checks_${SLURM_JOB_ID}.xml"
"$ATLAS_ENV_DIR/bin/python" scripts/illumina/prepare_fresh_batch.py \
    --source-root "$ATLAS_RAW_SOURCE_ROOT" --annotations "$ATLAS_FRESH_RUN_ROOT/annotations" \
    --output "$ATLAS_FRESH_RUN_ROOT/$ATLAS_SOURCE_NAME"
"$ATLAS_ENV_DIR/bin/python" scripts/illumina/extract_all.py \
    --manifest "$ATLAS_FRESH_RUN_ROOT/$ATLAS_SOURCE_NAME/manifest.json" \
    --output "$ATLAS_FRESH_RUN_ROOT/$ATLAS_OUTPUT_NAME" --first-record atac-seq --first-record drop-seq

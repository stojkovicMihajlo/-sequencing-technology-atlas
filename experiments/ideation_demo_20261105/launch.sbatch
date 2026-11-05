#!/usr/bin/env bash
#SBATCH --job-name=atlas-ideation-demo
#SBATCH --partition=xiaojie,normal
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=12G
#SBATCH --time=04:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_IDEATION_ROOT:?Set a fresh experiment root}"
: "${ATLAS_IDEATION_RUNTIME:?Set the frozen code directory}"
: "${ATLAS_IDEATION_PILOT:?Set the read-only source pilot}"
export ATLAS_REPOSITORY="$ATLAS_IDEATION_ROOT/$ATLAS_IDEATION_RUNTIME"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-ideation-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-ideation-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
backup_results() {
    result_code=$?
    trap - EXIT
    backup_root="$GROUP_HOME/mstojkov/atlas-results/ideation_demo_20261105"
    mkdir -p "$backup_root"
    # One bounded backup after the run, not a persistent synchronizer.
    if ! tar -czf "$backup_root/job_${SLURM_JOB_ID}.tar.gz" -C "$ATLAS_IDEATION_ROOT" \
        --exclude='__pycache__' .; then
        printf 'Backup failed; retain scratch results. Original exit=%s\n' "$result_code" >&2
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
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p pytest_asyncio.plugin -q \
    tests/ideation tests/illumina/test_illumina_history.py tests/execution/test_curation_codex_worker.py \
    --junitxml="$ATLAS_IDEATION_ROOT/software_${SLURM_JOB_ID}.xml"
"$ATLAS_ENV_DIR/bin/python" -m experiments.ideation_demo_20261105.snapshot \
    --pilot "$ATLAS_IDEATION_PILOT" --output "$ATLAS_IDEATION_ROOT/snapshot_01"
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.ideation.run prepare \
    --request "$ATLAS_REPOSITORY/experiments/ideation_demo_20261105/request.json" \
    --atlas "$ATLAS_IDEATION_ROOT/snapshot_01/atlas.json" \
    --history-manifest "$ATLAS_IDEATION_ROOT/snapshot_01/history_inputs.json" \
    --output "$ATLAS_IDEATION_ROOT/run_01"
"$ATLAS_ENV_DIR/bin/python" -u -m genomics_discovery.ideation.run run \
    --directory "$ATLAS_IDEATION_ROOT/run_01"

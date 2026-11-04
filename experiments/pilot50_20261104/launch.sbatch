#!/usr/bin/env bash
#SBATCH --job-name=atlas-pilot50
#SBATCH --partition=xiaojie,normal
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=24:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_PILOT_ROOT:?Select the new scratch experiment directory}"
: "${ATLAS_PILOT_RUNTIME:?Select its immutable runtime}"
: "${ATLAS_PILOT_OUTPUT:?Select an unused output directory}"
export ATLAS_REPOSITORY="$ATLAS_PILOT_ROOT/$ATLAS_PILOT_RUNTIME"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$SCRATCH/atlas-codex-secondary-20261102/sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
backup_results() {
    result_code=$?
    trap - EXIT
    backup_root="$GROUP_HOME/mstojkov/atlas-results/pilot50_20261104"
    backup_items=()
    for item in "$ATLAS_PILOT_OUTPUT" "software_${SLURM_JOB_ID}.xml" \
        "${ATLAS_PILOT_RUNTIME}.tar.gz" "${ATLAS_PILOT_RUNTIME}_manifest.json"; do
        [[ ! -e "$ATLAS_PILOT_ROOT/$item" ]] || backup_items+=("$item")
    done
    # Export existing results once; this does not call a model or admit graph links.
    if [[ -f "$ATLAS_PILOT_ROOT/$ATLAS_PILOT_OUTPUT/extractions/state.json" ]]; then
        "$ATLAS_ENV_DIR/bin/python" "$ATLAS_REPOSITORY/experiments/transcriptomics_revision_20261104/tracking.py" \
            --extraction-batch "$ATLAS_PILOT_ROOT/$ATLAS_PILOT_OUTPUT/extractions" \
            --output "$ATLAS_PILOT_ROOT/$ATLAS_PILOT_OUTPUT/tracking_final" \
            || printf 'Tracking export failed; source results remain preserved.\n' >&2
    fi
    backup_status=0
    mkdir -p "$backup_root" && tar -czf "$backup_root/job_${SLURM_JOB_ID}.tar.gz" \
        -C "$ATLAS_PILOT_ROOT" --exclude='__pycache__' "${backup_items[@]}" \
        && sha256sum "$backup_root/job_${SLURM_JOB_ID}.tar.gz" > "$ATLAS_PILOT_ROOT/backup_${SLURM_JOB_ID}.sha256" \
        || backup_status=$?
    if [[ "$backup_status" -ne 0 ]]; then
        printf 'Durable backup failed (%s); retain scratch results. Original job exit=%s\n' "$backup_status" "$result_code" >&2
        [[ "$result_code" -ne 0 ]] || result_code=$backup_status
    fi
    exit "$result_code"
}
trap backup_results EXIT
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib, json, os
from pathlib import Path
root = Path(os.environ['ATLAS_REPOSITORY'])
manifest = json.loads((root.parent / (root.name + '_manifest.json')).read_text())
for name, expected in manifest['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest() == expected, name
assert os.environ['CODEX_HOME'].endswith('/private/codex-atlas-runs')
assert not any(os.environ.get(k) for k in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN',
                                         'OPENAI_IDENTITY_TOKEN_FILE', 'OPENAI_AUTH_PROVIDER_ID'))
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(json.dumps(preflight_pinned_shell_environment()))
PY
"$ATLAS_CODEX_BIN_DIR/codex" --version
"$ATLAS_CODEX_BIN_DIR/codex" login status
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p pytest_asyncio.plugin -q \
    tests/illumina experiments/citation_lineage_pilot/test_contract.py \
    experiments/citation_lineage_pilot/test_pair.py experiments/transcriptomics_revision_20261104/test_*.py \
    experiments/pilot50_20261104/test_*.py \
    --junitxml="$ATLAS_PILOT_ROOT/software_${SLURM_JOB_ID}.xml"
"$ATLAS_ENV_DIR/bin/python" -u -m experiments.pilot50_20261104.run \
    --selection "$ATLAS_REPOSITORY/experiments/pilot50_20261104/selection_candidates.json" \
    --output "$ATLAS_PILOT_ROOT/$ATLAS_PILOT_OUTPUT" \
    --runtime "$ATLAS_REPOSITORY" --workers 4

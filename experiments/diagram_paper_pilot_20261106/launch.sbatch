#!/usr/bin/env bash
#SBATCH --job-name=diagram-paper-10
#SBATCH --partition=service,normal,xiaojie
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=04:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_PILOT_ROOT:?New scratch experiment root}"
: "${ATLAS_PILOT_RUNTIME:?Frozen runtime directory name}"
: "${ATLAS_PILOT_OUTPUT:?Unused extraction output directory name}"
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
    backup_root="$GROUP_HOME/mstojkov/atlas-results/diagram_paper_pilot_20261106"
    items=("$ATLAS_PILOT_RUNTIME.tar.gz" "${ATLAS_PILOT_RUNTIME}_manifest.json")
    for item in "$ATLAS_PILOT_OUTPUT" "${ATLAS_PILOT_RESUME_OUTPUT:-}" \
        "${ATLAS_PILOT_DEPENDENCY_OUTPUT:-}" "software_${SLURM_JOB_ID}.xml"; do
        [[ -n "$item" ]] || continue
        [[ ! -e "$ATLAS_PILOT_ROOT/$item" ]] || items+=("$item")
    done
    mkdir -p "$backup_root"
    if tar -czf "$backup_root/job_${SLURM_JOB_ID}.tar.gz" -C "$ATLAS_PILOT_ROOT" \
        --exclude='__pycache__' "${items[@]}"; then
        sha256sum "$backup_root/job_${SLURM_JOB_ID}.tar.gz" > "$ATLAS_PILOT_ROOT/backup_${SLURM_JOB_ID}.sha256"
    else
        printf 'Durable backup failed; retain scratch results.\n' >&2
        [[ "$result_code" -ne 0 ]] || result_code=1
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
from PIL import Image
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(json.dumps(preflight_pinned_shell_environment()))
PY
"$ATLAS_CODEX_BIN_DIR/codex" --version
"$ATLAS_CODEX_BIN_DIR/codex" login status
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
    tests/illumina/test_illumina_diagrams.py tests/illumina/test_illumina_review_controls.py \
    tests/illumina/test_illumina_routing_controls.py tests/illumina/test_illumina_label_review.py \
    tests/illumina/test_representation_contract.py tests/illumina/test_representation_pipeline.py \
    tests/illumina/test_representation_semantic_controls.py tests/illumina/test_illumina_graph.py \
    tests/illumina/test_illumina_batch_controls.py tests/illumina/test_illumina_operation_groups.py \
    tests/illumina/test_illumina_author_resume.py \
    experiments/diagram_paper_pilot_20261106/test_sources.py \
    experiments/diagram_paper_pilot_20261106/test_dependencies.py \
    --junitxml="$ATLAS_PILOT_ROOT/software_${SLURM_JOB_ID}.xml"
extra_args=()
if [[ -n "${ATLAS_PILOT_RESUME_OUTPUT:-}" ]]; then
    extra_args+=(--resume-authors "$ATLAS_PILOT_ROOT/$ATLAS_PILOT_RESUME_OUTPUT")
fi
if [[ "${ATLAS_PILOT_STOP_ON_FIRST_ISSUE:-0}" == 1 ]]; then
    extra_args+=(--stop-on-first-issue)
fi
if [[ -n "${ATLAS_PILOT_ADDITIONS_PATH:-}" ]]; then
    if [[ -n "${ATLAS_PILOT_DEPENDENCY_OUTPUT:-}" ]]; then
        printf 'Choose existing additions or new dependency recovery, not both.\n' >&2
        exit 1
    fi
    extra_args+=(--additions "$ATLAS_PILOT_ADDITIONS_PATH")
fi
if [[ -n "${ATLAS_PILOT_DEPENDENCY_OUTPUT:-}" ]]; then
    : "${ATLAS_PILOT_RESUME_OUTPUT:?Dependency recovery requires the evidence-request parent run}"
    recovery_args=()
    if [[ -n "${ATLAS_PILOT_EXISTING_ADDITIONS:-}" ]]; then
        recovery_args+=(--existing-additions "$ATLAS_PILOT_EXISTING_ADDITIONS")
    fi
    "$ATLAS_ENV_DIR/bin/python" -m experiments.diagram_paper_pilot_20261106.recover_dependencies \
        --requests "$ATLAS_REPOSITORY/experiments/diagram_paper_pilot_20261106/dependency_requests.json" \
        --previous-run "$ATLAS_PILOT_ROOT/$ATLAS_PILOT_RESUME_OUTPUT" \
        --output "$ATLAS_PILOT_ROOT/$ATLAS_PILOT_DEPENDENCY_OUTPUT" "${recovery_args[@]}"
    extra_args+=(--additions "$ATLAS_PILOT_ROOT/$ATLAS_PILOT_DEPENDENCY_OUTPUT/registered_additions.json")
fi
"$ATLAS_ENV_DIR/bin/python" -m experiments.diagram_paper_pilot_20261106.run \
    --library "$ATLAS_PROJECT_ROOT/technology_sources/illumina_library_20261105_07" \
    --vault "$ATLAS_PROJECT_ROOT/technology_sources/illumina_task2_sources_20261105_08" \
    --selection "$ATLAS_REPOSITORY/experiments/diagram_paper_pilot_20261106/${ATLAS_PILOT_SELECTION:-selected.json}" \
    --output "$ATLAS_PILOT_ROOT/$ATLAS_PILOT_OUTPUT" --workers "${ATLAS_PILOT_WORKERS:-4}" "${extra_args[@]}"

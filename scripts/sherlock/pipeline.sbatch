#!/usr/bin/env bash
#SBATCH --job-name=atlas-pipeline
#SBATCH --partition=normal,xiaojie,service
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=02:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
cat /etc/agents/AGENTS.md
cat /etc/agents/slurm.md /etc/agents/storage.md /etc/agents/software.md /etc/agents/policy.md
selected_repository="${ATLAS_REPOSITORY:?Select the Git checkout/frozen code root}"
selected_run_directory="${ATLAS_RUN_DIR:?Select a prepared pipeline run directory}"
selected_archive_root="${ATLAS_ARCHIVE_ROOT:-}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
export ATLAS_REPOSITORY="$selected_repository"
export ATLAS_RUN_DIR="$selected_run_directory"
if [[ -n "$selected_archive_root" ]]; then
    export ATLAS_ARCHIVE_ROOT="$selected_archive_root"
fi
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$selected_repository/scripts/sherlock/environment.sh"
source "$selected_repository/scripts/sherlock/atlas-account.sh"
[[ "$ATLAS_REPOSITORY" == "$selected_repository" ]] || { echo 'Repository helper changed the selected runtime' >&2; exit 2; }
[[ "$ATLAS_RUN_DIR" == "$selected_run_directory" ]] || { echo 'Repository helper changed the selected run' >&2; exit 2; }
export PYTHONPATH="$ATLAS_REPOSITORY/src:$ATLAS_REPOSITORY"
export PYTHONDONTWRITEBYTECODE=1
export ATLAS_ARCHIVE_ROOT="${ATLAS_ARCHIVE_ROOT:-$GROUP_HOME/mstojkov/atlas-results/pipeline}"
preserve_run() {
    pipeline_status=$?
    trap - EXIT
    "$ATLAS_ENV_DIR/bin/python" "$ATLAS_REPOSITORY/experiments/method_intake_audit_20261113/preserve_bytes.py" \
        --source "$ATLAS_RUN_DIR" --destination "$ATLAS_ARCHIVE_ROOT/job_$SLURM_JOB_ID" \
        --manifest "$ATLAS_ARCHIVE_ROOT/job_$SLURM_JOB_ID.verification.json" \
        > "$ATLAS_RUN_DIR.preservation.log" 2>&1 || pipeline_status=1
    exit "$pipeline_status"
}
trap preserve_run EXIT
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.execution.pipeline execute --run-dir "$ATLAS_RUN_DIR"

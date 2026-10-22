#!/usr/bin/env bash
#SBATCH --job-name=atlas-clean-extract
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=02:30:00
set -euo pipefail
hostname
: "${SLURM_JOB_ID:?Submit through Slurm}"
printf 'SLURM_JOB_ID=%s\n' "$SLURM_JOB_ID"
selected_runtime="${ATLAS_CLEAN_RUNTIME:?Select the frozen code-only runtime}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
export ATLAS_REPOSITORY="$selected_runtime"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export OMP_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
root="${1:?Select a new experiment root}"
[[ "$root" == "$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/qiu_experiments_20261022/clean_extraction_"* ]] || exit 2
mkdir -p "$root/audit"

preserve_result() {
    result_code=$?
    trap - EXIT
    printf '%s\n' "$result_code" > "$root/audit/exit-$SLURM_JOB_ID.txt"
    archive="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/qiu_experiments_20261022/archives/clean-extraction-$SLURM_JOB_ID.tar.gz"
    mkdir -p "$(dirname "$archive")"
    if "$ATLAS_ENV_DIR/bin/python" "$ATLAS_REPOSITORY/scripts/benchmark/archive_result.py" \
        --experiment "$root" --output "$archive" --project-root "$ATLAS_PROJECT_ROOT"; then
        durable="$GROUP_HOME/mstojkov/atlas-results/$(basename "$archive")"
        mkdir -p "$(dirname "$durable")"
        [[ ! -e "$durable" ]] || exit 3
        (set -o noclobber; cat "$archive" > "$durable")
        cmp -s "$archive" "$durable" || exit 3
        sha256sum "$durable" > "$durable.sha256"
        printf '%s\n' "$durable" > "$root/audit/archive-$SLURM_JOB_ID.txt"
    else
        echo 'Archive failed; scratch alone is not durable preservation' >&2
        exit 3
    fi
    exit "$result_code"
}
trap preserve_result EXIT
cd "$ATLAS_REPOSITORY"
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.execution_check \
    --workdir "$ATLAS_REPOSITORY" --require-slurm > "$root/audit/execution-$SLURM_JOB_ID.json"
"$ATLAS_ENV_DIR/bin/python" --version
codex --version
"$ATLAS_ENV_DIR/bin/python" scripts/benchmark/run_clean_extraction.py \
    "$root/plan.json" --output "$root/run_001"

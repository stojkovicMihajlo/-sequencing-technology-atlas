#!/usr/bin/env bash
#SBATCH --job-name=atlas-reference-comparisons
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=03:00:00

# Submit after the bounded reference job stops (afterany), including partial runs.
# No reference artifacts, source packets, frozen runtimes or default deployment change.
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
hostname
printf 'SLURM_JOB_ID=%s\n' "$SLURM_JOB_ID"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_BENCHMARK_RUNTIME:?Select the new frozen runtime}"
: "${ATLAS_COMPARISON_EXPERIMENT:?Select a fresh comparison experiment}"
: "${ATLAS_REFERENCE_WORK:?Select the stopped reference phase work directory}"
: "${ATLAS_REFERENCE_JOB_ID:?Record the reference job identity}"
[[ "$ATLAS_REFERENCE_JOB_ID" =~ ^[0-9]+$ ]]
export ATLAS_REPOSITORY="$ATLAS_BENCHMARK_RUNTIME"
source "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$ATLAS_REPOSITORY"
export OMP_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
experiment="$ATLAS_COMPARISON_EXPERIMENT"
work="$experiment/work"
mkdir "$work"
cd "$ATLAS_REPOSITORY"

preserve_phase() {
    phase="$1"
    name="$2"
    # archive_result deliberately confines input AND output to the project.
    # Keep its output outside the phase, then copy the verified archive to home.
    archive="$experiment/$name.tar.gz"
    "$ATLAS_ENV_DIR/bin/python" scripts/benchmark/archive_result.py \
        --experiment "$phase" --output "$archive" --project-root "$ATLAS_PROJECT_ROOT" \
        --ancestor-map /Users/mihajlostojkovic/Documents/qiu_lab/agents_snRNA "$ATLAS_PROJECT_ROOT" \
        --ancestor-map /scratch/groups/rbaltman/mstojkov/agents_snRNA "$ATLAS_PROJECT_ROOT" || return 1
    durable="$GROUP_HOME/mstojkov/atlas-results"
    mkdir -p "$durable" || return 1
    destination="$durable/$name.tar.gz"
    [[ ! -e "$destination" && ! -L "$destination" ]] || return 1
    (set -o noclobber; cat "$archive" > "$destination") || return 1
    cmp -s "$archive" "$destination" || return 1
    sha256sum "$destination" > "$work/$name.sha256" || return 1
    (set -o noclobber; cat "$work/$name.sha256" > "$destination.sha256") || return 1
    return 0
}

archive_comparisons() {
    result_code=$?
    trap - EXIT
    printf '%s\n' "$result_code" > "$work/job_exit_code-$SLURM_JOB_ID.txt"
    if ! preserve_phase "$work" "protocol-comparisons-$SLURM_JOB_ID"; then
        echo 'Comparison preservation failed; scratch is not a durable backup' >&2
        exit 3
    fi
    exit "$result_code"
}
trap archive_comparisons EXIT

# The first frozen reference launcher selected a node-local output rejected by
# its archive helper. Preserve that stopped phase explicitly without editing it.
preserve_phase "$ATLAS_REFERENCE_WORK" \
    "protocol-reference-$ATLAS_REFERENCE_JOB_ID-followup-$SLURM_JOB_ID"
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.execution_check \
    --workdir "$ATLAS_REPOSITORY" --require-slurm > "$work/execution_check.json"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q \
    --junitxml="$work/software-tests.xml" > "$work/software-tests.log" 2>&1
"$ATLAS_ENV_DIR/bin/python" scripts/benchmark/run_comparison_batch.py \
    --tasks "$ATLAS_REPOSITORY/benchmarks/protocol_graph_v1/comparisons.json" \
    --reference-run "$ATLAS_REFERENCE_WORK/references" \
    --output "$work/comparisons" --concurrency 4 > "$work/comparison-construction.log" 2>&1

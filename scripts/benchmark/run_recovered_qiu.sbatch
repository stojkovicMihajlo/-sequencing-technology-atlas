#!/usr/bin/env bash
#SBATCH --job-name=atlas-benchmark-recovered
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=06:00:00

# One bounded construction phase: retained sources, reviewed references, graphs,
# then reviewed cross-method comparisons. No production atlas publication.
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
hostname
printf 'SLURM_JOB_ID=%s\n' "$SLURM_JOB_ID"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_BENCHMARK_RUNTIME:?Choose a new frozen runtime}"
: "${ATLAS_BENCHMARK_EXPERIMENT:?Choose a fresh experiment directory}"
: "${ATLAS_PRIOR_REFERENCE_WORK:?Choose the stopped pilot work directory}"
: "${ATLAS_PRIOR_REFERENCE_JOB_ID:?Record the pilot job identity}"
[[ "$ATLAS_PRIOR_REFERENCE_JOB_ID" =~ ^[0-9]+$ ]]
export ATLAS_REPOSITORY="$ATLAS_BENCHMARK_RUNTIME"
source "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$ATLAS_REPOSITORY"
export OMP_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
export ATLAS_ANTIWORD_PREFIX="$GROUP_HOME/mstojkov/software/antiword/0.37-18-20261019-02"
export ANTIWORDHOME="$ATLAS_ANTIWORD_PREFIX/share/antiword"
export PATH="$ATLAS_ANTIWORD_PREFIX/bin:$PATH"
experiment="$ATLAS_BENCHMARK_EXPERIMENT"
work="$experiment/work"
mkdir "$work"
cd "$ATLAS_REPOSITORY"

preserve_phase() {
    phase="$1"
    name="$2"
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
}

finish() {
    result_code=$?
    trap - EXIT
    printf '%s\n' "$result_code" > "$work/job_exit_code-$SLURM_JOB_ID.txt"
    if ! preserve_phase "$work" "protocol-reference-recovered-$SLURM_JOB_ID"; then
        echo 'Result preservation failed; scratch is not a durable backup' >&2
        exit 3
    fi
    exit "$result_code"
}
trap finish EXIT

# Submit with afterany: the pilot's historical archival bug is already diagnosed.
# It must have stopped before its immutable artifacts are preserved here.
preserve_phase "$ATLAS_PRIOR_REFERENCE_WORK" \
    "protocol-reference-$ATLAS_PRIOR_REFERENCE_JOB_ID-followup-$SLURM_JOB_ID"
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.execution_check \
    --workdir "$ATLAS_REPOSITORY" --require-slurm > "$work/execution_check.json"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q \
    --junitxml="$work/software-tests.xml" > "$work/software-tests.log" 2>&1

cases="$ATLAS_REPOSITORY/benchmarks/protocol_graph_v1/cases.json"
recipes="$ATLAS_REPOSITORY/benchmarks/protocol_graph_v1/source_recovery_20261020.json"
"$ATLAS_ENV_DIR/bin/python" scripts/benchmark/recheck_sources.py \
    --cases "$cases" --prior-sources "$ATLAS_PRIOR_REFERENCE_WORK/sources" \
    --output "$work/rechecked" > "$work/source-recheck.log" 2>&1
"$ATLAS_ENV_DIR/bin/python" scripts/benchmark/recover_supplements.py \
    --cases "$cases" --prior-sources "$ATLAS_PRIOR_REFERENCE_WORK/sources" \
    --recipes "$recipes" --output "$work/recovered" > "$work/source-recovery.log" 2>&1
"$ATLAS_ENV_DIR/bin/python" scripts/benchmark/combine_sources.py \
    --cases "$cases" --rechecked "$work/rechecked" --recovered "$work/recovered" \
    --output "$work/sources" > "$work/source-combination.log" 2>&1

# Keep per-case failures visible and still produce the remaining comparisons/report.
reference_code=0
"$ATLAS_ENV_DIR/bin/python" scripts/benchmark/run_reference_batch.py \
    --manifest "$work/sources/annotation_manifest.json" --output "$work/references" \
    --concurrency 4 > "$work/reference-construction.log" 2>&1 || reference_code=$?
comparison_code=0
"$ATLAS_ENV_DIR/bin/python" scripts/benchmark/run_comparison_batch.py \
    --tasks "$ATLAS_REPOSITORY/benchmarks/protocol_graph_v1/comparisons.json" \
    --reference-run "$work/references" --output "$work/comparisons" \
    --concurrency 4 > "$work/comparison-construction.log" 2>&1 || comparison_code=$?
"$ATLAS_ENV_DIR/bin/python" scripts/benchmark/report.py \
    --design "$cases" --comparisons "$ATLAS_REPOSITORY/benchmarks/protocol_graph_v1/comparisons.json" \
    --work "$work" --output "$work/report"
printf 'reference_exit_code=%s\ncomparison_exit_code=%s\n' \
    "$reference_code" "$comparison_code" > "$work/stage_exit_codes.txt"
[[ "$reference_code" == 0 && "$comparison_code" == 0 ]]

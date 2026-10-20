#!/usr/bin/env bash
#SBATCH --job-name=atlas-reference-benchmark
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=06:00:00

# Bounded construction job. No self-resubmission, persistent service or credential copy.
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_BENCHMARK_RUNTIME:?Select the frozen Git archive explicitly}"
: "${ATLAS_BENCHMARK_EXPERIMENT:?Select a new experiment directory}"
export ATLAS_REPOSITORY="$ATLAS_BENCHMARK_RUNTIME"
source "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$ATLAS_REPOSITORY"
export OMP_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
stage="${1:-build}"
experiment="$ATLAS_BENCHMARK_EXPERIMENT"
work="$experiment/work"
mkdir -p "$work"
cd "$ATLAS_REPOSITORY"

archive_result() {
    result_code=$?
    trap - EXIT
    printf '%s\n' "$result_code" > "$work/job_exit_code-$SLURM_JOB_ID.txt"
    archive="$L_SCRATCH_JOB/protocol-reference-$SLURM_JOB_ID.tar.gz"
    if "$ATLAS_ENV_DIR/bin/python" scripts/benchmark/archive_result.py \
        --experiment "$work" --output "$archive" --project-root "$ATLAS_PROJECT_ROOT"; then
        durable="$GROUP_HOME/mstojkov/atlas-results"
        mkdir -p "$durable"
        destination="$durable/$(basename "$archive")"
        if [[ -e "$destination" ]]; then
            echo 'Refusing to overwrite a historical durable archive' >&2
            exit 3
        fi
        cp "$archive" "$destination"
        sha256sum "$destination" > "$work/durable_archive-$SLURM_JOB_ID.sha256"
        cp "$work/durable_archive-$SLURM_JOB_ID.sha256" "$destination.sha256"
    else
        echo 'Result preservation failed; scratch is not a durable backup' >&2
        exit 3
    fi
    exit "$result_code"
}
trap archive_result EXIT
hostname
"$ATLAS_ENV_DIR/bin/python" --version
codex --version
codex login status
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.execution_check \
    --workdir "$ATLAS_REPOSITORY" --require-slurm > "$work/execution_check.json"

case "$stage" in
    build|sources)
        "$ATLAS_ENV_DIR/bin/python" -m pytest -q \
            --junitxml="$work/software-tests.xml" > "$work/software-tests.log" 2>&1
        "$ATLAS_ENV_DIR/bin/python" scripts/benchmark/prepare_sources.py \
            --cases "$ATLAS_REPOSITORY/benchmarks/protocol_graph_v1/cases.json" \
            --additional-leads "$ATLAS_REPOSITORY/benchmarks/protocol_graph_v1/additional_source_leads.json" \
            --project-root "$ATLAS_PROJECT_ROOT" --output "$work/sources" \
            --concurrency 4 > "$work/source-preparation.log" 2>&1
        ;;
    references) ;;
    *) echo 'Use build, sources or references' >&2; exit 2 ;;
esac

if [[ "$stage" == build || "$stage" == references ]]; then
    "$ATLAS_ENV_DIR/bin/python" scripts/benchmark/run_reference_batch.py \
        --manifest "$work/sources/annotation_manifest.json" \
        --output "$work/references" --concurrency 4 > "$work/reference-construction.log" 2>&1
fi

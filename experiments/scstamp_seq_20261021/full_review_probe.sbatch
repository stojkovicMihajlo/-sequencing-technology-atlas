#!/usr/bin/env bash
#SBATCH --job-name=atlas-full-review-probe
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=02:00:00
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
hostname
selected_runtime="${ATLAS_FRESH_METHOD_RUNTIME:?Select frozen reviewer runtime}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
export ATLAS_REPOSITORY="$selected_runtime"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export OMP_NUM_THREADS=1
root="${1:?Fresh control experiment root}"
receipt_sha="${2:?Pin the independently audited host receipt}"
obligations_sha="${3:?Pin the original candidate-blind source obligations}"
driver_sha="${4:?Pin the control runner}"
[[ "$root" == "$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/prototype_validation/"* ]] || exit 2
cd "$ATLAS_REPOSITORY"
mkdir -p "$root/audit"
preserve_result() {
    result_code=$?
    trap - EXIT
    printf '%s\n' "$result_code" > "$root/audit/exit-$SLURM_JOB_ID.txt"
    archive="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/prototype_validation/archives/full-review-probe-$SLURM_JOB_ID.tar.gz"
    mkdir -p "$(dirname "$archive")"
    if "$ATLAS_ENV_DIR/bin/python" scripts/benchmark/archive_result.py \
        --experiment "$root" --output "$archive" --project-root "$ATLAS_PROJECT_ROOT"; then
        durable="$GROUP_HOME/mstojkov/atlas-results/$(basename "$archive")"
        mkdir -p "$(dirname "$durable")"
        [[ ! -e "$durable" ]] || exit 3
        cp -n "$archive" "$durable"
        [[ "$(sha256sum "$archive" | cut -d' ' -f1)" == "$(sha256sum "$durable" | cut -d' ' -f1)" ]] || exit 3
        sha256sum "$durable" > "$durable.sha256"
    else
        echo 'Failed to preserve full-review probe' >&2
        exit 3
    fi
    exit "$result_code"
}
trap preserve_result EXIT
[[ "$(sha256sum "$root/driver.py" | cut -d' ' -f1)" == "$driver_sha" ]]
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.execution_check \
    --workdir "$ATLAS_REPOSITORY" --require-slurm > "$root/audit/execution-$SLURM_JOB_ID.json"
parent="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/method_experiments/scstamp-seq/20261021-01/extraction/run_001"
"$ATLAS_ENV_DIR/bin/python" "$root/driver.py" \
    --candidate "$root/inputs/candidate.json" --packet "$parent/source_packet.json" \
    --obligations "$parent/attempts/001_source_obligations/artifact.json" \
    --receipt "$root/host/expected_controls.json" --output "$root/probe" \
    --expected-receipt-sha256 "$receipt_sha" --expected-obligations-sha256 "$obligations_sha" \
    --omission-obligation SO-PLATE-SIZE-FRACTIONS

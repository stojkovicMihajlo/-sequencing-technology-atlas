#!/usr/bin/env bash
#SBATCH --job-name=atlas-rules-check
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=03:00:00
set -euo pipefail
hostname
: "${SLURM_JOB_ID:?Submit through Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
phase="${1:?Supply the fresh experiment directory}"
[[ "$phase" == "$ATLAS_PROJECT_ROOT"/technology_atlas/v2/work/* ]]
cd "$phase"
mkdir checks
export ATLAS_ENV_DIR="$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312"

preserve() {
    mkdir -p "$phase/work/bootstrap"
    printf '%s\n' "$result_code" > "$phase/work/job_exit_code-$SLURM_JOB_ID.txt"
    cp -R "$phase/checks" "$phase/work/software_checks"
    for name in runtime.tar.gz inputs.sha256 run.py run.sbatch config.json container_embeddings.py; do
        cp "$phase/$name" "$phase/work/bootstrap/$name"
    done
    [[ ! -f "$phase/driver.log" ]] || cp "$phase/driver.log" "$phase/work/driver.log"
    archive="$phase/genomics-rules-$SLURM_JOB_ID.tar.gz"
    if [[ -f "$phase/candidate/scripts/benchmark/archive_result.py" ]]; then
        PYTHONPATH="$phase/candidate/src" "$ATLAS_ENV_DIR/bin/python" \
            "$phase/candidate/scripts/benchmark/archive_result.py" \
            --experiment "$phase/work" --output "$archive" --project-root "$ATLAS_PROJECT_ROOT" \
            --ancestor-map /Users/mihajlostojkovic/Documents/qiu_lab/agents_snRNA "$ATLAS_PROJECT_ROOT" \
            --ancestor-map /scratch/groups/rbaltman/mstojkov/agents_snRNA "$ATLAS_PROJECT_ROOT" \
            > "$phase/archive_receipt.json"
    else
        [[ ! -e "$phase/work/selection" ]]
        tar -czf "$archive" -C "$phase" work
        printf '%s\n' '{"status":"bootstrap_failure_only"}' > "$phase/archive_receipt.json"
    fi
    destination="$GROUP_HOME/mstojkov/atlas-results/genomics-rules-$SLURM_JOB_ID.tar.gz"
    mkdir -p "$(dirname "$destination")"
    [[ ! -e "$destination" && ! -L "$destination" ]]
    (set -o noclobber; cat "$archive" > "$destination")
    cmp -s "$archive" "$destination"
    sha256sum "$destination" > "$phase/durable_archive.sha256"
    (set -o noclobber; cat "$phase/durable_archive.sha256" > "$destination.sha256")
    (set -o noclobber; cat "$phase/archive_receipt.json" > "$destination.receipt.json")
}
finish() {
    result_code=$?
    trap - EXIT
    if ! (set -e; preserve); then
        echo 'Archive preservation failed; scratch is not a durable backup' >&2
        exit 3
    fi
    exit "$result_code"
}
trap finish EXIT

sha256sum -c inputs.sha256
mkdir candidate
tar -xzf runtime.tar.gz -C candidate
export ATLAS_REPOSITORY="$phase/candidate"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$ATLAS_REPOSITORY"
export OMP_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
export HF_HUB_OFFLINE=1
cd "$ATLAS_REPOSITORY"
"$ATLAS_ENV_DIR/bin/python" --version
codex --version
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.execution_check \
    --workdir "$ATLAS_REPOSITORY" --require-slurm > "$phase/checks/execution_check.json"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q \
    tests/test_comparison_profiles.py tests/test_comparison_profile_tools.py \
    tests/test_workflow_unit_selection.py tests/test_workflow_unit_selection_live.py \
    tests/test_concept_admission.py tests/test_operation_registry_live.py \
    tests/test_frozen_matching.py tests/test_shared_registry_regressions.py \
    --junitxml="$phase/checks/software-tests.xml" > "$phase/checks/software-tests.log" 2>&1
"$ATLAS_ENV_DIR/bin/python" "$phase/run.py" "$phase/config.json" "$phase/work" \
    > "$phase/driver.log" 2>&1
(cd "$phase"; sha256sum -c inputs.sha256)

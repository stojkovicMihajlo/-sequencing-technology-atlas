#!/usr/bin/env bash
#SBATCH --job-name=atlas-ordered-3
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=04:00:00
set -euo pipefail
hostname
: "${SLURM_JOB_ID:?Submit through Slurm}"
printf 'SLURM_JOB_ID=%s\n' "$SLURM_JOB_ID"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
phase="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/qiu_experiments_20261021/ordered_extraction_01"
cd "$phase"
sha256sum -c inputs.sha256
mkdir candidate checks
tar -xzf runtime.tar.gz -C candidate
export ATLAS_REPOSITORY="$phase/candidate"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export OMP_NUM_THREADS=1
export TMPDIR="${L_SCRATCH_JOB:?}/atlas-tmp"
mkdir -p "$TMPDIR"

preserve() {
    result_code=$?
    trap - EXIT
    mkdir -p "$phase/work"
    printf '%s\n' "$result_code" > "$phase/work/job_exit_code-$SLURM_JOB_ID.txt"
    cp -R "$phase/checks" "$phase/work/software_checks"
    for name in runtime.tar.gz runtime_commit.txt run.sbatch run.py selection.json inputs.sha256; do
        cp "$phase/$name" "$phase/work/$name"
    done
    [[ ! -f "$phase/driver.log" ]] || cp "$phase/driver.log" "$phase/work/driver.log"
    archive="$phase/ordered-extraction-$SLURM_JOB_ID.tar.gz"
    "$ATLAS_ENV_DIR/bin/python" "$ATLAS_REPOSITORY/scripts/benchmark/archive_result.py" \
        --experiment "$phase/work" --output "$archive" --project-root "$ATLAS_PROJECT_ROOT" \
        --ancestor-map /Users/mihajlostojkovic/Documents/qiu_lab/agents_snRNA "$ATLAS_PROJECT_ROOT" \
        --ancestor-map /scratch/groups/rbaltman/mstojkov/agents_snRNA "$ATLAS_PROJECT_ROOT" \
        > "$phase/archive_receipt.json"
    destination="$GROUP_HOME/mstojkov/atlas-results/ordered-extraction-$SLURM_JOB_ID.tar.gz"
    mkdir -p "$(dirname "$destination")"
    [[ ! -e "$destination" ]]
    (set -o noclobber; cat "$archive" > "$destination")
    cmp -s "$archive" "$destination"
    sha256sum "$destination" > "$phase/durable_archive.sha256"
    cp "$phase/durable_archive.sha256" "$destination.sha256"
    cp "$phase/archive_receipt.json" "$destination.receipt.json"
    exit "$result_code"
}
trap preserve EXIT
cd "$ATLAS_REPOSITORY"
"$ATLAS_ENV_DIR/bin/python" --version
codex --version
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.execution_check \
    --workdir "$ATLAS_REPOSITORY" --require-slurm > "$phase/checks/execution_check.json"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q \
    tests/test_workflow_extraction_contract.py tests/test_workflow_extraction_runtime.py \
    tests/test_workflow_completion_runtime.py tests/test_workflow_field_contract.py \
    tests/test_workflow_field_review_runtime.py --junitxml="$phase/checks/tests.xml" \
    > "$phase/checks/tests.log" 2>&1
"$ATLAS_ENV_DIR/bin/python" "$phase/run.py" "$phase" > "$phase/driver.log" 2>&1
cd "$phase"
sha256sum -c inputs.sha256

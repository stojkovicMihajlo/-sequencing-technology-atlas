#!/usr/bin/env bash
#SBATCH --job-name=atlas-ten-overview
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=06:00:00
set -euo pipefail
hostname
: "${SLURM_JOB_ID:?Submit through Slurm}"
printf 'SLURM_JOB_ID=%s\n' "$SLURM_JOB_ID"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
phase="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/qiu_experiments_20261020/ten_method_overview_01"
cd "$phase"
mkdir checks
export ATLAS_ENV_DIR="$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312"

preserve() {
    mkdir -p "$phase/work" || return 1
    printf '%s\n' "$result_code" > "$phase/work/job_exit_code-$SLURM_JOB_ID.txt" || return 1
    cp -R "$phase/checks" "$phase/work/software_checks" || return 1
    if [[ -f "$phase/driver.log" ]]; then cp "$phase/driver.log" "$phase/work/driver.log" || return 1; fi
    for name in runtime.tar.gz runtime.sha256 run.sbatch run.py selection.json container_embeddings.py inputs.sha256; do
        if [[ ! -e "$phase/work/$name" ]]; then cp "$phase/$name" "$phase/work/$name" || return 1; fi
    done
    archive="$phase/ten-method-overview-$SLURM_JOB_ID.tar.gz"
    if [[ -f "$phase/candidate/scripts/benchmark/archive_result.py" ]]; then
        PYTHONPATH="$phase/candidate/src" "$ATLAS_ENV_DIR/bin/python" \
            "$phase/candidate/scripts/benchmark/archive_result.py" \
            --experiment "$phase/work" --output "$archive" --project-root "$ATLAS_PROJECT_ROOT" \
            --ancestor-map /Users/mihajlostojkovic/Documents/qiu_lab/agents_snRNA "$ATLAS_PROJECT_ROOT" \
            --ancestor-map /scratch/groups/rbaltman/mstojkov/agents_snRNA "$ATLAS_PROJECT_ROOT" \
            > "$phase/archive_receipt.json" || return 1
    else
        # Only bootstrap inputs exist in this branch: no model or batch ran.
        [[ ! -e "$phase/work/batch" ]] || return 1
        tar -czf "$archive" -C "$phase" work || return 1
        printf '%s\n' '{"status":"bootstrap_failure_only_no_source_closure_claim"}' \
            > "$phase/archive_receipt.json" || return 1
    fi
    destination="$GROUP_HOME/mstojkov/atlas-results/ten-method-overview-$SLURM_JOB_ID.tar.gz"
    mkdir -p "$(dirname "$destination")" || return 1
    [[ ! -e "$destination" && ! -L "$destination" ]] || return 1
    (set -o noclobber; cat "$archive" > "$destination") || return 1
    cmp -s "$archive" "$destination" || return 1
    sha256sum "$destination" > "$phase/durable_archive.sha256" || return 1
    (set -o noclobber; cat "$phase/durable_archive.sha256" > "$destination.sha256") || return 1
    (set -o noclobber; cat "$phase/archive_receipt.json" > "$destination.receipt.json") || return 1
}
finish() {
    result_code=$?
    trap - EXIT
    if ! preserve; then
        echo 'Preservation failed; do not treat scratch as a durable backup' >&2
        exit 3
    fi
    exit "$result_code"
}
trap finish EXIT

sha256sum -c inputs.sha256
mkdir candidate
tar -xzf runtime.tar.gz -C candidate
export ATLAS_REPOSITORY="$phase/candidate"
source "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$ATLAS_REPOSITORY"
export OMP_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
export HF_HUB_OFFLINE=1
export ATLAS_ANTIWORD_PREFIX="$GROUP_HOME/mstojkov/software/antiword/0.37-18-20261019-02"
export ANTIWORDHOME="$ATLAS_ANTIWORD_PREFIX/share/antiword"
export PATH="$ATLAS_ANTIWORD_PREFIX/bin:$PATH"
cd "$ATLAS_REPOSITORY"
"$ATLAS_ENV_DIR/bin/python" --version
codex --version
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.execution_check \
    --workdir "$ATLAS_REPOSITORY" --require-slurm > "$phase/checks/execution_check.json"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q --junitxml="$phase/checks/software-tests.xml" \
    > "$phase/checks/software-tests.log" 2>&1
# The driver registers neutral-scope packets and freezes the exact10 plan before
# any model call, then runs the existing extraction/admission paths once.
"$ATLAS_ENV_DIR/bin/python" "$phase/run.py" "$phase" > "$phase/driver.log" 2>&1
(cd "$phase"; sha256sum -c inputs.sha256)

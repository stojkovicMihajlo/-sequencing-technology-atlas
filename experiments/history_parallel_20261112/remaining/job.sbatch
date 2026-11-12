#!/usr/bin/env bash
#SBATCH --job-name=atlas-history-remaining
#SBATCH --partition=normal,xiaojie
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=06:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
export ATLAS_HISTORY_STAGE_NAME=history_remaining_20261112
export ATLAS_HISTORY_BASE_STAGE=history_parallel_20261112
export ATLAS_HISTORY_BASE_RUNTIME=runtime_52073185
export ATLAS_HISTORY_ALL_READY=1
export ATLAS_HISTORY_EXCLUDE_SELECTION="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/history_parallel_20261112/preflight_52073185/selection.json"
export ATLAS_HISTORY_BACKUP_ROOT="$GROUP_HOME/mstojkov/atlas-results/history_remaining_20261112"
# batch.sbatch is the pinned common bootstrap shipped with this job, not a live
# repository checkout. It performs tests/readiness before any native model call.
source "$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/history_remaining_20261112/batch.sbatch"

#!/usr/bin/env bash
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Run inside Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
run_root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
export ATLAS_REPOSITORY="$run_root/runtime_07"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$SCRATCH/atlas-codex-runtime-20261031/sqlite"
export TMPDIR="${L_SCRATCH_JOB:?}/graph-probe-tmp"
mkdir -p "$TMPDIR"
"$ATLAS_ENV_DIR/bin/python" "$run_root/staging/graph_tool_probe.py" "$run_root"

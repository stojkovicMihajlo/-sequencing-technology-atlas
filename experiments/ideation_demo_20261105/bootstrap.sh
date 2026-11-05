#!/usr/bin/env bash
# Submitted to Slurm once; unpack and verify one frozen runtime in its allocation.
set -euo pipefail
: "${SLURM_JOB_ID:?Run inside a Slurm allocation}"
: "${ATLAS_IDEATION_ROOT:?Set the experiment directory}"
: "${ATLAS_IDEATION_RUNTIME:?Set the frozen runtime name}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
runtime="$ATLAS_IDEATION_ROOT/$ATLAS_IDEATION_RUNTIME"
mkdir "$runtime"
tar -xzf "$ATLAS_IDEATION_ROOT/${ATLAS_IDEATION_RUNTIME}.tar.gz" -C "$runtime"
exec bash "$runtime/experiments/ideation_demo_20261105/launch.sbatch"

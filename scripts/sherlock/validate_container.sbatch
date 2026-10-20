#!/bin/bash
#SBATCH --job-name=atlas-container-check
#SBATCH --partition=service
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
: "${1:?Pass the built SIF image}"
: "${2:?Pass the frozen harness runtime directory}"
: "${3:?Pass a new result directory under the project scratch tree}"
IMAGE=$(realpath "$1")
RUNTIME=$(realpath "$2")
RESULT="$3"
PROJECT="$GROUP_SCRATCH/mstojkov/agents_snRNA"
case "$RUNTIME" in "$PROJECT/technology_atlas/v2/work/"*) ;; *) exit 2 ;; esac
case "$RESULT" in "$PROJECT/technology_atlas/v2/work/"*) ;; *) exit 2 ;; esac
test ! -e "$RESULT"
mkdir -p "$RESULT" "$L_SCRATCH_JOB/atlas-test-tmp"
sha256sum "$IMAGE" "$RUNTIME/release.json" > "$RESULT/inputs.sha256"
apptainer --version > "$RESULT/apptainer-version.txt"
# Preserve the real project paths for source/provenance references. The code and
# papers are read-only; only this result directory and node-local tmp are writable.
# No login directory, SSH keys, host home or credential file is mounted.
apptainer exec --cleanenv --contain --no-home --no-mount hostfs,cwd,bind-paths \
    --bind "$PROJECT:$PROJECT:ro" \
    --bind "$RESULT:$RESULT:rw" \
    --bind "$L_SCRATCH_JOB/atlas-test-tmp:/atlas-tmp:rw" \
    --bind /etc/agents:/etc/agents:ro \
    --env "PYTHONPATH=$RUNTIME/src:$RUNTIME" \
    --env "ATLAS_PROJECT_ROOT=$PROJECT" \
    --env "SLURM_JOB_ID=$SLURM_JOB_ID" \
    --env TMPDIR=/atlas-tmp \
    --cwd "$RUNTIME" \
    "$IMAGE" python -m pytest -q -p no:cacheprovider --junitxml="$RESULT/tests.xml"
echo 'Container regression checks completed; model execution is a separate gate.'

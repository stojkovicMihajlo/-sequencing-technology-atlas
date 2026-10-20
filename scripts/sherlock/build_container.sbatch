#!/bin/bash
#SBATCH --job-name=atlas-container-build
#SBATCH --partition=service
#SBATCH --time=00:45:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
: "${1:?Pass the directory with atlas.def and requirements.lock}"
: "${2:?Pass a new output directory under your GROUP_HOME software area}"
RECIPE_DIR=$(realpath "$1")
INSTALL_DIR="$2"
case "$INSTALL_DIR" in
    "$GROUP_HOME/mstojkov/software/atlas/"*) ;;
    *) echo 'Image installation must use the personal GROUP_HOME software area' >&2; exit 2 ;;
esac
test ! -e "$INSTALL_DIR"
test -f "$RECIPE_DIR/atlas.def"
test -f "$RECIPE_DIR/requirements.lock"
command -v apptainer
apptainer --version
hostname
export APPTAINER_CACHEDIR="$SCRATCH/atlas-package-cache/apptainer"
export APPTAINER_TMPDIR="$L_SCRATCH_JOB/atlas-container-build"
mkdir -p "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"
cd "$RECIPE_DIR"
# Standard unprivileged build only. Never enable privilege, alter namespaces,
# start Docker/BuildKit daemons, or work around a denied container build.
apptainer build --mksquashfs-args "-processors $SLURM_CPUS_PER_TASK" \
    "$APPTAINER_TMPDIR/atlas.sif" atlas.def
mkdir -p "$INSTALL_DIR"
cp "$APPTAINER_TMPDIR/atlas.sif" "$INSTALL_DIR/atlas.sif"
sha256sum atlas.def requirements.lock "$INSTALL_DIR/atlas.sif" > "$INSTALL_DIR/build.sha256"
apptainer inspect --json "$INSTALL_DIR/atlas.sif" > "$INSTALL_DIR/image-metadata.json"
printf '%s\n' "$SLURM_JOB_ID" > "$INSTALL_DIR/slurm-job-id.txt"
echo "Built and tested: $INSTALL_DIR/atlas.sif"

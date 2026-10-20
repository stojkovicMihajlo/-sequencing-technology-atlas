#!/bin/bash
#SBATCH --job-name=atlas-run
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --time=12:00:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
set -euo pipefail
: "${SLURM_JOB_ID:?Submit with sbatch; do not run on a login node}"
source "$GROUP_SCRATCH/mstojkov/agents_snRNA/sequencing-technology-atlas/scripts/sherlock/environment.sh"
if (( $# < 2 )); then
    echo 'Usage: sbatch run.sbatch genomics_discovery.curation.MODULE COMMAND [ARGS...]' >&2
    exit 2
fi
case "$1" in
    genomics_discovery.curation.*) ;;
    *) echo 'Expected a genomics_discovery.curation module' >&2; exit 2 ;;
esac
[[ -x "$ATLAS_ENV_DIR/bin/python" ]] || { echo 'Run setup_and_check.sbatch first' >&2; exit 2; }
cd "$ATLAS_REPOSITORY"
hostname
"$ATLAS_ENV_DIR/bin/python" --version
codex --version
case "$1:${2:-}" in
    genomics_discovery.curation.extraction_batch:run|genomics_discovery.curation.deployment:run|genomics_discovery.curation.workflow_extraction:run|genomics_discovery.curation.workflow_matching:run|genomics_discovery.curation.source_acquisition:*)
        [[ -f "$ATLAS_REPOSITORY/src/genomics_discovery/curation/execution_check.py" ]] || {
            echo 'Select a validated runtime using ATLAS_REPOSITORY before launching native jobs' >&2
            exit 2
        }
        "$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.execution_check \
            --workdir "$ATLAS_REPOSITORY" --require-slurm
        ;;
esac
# No sandbox overrides, credential copying, or automatic resubmission.
# Historical Mac plans are archives, not directly resumable Linux plans.
exec "$ATLAS_ENV_DIR/bin/python" -m "$@"

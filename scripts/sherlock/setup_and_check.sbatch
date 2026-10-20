#!/bin/bash
#SBATCH --job-name=atlas-linux-check
#SBATCH --partition=service
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
set -euo pipefail
: "${SLURM_JOB_ID:?Submit with sbatch; do not run on a login node}"
source "$GROUP_SCRATCH/mstojkov/agents_snRNA/sequencing-technology-atlas/scripts/sherlock/environment.sh"
cd "$ATLAS_REPOSITORY"
report="$ATLAS_PROJECT_ROOT/migration/sherlock_20261018/linux-check-$SLURM_JOB_ID"
mkdir -p "$report"
hostname
python3 --version
uv --version
codex --version
uv sync --locked --extra dev --python "$(command -v python3)" --no-python-downloads
"$ATLAS_ENV_DIR/bin/python" -m pip --version 2>/dev/null || true
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.deployment --help
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.extraction_batch --help
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.technology_graph --help
"$ATLAS_ENV_DIR/bin/python" -m pytest -q -p no:cacheprovider --junitxml="$report/tests.xml"

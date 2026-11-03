#!/usr/bin/env bash
#SBATCH --job-name=illumina-audit
#SBATCH --partition=xiaojie,normal
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=00:30:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
run_root="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/illumina_extraction_20261102"
audit_root="$run_root/audit_20261103_01"
export ATLAS_REPOSITORY="$run_root/runtime_03"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export OMP_NUM_THREADS=1
cd "$ATLAS_REPOSITORY"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -q tests/illumina \
  --junitxml="$audit_root/baseline_tests_${SLURM_JOB_ID}.xml"
"$ATLAS_ENV_DIR/bin/python" "$audit_root/audit.py" "$run_root" --output "$audit_root/report"
sha256sum "$GROUP_HOME/mstojkov/atlas-results/illumina_extraction_20261102/job_50822494.tar.gz" \
  > "$audit_root/backup_rehash.sha256"
backup_root="$GROUP_HOME/mstojkov/atlas-results/illumina_extraction_20261102"
tar -czf "$backup_root/audit_${SLURM_JOB_ID}.tar.gz" -C "$run_root" audit_20261103_01

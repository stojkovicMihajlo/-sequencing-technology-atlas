#!/usr/bin/env bash
#SBATCH --job-name=atlas-lineage-review
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=01:00:00
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Submit through Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
pilot_root="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/citation_lineage_20261102"
export ATLAS_REPOSITORY="$pilot_root/runtime_02"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export OMP_NUM_THREADS=1
export CODEX_SQLITE_HOME="$SCRATCH/atlas-codex-runtime-20261031/sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/lineage-cache"
cd "$ATLAS_REPOSITORY"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib,json,os
from pathlib import Path
root=Path(os.environ['ATLAS_REPOSITORY'])
receipt=json.loads((root.parent/'runtime_02_manifest.json').read_text())
for name,expected in receipt['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(json.dumps(preflight_pinned_shell_environment()))
PY
"$ATLAS_ENV_DIR/bin/python" -m pytest -q experiments/citation_lineage_pilot/test_contract.py
"$ATLAS_ENV_DIR/bin/python" experiments/citation_lineage_pilot/run.py review-existing \
  --previous "$pilot_root/atac_01" --output "$pilot_root/atac_review_02"

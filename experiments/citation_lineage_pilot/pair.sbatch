#!/usr/bin/env bash
#SBATCH --job-name=atlas-method-pair
#SBATCH --partition=xiaojie,normal
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=01:30:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
pair_root="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/method_pair_20261102"
export ATLAS_REPOSITORY="$pair_root/runtime_01"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export OMP_NUM_THREADS=1
export CODEX_SQLITE_HOME="$SCRATCH/atlas-codex-runtime-20261031/sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/pair-cache"
cd "$ATLAS_REPOSITORY"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib,json,os
from pathlib import Path
root=Path(os.environ['ATLAS_REPOSITORY'])
receipt=json.loads((root.parent/'runtime_01_manifest.json').read_text())
for name,expected in receipt['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(json.dumps(preflight_pinned_shell_environment()))
PY
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -q \
  experiments/citation_lineage_pilot/test_pair.py experiments/citation_lineage_pilot/test_contract.py \
  --junitxml="$pair_root/software_checks.xml"
"$ATLAS_ENV_DIR/bin/python" experiments/citation_lineage_pilot/pair_run.py run \
  --plan "$pair_root/plan.json" --output "$pair_root/atac_chipmentation_01"
tar -czf "$pair_root/atac_chipmentation_01.tar.gz" -C "$pair_root" atac_chipmentation_01
sha256sum "$pair_root/atac_chipmentation_01.tar.gz"

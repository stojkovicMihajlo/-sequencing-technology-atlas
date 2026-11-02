#!/usr/bin/env bash
#SBATCH --job-name=atlas-atac-lineage
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=01:30:00
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Submit through Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
pilot_root="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/citation_lineage_20261102"
# Same immutable code and prompts as the first case. This selects a different
# method, without retrying or changing the provider-blocked ClickSeq request.
export ATLAS_REPOSITORY="$pilot_root/runtime_01"
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
receipt=json.loads((root.parent/'runtime_01_manifest.json').read_text())
for name,expected in receipt['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(json.dumps(preflight_pinned_shell_environment()))
PY
source_root="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/illumina_full_20261031"
"$ATLAS_ENV_DIR/bin/python" experiments/citation_lineage_pilot/run.py run \
  --batch "$source_root/scope_repair_pilot_01" --record atac-seq --paper paper:24097267 \
  --article "$source_root/sources_05/imports/16/161b7c162f0d176cf22b852d4eeeaf9888f5dde50f00afd01975050bf94ff41c.xml" \
  --inventory "$source_root/sources_04/inventory.json" \
  --output "$pilot_root/atac_01"

#!/usr/bin/env bash
#SBATCH --job-name=illumina-papers
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=04:00:00
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Submit through Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
mkdir -p "$root/runtime_02"
tar -xzf "$root/staging/runtime_02.tar.gz" -C "$root/runtime_02"
export ATLAS_REPOSITORY="$root/runtime_02"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
cd "$ATLAS_REPOSITORY"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q tests/test_illumina_sources.py > "$root/logs/source-tests-$SLURM_JOB_ID.txt"
set +e
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.illumina_sources recover-fulltext \
    "$root/sources_02/sources.json" --output "$root/sources_03" --fulltext-workers 3
recovery_exit=$?
set -e
printf '%s\n' "$recovery_exit" > "$root/logs/paper-recovery-exit.txt"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import json, os
from pathlib import Path
root=Path(os.environ['ATLAS_PROJECT_ROOT'])/'technology_atlas/v2/work/illumina_full_20261031'
state=json.loads((root/'sources_03/sources.json').read_text())
print(json.dumps({'status':state['status'],'statistics':state.get('statistics')},indent=2))
missing={k:v for k,v in state['fulltexts'].items() if v['status']!='fulltext_acquired'}
(root/'paper_recovery_gaps.json').write_text(json.dumps(missing,indent=2)+'\n')
PY

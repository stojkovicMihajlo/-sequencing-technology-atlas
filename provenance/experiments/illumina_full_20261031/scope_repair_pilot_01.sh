#!/usr/bin/env bash
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Run inside Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
run_root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
printf '%s  %s\n' 85e7526a1388d3b199a74ed6bb61c9819e592c84d6cc164d1bcd7f2af74066d8 "$run_root/staging/runtime_08.tar.gz" | sha256sum -c -
mkdir "$run_root/runtime_08"
tar -xzf "$run_root/staging/runtime_08.tar.gz" -C "$run_root/runtime_08"
export ATLAS_REPOSITORY="$run_root/runtime_08"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export OMP_NUM_THREADS=1
export CODEX_SQLITE_HOME="$SCRATCH/atlas-codex-runtime-20261031/sqlite"
export TMPDIR="${L_SCRATCH_JOB:?}/scope-repair-pilot-tmp"
mkdir -p "$TMPDIR"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib,json,os
from pathlib import Path
root=Path(os.environ['ATLAS_REPOSITORY'])
receipt=json.loads((root.parent/'staging/runtime_08.json').read_bytes())
assert all(hashlib.sha256((root/p).read_bytes()).hexdigest()==h for p,h in receipt['files'].items())
PY
"$ATLAS_ENV_DIR/bin/python" -m pytest -q "$ATLAS_REPOSITORY/tests" > "$run_root/logs/runtime08-tests-$SLURM_JOB_ID.txt"
# Named general defects: unshown-scope inflation (Aba/ATAC), independent-branch
# collapse (G&T). These are new attempts, not edits to earlier accepted artifacts.
if "$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.illumina_batch run \
 "$run_root/sources_05/sources.json" --output "$run_root/scope_repair_pilot_01" \
 --record aba-seq --record atac-seq --record g-t-seq --workers 3 --max-revisions 1; then
 batch_exit=0
else
 batch_exit=$?
fi
printf '%s\n' "$batch_exit" > "$run_root/logs/scope-repair-pilot-native-exit-$SLURM_JOB_ID.txt"
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.illumina_graph build \
 "$run_root/scope_repair_pilot_01" "$run_root/sources_05/inventory.json" --output "$run_root/scope_repair_pilot_graph_01.json"

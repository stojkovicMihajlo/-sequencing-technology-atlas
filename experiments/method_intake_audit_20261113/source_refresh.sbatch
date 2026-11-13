#!/usr/bin/env bash
#SBATCH --job-name=atlas-source-refresh
#SBATCH --partition=normal,xiaojie,service
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --time=02:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_SOURCE_REFRESH_STAGE:?Exact reviewed source-only release directory required}"
stage="$ATLAS_SOURCE_REFRESH_STAGE"
export ATLAS_REPOSITORY="$stage/runtime"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$stage:$ATLAS_REPOSITORY/src"
export PYTHONDONTWRITEBYTECODE=1
cd "$stage"
results="$stage/results_${SLURM_JOB_ID}"
mkdir "$results"
preserve_results() {
    code=$?
    trap - EXIT
    destination="$GROUP_HOME/mstojkov/atlas-results/method_intake_audit_20261113/source_refresh_${SLURM_JOB_ID}"
    "$ATLAS_ENV_DIR/bin/python" preserve_bytes.py --source "$results" --destination "$destination" --manifest "${destination}.PRESERVATION_MANIFEST.json" || code=1
    exit "$code"
}
trap preserve_results EXIT
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
from pathlib import Path
import hashlib,json,os
stage=Path(os.environ['ATLAS_SOURCE_REFRESH_STAGE'])
for name,pin in json.loads((stage/'PAYLOAD_MANIFEST.json').read_text())['files'].items():
    assert hashlib.sha256((stage/name).read_bytes()).hexdigest()==pin,name
PY
"$ATLAS_ENV_DIR/bin/python" -m unittest discover -s "$stage" -p 'test_refresh_discovery_pool.py' -v > "$results/software.out" 2>&1
"$ATLAS_ENV_DIR/bin/python" refresh_discovery_pool.py --inputs source_refresh_inputs.json --plan source_refresh_plan.json --phase probe --output "$results/probe" > "$results/probe.out"
"$ATLAS_ENV_DIR/bin/python" refresh_discovery_pool.py --inputs source_refresh_inputs.json --plan source_refresh_plan.json --phase expand --prior-overlay "$results/probe/OVERLAY.json" --output "$results/expand" > "$results/expand.out"

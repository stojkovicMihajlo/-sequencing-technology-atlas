#!/usr/bin/env bash
#SBATCH --job-name=abstract-intake7
#SBATCH --partition=normal,xiaojie,service
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:50:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_INTAKE_STAGE:?Set the exact fresh reviewed release directory}"
stage="$ATLAS_INTAKE_STAGE"
export ATLAS_REPOSITORY="$stage/runtime"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
[[ "$CODEX_HOME" == "$GROUP_HOME/mstojkov/private/codex-atlas-runs" ]]
[[ "$ATLAS_CODEX_ACCOUNT_LABEL" == atlas-agent-runs ]]
[[ -z "${OPENAI_API_KEY+x}" && -z "${CODEX_API_KEY+x}" ]]
export PYTHONPATH="$stage:$ATLAS_REPOSITORY/src"
export PYTHONDONTWRITEBYTECODE=1
cd "$stage"
results="$stage/results_${SLURM_JOB_ID}"
mkdir "$results"
durable_copy() {
    code=$?
    trap - EXIT
    destination="$GROUP_HOME/mstojkov/atlas-results/method_intake_audit_20261113/run_${SLURM_JOB_ID}"
    mkdir -p "$(dirname "$destination")"
    "$ATLAS_ENV_DIR/bin/python" "$stage/preserve_bytes.py" \
      --source "$results" --destination "$destination" \
      --manifest "${destination}.PRESERVATION_MANIFEST.json" || code=1
    exit "$code"
}
trap durable_copy EXIT
export ATLAS_INTAKE_RESULTS="$results"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib,json,os
from pathlib import Path
stage=Path(os.environ['ATLAS_INTAKE_STAGE'])
for relative,expected in json.loads((stage/'PAYLOAD_MANIFEST.json').read_text())['files'].items():
    actual=hashlib.sha256((stage/relative).read_bytes()).hexdigest()
    if actual != expected:raise ValueError('Frozen payload changed: '+relative)
from genomics_discovery.execution.codex_worker import CodexWorker
import inspect
assert 'response_schema_path' in inspect.signature(CodexWorker.run).parameters
PY
"$ATLAS_ENV_DIR/bin/python" -m unittest discover -s "$stage" -p 'test_live_pilot.py' -v > "$results/software.out" 2>&1
"$ATLAS_CODEX_BIN_DIR/codex" --version > "$results/codex_version.txt"
"$ATLAS_CODEX_BIN_DIR/codex" exec --help > "$results/codex_exec_help.txt"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import os
from pathlib import Path
out=Path(os.environ['ATLAS_INTAKE_RESULTS'])
assert (out/'codex_version.txt').read_text().strip().endswith('0.163.0')
assert '--output-schema' in (out/'codex_exec_help.txt').read_text()
PY
# First obtain/verify all seven authoritative abstracts. No fulltext requests.
"$ATLAS_ENV_DIR/bin/python" live_pilot.py acquire --targets live_targets.json --output "$results/metadata" > "$results/acquisition.out"
# Actual minimal structured-final probe; stop before science if unsupported.
"$ATLAS_ENV_DIR/bin/python" live_pilot.py probe --output "$results/schema_probe" > "$results/probe.out"
# Two independent blinded screens; no token quota is imposed.
"$ATLAS_ENV_DIR/bin/python" live_pilot.py screen --metadata "$results/metadata/METADATA_MANIFEST.json" --output "$results/screens" > "$results/screen.out"

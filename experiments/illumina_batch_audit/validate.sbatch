#!/usr/bin/env bash
#SBATCH --job-name=illumina-audit-validate
#SBATCH --partition=xiaojie,normal
#SBATCH --cpus-per-task=2
#SBATCH --mem=12G
#SBATCH --time=01:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
run_root="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/illumina_extraction_20261102"
audit_root="$run_root/audit_20261103_01"
: "${ATLAS_AUDIT_RUNTIME_NAME:?Set the new frozen audit runtime explicitly}"
export ATLAS_REPOSITORY="$run_root/$ATLAS_AUDIT_RUNTIME_NAME"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export OMP_NUM_THREADS=1
export CODEX_SQLITE_HOME="$SCRATCH/atlas-codex-secondary-20261102/sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-cache"
cd "$ATLAS_REPOSITORY"
backup() {
    code=$?
    trap - EXIT
    tar -czf "$GROUP_HOME/mstojkov/atlas-results/illumina_extraction_20261102/audit_validation_${SLURM_JOB_ID}.tar.gz" \
      -C "$run_root" --exclude='*.tar.gz' audit_20261103_01 "$ATLAS_AUDIT_RUNTIME_NAME" "${ATLAS_AUDIT_RUNTIME_NAME}_manifest.json"
    exit "$code"
}
trap backup EXIT
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib,json,os
from pathlib import Path
root=Path(os.environ['ATLAS_REPOSITORY'])
manifest=json.loads(root.with_name(root.name+'_manifest.json').read_text())
for name,expected in manifest['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
assert os.environ['ATLAS_CODEX_ACCOUNT_LABEL']=='atlas-agent-runs'
assert not any(os.environ.get(k) for k in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN','OPENAI_IDENTITY_TOKEN_FILE'))
PY
"$ATLAS_CODEX_BIN_DIR/codex" --version
"$ATLAS_CODEX_BIN_DIR/codex" login status
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p pytest_asyncio.plugin -q \
  tests/illumina tests/execution/test_curation_codex_worker.py tests/execution/test_codex_terminal_failure_diagnostics.py \
  --junitxml="$audit_root/validation_tests_${SLURM_JOB_ID}.xml"
"$ATLAS_ENV_DIR/bin/python" "$ATLAS_REPOSITORY/experiments/illumina_batch_audit/audit.py" \
  "$run_root" --output "$audit_root/report_${SLURM_JOB_ID}"
"$ATLAS_ENV_DIR/bin/python" "$ATLAS_REPOSITORY/experiments/illumina_batch_audit/validate.py" \
  "$audit_root/validation_plan.json" --output "$audit_root/forward_validation_${SLURM_JOB_ID}"

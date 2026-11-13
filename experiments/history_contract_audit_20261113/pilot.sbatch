#!/usr/bin/env bash
# Prepared only. Root reviews/stages the payload and submits; this file does not self-submit.
#SBATCH --job-name=atlas-focus2-review
#SBATCH --partition=xiaojie
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --output=focused-review-%j.out
set -euo pipefail
hostname
printf '%s\n' "SLURM_JOB_ID=${SLURM_JOB_ID-}"
cat /etc/agents/AGENTS.md
cat /etc/agents/storage.md /etc/agents/slurm.md /etc/agents/software.md /etc/agents/policy.md
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
source /home/groups/xiaojie/mstojkov/atlas-codex-account.sh
: "${SLURM_JOB_ID:?Native work requires Slurm}"
: "${ATLAS_FOCUSED_PAYLOAD:?Root must stage the reviewed payload, including shared preserve_bytes.py}"
ATLAS_FOCUSED_WORK="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/history_contract_audit_20261113"
ATLAS_FOCUSED_BASE="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/history_parallel_20261112/runtime_52073185"
ATLAS_FOCUSED_RELEASE="$ATLAS_FOCUSED_WORK/runtime_$SLURM_JOB_ID"
ATLAS_FOCUSED_OUTPUT="$ATLAS_FOCUSED_WORK/run_$SLURM_JOB_ID"
export ATLAS_REPOSITORY="$ATLAS_FOCUSED_BASE"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_FOCUSED_PAYLOAD:$ATLAS_REPOSITORY/src"
preserve_on_exit() {
    ATLAS_FOCUSED_EXIT_STATUS=$?
    trap - EXIT
    if ! "$ATLAS_ENV_DIR/bin/python" "$ATLAS_FOCUSED_PAYLOAD/experiments/history_contract_audit_20261113/preserve_pilot_outputs.py" \
        --output "$ATLAS_FOCUSED_OUTPUT" --payload "$ATLAS_FOCUSED_PAYLOAD" --release "$ATLAS_FOCUSED_RELEASE" \
        --destination "$GROUP_HOME/mstojkov/atlas-results/history_contract_audit_20261113/job_$SLURM_JOB_ID"; then
        printf '%s\n' 'Durable preservation failed; all scratch originals remain. Do not clean them up.' >&2
        exit 1
    fi
    exit "$ATLAS_FOCUSED_EXIT_STATUS"
}
trap preserve_on_exit EXIT
[[ "${ATLAS_CODEX_ACCOUNT_LABEL-}" == atlas-agent-runs ]]
[[ "${CODEX_HOME-}" == "$GROUP_HOME/mstojkov/private/codex-atlas-runs" ]]
# Separate model-free administrative repair; identical partial copies resume.
"$ATLAS_ENV_DIR/bin/python" "$ATLAS_FOCUSED_PAYLOAD/experiments/method_intake_audit_20261113/preserve_bytes.py" \
    --source "$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/method_intake_audit_20261113/live_release_02/results_52176596" \
    --destination "$GROUP_HOME/mstojkov/atlas-results/method_intake_audit_20261113/run_52176596" \
    --manifest "$GROUP_HOME/mstojkov/atlas-results/method_intake_audit_20261113/run_52176596.verification.json"
"$ATLAS_ENV_DIR/bin/python" "$ATLAS_FOCUSED_PAYLOAD/experiments/history_contract_audit_20261113/build_pilot_release.py" \
    --base "$ATLAS_FOCUSED_BASE" --destination "$ATLAS_FOCUSED_RELEASE" --payload-root "$ATLAS_FOCUSED_PAYLOAD"
export ATLAS_REPOSITORY="$ATLAS_FOCUSED_RELEASE"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$ATLAS_REPOSITORY/experiments/citation_lineage_pilot"
cd "$ATLAS_REPOSITORY"
for ATLAS_FOCUSED_REQUIRED in experiments/citation_lineage_pilot/run.py experiments/citation_lineage_pilot/prompts.py \
    experiments/history_contract_audit_20261113/test_focused_reading.py experiments/history_contract_audit_20261113/test_release_builder.py \
    tests/illumina/test_illumina_history.py tests/illumina/test_history_method_identity.py tests/illumina/test_history_inputs.py \
    tests/illumina/test_history_view.py tests/illumina/test_history_bindings.py tests/illumina/test_history_identity_compiler.py; do
    [[ -f "$ATLAS_FOCUSED_REQUIRED" ]] || { printf 'Missing preflight file: %s\n' "$ATLAS_FOCUSED_REQUIRED" >&2; exit 2; }
done
"$ATLAS_ENV_DIR/bin/python" -m unittest discover -s experiments/history_contract_audit_20261113 -p 'test_*.py' -v
"$ATLAS_ENV_DIR/bin/python" -m pytest experiments/citation_lineage_pilot tests/illumina/test_illumina_history.py \
    tests/illumina/test_history_method_identity.py tests/illumina/test_history_inputs.py \
    tests/illumina/test_history_view.py tests/illumina/test_history_bindings.py \
    tests/illumina/test_history_identity_compiler.py --import-mode=importlib -p no:cacheprovider -q
"$ATLAS_CODEX_BIN_DIR/codex" --version
"$ATLAS_CODEX_BIN_DIR/codex" login status
# Existing native custody gate runs for BOTH complete prior analyses before either model starts.
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import json
from pathlib import Path
import run
plan=json.loads(Path('experiments/history_contract_audit_20261113/pilot_plan.json').read_text())
for item in plan['reuse'].values():
    run.validate_completed_citation(Path(item['previous_run']))
PY
mkdir "$ATLAS_FOCUSED_OUTPUT"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src"
"$ATLAS_ENV_DIR/bin/python" experiments/citation_lineage_pilot/run.py review-existing \
    --previous "$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/history_parallel_20261112/run_52073185/citations/eclip" \
    --output "$ATLAS_FOCUSED_OUTPUT/eclip" --reading-policy focused > "$ATLAS_FOCUSED_OUTPUT/eclip.log" 2>&1 &
ATLAS_FOCUSED_ECLIP_PID=$!
"$ATLAS_ENV_DIR/bin/python" experiments/citation_lineage_pilot/run.py review-existing \
    --previous "$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/history_parallel_20261112/run_52073185/citations/scatac-seq-microfluidics" \
    --output "$ATLAS_FOCUSED_OUTPUT/scatac-seq-microfluidics" --reading-policy focused > "$ATLAS_FOCUSED_OUTPUT/scatac.log" 2>&1 &
ATLAS_FOCUSED_SCATAC_PID=$!
ATLAS_FOCUSED_RESULT=0
wait "$ATLAS_FOCUSED_ECLIP_PID" || ATLAS_FOCUSED_RESULT=1
wait "$ATLAS_FOCUSED_SCATAC_PID" || ATLAS_FOCUSED_RESULT=1
# Preserve all outputs/logs; no canonical/index update, no cleanup or log compression.
exit "$ATLAS_FOCUSED_RESULT"

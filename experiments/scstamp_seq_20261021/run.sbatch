#!/usr/bin/env bash
#SBATCH --job-name=atlas-fresh-method
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=04:00:00
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
hostname
selected_runtime="${ATLAS_FRESH_METHOD_RUNTIME:?Select a frozen runtime}"
selected_commit="${ATLAS_RUNTIME_COMMIT:?Record its Git commit}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
export ATLAS_REPOSITORY="$selected_runtime"
export ATLAS_RUNTIME_COMMIT="$selected_commit"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export OMP_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
stage="${1:?Use acquire, recover-extract or extract}"
method_root="${2:?Select a fresh per-method directory}"
case "$stage" in acquire|recover-extract|extract) ;; *) exit 2 ;; esac
[[ "$method_root" == "$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/method_experiments/"* ]] || exit 2
mkdir -p "$method_root/audit"
cd "$ATLAS_REPOSITORY"

preserve_result() {
    result_code=$?
    trap - EXIT
    printf '%s\n' "$result_code" > "$method_root/audit/exit-$stage-$SLURM_JOB_ID.txt"
    archive="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/method_experiments/archives/scstamp-$stage-$SLURM_JOB_ID.tar.gz"
    if "$ATLAS_ENV_DIR/bin/python" scripts/benchmark/archive_result.py \
        --experiment "$method_root" --output "$archive" --project-root "$ATLAS_PROJECT_ROOT"; then
        durable="$GROUP_HOME/mstojkov/atlas-results/$(basename "$archive")"
        mkdir -p "$(dirname "$durable")"
        [[ ! -e "$durable" ]] || exit 3
        cp -n "$archive" "$durable"
        [[ "$(sha256sum "$archive" | cut -d' ' -f1)" == "$(sha256sum "$durable" | cut -d' ' -f1)" ]] || exit 3
        sha256sum "$durable" > "$durable.sha256"
        printf '%s\n' "$durable" > "$method_root/audit/archive-$stage-$SLURM_JOB_ID.txt"
    else
        echo 'Archive failed; scratch alone is not durable preservation' >&2
        exit 3
    fi
    exit "$result_code"
}
trap preserve_result EXIT
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.execution_check \
    --workdir "$ATLAS_REPOSITORY" --require-slurm > "$method_root/audit/execution-$stage-$SLURM_JOB_ID.json"
"$ATLAS_ENV_DIR/bin/python" - "$stage" "$method_root" <<'PY'
import json, os
from datetime import datetime, timezone
from pathlib import Path
import sys
stage, root = sys.argv[1], Path(sys.argv[2])
record = {"stage": stage, "slurm_job_id": os.environ["SLURM_JOB_ID"],
          "started_at": datetime.now(timezone.utc).isoformat(),
          "runtime": os.environ["ATLAS_REPOSITORY"], "runtime_commit": os.environ["ATLAS_RUNTIME_COMMIT"],
          "method_root": str(root), "method_id": "scstamp-seq", "model": "gpt-6.1-sol", "effort": "xhigh",
          "stage_timeout_seconds": None, "canonical_promotion": False}
with (root / f"launch-{stage}-{record['slurm_job_id']}.json").open("x") as handle:
    json.dump(record, handle, indent=2)
PY

if [[ "$stage" == acquire ]]; then
    [[ ! -e "$method_root/inputs" && ! -e "$method_root/acquisition" ]] || exit 2
    cp -R experiments/scstamp_seq_20261021/inputs "$method_root/inputs"
    cp experiments/scstamp_seq_20261021/README.md "$method_root/README.md"
    "$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.source_worklist tasks \
        --readiness "$method_root/inputs/readiness.json" --output "$method_root/inputs/worklist.json"
    "$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.source_acquisition \
        "$method_root/inputs/worklist.json" --output "$method_root/acquisition" \
        --method scstamp-seq --scopes "$method_root/inputs/scopes.json" --concurrency 1
elif [[ "$stage" == recover-extract ]]; then
    "$ATLAS_ENV_DIR/bin/python" experiments/scstamp_seq_20261021/recover_and_extract.py "$method_root"
else
    packet="${3:?Supply the inspected registered packet path}"
    "$ATLAS_ENV_DIR/bin/python" - "$packet" "$method_root/extraction/run_001" <<'PY'
import asyncio, json, sys
from pathlib import Path
from genomics_discovery.curation.codex_worker import CodexWorker
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute
result = asyncio.run(execute(Path(sys.argv[1]), Path(sys.argv[2]),
    worker=CodexWorker(model="gpt-6.1-sol", effort="xhigh", timeout_seconds=None,
                       web_search=False, network_access=False),
    mode="specialists", max_revisions=1,
    require_coverage=True, require_source_coverage=True, require_field_review=True))
print(json.dumps({"status": result["status"], "run": result["run"],
                  "workflow": result.get("workflow"), "review": result.get("review"),
                  "error": result.get("error")}))
raise SystemExit(1 if result["status"] in {"failed", "cancelled"} else 0)
PY
fi

#!/usr/bin/env bash
#SBATCH --job-name=atlas-diagram-content10
#SBATCH --partition=xiaojie,normal
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=06:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_DIAGRAM_ROOT:?Pinned experiment directory on Qiu scratch}"
: "${ATLAS_DIAGRAM_EXAMPLES_MANIFEST:?Ten-example Illumina packet manifest with PNGs}"
export ATLAS_DIAGRAM_RUNTIME="${ATLAS_DIAGRAM_RUNTIME:-runtime_${SLURM_JOB_ID}}"
export ATLAS_REPOSITORY="$ATLAS_DIAGRAM_ROOT/$ATLAS_DIAGRAM_RUNTIME"
if [[ ! -d "$ATLAS_REPOSITORY" ]]; then
    export ATLAS_DIAGRAM_BASE_RUNTIME="${ATLAS_DIAGRAM_BASE_RUNTIME:-$ATLAS_DIAGRAM_ROOT/source_runtime_52084756}"
    python3 - <<'PY'
import os,json,hashlib,shutil
from pathlib import Path
stage=Path(os.environ['ATLAS_DIAGRAM_ROOT']);base=Path(os.environ['ATLAS_DIAGRAM_BASE_RUNTIME'])
base_manifest=base.parent/(base.name+'_manifest.json');manifest=json.loads(base_manifest.read_text())
runtime=Path(os.environ['ATLAS_REPOSITORY']);runtime.mkdir(exist_ok=False)
files={}
def copy(source,name,pin):
    assert hashlib.sha256(source.read_bytes()).hexdigest()==pin,name
    target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target);files[name]=pin
for name,pin in manifest['files'].items():copy(base/name,name,pin)
patch=json.loads((stage/'patch_pins.json').read_text())
for name,pin in patch['files'].items():
    assert name.startswith('experiments/diagram_generation10_20261112/') or name=='src/genomics_discovery/illumina/paper_reads.py',name
    copy(stage/'patch'/name,name,pin)
(stage/(runtime.name+'_manifest.json')).write_text(json.dumps({'files':files,
    'base_manifest_sha256':hashlib.sha256(base_manifest.read_bytes()).hexdigest(),'patch':patch},indent=2))
PY
fi
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-diagram-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-diagram-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
backup_results() {
    result_code=$?;trap - EXIT
    dest="$GROUP_HOME/mstojkov/atlas-results/diagram_generation10_20261112"
    mkdir -p "$dest"
    items=("${ATLAS_DIAGRAM_RUNTIME}_manifest.json" "patch_pins.json" "job.sbatch")
    [[ ! -e "$ATLAS_DIAGRAM_ROOT/patch.tar.gz" ]] || items+=("patch.tar.gz")
    for item in "run_${SLURM_JOB_ID}" "software_${SLURM_JOB_ID}.xml"; do
        [[ ! -e "$ATLAS_DIAGRAM_ROOT/$item" ]] || items+=("$item")
    done
    tar --exclude=__pycache__ --exclude=.pytest_cache -czf "$dest/job_${SLURM_JOB_ID}.tar.gz" \
        -C "$ATLAS_DIAGRAM_ROOT" "${items[@]}" || result_code=1
    exit "$result_code"
}
trap backup_results EXIT
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import os,json,hashlib
from pathlib import Path
root=Path(os.environ['ATLAS_REPOSITORY'])
manifest=json.loads((root.parent/(root.name+'_manifest.json')).read_text())
for name,pin in manifest['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==pin,name
assert os.environ['CODEX_HOME'].endswith('/private/codex-atlas-runs')
assert not any(os.environ.get(k) for k in ['OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN'])
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(preflight_pinned_shell_environment())
PY
if [[ -z "${ATLAS_DIAGRAM_SOURCES_MANIFEST:-}" ]]; then
    "$ATLAS_ENV_DIR/bin/python" -m experiments.diagram_generation10_20261112.assemble_downloaded \
      --candidates "$ATLAS_REPOSITORY/experiments/diagram_generation10_20261112/candidates.json" \
      --source-run "${ATLAS_DIAGRAM_SOURCE_RUN:-$ATLAS_DIAGRAM_ROOT/sources_52084756}" \
      --output "$ATLAS_DIAGRAM_ROOT/assembled_sources_${SLURM_JOB_ID}"
    export ATLAS_DIAGRAM_SOURCES_MANIFEST="$ATLAS_DIAGRAM_ROOT/assembled_sources_${SLURM_JOB_ID}/input_manifest.json"
fi
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
  experiments/diagram_generation10_20261112/test_content.py \
  --junitxml="$ATLAS_DIAGRAM_ROOT/software_${SLURM_JOB_ID}.xml"
"$ATLAS_CODEX_BIN_DIR/codex" login status
"$ATLAS_ENV_DIR/bin/python" -m experiments.diagram_generation10_20261112.run run \
  --manifest "$ATLAS_DIAGRAM_SOURCES_MANIFEST" --examples "$ATLAS_DIAGRAM_EXAMPLES_MANIFEST" \
  --output "$ATLAS_DIAGRAM_ROOT/run_${SLURM_JOB_ID}" --agents "${ATLAS_DIAGRAM_AGENTS:-10}"

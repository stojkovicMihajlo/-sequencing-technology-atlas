#!/usr/bin/env bash
#SBATCH --job-name=atlas-diagram10-sources
#SBATCH --partition=xiaojie,normal,service
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=01:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
work="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work"
stage="$work/diagram_generation10_20261112"
export ATLAS_DIAGRAM_SOURCE_STAGE="$stage"
export ATLAS_REPOSITORY="$stage/source_runtime_${SLURM_JOB_ID}"
backup_results(){
    code=$?;trap - EXIT
    dest="$GROUP_HOME/mstojkov/atlas-results/diagram_generation10_20261112"
    mkdir -p "$dest"
    items=("source_patch_pins.json" "acquire.sbatch")
    for item in "sources_${SLURM_JOB_ID}" "source_runtime_${SLURM_JOB_ID}_manifest.json"; do
        [[ ! -e "$stage/$item" ]] || items+=("$item")
    done
    tar --exclude=__pycache__ -czf "$dest/source_job_${SLURM_JOB_ID}.tar.gz" -C "$stage" "${items[@]}" || code=1
    exit "$code"
}
trap backup_results EXIT
python3 - <<'PY'
from pathlib import Path
import json,hashlib,shutil,os
s=Path(os.environ['ATLAS_DIAGRAM_SOURCE_STAGE']);prior=s.parent/'evidence_remaining33_20261112'
base=prior/'runtime_52071333';mp=prior/'runtime_52071333_manifest.json'
manifest=json.loads(mp.read_text());updates=json.loads((s/'source_patch_pins.json').read_text())
runtime=Path(os.environ['ATLAS_REPOSITORY']);runtime.mkdir(exist_ok=False);files={}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def copy(source,name,pin):
    assert sha(source)==pin,name
    target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);files[name]=pin
for name,pin in manifest['files'].items():copy(base/name,name,pin)
for name,pin in updates['files'].items():copy(s/'source_patch'/name,name,pin)
(s/('source_runtime_'+os.environ['SLURM_JOB_ID']+'_manifest.json')).write_text(json.dumps({'base_manifest_sha256':sha(mp),'files':files,'patch':updates},indent=2))
PY
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
cd "$ATLAS_REPOSITORY"
"$ATLAS_ENV_DIR/bin/python" -m experiments.diagram_generation10_20261112.acquire_inputs \
    --candidates "$ATLAS_REPOSITORY/experiments/diagram_generation10_20261112/candidates.json" \
    --output "$stage/sources_${SLURM_JOB_ID}"

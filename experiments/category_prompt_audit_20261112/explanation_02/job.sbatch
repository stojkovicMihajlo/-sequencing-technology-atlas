#!/usr/bin/env bash
# Source-preserving follow-up of the operation explanation contract.
#SBATCH --job-name=atlas-explanation
#SBATCH --partition=normal,xiaojie,service
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=02:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
root="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/category_prompt_audit_20261112"
stage="$root/explanation_02"
export ATLAS_REPOSITORY="$stage/runtime"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
export ATLAS_EXPLANATION_STAGE="$stage"
backup_results() {
    code=$?
    trap - EXIT
    backup="$GROUP_HOME/mstojkov/atlas-results/category_prompt_audit_20261112"
    mkdir -p "$backup"
    tar --exclude=runtime -czf "$backup/explanation_${SLURM_JOB_ID}.tar.gz" \
        -C "$root" explanation_02 || code=1
    exit "$code"
}
trap backup_results EXIT
python3 - <<'PY'
import hashlib,json,os,shutil
from pathlib import Path
stage=Path(os.environ['ATLAS_EXPLANATION_STAGE']); root=stage.parent
runtime=Path(os.environ['ATLAS_REPOSITORY']); runtime.mkdir(exist_ok=False)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
parent=json.loads((root/'full_runtime_manifest.json').read_text())
files=dict(parent['files'])
for rel,pin in files.items():
    source=root/'runtime_full_01'/rel
    assert sha(source)==pin,rel
    target=runtime/rel; target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target)
patch=json.loads((stage/'pins.json').read_text())
changes={}
for rel,pin in patch['files'].items():
    source=stage/'patch'/rel; assert sha(source)==pin,rel
    target=runtime/rel; target.parent.mkdir(parents=True,exist_ok=True)
    changes[rel]={'before':files.get(rel),'after':pin}
    shutil.copyfile(source,target); files[rel]=pin
(stage/'runtime_manifest.json').write_text(json.dumps({
    'code_commit':patch['code_commit'],'parent_manifest_sha256':sha(root/'full_runtime_manifest.json'),
    'files':files,'changes':changes},indent=2)+'\n')
PY
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-explanation-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-explanation-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
    tests/illumina/test_representation_contract.py tests/illumina/test_representation_pipeline.py \
    tests/illumina/test_representation_semantic_controls.py tests/illumina/test_assignment_contract.py \
    tests/illumina/test_revision_handoff.py tests/illumina/test_information_effect_experiment.py \
    tests/illumina/test_illumina_author_resume.py --junitxml="$stage/software.xml"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import json,os
from pathlib import Path
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
from experiments.readability_audit_20261108.rerun import prepare,read
s=Path(os.environ['ATLAS_EXPLANATION_STAGE'])
assert os.environ['CODEX_HOME'].endswith('/private/codex-atlas-runs')
assert not any(os.environ.get(k) for k in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN','OPENAI_IDENTITY_TOKEN_FILE','OPENAI_AUTH_PROVIDER_ID'))
policy=preflight_pinned_shell_environment()
prepared=prepare(read(s/'plan.json'))
(s/'preflight.json').write_text(json.dumps({'policy':str(policy),
    'methods':[m['method_id'] for m,_,_,_ in prepared],
    'model':'gpt-6.1-sol','effort':'xhigh','source_bundles_unchanged':True},indent=2)+'\n')
PY
"$ATLAS_CODEX_BIN_DIR/codex" login status
"$ATLAS_ENV_DIR/bin/python" -m experiments.readability_audit_20261108.rerun \
    --plan "$stage/plan.json" --output "$stage/run" --workers 2

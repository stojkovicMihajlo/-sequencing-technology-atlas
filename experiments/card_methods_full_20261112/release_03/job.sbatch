#!/usr/bin/env bash
#SBATCH --job-name=atlas-cards-revisions
#SBATCH --partition=xiaojie,normal,service
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=16:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
work="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work"
stage="$work/card_methods_full_20261112/release_03"
export ATLAS_WORK_ROOT="$work"
export ATLAS_REPOSITORY="$stage/runtime_03"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
export ATLAS_FULL_STAGE="$stage"
backup_results() {
    code=$?
    trap - EXIT
    dest="$GROUP_HOME/mstojkov/atlas-results/card_methods_full_20261112"
    mkdir -p "$dest"
    tar --exclude=__pycache__ --exclude=.pytest_cache -czf "$dest/job_${SLURM_JOB_ID}.tar.gz" \
        -C "$stage" . || code=1
    exit "$code"
}
trap backup_results EXIT
python3 - <<'PY'
import hashlib,json,os,shutil
from pathlib import Path
s=Path(os.environ['ATLAS_FULL_STAGE']); runtime=Path(os.environ['ATLAS_REPOSITORY'])
runtime.mkdir(exist_ok=False)
work=Path(os.environ['ATLAS_WORK_ROOT'])
parent=work/'category_prompt_audit_20261112/explanation_02'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
base=json.loads((parent/'runtime_manifest.json').read_text()); files=dict(base['files'])
for name,pin in files.items():
    source=parent/'runtime'/name; assert sha(source)==pin,name
    target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
support=work/'card_methods_full_20261106'
support_manifest=support/'runtime_04_manifest.json'
for name,pin in json.loads(support_manifest.read_text())['files'].items():
    if name in files: continue
    source=support/'runtime_04'/name; assert sha(source)==pin,name
    target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    files[name]=pin
patch=json.loads((s/'patch_pins.json').read_text()); changes={}
for name,pin in patch['files'].items():
    source=s/'patch'/name; assert sha(source)==pin,name
    target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True)
    changes[name]={'before':files.get(name),'after':pin}
    shutil.copyfile(source,target);files[name]=pin
(s/'runtime_manifest.json').write_text(json.dumps({'files':files,'changes':changes,
    'parent_manifest_sha256':sha(parent/'runtime_manifest.json'),
    'support_manifest_sha256':sha(support_manifest),'patch':patch},indent=2)+'\n')
PY
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
    tests/illumina/test_representation_contract.py tests/illumina/test_representation_pipeline.py \
    tests/illumina/test_representation_semantic_controls.py tests/illumina/test_assignment_contract.py \
    tests/illumina/test_revision_handoff.py tests/illumina/test_information_effect_experiment.py \
    tests/illumina/test_illumina_author_resume.py tests/illumina/test_illumina_batch_controls.py \
    experiments/diagram_paper_pilot_20261106/test_sources.py \
    experiments/diagram_paper_pilot_20261106/test_dependencies.py \
    experiments/card_methods_full_20261106/test_run.py \
    experiments/card_methods_full_20261112/test_recovery.py \
    --junitxml="$stage/software_02.xml"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import os,json
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
assert os.environ['CODEX_HOME'].endswith('/private/codex-atlas-runs')
assert not any(os.environ.get(k) for k in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN','OPENAI_IDENTITY_TOKEN_FILE','OPENAI_AUTH_PROVIDER_ID'))
print(json.dumps(preflight_pinned_shell_environment()))
PY
"$ATLAS_CODEX_BIN_DIR/codex" login status
"$ATLAS_ENV_DIR/bin/python" -m experiments.card_methods_full_20261112.preflight \
    --previous "$work/card_methods_full_20261106/run_03/state.json" --output "$stage/audit_02"
"$ATLAS_ENV_DIR/bin/python" -m experiments.card_methods_full_20261112.recover \
    --previous "$work/card_methods_full_20261106/run_03/state.json" --audit "$stage/audit_02" \
    --library "$ATLAS_PROJECT_ROOT/technology_sources/illumina_library_20261105_07" \
    --vault "$ATLAS_PROJECT_ROOT/technology_sources/illumina_task2_sources_20261105_08" \
    --scope "$ATLAS_REPOSITORY/experiments/card_methods_full_20261106/card_scope.csv" \
    --pilot "$ATLAS_REPOSITORY/experiments/diagram_paper_pilot_20261106/selected.json" \
    --existing "$work/diagram_paper_pilot_20261106/dependencies_04/registered_additions.json" \
    --output "$stage/recovery_02" --contact-email mstojkov@stanford.edu
"$ATLAS_ENV_DIR/bin/python" -m experiments.card_methods_full_20261106.run \
    --library "$ATLAS_PROJECT_ROOT/technology_sources/illumina_library_20261105_07" \
    --vault "$ATLAS_PROJECT_ROOT/technology_sources/illumina_task2_sources_20261105_08" \
    --scope "$ATLAS_REPOSITORY/experiments/card_methods_full_20261106/card_scope.csv" \
    --pilot-selection "$ATLAS_REPOSITORY/experiments/diagram_paper_pilot_20261106/selected.json" \
    --prior-run "$work/card_methods_full_20261106/run_03" \
    --additions "$stage/recovery_02/registered_additions.json" \
    --intervention-holds "$stage/recovery_02/holds.json" \
    --primary-overrides "$stage/recovery_02/primary_overrides.json" \
    --output "$stage/run_02" --workers 10 --max-revisions 2 --fresh

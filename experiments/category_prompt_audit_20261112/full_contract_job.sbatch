#!/usr/bin/env bash
# Focused full-harness revision. Uses the existing production extraction runner.
#SBATCH --job-name=atlas-full-contract
#SBATCH --partition=normal,xiaojie,service
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=04:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
root="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/category_prompt_audit_20261112"
export ATLAS_REPOSITORY="$root/runtime_full_01"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
mkdir "$ATLAS_REPOSITORY"
tar -xzf "$root/../information_effect_20261108/runtime_01.tar.gz" -C "$ATLAS_REPOSITORY"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-full-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-full-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
backup_results() {
    code=$?
    trap - EXIT
    backup="$GROUP_HOME/mstojkov/atlas-results/category_prompt_audit_20261112"
    mkdir -p "$backup"
    items=(staging_full)
    for name in full_run_01 full_runtime_manifest.json full_software.xml full_preflight.json; do
        [[ ! -e "$root/$name" ]] || items+=("$name")
    done
    tar -czf "$backup/full_${SLURM_JOB_ID}.tar.gz" -C "$root" "${items[@]}" || code=1
    exit "$code"
}
trap backup_results EXIT
cd "$ATLAS_REPOSITORY"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib,json,os,shutil
from pathlib import Path
r=Path(os.environ['ATLAS_REPOSITORY']); root=r.parent
old=json.loads((root.parent/'information_effect_20261108/runtime_01_manifest.json').read_text())
for name,pin in old['files'].items():
    assert hashlib.sha256((r/name).read_bytes()).hexdigest()==pin,name
files=dict(old['files']); changed={}
for name in ('prompts.py','models.py','representation.py'):
    rel='src/genomics_discovery/illumina/'+name
    source=root/'staging_full'/name
    expected=json.loads((root/'staging_full/pins.json').read_text())[name]
    assert hashlib.sha256(source.read_bytes()).hexdigest()==expected,name
    shutil.copyfile(source,r/rel); changed[rel]={'before':files[rel],'after':expected};files[rel]=expected
(root/'full_runtime_manifest.json').write_text(json.dumps({'files':files,'changes':changed},indent=2)+'\n')
PY
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
    tests/illumina/test_representation_contract.py tests/illumina/test_representation_pipeline.py \
    tests/illumina/test_representation_semantic_controls.py tests/illumina/test_assignment_contract.py \
    tests/illumina/test_revision_handoff.py tests/illumina/test_information_effect_experiment.py \
    --junitxml="$root/full_software.xml"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import json,os
from pathlib import Path
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
from experiments.readability_audit_20261108.rerun import prepare,read
r=Path(os.environ['ATLAS_REPOSITORY']).parent
assert os.environ['CODEX_HOME'].endswith('/private/codex-atlas-runs')
assert not any(os.environ.get(k) for k in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN','OPENAI_IDENTITY_TOKEN_FILE','OPENAI_AUTH_PROVIDER_ID'))
policy=preflight_pinned_shell_environment()
prepared=prepare(read(r/'staging_full/plan.json'))
(r/'full_preflight.json').write_text(json.dumps({'policy':str(policy),'methods':[m['method_id'] for m,_,_,_ in prepared],'model':'gpt-6.1-sol','effort':'xhigh','compact_adapter_used':False},indent=2)+'\n')
print('All four fixed-source work units preflighted; full production runner selected.')
PY
"$ATLAS_CODEX_BIN_DIR/codex" login status
"$ATLAS_ENV_DIR/bin/python" -m experiments.readability_audit_20261108.rerun \
    --plan "$root/staging_full/plan.json" --output "$root/full_run_01" --workers 2

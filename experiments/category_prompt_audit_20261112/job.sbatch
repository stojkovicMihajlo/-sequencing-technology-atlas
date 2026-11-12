#!/usr/bin/env bash
#SBATCH --job-name=atlas-category-prompt
#SBATCH --partition=normal,xiaojie,service
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=04:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
root="$GROUP_SCRATCH/mstojkov/agents_snRNA/technology_atlas/v2/work/category_prompt_audit_20261112"
export ATLAS_REPOSITORY="$root/runtime_01"
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
if [[ "${1:-}" == preflight ]]; then
    mkdir "$ATLAS_REPOSITORY"
    tar -xzf "$root/../information_effect_20261108/runtime_01.tar.gz" -C "$ATLAS_REPOSITORY"
    mkdir -p "$ATLAS_REPOSITORY/experiments/category_prompt_audit_20261112"
    cp "$root/staging/run.py" "$root/staging/author_contract.txt" "$ATLAS_REPOSITORY/experiments/category_prompt_audit_20261112/"
fi
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-category-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
backup_results() {
    code=$?
    trap - EXIT
    backup="$GROUP_HOME/mstojkov/atlas-results/category_prompt_audit_20261112"
    mkdir -p "$backup"
    if [[ -n "${case_name:-}" && -d "$root/$case_name" ]]; then
        tar -czf "$backup/job_${SLURM_JOB_ID}_${SLURM_ARRAY_TASK_ID:-0}.tar.gz" -C "$root" "$case_name" || code=1
    elif [[ "${1:-}" == preflight ]]; then
        tar -czf "$backup/preflight_${SLURM_JOB_ID}.tar.gz" -C "$root" staging plans runtime_01_manifest.json prompt_checks.json software.xml || code=1
    elif [[ "${1:-}" == batch ]]; then
        items=(staging plans runtime_01_manifest.json prompt_checks.json)
        for d in "$root"/case_*; do
            [[ ! -d "$d" ]] || items+=("${d##*/}")
        done
        tar -czf "$backup/batch_${SLURM_JOB_ID}.tar.gz" -C "$root" "${items[@]}" || code=1
    fi
    exit "$code"
}
trap 'backup_results "${1:-}"' EXIT
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import os
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
assert os.environ['CODEX_HOME'].endswith('/private/codex-atlas-runs')
assert not any(os.environ.get(k) for k in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN','OPENAI_IDENTITY_TOKEN_FILE','OPENAI_AUTH_PROVIDER_ID'))
print(preflight_pinned_shell_environment())
PY
"$ATLAS_CODEX_BIN_DIR/codex" login status
if [[ "${1:-}" == preflight ]]; then
    "$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path(os.environ['ATLAS_REPOSITORY'])
old=json.loads((r.parent.parent/'information_effect_20261108/runtime_01_manifest.json').read_text())
for name,pin in old['files'].items():
    assert hashlib.sha256((r/name).read_bytes()).hexdigest()==pin,name
files=dict(old['files'])
for p in (r/'experiments/category_prompt_audit_20261112').iterdir():
    if p.is_file(): files[str(p.relative_to(r))]=hashlib.sha256(p.read_bytes()).hexdigest()
(r.parent/'runtime_01_manifest.json').write_text(json.dumps({'files':files},indent=2)+'\n')
PY
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
        tests/illumina/test_representation_contract.py tests/illumina/test_representation_pipeline.py \
        tests/illumina/test_representation_semantic_controls.py tests/illumina/test_assignment_contract.py \
        tests/illumina/test_revision_handoff.py --junitxml="$root/software.xml"
    "$ATLAS_ENV_DIR/bin/python" -m experiments.category_prompt_audit_20261112.run --preflight-all "$root/plans" > "$root/prompt_checks.json"
else
    "$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path(os.environ['ATLAS_REPOSITORY'])
for name,pin in json.loads((r.parent/'runtime_01_manifest.json').read_text())['files'].items():
    assert hashlib.sha256((r/name).read_bytes()).hexdigest()==pin,name
PY
    run_case() {
        local index="$1" suffix plan profile
        printf -v suffix '%02d' "$index"
        plan="$root/plans/plan_$suffix.json"
        profile=compact
        [[ "$index" -le 10 ]] || profile=current
        "$ATLAS_ENV_DIR/bin/python" -m experiments.category_prompt_audit_20261112.run \
            --plan "$plan" --output "$root/case_$suffix" --profile "$profile"
    }
    if [[ "${1:-}" == batch ]]; then
        # Finite, explicitly selected cases. No retry, supervision or resubmission.
        # Start fresh controls alongside their compact counterparts.
        cases=(11 10 12 4 1 2 3 5 6 7 8 9)
        result=0
        for ((i=0; i<${#cases[@]}; i+=2)); do
            run_case "${cases[i]}" > "$root/case_${cases[i]}_launcher.log" 2>&1 & left_pid=$!
            run_case "${cases[i+1]}" > "$root/case_${cases[i+1]}_launcher.log" 2>&1 & right_pid=$!
            wait "$left_pid" || result=1
            wait "$right_pid" || result=1
        done
        exit "$result"
    else
        case_index="${2:-${SLURM_ARRAY_TASK_ID:-}}"
        [[ -n "$case_index" ]] || { echo 'Missing case index' >&2; exit 2; }
        printf -v case_name 'case_%02d' "$case_index"
        run_case "$case_index"
    fi
fi

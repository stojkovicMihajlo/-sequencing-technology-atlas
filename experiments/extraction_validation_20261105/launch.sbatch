#!/usr/bin/env bash
#SBATCH --job-name=atlas-matched-extract
#SBATCH --partition=xiaojie,normal
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=06:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
: "${ATLAS_VALIDATION_ROOT:?Set a fresh scratch experiment root}"
: "${ATLAS_VALIDATION_SOURCE:?Select the completed source run}"
module load devel python/3.12.1
export ATLAS_REPOSITORY="$ATLAS_VALIDATION_ROOT/runtime_01"
mkdir "$ATLAS_REPOSITORY"
python3 - <<'PY'
import hashlib,json,os,tarfile
from pathlib import Path
root=Path(os.environ['ATLAS_VALIDATION_ROOT'])
base=Path(os.environ['ATLAS_VALIDATION_SOURCE']).parent
runtime=Path(os.environ['ATLAS_REPOSITORY'])
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(base/'runtime_01.tar.gz')=='e1ce60b61764a0a3866dff4baf5712a09ea5951cac4ce17e5a2238ea2004bcd7'
assert sha(base/'runtime_01_manifest.json')=='b111b6f76bfd1fcc017f2abc8781f9a0fd851bd419c4a74dbcfded66a6f1a747'
original=json.loads((base/'runtime_01_manifest.json').read_text())
overlay=json.loads((root/'code_manifest.json').read_text())
assert sha(root/'code.tar.gz')==overlay['archive_sha256']
with tarfile.open(base/'runtime_01.tar.gz') as tf:
    for member in tf:
        if member.name.rstrip('/')=='runtime_01': continue
        assert member.name.startswith('runtime_01/'),member.name
        member.name=member.name[len('runtime_01/'):]
        tf.extract(member,runtime,filter='data')
with tarfile.open(root/'code.tar.gz') as tf: tf.extractall(runtime,filter='data')
combined={'format_version':'frozen_runtime/1','base_git_commit':original['git_commit'],
          'validation_git_commit':overlay['commit'],'files':{**original['files'],**overlay['files']}}
for name,pin in combined['files'].items(): assert sha(runtime/name)==pin,name
(root/'runtime_01_manifest.json').write_text(json.dumps(combined,indent=2)+'\n')
PY
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
source "$ATLAS_REPOSITORY/scripts/sherlock/atlas-account.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export CODEX_SQLITE_HOME="$L_SCRATCH_JOB/atlas-validation-sqlite"
export XDG_CACHE_HOME="$L_SCRATCH_JOB/atlas-validation-cache"
export OMP_NUM_THREADS=1
mkdir -p "$CODEX_SQLITE_HOME" "$XDG_CACHE_HOME"
cd "$ATLAS_REPOSITORY"
backup_results() {
    result_code=$?
    trap - EXIT
    backup_root="$GROUP_HOME/mstojkov/atlas-results/extraction_validation_20261105"
    mkdir -p "$backup_root"
    if ! tar -czf "$backup_root/job_${SLURM_JOB_ID}.tar.gz" -C "$ATLAS_VALIDATION_ROOT" \
        --exclude='__pycache__' .; then
        printf 'Backup failed; retain scratch results. Original exit=%s\n' "$result_code" >&2
        [[ "$result_code" -ne 0 ]] || result_code=1
    fi
    exit "$result_code"
}
trap backup_results EXIT
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import json,os
assert os.environ['CODEX_HOME'].endswith('/private/codex-atlas-runs')
assert not any(os.environ.get(k) for k in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN',
                                         'OPENAI_IDENTITY_TOKEN_FILE','OPENAI_AUTH_PROVIDER_ID'))
from genomics_discovery.execution.codex_worker import preflight_pinned_shell_environment
print(json.dumps(preflight_pinned_shell_environment()))
PY
"$ATLAS_CODEX_BIN_DIR/codex" --version
"$ATLAS_CODEX_BIN_DIR/codex" login status
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p pytest_asyncio.plugin -q \
    tests/illumina experiments/citation_lineage_pilot/test_contract.py \
    experiments/citation_lineage_pilot/test_pair.py experiments/pilot50_20261104/test_*.py \
    experiments/extraction_validation_20261105/test_*.py \
    --junitxml="$ATLAS_VALIDATION_ROOT/software_${SLURM_JOB_ID}.xml"
"$ATLAS_ENV_DIR/bin/python" -u -m experiments.extraction_validation_20261105.run \
    --source-run "$ATLAS_VALIDATION_SOURCE" --output "$ATLAS_VALIDATION_ROOT/run_01" \
    --record smart_seq --record smart_seq2 --record cel_seq --record mars_seq \
    --record sci_rna_seq --record icshape --workers 4 \
    --reason 'User requested fresh extraction after the pilot audit; same-source validation of coverage, scope, modality and correction-handoff changes. Missing-source holds may remain.'

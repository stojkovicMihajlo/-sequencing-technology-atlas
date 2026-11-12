#!/usr/bin/env bash
#SBATCH --job-name=atlas-history-parallel50
#SBATCH --partition=normal,xiaojie
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=06:00:00
set -euo pipefail
hostname
printenv SLURM_JOB_ID
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
export ATLAS_HISTORY_ROOT="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/history_parallel_20261112"
prior="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/history_pilot_20261112"
export ATLAS_HISTORY_RUNTIME="runtime_${SLURM_JOB_ID}"
export ATLAS_HISTORY_OUTPUT="run_${SLURM_JOB_ID}"
export ATLAS_HISTORY_PREFLIGHT="preflight_${SLURM_JOB_ID}"
export ATLAS_HISTORY_CITATION_ONLY=1
export ATLAS_HISTORY_BACKUP_ROOT="$GROUP_HOME/mstojkov/atlas-results/history_parallel_20261112"
export ATLAS_REPOSITORY="$ATLAS_HISTORY_ROOT/$ATLAS_HISTORY_RUNTIME"
original_batch="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/card_methods_full_20261112/release_03/run_02/extraction"
mkdir "$ATLAS_REPOSITORY"
tar -xzf "$prior/runtime_52063823.tar.gz" -C "$ATLAS_REPOSITORY"
python3 - "$prior/runtime_52063823_manifest.json" "$ATLAS_REPOSITORY" <<'PY'
import hashlib,json,pathlib,sys
manifest=json.loads(pathlib.Path(sys.argv[1]).read_text());root=pathlib.Path(sys.argv[2])
for name,expected in manifest['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
PY
tar -xzf "$ATLAS_HISTORY_ROOT/updates.tar.gz" -C "$ATLAS_REPOSITORY"
python3 - "$prior/runtime_52063823_manifest.json" "$ATLAS_HISTORY_ROOT/update_pins.json" "$ATLAS_REPOSITORY" <<'PY'
import hashlib,json,pathlib,sys,tarfile
base=json.loads(pathlib.Path(sys.argv[1]).read_text());updates=json.loads(pathlib.Path(sys.argv[2]).read_text());root=pathlib.Path(sys.argv[3])
pins={**base['files'],**updates}
for name,expected in pins.items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
archive=root.parent/(root.name+'.tar.gz')
with tarfile.open(archive,'w:gz') as tar:
    for name in sorted(pins):tar.add(root/name,arcname=name)
manifest={'files':pins,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
          'base_manifest_sha256':hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest(),
          'reason':'Source-ready parallel method histories with reviewed canonical method endpoints and scientific change records.'}
(root.parent/(root.name+'_manifest.json')).write_text(json.dumps(manifest,indent=2))
PY
export ATLAS_CODEX_BIN_DIR="$GROUP_HOME/mstojkov/software/codex/0.163.0/bin"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
cd "$ATLAS_REPOSITORY"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$ATLAS_ENV_DIR/bin/python" -m pytest -p asyncio -q \
  experiments/history_parallel_20261112/test_prepare.py \
  tests/illumina/test_history_method_identity.py \
  experiments/history_changes_20261106/test_connections.py \
  tests/illumina/test_history_view.py \
  --junitxml="$ATLAS_HISTORY_ROOT/additional_software_${SLURM_JOB_ID}.xml"
if ! "$ATLAS_ENV_DIR/bin/python" -m experiments.history_parallel_20261112.prepare \
    --batch "$original_batch" --output "$ATLAS_HISTORY_ROOT/$ATLAS_HISTORY_PREFLIGHT" \
    --library "$ATLAS_PROJECT_ROOT/technology_sources/illumina_library_20261105_07" \
    --count 50 --expected-source-job 52055656; then
    # A blocked source preflight must not fall through to any model command.
    mkdir -p "$ATLAS_HISTORY_BACKUP_ROOT/$ATLAS_HISTORY_PREFLIGHT"
    [[ ! -f "$ATLAS_HISTORY_ROOT/$ATLAS_HISTORY_PREFLIGHT/PREFLIGHT.json" ]] || \
      cp "$ATLAS_HISTORY_ROOT/$ATLAS_HISTORY_PREFLIGHT/PREFLIGHT.json" "$ATLAS_HISTORY_BACKUP_ROOT/$ATLAS_HISTORY_PREFLIGHT/"
    exit 2
fi
export ATLAS_HISTORY_BATCH="$ATLAS_HISTORY_ROOT/$ATLAS_HISTORY_PREFLIGHT/accepted_snapshot"
export ATLAS_HISTORY_SELECTION="$ATLAS_HISTORY_ROOT/$ATLAS_HISTORY_PREFLIGHT/selection.json"
export ATLAS_HISTORY_METHOD_INVENTORY="$ATLAS_HISTORY_ROOT/$ATLAS_HISTORY_PREFLIGHT/method_inventory.json"
# No hidden agent cap: omitted ATLAS_HISTORY_AGENTS/WORKERS lets the existing CLI
# select one parallel slot per selected method. Either variable can override it.
source "$ATLAS_REPOSITORY/experiments/history_changes_20261106/launch.sbatch"

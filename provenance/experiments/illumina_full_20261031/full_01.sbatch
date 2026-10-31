#!/usr/bin/env bash
#SBATCH --job-name=illumina-all-diagrams
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=06:00:00
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Submit through Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
run_root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
export ATLAS_REPOSITORY="$run_root/runtime_05"
[[ ! -e "$ATLAS_REPOSITORY" ]] || { echo 'Runtime exists; use a new registered job, never overwrite.' >&2; exit 2; }
mkdir -p "$ATLAS_REPOSITORY" "$run_root/logs"
[[ "$(sha256sum "$run_root/staging/runtime_05.tar.gz" | cut -d ' ' -f1)" == 415e6c104211a6d83f56c01cd02d7c552680512de9fc93722fccb767eb12d7b4 ]]
tar -xzf "$run_root/staging/runtime_05.tar.gz" -C "$ATLAS_REPOSITORY"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
image_dependencies="$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$image_dependencies"
export OMP_NUM_THREADS=1
export XDG_CACHE_HOME="$run_root/disposable/xdg-cache"
export TMPDIR="${L_SCRATCH_JOB:?}/illumina-tmp"
mkdir -p "$TMPDIR"
private_runtime="$SCRATCH/atlas-codex-runtime-20261031"
export CODEX_SQLITE_HOME="$private_runtime/sqlite"
[[ "$(readlink "$HOME/.codex/cache")" == "$private_runtime/cache" ]]
[[ "$(readlink "$HOME/.codex/tmp")" == "$private_runtime/tmp" ]]
"$ATLAS_ENV_DIR/bin/python" - <<'PY' > "$run_root/logs/runtime-files-$SLURM_JOB_ID.json"
import hashlib,json,os
from pathlib import Path
root=Path(os.environ['ATLAS_REPOSITORY'])
receipt=json.loads((root.parent/'staging/runtime_05.json').read_text())
for name,expected in receipt['files'].items():
 assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
print(json.dumps({'verified_files':len(receipt['files']),'runtime':str(root),'slurm_job_id':os.environ['SLURM_JOB_ID']},indent=2))
PY
cd "$ATLAS_REPOSITORY"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q tests/test_illumina*.py > "$run_root/logs/full-software-$SLURM_JOB_ID.txt"
"$ATLAS_ENV_DIR/bin/python" - <<'PY' > "$run_root/logs/full-environment-$SLURM_JOB_ID.json"
import json,os,PIL
from genomics_discovery.curation.codex_worker import preflight_pinned_shell_environment
print(json.dumps({'slurm_job_id':os.environ['SLURM_JOB_ID'],'pillow':PIL.__version__,'shell':preflight_pinned_shell_environment()},indent=2))
PY
codex --version
codex login status

mkdir "$run_root/recovered_sources_03"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib,json,os
from pathlib import Path
root=Path(os.environ['ATLAS_REPOSITORY']).parent
receipt=json.loads((root/'staging/source_recovery_03_archive.json').read_text())
assert hashlib.sha256((root/'staging/source_recovery_03.tar.gz').read_bytes()).hexdigest()==receipt['archive_sha256']
PY
tar -xzf "$run_root/staging/source_recovery_03.tar.gz" -C "$run_root/recovered_sources_03"
"$ATLAS_ENV_DIR/bin/python" scripts/illumina_poster_inventory.py \
 --sources "$run_root/sources_03/sources.json" --inventory "$run_root/sources_03/inventory.json" --output "$run_root/poster_index_02"
"$ATLAS_ENV_DIR/bin/python" scripts/illumina_cached_sources.py \
 --sources "$run_root/sources_03/sources.json" --workspace "$ATLAS_PROJECT_ROOT" \
 --root "$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/migration_to_xiaojie_20261019/validation_48532750" \
 --root "$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/deployment_20261018/source_acquisition_bulk_01" \
 --root "$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/deployment_20261018/source_recovery_doi_01" \
 --output "$run_root/cache_recovery_01"
"$ATLAS_ENV_DIR/bin/python" scripts/illumina_merge_sources.py \
 --source-snapshot "$run_root/sources_03/sources.json" \
 --recovery-bundle "$run_root/recovered_sources_03/external_recovery_03/recovery_bundle.json" \
 --cached-recoveries "$run_root/cache_recovery_01/recoveries.json" \
 --path-map "/Users/mihajlostojkovic/Documents/qiu_lab/agents_snRNA/sequencing-technology-atlas/provenance/experiments/illumina_full_20261031/sources=$run_root/recovered_sources_03" \
 --path-map "/Users/mihajlostojkovic/Documents/qiu_lab/agents_snRNA=$ATLAS_PROJECT_ROOT" \
 --lookup-root "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas" --output "$run_root/sources_04"
"$ATLAS_ENV_DIR/bin/python" - <<'PY' > "$run_root/logs/all-source-preflight-$SLURM_JOB_ID.json"
import json,os
from collections import Counter
from pathlib import Path
from genomics_discovery.curation.illumina_batch import card_sources,read
root=Path(os.environ['ATLAS_REPOSITORY']).parent/'sources_04'
sources,inventory=read(root/'sources.json'),read(root/'inventory.json')
rows=[]
for card in inventory['cards']:
 registry,images=card_sources(card,sources,root)
 assert images,card['record_id']
 rows.append({'record_id':card['record_id'],'images':len(images),'levels':dict(Counter(s['content_level'] for s in registry.values()))})
print(json.dumps({'cards_preflighted':len(rows),'cards':rows},indent=2))
PY
if "$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.illumina_batch run \
 "$run_root/sources_04/sources.json" --output "$run_root/all_cards_01" --workers 12 --max-revisions 1; then
 batch_exit=0
else
 batch_exit=$?
fi
printf '%s\n' "$batch_exit" > "$run_root/logs/all-cards-native-exit-$SLURM_JOB_ID.txt"
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.illumina_graph build \
 "$run_root/all_cards_01" "$run_root/sources_04/inventory.json" --output "$run_root/architecture_atlas_01.json"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import json,os
from pathlib import Path
from genomics_discovery.curation.illumina_batch import read
from genomics_discovery.curation.illumina_graph import protocol_text
root=Path(os.environ['ATLAS_REPOSITORY']).parent
state=read(root/'all_cards_01/state.json')
output=root/'protocol_views_01'
output.mkdir()
for ident,row in state['cards'].items():
 graph=Path(row.get('attempt_directory',''))/'graph.json'
 if graph.is_file(): (output/(ident+'.txt')).write_text(protocol_text(read(graph)))
(root/'batch_summary_01.json').write_text(json.dumps({'counts':state['counts'],'cards':len(state['cards']),
 'model':state['policy']['model'],'effort':state['policy']['effort'],'finished_at':state.get('finished_at'),
 'scope':'Catalogue cards plus explicitly recovered MDA diagram. Poster-only methods tracked separately; no automatic ancestry or equivalence.'},indent=2)+'\n')
print((root/'batch_summary_01.json').read_text())
PY

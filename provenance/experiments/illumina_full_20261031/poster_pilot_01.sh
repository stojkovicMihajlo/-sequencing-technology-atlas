#!/usr/bin/env bash
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Run inside Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
run_root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
export ATLAS_REPOSITORY="$run_root/runtime_07"
[[ ! -e "$ATLAS_REPOSITORY" ]] || { echo 'Runtime already exists; preserve it.' >&2; exit 2; }
[[ "$(sha256sum "$run_root/staging/runtime_07.tar.gz" | cut -d ' ' -f1)" == 6677d1254365e191c23fe84ed4c685ff13d90c3aaccae4ec3dfe31b9e83bf662 ]]
mkdir "$ATLAS_REPOSITORY"
tar -xzf "$run_root/staging/runtime_07.tar.gz" -C "$ATLAS_REPOSITORY"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
image_dependencies="$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$image_dependencies"
export OMP_NUM_THREADS=1
export CODEX_SQLITE_HOME="$SCRATCH/atlas-codex-runtime-20261031/sqlite"
export TMPDIR="${L_SCRATCH_JOB:?}/poster-pilot-tmp"
mkdir -p "$TMPDIR"
cd "$ATLAS_REPOSITORY"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q tests/test_illumina*.py > "$run_root/logs/poster-software-$SLURM_JOB_ID.txt"
"$ATLAS_ENV_DIR/bin/python" scripts/illumina_poster_sources.py \
 --overlay "$run_root/poster_citation_runtime_01/proposals.json" \
 --base-sources "$run_root/sources_04/sources.json" --asset-root "$run_root/sources_02/assets" \
 --resolutions "$run_root/poster_paper_sources_01/poster_paper_candidates.json" --output "$run_root/poster_sources_01"
"$ATLAS_ENV_DIR/bin/python" - <<'PY' > "$run_root/logs/poster-source-preflight-$SLURM_JOB_ID.json"
import json,os
from collections import Counter
from pathlib import Path
from genomics_discovery.curation.illumina_batch import card_sources,read
root=Path(os.environ['ATLAS_REPOSITORY']).parent/'poster_sources_01'
sources,inventory=read(root/'sources.json'),read(root/'inventory.json')
rows=[]
for card in inventory['cards']:
 registry,images=card_sources(card,sources,root)
 assert images,card['record_id']
 rows.append({'id':card['record_id'],'title':card['source_title'],'levels':dict(Counter(s['content_level'] for s in registry.values()))})
print(json.dumps({'cards_preflighted':len(rows),'cards':rows},indent=2))
PY
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.illumina_batch run \
 "$run_root/poster_sources_01/sources.json" --output "$run_root/poster_pilot_01" --workers 2 \
 --record poster_059c5fc2446bb98d12f9f44d --record poster_6458f4821cc17b04f7aa6b30 --max-revisions 1

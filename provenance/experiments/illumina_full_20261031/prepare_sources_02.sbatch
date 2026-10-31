#!/usr/bin/env bash
#SBATCH --job-name=illumina-sources
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=04:00:00
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Submit through Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
source "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas/scripts/sherlock/environment.sh"
root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
mkdir -p "$root/logs" "$root/source_runtime_02"
tar -xzf "$root/staging/source_preparation_02.tar.gz" -C "$root/source_runtime_02"
sha256sum "$root/staging/source_preparation_02.tar.gz" > "$root/source_runtime_02/bundle.sha256"
cd "$root/source_runtime_02"

# Public-source acquisition has no dependency on Codex or writes to HOME.
"$ATLAS_ENV_DIR/bin/python" illumina_sources.py inventory methods.jsonl \
    --selector selector.js --output "$root/inventory_02.json"
set +e
"$ATLAS_ENV_DIR/bin/python" illumina_sources.py acquire "$root/inventory_02.json" \
    --output "$root/sources_02" --workers 4 --metadata
acquisition_exit=$?
set -e
printf '%s\n' "$acquisition_exit" > "$root/logs/source-acquisition-02-exit.txt"
mkdir -p "$root/collection_text_02"
for document in "$root/sources_02/assets/"*.pdf; do
    [[ -f "$document" ]] || continue
    pdftotext -layout "$document" "$root/collection_text_02/$(basename "$document" .pdf).txt" || true
done
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import json, os
from pathlib import Path
root=Path(os.environ['ATLAS_PROJECT_ROOT'])/'technology_atlas/v2/work/illumina_full_20261031'
state=json.loads((root/'sources_02/sources.json').read_text())
print(json.dumps({'status':state['status'],'statistics':state.get('statistics')},indent=2))
PY

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
mkdir -p "$root/logs" "$root/source_runtime_01"
tar -xzf "$root/staging/source_preparation_01.tar.gz" -C "$root/source_runtime_01"
sha256sum "$root/staging/source_preparation_01.tar.gz" > "$root/source_runtime_01/bundle.sha256"

# Only disposable Codex cache/tmp are relocated. Authentication stays in HOME.
# A private user-scratch parent protects cache ownership from group access.
private_runtime="$SCRATCH/atlas-codex-runtime-20261031"
umask 077
mkdir -p "$private_runtime"
chmod 700 "$private_runtime"
if ! pgrep -u "$USER" -x codex >/dev/null; then
    for name in tmp cache; do
        origin="$HOME/.codex/$name"
        target="$private_runtime/$name"
        if [[ -d "$origin" && ! -L "$origin" && ! -e "$target" ]]; then
            mv "$origin" "$target"
            ln -s "$target" "$origin"
        fi
    done
else
    echo 'Existing Codex process detected; cache relocation deferred.' >&2
fi
umask 022
ls -ld "$HOME/.codex/tmp" "$HOME/.codex/cache" > "$root/logs/codex-cache-location.txt"

cd "$root/source_runtime_01"
"$ATLAS_ENV_DIR/bin/python" illumina_sources.py inventory methods.jsonl \
    --selector selector.js --output "$root/inventory_01.json"
set +e
"$ATLAS_ENV_DIR/bin/python" illumina_sources.py acquire "$root/inventory_01.json" \
    --output "$root/sources_01" --workers 4 --metadata --fulltext --fulltext-workers 3
acquisition_exit=$?
set -e
printf '%s\n' "$acquisition_exit" > "$root/logs/source-acquisition-exit.txt"

# Preserve actual resources for poster-row coverage assessment.
mkdir -p "$root/collection_text"
for document in "$root/sources_01/assets/"*.pdf; do
    [[ -f "$document" ]] || continue
    pdftotext -layout "$document" "$root/collection_text/$(basename "$document" .pdf).txt" || true
done
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import json, os
from pathlib import Path
root=Path(os.environ['ATLAS_PROJECT_ROOT'])/'technology_atlas/v2/work/illumina_full_20261031'
state=json.loads((root/'sources_01/sources.json').read_text())
print(json.dumps({'status':state['status'],'statistics':state.get('statistics')},indent=2))
PY

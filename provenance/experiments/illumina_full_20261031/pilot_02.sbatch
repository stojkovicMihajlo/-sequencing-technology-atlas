#!/usr/bin/env bash
#SBATCH --job-name=illumina-diagram-pilot
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --time=03:00:00
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Submit through Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
export ATLAS_REPOSITORY="$root/pilot_runtime_02"
mkdir -p "$ATLAS_REPOSITORY"
tar -xzf "$root/staging/runtime_02.tar.gz" -C "$ATLAS_REPOSITORY"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export OMP_NUM_THREADS=1
export PIP_CACHE_DIR="$root/disposable/pip-cache"
export XDG_CACHE_HOME="$root/disposable/xdg-cache"
export TMPDIR="$root/disposable/tmp-$SLURM_JOB_ID"
mkdir -p "$TMPDIR" "$root/logs"

# Relocate only the private Codex cache/temp directories. Never move login files.
private_runtime="$SCRATCH/atlas-codex-runtime-20261031"
umask 077
mkdir -p "$private_runtime"
chmod 700 "$private_runtime"
if pgrep -u "$USER" -x codex >/dev/null; then
    echo 'Codex is active; do not move its cache while in use.' >&2
    exit 4
fi
for name in cache tmp; do
    origin="$HOME/.codex/$name"
    target="$private_runtime/$name"
    if [[ -d "$origin" && ! -L "$origin" && ! -e "$target" ]]; then
        mv "$origin" "$target"
    fi
done
# Create links only after both directory moves have released HOME space.
for name in cache tmp; do
    origin="$HOME/.codex/$name"
    target="$private_runtime/$name"
    mkdir -p "$target"
    if [[ ! -e "$origin" && ! -L "$origin" ]]; then ln -s "$target" "$origin"; fi
done
export CODEX_SQLITE_HOME="$private_runtime/sqlite"
mkdir -p "$CODEX_SQLITE_HOME"
umask 022
ls -ld "$HOME/.codex/tmp" "$HOME/.codex/cache" > "$root/logs/codex-cache-location-$SLURM_JOB_ID.txt"

# Inspect module availability before installing an isolated image dependency.
module spider py-pillow > "$root/logs/pillow-module-$SLURM_JOB_ID.txt" 2>&1 || true
image_dependencies="$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
if [[ ! -d "$image_dependencies/PIL" ]]; then
    uv pip install --python "$ATLAS_ENV_DIR/bin/python" --only-binary=:all: --target "$image_dependencies" 'Pillow==12.0.0'
fi
export PYTHONPATH="$ATLAS_REPOSITORY/src:$image_dependencies"
cd "$ATLAS_REPOSITORY"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q tests/test_illumina_diagrams.py tests/test_illumina_review_controls.py \
    tests/test_illumina_batch_controls.py tests/test_illumina_sources.py > "$root/logs/pilot-software-$SLURM_JOB_ID.txt"
"$ATLAS_ENV_DIR/bin/python" - <<'PY' > "$root/logs/pilot-environment-$SLURM_JOB_ID.json"
import json, os, PIL
from genomics_discovery.curation.codex_worker import preflight_pinned_shell_environment
print(json.dumps({'slurm_job_id':os.environ['SLURM_JOB_ID'],'pillow':PIL.__version__,
 'shell':preflight_pinned_shell_environment()},indent=2))
PY
codex --version
codex login status
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.illumina_batch run \
    "$root/sources_03/sources.json" --output "$root/pilot_02" --workers 2 \
    --record clickseq --record g-t-seq --max-revisions 1

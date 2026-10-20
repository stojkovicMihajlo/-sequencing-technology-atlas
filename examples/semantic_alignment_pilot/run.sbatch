#!/usr/bin/env bash
#SBATCH --job-name=atlas-alignment-23
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=02:00:00
set -euo pipefail
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
experiment="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/qiu_experiments_20261019/semantic_alignment_23"
export ATLAS_REPOSITORY="$experiment/candidate"
source "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas/scripts/sherlock/environment.sh"
export ATLAS_ENV_DIR="$GROUP_HOME/mstojkov/envs/atlas-alignment-20261020-py312"
export UV_PROJECT_ENVIRONMENT="$ATLAS_ENV_DIR"
export XDG_CACHE_HOME="$SCRATCH/atlas-alignment-cache/xdg"
export HF_HOME="$SCRATCH/atlas-alignment-cache/huggingface"
export PIP_CACHE_DIR="$SCRATCH/atlas-alignment-cache/pip"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src"
export OMP_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
cd "$ATLAS_REPOSITORY"
archive_result() {
    result_code=$?
    trap - EXIT
    printf '%s\n' "$result_code" > "$experiment/job_exit_code.txt"
    archive="$L_SCRATCH_JOB/semantic_alignment_23-job-$SLURM_JOB_ID.tar.gz"
    tar --exclude='semantic_alignment_23/semantic_cache' --exclude='__pycache__' --exclude='.pytest_cache' -czf "$archive" -C "$(dirname "$experiment")" semantic_alignment_23
    durable="$GROUP_HOME/mstojkov/atlas-results"
    mkdir -p "$durable"
    cp "$archive" "$durable/"
    sha256sum "$durable/$(basename "$archive")" > "$experiment/durable_archive.sha256"
    cp "$experiment/durable_archive.sha256" "$durable/$(basename "$archive").sha256"
    exit "$result_code"
}
trap archive_result EXIT
hostname
codex --version
codex login status
uv sync --frozen --extra dev --extra semantic --python 3.12.1 --no-managed-python
uv pip freeze --python "$ATLAS_ENV_DIR/bin/python" > "$experiment/environment.txt"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q --junitxml="$experiment/tests.xml" > "$experiment/tests.log" 2>&1
"$ATLAS_ENV_DIR/bin/python" - "$experiment" <<'PYSETUP'
import json,sys,time
from pathlib import Path
from genomics_discovery.curation.technology_graph.embeddings import LocalEmbedder,EmbeddingCache
experiment=Path(sys.argv[1])
cache=experiment/'semantic_cache'
cache.mkdir(exist_ok=True)
t=time.monotonic()
backend=LocalEmbedder(cache/'models',download=True)
embeddings=EmbeddingCache(cache/'vectors.sqlite3',backend)
values=embeddings.encode(['Source-bound operation description for a semantic retrieval smoke check.'])
(experiment/'embedding_setup.json').write_text(json.dumps({'metadata':embeddings.metadata,'namespace':embeddings.namespace,'dimensions':len(values[0]),'startup_seconds':time.monotonic()-t,'boundary':'Embedding availability only, not scientific matching validation.'},indent=2)+'\n')
PYSETUP
"$ATLAS_ENV_DIR/bin/python" "$experiment/run.py" --project-root "$ATLAS_PROJECT_ROOT" --run technology_atlas/v2/work/qiu_experiments_20261019/semantic_alignment_23/live --semantic-cache "$experiment/semantic_cache" > "$experiment/live.log" 2>&1

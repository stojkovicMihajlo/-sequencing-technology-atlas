#!/usr/bin/env bash
#SBATCH --job-name=atlas-alignment-23d
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=02:00:00
set -euo pipefail
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
experiment="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/qiu_experiments_20261019/semantic_alignment_23"
attempt="$experiment/container_run_02"
export ATLAS_REPOSITORY="$experiment/candidate"
source "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY:$ATLAS_REPOSITORY/src"
export OMP_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
cd "$ATLAS_REPOSITORY"
archive_result() {
    result_code=$?
    trap - EXIT
    printf '%s\n' "$result_code" > "$attempt/job_exit_code-$SLURM_JOB_ID.txt"
    archive="$L_SCRATCH_JOB/semantic_alignment_23-job-$SLURM_JOB_ID.tar.gz"
    tar --exclude='*/semantic_cache' --exclude='__pycache__' --exclude='.pytest_cache' -czf "$archive" -C "$(dirname "$experiment")" semantic_alignment_23
    durable="$GROUP_HOME/mstojkov/atlas-results"
    mkdir -p "$durable"
    cp "$archive" "$durable/"
    sha256sum "$durable/$(basename "$archive")" > "$attempt/durable_archive-$SLURM_JOB_ID.sha256"
    cp "$attempt/durable_archive-$SLURM_JOB_ID.sha256" "$durable/$(basename "$archive").sha256"
    exit "$result_code"
}
trap archive_result EXIT
hostname
codex --version
codex login status
# Exact frozen candidate already passed all1592 cases in attempt01; preserve and bind it.
sha256sum "$experiment/container_run_01/tests.xml" > "$attempt/prior_linux_tests.sha256"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q "$attempt/test_container_embeddings.py" > "$attempt/bridge_tests.log" 2>&1
image="$L_SCRATCH_JOB/atlas.sif"
cp "$GROUP_HOME/mstojkov/software/atlas/20261019-01/atlas.sif" "$image"
sha256sum "$image" > "$attempt/container_image.sha256"
test "$(cut -d' ' -f1 "$attempt/container_image.sha256")" = "$(cut -d' ' -f1 "$experiment/container_run_01/container_image.sha256")"
cmp "$experiment/container_run_01/semantic_requirements.txt" "$attempt/semantic_requirements.txt"
semantic_env="$GROUP_HOME/mstojkov/envs/atlas-alignment-container-20261020"
mkdir -p "$semantic_env" "$attempt/semantic_cache/models" "$L_SCRATCH_JOB/embedding_tmp"
base=(apptainer exec --cleanenv --containall --no-home --pwd /tmp
      --bind /etc/agents:/etc/agents:ro
      --bind "$semantic_env:/opt/semantic:ro"
      --bind "$L_SCRATCH_JOB/embedding_tmp:/tmp:rw"
      --env HF_HOME=/tmp/huggingface --env HF_XET_CACHE=/tmp/xet
      --env XDG_CACHE_HOME=/tmp/xdg)
# Do not rewrite the completed environment from attempt01.
test -f "$semantic_env/pyvenv.cfg"
"${base[@]}" "$image" /opt/semantic/bin/python -m pip freeze > "$attempt/semantic_environment.txt"
cmp "$experiment/container_run_01/semantic_environment.txt" "$attempt/semantic_environment.txt"
"${base[@]}" --bind "$ATLAS_REPOSITORY:/atlas:ro" --bind "$attempt/semantic_cache/models:/models:rw" --env PYTHONPATH=/atlas/src "$image" /opt/semantic/bin/python -c 'from genomics_discovery.curation.technology_graph.embeddings import LocalEmbedder; import json; b=LocalEmbedder(__import__("pathlib").Path("/models"),download=True); root=__import__("pathlib").Path("/models"); m=__import__("pathlib").Path(b.model.model._model_dir).resolve(); assert m.is_relative_to(root); assert all(p.resolve().is_relative_to(root) for p in m.rglob("*") if p.is_file()); print(json.dumps({"metadata":b.metadata,"model_root":str(m),"all_artifacts_persist_under_models":True}))' > "$attempt/embedding_model.json"
"$ATLAS_ENV_DIR/bin/python" - "$attempt" "$image" "$semantic_env" "$ATLAS_REPOSITORY" <<'PYSETUP'
import json,sys,time
from pathlib import Path
attempt,image,env,code=map(Path,sys.argv[1:])
sys.path.insert(0,str(attempt))
from container_embeddings import ContainerEmbedder
from genomics_discovery.curation.technology_graph.embeddings import EmbeddingCache
from genomics_discovery.curation.operation_registry.semantic_candidates import retrieve
from genomics_discovery.curation.workflow_extraction.models import digest
t=time.monotonic()
backend=ContainerEmbedder(image,env,code,attempt/'semantic_cache/models')
cache=EmbeddingCache(attempt/'semantic_cache/vectors.sqlite3',backend)
# One archived registry definition and an ID/name-adapted mechanical neighbor.
# This exercises the real backend, not biology or semantic discrimination.
catalogue=json.loads((code/'examples/frozen_matching/frozen_catalogue.json').read_text())
query=next(iter(catalogue['concepts'].values()))
other=json.loads(json.dumps(query));other['definition']['id']='embedding_smoke_other';other['definition']['name']='Embedding backend verification neighbor'
routes,ranking=retrieve(query,{'neighbor':other},{},cache,1)
assert routes.get('neighbor') and digest(other) in ranking
(attempt/'embedding_setup.json').write_text(json.dumps({'metadata':cache.metadata,'namespace':cache.namespace,'retrieved_routes':routes,'ranking':ranking,'cache_stats':cache.last_stats,'startup_seconds':time.monotonic()-t,'boundary':'Real container embedding/host retrieval smoke test on one synthetic neighbor; no scientific identity or recall assessment.'},indent=2)+'\n')
PYSETUP
"$ATLAS_ENV_DIR/bin/python" "$attempt/run.py" --project-root "$ATLAS_PROJECT_ROOT" --run technology_atlas/v2/work/qiu_experiments_20261019/semantic_alignment_23/live_container_02 --semantic-cache "$attempt/semantic_cache" --embedding-image "$image" --embedding-environment "$semantic_env" --embedding-code-root "$ATLAS_REPOSITORY" > "$attempt/live.log" 2>&1

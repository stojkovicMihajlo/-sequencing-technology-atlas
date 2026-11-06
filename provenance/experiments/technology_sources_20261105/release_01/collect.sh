#!/bin/bash
#SBATCH --job-name=method-source-collect
#SBATCH --partition=xiaojie
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
set -eo pipefail
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
ml devel python/3.12.1 system poppler/0.47.0
source "$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312/bin/activate"
atlas_project="$GROUP_SCRATCH/mstojkov/agents_snRNA"
atlas_stage="$atlas_project/technology_atlas/v2/work/technology_sources_20261105/source_library_release_01"
cd "$atlas_stage/runtime_01"
export PYTHONPATH="$PWD/src:$PWD" PYTHONDONTWRITEBYTECODE=1
atlas_review="$PWD/provenance/experiments/technology_sources_20261105/identity_review"
python -m pytest -q -p no:cacheprovider tests/illumina/test_technology_source_curation.py tests/illumina/test_technology_source_collection.py
python -m scripts.illumina.technology_sources.review_papers \
 --source-root "$atlas_project/technology_sources/illumina_all_paper_review_20261105" \
 --output "$atlas_project/technology_sources/illumina_selected_sources_20261105" \
 --decisions "$atlas_review/decisions.json" --bindings "$atlas_review/reference_bindings.json"
python -m scripts.illumina.technology_sources.literature_search \
 --plan "$PWD/provenance/experiments/technology_sources_20261105/post_2016_search_plan.json" \
 --output "$atlas_stage/post_2016_discovery" > "$atlas_stage/literature_search.log" 2>&1 &
atlas_search_pid=$!
python -m scripts.illumina.technology_sources.recover_selected \
 --root "$atlas_project/technology_sources/illumina_selected_sources_20261105" \
 --holds "$atlas_review/progress/SOURCE_HOLDS.json"
wait "$atlas_search_pid"
atlas_backup="$GROUP_HOME/mstojkov/atlas-results/technology_sources_20261105"
mkdir -p "$atlas_backup"
tar -czf "$atlas_stage/source_collection_${SLURM_JOB_ID}.tar.gz" -C "$atlas_project" \
 technology_sources/illumina_selected_sources_20261105 \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/post_2016_discovery \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/runtime_01.tar.gz \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/collect.sh
cp "$atlas_stage/source_collection_${SLURM_JOB_ID}.tar.gz" "$atlas_backup/"
sha256sum "$atlas_backup/source_collection_${SLURM_JOB_ID}.tar.gz" > "$atlas_stage/backup.sha256"
printf 'Selected method source collection and discovery search completed. No model calls.\n'

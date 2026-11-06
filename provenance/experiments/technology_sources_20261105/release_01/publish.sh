#!/bin/bash
#SBATCH --job-name=method-library-publish
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
cd "$atlas_stage/publication_runtime_01"
export PYTHONPATH="$PWD/src:$PWD" PYTHONDONTWRITEBYTECODE=1
atlas_review="$PWD/provenance/experiments/technology_sources_20261105/identity_review"
python -m pytest -q -p no:cacheprovider tests/illumina/test_technology_source_curation.py tests/illumina/test_technology_source_collection.py
python -m scripts.illumina.technology_sources.publish_curated \
 --source-root "$atlas_project/technology_sources/illumina_selected_sources_20261105" \
 --output "$atlas_project/technology_sources/illumina_library_20261105" \
 --decisions "$atlas_review/decisions.json" --bindings "$atlas_review/reference_bindings.json" \
 --source-holds "$atlas_review/progress/SOURCE_HOLDS.json" \
 --category-assignments "$atlas_review/category_assignments.json"
if [[ ! -e "$atlas_project/technology_sources/library" && ! -L "$atlas_project/technology_sources/library" ]]; then
 ln -s illumina_library_20261105 "$atlas_project/technology_sources/library"
fi
python -m scripts.illumina.technology_sources.poster_check \
 --source-root "$atlas_project/technology_sources/illumina_selected_sources_20261105" \
 --decisions "$atlas_review/decisions.json" --bindings "$atlas_review/reference_bindings.json" \
 --output "$atlas_stage/poster_inspection_02"
atlas_backup="$GROUP_HOME/mstojkov/atlas-results/technology_sources_20261105"
mkdir -p "$atlas_backup"
tar -czf "$atlas_stage/library_${SLURM_JOB_ID}.tar.gz" -C "$atlas_project" \
 technology_sources/illumina_library_20261105 \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/poster_inspection_02 \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/publication_runtime_01.tar.gz \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/publish.sh
cp "$atlas_stage/library_${SLURM_JOB_ID}.tar.gz" "$atlas_backup/"
sha256sum "$atlas_backup/library_${SLURM_JOB_ID}.tar.gz" > "$atlas_stage/library_backup.sha256"
printf 'Nested method-folder publication and original-poster row rendering completed. Visual inspection remains separately recorded.\n'

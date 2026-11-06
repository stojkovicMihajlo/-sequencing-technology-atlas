#!/bin/bash
#SBATCH --job-name=atlas-task2-sources
#SBATCH --partition=xiaojie,service,normal
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
set -eo pipefail
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
ml devel python/3.12.1 system poppler/0.47.0
source "$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312/bin/activate"
atlas_project="$GROUP_SCRATCH/mstojkov/agents_snRNA"
atlas_stage="$atlas_project/technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/task2_03"
cd "$atlas_stage/runtime"
export PYTHONPATH="$PWD/src:$PWD" PYTHONDONTWRITEBYTECODE=1
atlas_review="$PWD/provenance/experiments/technology_sources_20261105/identity_review"
atlas_inputs="$PWD/provenance/experiments/technology_sources_20261105/release_01"
python -m pytest -q -p no:cacheprovider tests/illumina/test_technology_source_curation.py tests/illumina/test_technology_source_collection.py tests/illumina/test_illumina_external_sources.py
python -m scripts.illumina.technology_sources.review_papers \
 --source-root "$atlas_project/technology_sources/illumina_library_sources_20261105" \
 --output "$atlas_project/technology_sources/illumina_task2_sources_20261105_03" \
 --decisions "$atlas_review/decisions.json" --bindings "$atlas_inputs/reference_bindings_with_protocol.json"
python -m scripts.illumina.technology_sources.pmc_cloud \
 --root "$atlas_project/technology_sources/illumina_task2_sources_20261105_03" \
 --holds "$atlas_review/progress/SOURCE_HOLDS.json"
atlas_backup="$GROUP_HOME/mstojkov/atlas-results/technology_sources_20261105"
tar -czf "$atlas_stage/source_recovery_${SLURM_JOB_ID}.tar.gz" -C "$atlas_project" \
 technology_sources/illumina_task2_sources_20261105_03 \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/task2_03/runtime.tar.gz \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/task2_03/collect.sh
cp "$atlas_stage/source_recovery_${SLURM_JOB_ID}.tar.gz" "$atlas_backup/"
sha256sum "$atlas_backup/source_recovery_${SLURM_JOB_ID}.tar.gz" > "$atlas_stage/backup.sha256"
printf 'Assigned-paper recovery finished; source QA and publication remain separate.\n'

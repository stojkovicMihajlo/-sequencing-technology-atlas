#!/bin/bash
set -eo pipefail
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
ml devel python/3.12.1 system poppler/0.47.0
atlas_project="$GROUP_SCRATCH/mstojkov/agents_snRNA"
atlas_stage="$atlas_project/technology_atlas/v2/work/technology_sources_20261105/source_library_release_01"
cd "$atlas_stage/inspection_runtime_01"
export PYTHONPATH="$PWD/src:$PWD" PYTHONDONTWRITEBYTECODE=1
atlas_review="$atlas_stage/runtime_01/provenance/experiments/technology_sources_20261105/identity_review"
python -m scripts.illumina.technology_sources.poster_check \
 --source-root "$atlas_project/technology_sources/illumina_all_paper_review_20261105" \
 --decisions "$atlas_review/decisions.json" --bindings "$atlas_review/reference_bindings.json" \
 --output "$atlas_stage/poster_inspection_01"

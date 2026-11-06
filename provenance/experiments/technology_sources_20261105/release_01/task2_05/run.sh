#!/bin/bash
#SBATCH --job-name=atlas-task2-cache-audit
#SBATCH --partition=xiaojie,service,normal
#SBATCH --time=00:45:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
set -eo pipefail
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
ml devel python/3.12.1 system poppler/0.47.0
source "$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312/bin/activate"
atlas_project="$GROUP_SCRATCH/mstojkov/agents_snRNA"
atlas_stage="$atlas_project/technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/task2_05"
cd "$atlas_stage/runtime"
export PYTHONPATH="$PWD/src:$PWD" PYTHONDONTWRITEBYTECODE=1
atlas_inputs="$PWD/provenance/experiments/technology_sources_20261105/release_01"
atlas_review="$atlas_inputs/../identity_review"
atlas_task="$atlas_stage/records"
atlas_prior_task="$atlas_project/technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/task2_04/records"
atlas_sources="$atlas_project/technology_sources/illumina_task2_sources_20261105_06"
atlas_view="$atlas_project/technology_sources/illumina_library_20261105_05"
atlas_previous="$atlas_project/technology_sources/illumina_library_20261105_04"
atlas_prior_sources="$atlas_project/technology_sources/illumina_task2_sources_20261105_05"
python -m pytest -q -p no:cacheprovider tests/illumina/test_technology_source_curation.py \
 tests/illumina/test_technology_source_collection.py tests/illumina/test_illumina_external_sources.py \
 tests/illumina/test_source_recovery_cache.py tests/illumina/test_repository_recovery_attempts.py
python "$atlas_inputs/task2_05/prepare_cached_sources.py" --project "$atlas_project" \
 --source-root "$atlas_prior_sources" --output "$atlas_sources" \
 --decisions "$atlas_review/decisions.json" --bindings "$atlas_prior_task/reference_bindings.json" \
 --scope-reviews "$atlas_inputs/task2_05/CACHED_PAPER_SCOPE_REVIEWS.json" --records "$atlas_task"
cp "$atlas_prior_task/author_protocols.json" "$atlas_task/"
python -m scripts.illumina.technology_sources.publish_curated \
 --source-root "$atlas_sources" --output "$atlas_view" \
 --decisions "$atlas_review/decisions.json" --bindings "$atlas_task/reference_bindings.json" \
 --source-holds "$atlas_review/progress/SOURCE_HOLDS.json" \
 --category-assignments "$atlas_inputs/task2_03/category_assignments.json" \
 --source-quality-review "$atlas_inputs/SOURCE_QUALITY_REVIEW.json" \
 --description-reviews "$atlas_inputs/task2_03/description_reviews.json" \
 --poster-only-reviews "$atlas_prior_task/POSTER_ONLY_REVIEW.json"
python "$atlas_inputs/task2_03/package_sources.py" --root "$atlas_view" --source-root "$atlas_sources"
python "$atlas_inputs/task2_04/recover_sources.py" finish --project "$atlas_project" \
 --inputs "$atlas_inputs" --task "$atlas_task" --previous-sources "$atlas_prior_sources" \
 --previous-view "$atlas_previous" --sources "$atlas_sources" --view "$atlas_view"
atlas_archive="$atlas_stage/task2_cache_${SLURM_JOB_ID}.tar.gz"
tar -czf "$atlas_archive" -C "$atlas_project" \
 technology_sources/illumina_task2_sources_20261105_06 technology_sources/illumina_library_20261105_05 \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/task2_05/records \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/task2_05/runtime.tar.gz \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/task2_05/run.sh
atlas_backup="$GROUP_HOME/mstojkov/atlas-results/technology_sources_20261105"
cp "$atlas_archive" "$atlas_backup/"
sha256sum "$atlas_backup/task2_cache_${SLURM_JOB_ID}.tar.gz" > "$atlas_stage/backup.sha256"
python - "$atlas_project/technology_sources" "$atlas_stage" <<'PY'
import json,os,sys
from pathlib import Path
r,stage=map(Path,sys.argv[1:]);pointer=r/'library';target=r/'illumina_library_20261105_05'
s=json.loads((target/'TASK2_SUMMARY.json').read_text())
assert s['named_method_packages']==273 and s['poster_only_packages']==99
assert s['methods_with_downloaded_method_paper']>=227
assert json.loads((target/'SUMMARY.json').read_text())['validation']['passed']
if pointer.resolve()!=r/'illumina_library_20261105_04':
 raise ValueError('The live library changed; do not overwrite another release')
t=r/'library.task2-05';t.symlink_to(target.name);os.replace(t,pointer)
(stage/'PUBLICATION_RECEIPT.json').write_text(json.dumps({'snapshot':str(target),
 'summary':s,'durable_backup':(stage/'backup.sha256').read_text()},indent=2)+'\n')
print('PUBLISHED',target,flush=True)
PY

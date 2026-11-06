#!/bin/bash
#SBATCH --job-name=atlas-task2-package
#SBATCH --partition=xiaojie,service,normal
#SBATCH --time=01:30:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
set -eo pipefail
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
ml devel python/3.12.1 system poppler/0.47.0
source "$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312/bin/activate"
atlas_project="$GROUP_SCRATCH/mstojkov/agents_snRNA"
atlas_stage="$atlas_project/technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/task2_03"
cd "$atlas_stage/publication_runtime"
export PYTHONPATH="$PWD/src:$PWD" PYTHONDONTWRITEBYTECODE=1
atlas_review="$PWD/provenance/experiments/technology_sources_20261105/identity_review"
atlas_inputs="$PWD/provenance/experiments/technology_sources_20261105/release_01"
atlas_task="$atlas_inputs/task2_03"
atlas_sources="$atlas_project/technology_sources/illumina_task2_sources_20261105_04"
atlas_view="$atlas_project/technology_sources/illumina_library_20261105_03"
python -m pytest -q -p no:cacheprovider tests/illumina/test_technology_source_curation.py tests/illumina/test_technology_source_collection.py tests/illumina/test_illumina_external_sources.py
python -m scripts.illumina.technology_sources.review_papers \
 --source-root "$atlas_project/technology_sources/illumina_task2_sources_20261105_03" \
 --output "$atlas_sources" \
 --decisions "$atlas_review/decisions.json" --bindings "$atlas_inputs/reference_bindings_with_protocol.json"
python "$atlas_task/recover_repositories.py" --root "$atlas_sources" \
 --targets "$atlas_project/technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/source_review_03/MISSING_ASSIGNED_PAPERS.json" \
 --holds "$atlas_review/progress/SOURCE_HOLDS.json" --additional-urls "$atlas_task/additional_urls.json"
python -m scripts.illumina.technology_sources.publish_curated \
 --source-root "$atlas_sources" --output "$atlas_view" \
 --decisions "$atlas_review/decisions.json" --bindings "$atlas_inputs/reference_bindings_with_protocol.json" \
 --source-holds "$atlas_review/progress/SOURCE_HOLDS.json" \
 --category-assignments "$atlas_task/category_assignments.json" \
 --source-quality-review "$atlas_inputs/SOURCE_QUALITY_REVIEW.json" \
 --description-reviews "$atlas_task/description_reviews.json" \
 --poster-only-reviews "$atlas_task/POSTER_ONLY_REVIEW.json"
python "$atlas_task/package_sources.py" --root "$atlas_view" --source-root "$atlas_sources"
atlas_backup="$GROUP_HOME/mstojkov/atlas-results/technology_sources_20261105"
tar -czf "$atlas_stage/task2_package_${SLURM_JOB_ID}.tar.gz" -C "$atlas_project" \
 technology_sources/illumina_task2_sources_20261105_04 \
 technology_sources/illumina_library_20261105_03 \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/task2_03/publication_runtime.tar.gz \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/task2_03/publish.sh
cp "$atlas_stage/task2_package_${SLURM_JOB_ID}.tar.gz" "$atlas_backup/"
sha256sum "$atlas_backup/task2_package_${SLURM_JOB_ID}.tar.gz" > "$atlas_stage/publication_backup.sha256"
python - "$atlas_project/technology_sources" <<'ENDPY'
import os,sys,json
from pathlib import Path
r=Path(sys.argv[1]);target=r/'illumina_library_20261105_03';pointer=r/'library'
x=json.loads((target/'TASK2_SUMMARY.json').read_text())
assert x['named_method_packages']==273 and x['poster_only_packages']==99
assert x['methods_with_downloaded_method_paper']>=215
assert json.loads((target/'SUMMARY.json').read_text())['validation']['passed']
if not pointer.is_symlink() or pointer.resolve()!=r/'illumina_library_20261105_02':
 raise ValueError('Active library changed; do not overwrite another publication')
temp=r/'library.task2-new';temp.symlink_to(target.name);os.replace(temp,pointer)
(r/'CURRENT_LIBRARY.txt').write_text(str(pointer)+'\nRead READ_FIRST.txt and SOURCE_PACKAGE_CHECKLIST.csv. Source packages are not extracted workflows.\n')
print('ACTIVE_LIBRARY',pointer,'->',target)
ENDPY

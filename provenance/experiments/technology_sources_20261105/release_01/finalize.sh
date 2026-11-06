#!/bin/bash
#SBATCH --job-name=method-library-finalize
#SBATCH --partition=xiaojie,service,normal
#SBATCH --time=01:30:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
set -eo pipefail
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
ml devel python/3.12.1 system poppler/0.47.0
source "$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312/bin/activate"
atlas_project="$GROUP_SCRATCH/mstojkov/agents_snRNA"
atlas_stage="$atlas_project/technology_atlas/v2/work/technology_sources_20261105/source_library_release_01"
cd "$atlas_stage/final_runtime_01"
export PYTHONPATH="$PWD/src:$PWD" PYTHONDONTWRITEBYTECODE=1
atlas_review="$PWD/provenance/experiments/technology_sources_20261105/identity_review"
atlas_inputs="$PWD/provenance/experiments/technology_sources_20261105/release_01"
python -m pytest -q -p no:cacheprovider tests/illumina/test_technology_source_curation.py tests/illumina/test_technology_source_collection.py tests/illumina/test_illumina_external_sources.py
python -m scripts.illumina.technology_sources.assemble_sources \
 --source-root "$atlas_project/technology_sources/illumina_selected_sources_20261105" \
 --output "$atlas_project/technology_sources/illumina_library_sources_20261105" \
 --decisions "$atlas_review/decisions.json" --bindings "$atlas_inputs/reference_bindings_with_protocol.json" \
 --inspection "$atlas_stage/poster_inspection_02" --additions "$atlas_inputs/source_additions.json" \
 --cache-index "$atlas_project/technology_atlas/v2/work/migration_to_xiaojie_20261019/validation_48532750/source_receipts.json" \
 --project-root "$atlas_project"
python -m scripts.illumina.technology_sources.publish_curated \
 --source-root "$atlas_project/technology_sources/illumina_library_sources_20261105" \
 --output "$atlas_project/technology_sources/illumina_library_20261105_02" \
 --decisions "$atlas_review/decisions.json" --bindings "$atlas_inputs/reference_bindings_with_protocol.json" \
 --source-holds "$atlas_review/progress/SOURCE_HOLDS.json" \
 --category-assignments "$atlas_review/category_assignments.json"
python - "$atlas_project/technology_sources" <<'ENDPY'
from pathlib import Path
import os,sys,json
root=Path(sys.argv[1]); pointer=root/'library'; target=root/'illumina_library_20261105_02'
if pointer.is_symlink():
 if pointer.resolve() not in [root/'illumina_library_20261105',target]: raise ValueError('Active library pointer belongs to another release')
elif pointer.exists(): raise ValueError('Refusing to replace a physical library directory')
tmp=root/'library.new'; tmp.symlink_to(target.name); os.replace(tmp,pointer)
(root/'CURRENT_LIBRARY.txt').write_text(str(pointer)+'\nOne canonical method folder under category/subclassification. See CHECKLIST.csv and MISSING_METHOD_PAPER_FILES.csv.\n')
print('ACTIVE_LIBRARY',str(pointer),'->',str(target))
ENDPY
atlas_backup="$GROUP_HOME/mstojkov/atlas-results/technology_sources_20261105"
mkdir -p "$atlas_backup"
tar -czf "$atlas_stage/final_library_${SLURM_JOB_ID}.tar.gz" -C "$atlas_project" \
 technology_sources/illumina_library_sources_20261105 \
 technology_sources/illumina_library_20261105_02 \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/final_runtime_01.tar.gz \
 technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/finalize.sh
cp "$atlas_stage/final_library_${SLURM_JOB_ID}.tar.gz" "$atlas_backup/"
sha256sum "$atlas_backup/final_library_${SLURM_JOB_ID}.tar.gz" > "$atlas_stage/final_library_backup.sha256"
printf 'Source folders finalized; exact scientific and acquisition limitations remain explicit in their records.\n'

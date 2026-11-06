#!/bin/bash
#SBATCH --job-name=atlas-remaining-oa-copies
#SBATCH --partition=xiaojie,service,normal
#SBATCH --time=01:15:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
set -eo pipefail
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
ml devel python/3.12.1 system poppler/0.47.0
source "$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312/bin/activate"
atlas_project="$GROUP_SCRATCH/mstojkov/agents_snRNA"
atlas_base="$atlas_project/technology_atlas/v2/work/technology_sources_20261105/source_library_release_01"
atlas_stage="$atlas_base/task2_08"
atlas_runtime="$atlas_base/task2_05/runtime"
atlas_inputs="$atlas_runtime/provenance/experiments/technology_sources_20261105/release_01"
atlas_prior="$atlas_base/task2_05/records"
atlas_sources="$atlas_project/technology_sources/illumina_task2_sources_20261105_09"
atlas_view="$atlas_project/technology_sources/illumina_library_20261105_08"
cd "$atlas_stage"
export PYTHONPATH="$atlas_runtime/src:$atlas_runtime" PYTHONDONTWRITEBYTECODE=1
python - "$atlas_stage/APPROVAL_STATUS.json" <<'APPROVAL'
import hashlib,json,os,sys
from pathlib import Path
record=json.loads(Path(sys.argv[1]).read_text())
if record.get('status')!='approved' or hashlib.sha256(os.environ.get('ATLAS_PUBLIC_ACCESS_CONTACT','').encode()).hexdigest()!=record.get('approved_contact_sha256'):
    raise SystemExit('Unpaywall contact-email disclosure has not been approved; no lookup will run.')
APPROVAL
python discover_unpaywall.py --source-root "$atlas_project/technology_sources/illumina_task2_sources_20261105_08" \
 --previous-view "$atlas_project/technology_sources/illumina_library_20261105_07" \
 --output "$atlas_stage/discovery" --match-module "$atlas_base/task2_07/recover_open_copies.py" \
 --email "${ATLAS_PUBLIC_ACCESS_CONTACT:?Requires explicit user approval before submitting this job}"
python "$atlas_base/task2_06/recover_remaining.py" --source-root "$atlas_project/technology_sources/illumina_task2_sources_20261105_08" \
 --output "$atlas_sources" --decisions "$atlas_inputs/../identity_review/decisions.json" \
 --bindings "$atlas_prior/reference_bindings.json" --targets "$atlas_stage/discovery/remaining_papers.json" \
 --prior-mapping "$atlas_stage/discovery/no_repeated_pmc_routes.json" \
 --records "$atlas_stage/records"
cp "$atlas_prior/reference_bindings.json" "$atlas_stage/records/"
cp "$atlas_prior/author_protocols.json" "$atlas_stage/records/"
python -m scripts.illumina.technology_sources.publish_curated --source-root "$atlas_sources" --output "$atlas_view" \
 --decisions "$atlas_inputs/../identity_review/decisions.json" --bindings "$atlas_stage/records/reference_bindings.json" \
 --source-holds "$atlas_inputs/../identity_review/progress/SOURCE_HOLDS.json" \
 --category-assignments "$atlas_inputs/task2_03/category_assignments.json" \
 --source-quality-review "$atlas_inputs/SOURCE_QUALITY_REVIEW.json" \
 --description-reviews "$atlas_inputs/task2_03/description_reviews.json" \
 --poster-only-reviews "$atlas_base/task2_04/records/POSTER_ONLY_REVIEW.json"
python "$atlas_inputs/task2_03/package_sources.py" --root "$atlas_view" --source-root "$atlas_sources"
python "$atlas_inputs/task2_04/recover_sources.py" finish --project "$atlas_project" \
 --inputs "$atlas_inputs" --task "$atlas_stage/records" \
 --previous-sources "$atlas_project/technology_sources/illumina_task2_sources_20261105_08" \
 --previous-view "$atlas_project/technology_sources/illumina_library_20261105_07" \
 --sources "$atlas_sources" --view "$atlas_view"
python - "$atlas_project" "$atlas_stage" "$GROUP_HOME/mstojkov/atlas-results/technology_sources_20261105" <<'PY'
import hashlib,json,os,shutil,sys,tarfile
from pathlib import Path
r,stage,backup=map(Path,sys.argv[1:])
source=r/'technology_sources/illumina_task2_sources_20261105_09'
view=r/'technology_sources/illumina_library_20261105_08'
summary=json.loads((view/'TASK2_SUMMARY.json').read_text())
assert summary['named_method_packages']==273 and summary['poster_only_packages']==99
assert summary['methods_with_downloaded_method_paper']>=236
assert summary['missing_assigned_publication_files']<=48
assert json.loads((view/'SUMMARY.json').read_text())['validation']['passed']
archive=stage/('remaining_papers_'+os.environ['SLURM_JOB_ID']+'.tar.gz')
with tarfile.open(archive,'w:gz',dereference=True) as tf:
    tf.add(source,arcname=str(source.relative_to(r)))
    tf.dereference=False
    tf.add(view,arcname=str(view.relative_to(r)))
    tf.add(stage/'records',arcname=str((stage/'records').relative_to(r)))
    for n in ['run.sh','discover_unpaywall.py','INPUTS.json','discovery']:
        tf.add(stage/n,arcname=str((stage/n).relative_to(r)))
state=json.loads((source/'collection.json').read_text())
expected={str(source.relative_to(r)/f['path']):f['sha256'] for f in state['files'].values()}
checked=set()
with tarfile.open(archive,'r|gz') as tf:
    for member in tf:
        if member.name not in expected:continue
        if not member.isfile():raise ValueError('Source backup contains an external link: '+member.name)
        stream=tf.extractfile(member);digest=hashlib.sha256()
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        if digest.hexdigest()!=expected[member.name]:raise ValueError('Backup source hash differs: '+member.name)
        checked.add(member.name)
if checked!=set(expected):raise ValueError('Incomplete original-file backup')
backup.mkdir(parents=True,exist_ok=True)
destination=backup/archive.name
if destination.exists():raise ValueError('Refusing to replace a durable archive')
shutil.copyfile(archive,destination)
def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
pin=digest(archive)
if digest(destination)!=pin:raise ValueError('Durable archive bytes differ')
pointer=r/'technology_sources/library'
if pointer.resolve()!=r/'technology_sources/illumina_library_20261105_07':
    raise ValueError('Live library changed; do not overwrite another publication')
tmp=pointer.with_name('library.task2-08');tmp.symlink_to(view.name);os.replace(tmp,pointer)
receipt={'summary':summary,'snapshot':str(view),'source_vault':str(source),
 'self_contained_archive':str(destination),'archive_sha256':pin,
 'archived_original_files_verified':len(checked),'new_model_calls':0,'task3_started':False}
(stage/'PUBLICATION_RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt),flush=True)
PY

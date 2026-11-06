#!/bin/bash
#SBATCH --job-name=illumina-card-source-audit
#SBATCH --partition=service,normal,xiaojie
#SBATCH --time=01:30:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
set -eo pipefail
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
ml devel python/3.12.1 system poppler/0.47.0
source "$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312/bin/activate"
atlas_project="$GROUP_SCRATCH/mstojkov/agents_snRNA"
atlas_stage="$atlas_project/technology_atlas/v2/work/technology_sources_20261105/card_coverage_20261106"
atlas_runtime="$atlas_project/technology_atlas/v2/work/technology_sources_20261105/source_library_release_01/task2_05/runtime"
cd "$atlas_stage"
export PYTHONPATH="$atlas_runtime/src:$atlas_runtime" PYTHONDONTWRITEBYTECODE=1
python audit_cards.py \
 --source-root "$atlas_project/technology_sources/illumina_task2_sources_20261105_08" \
 --view "$atlas_project/technology_sources/illumina_library_20261105_07" \
 --output "$atlas_stage/attempt_01" --parser-module "$atlas_stage/legacy_card_parser.py"
python - "$atlas_stage" "$GROUP_HOME/mstojkov/atlas-results/technology_sources_20261105" <<'PY'
import hashlib,json,os,shutil,sys,tarfile
from pathlib import Path
stage,backup=map(Path,sys.argv[1:])
archive=stage/('card_audit_'+os.environ['SLURM_JOB_ID']+'.tar.gz')
with tarfile.open(archive,'w:gz') as tf:
    for name in ['attempt_01','audit_cards.py','legacy_card_parser.py','run.sh','INPUTS.json']:
        tf.add(stage/name,arcname=name)
backup.mkdir(parents=True,exist_ok=True)
target=backup/archive.name
if target.exists():raise ValueError('Refusing to replace a durable audit archive')
shutil.copyfile(archive,target)
def digest(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
pin=digest(archive)
if pin!=digest(target):raise ValueError('Durable audit archive hash differs')
(stage/'BACKUP_RECEIPT.json').write_text(json.dumps({'job_id':os.environ['SLURM_JOB_ID'],
    'archive':str(target),'sha256':pin,'live_library_modified':False},indent=2)+'\n')
PY

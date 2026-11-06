#!/bin/bash
#SBATCH --job-name=atlas-all-paper-audit
#SBATCH --partition=xiaojie
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
set -eo pipefail
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
ml devel python/3.12.1 system poppler/0.47.0
source "$GROUP_HOME/mstojkov/envs/sequencing-technology-atlas-py312/bin/activate"
atlas_project="$GROUP_SCRATCH/mstojkov/agents_snRNA"
atlas_stage="$atlas_project/technology_atlas/v2/work/technology_sources_20261105/identity_review_02"
export PYTHONPATH="$atlas_stage/runtime:$atlas_project/technology_atlas/v2/work/technology_sources_20261105/runtime_02/src"
cd "$atlas_stage/runtime"
python -m scripts.illumina.technology_sources.article_identity \
 --root "$atlas_project/technology_sources/illumina_20261105" \
 --output "$atlas_stage/original_article_identity"
python -m scripts.illumina.technology_sources.review_papers \
 --source-root "$atlas_project/technology_sources/illumina_20261105" \
 --output "$atlas_project/technology_sources/illumina_all_paper_review_20261105" \
 --decisions "$atlas_stage/decisions.json" --bindings "$atlas_stage/reference_bindings.json" \
 --include-source-bibliography --recover
python -m scripts.illumina.technology_sources.article_identity \
 --root "$atlas_project/technology_sources/illumina_all_paper_review_20261105" \
 --output "$atlas_stage/acquired_article_identity"
python -m scripts.illumina.technology_sources.audit_progress \
 --collection "$atlas_project/technology_sources/illumina_20261105/collection.json" \
 --acquired-checks "$atlas_project/technology_sources/illumina_all_paper_review_20261105/PAPER_CHECKS.json" \
 --reviews "$atlas_stage/paper_content_review.json" --aliases "$atlas_stage/paper_aliases.json" \
 --article-checks "$atlas_stage/acquired_article_identity/ARTICLE_IDENTITY_CHECKS.json" \
 --output "$atlas_stage/progress"
atlas_backup="$GROUP_HOME/mstojkov/atlas-results/technology_sources_20261105"
mkdir -p "$atlas_backup"
tar -czf "$atlas_stage/all_paper_audit_${SLURM_JOB_ID}.tar.gz" -C "$atlas_project" \
 technology_sources/illumina_all_paper_review_20261105 \
 technology_atlas/v2/work/technology_sources_20261105/identity_review_02/progress \
 technology_atlas/v2/work/technology_sources_20261105/identity_review_02/original_article_identity \
 technology_atlas/v2/work/technology_sources_20261105/identity_review_02/acquired_article_identity
cp "$atlas_stage/all_paper_audit_${SLURM_JOB_ID}.tar.gz" "$atlas_backup/"
sha256sum "$atlas_backup/all_paper_audit_${SLURM_JOB_ID}.tar.gz" > "$atlas_stage/backup.sha256"
printf 'All-paper acquisition and bibliographic identity audit finished; scientific review remains separately tracked. No model calls.\n'

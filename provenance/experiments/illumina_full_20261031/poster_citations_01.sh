#!/usr/bin/env bash
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Run inside Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
export ATLAS_REPOSITORY="$root/runtime_05"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
mkdir "$root/poster_citation_runtime_01"
tar -xzf "$root/staging/poster_citations_01.tar.gz" -C "$root/poster_citation_runtime_01"
cd "$root/poster_citation_runtime_01"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q tests/test_illumina_poster_citations.py > "$root/logs/poster-citation-tests-$SLURM_JOB_ID.txt"
"$ATLAS_ENV_DIR/bin/python" scripts/illumina_poster_citations.py --proposals proposals.json \
 --sources "$root/sources_04/sources.json" --output "$root/poster_paper_sources_01" --network --fulltext

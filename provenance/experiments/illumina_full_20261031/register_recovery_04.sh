#!/usr/bin/env bash
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Run inside Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
run_root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
export ATLAS_REPOSITORY="$run_root/runtime_07"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
mkdir "$run_root/registration_runtime_03"
tar -xzf "$run_root/staging/registration_runtime_03.tar.gz" -C "$run_root/registration_runtime_03"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q "$run_root/registration_runtime_03/test_register_research_recovery.py" > "$run_root/logs/registration-tests-04-$SLURM_JOB_ID.txt"
old_prefix="/Users/mihajlostojkovic/Documents/qiu_lab/agents_snRNA/sequencing-technology-atlas/provenance/experiments/illumina_full_20261031/sources"
common=(--overlay "$run_root/poster_citation_recovery_04/recovery_overlay.json"
 --path-map "$old_prefix/poster_citation_review_02=$run_root/poster_citation_recovery_02"
 --path-map "$old_prefix/poster_citation_review_04=$run_root/poster_citation_recovery_04")
"$ATLAS_ENV_DIR/bin/python" "$run_root/registration_runtime_03/register_research_recovery.py" \
 --sources "$run_root/poster_sources_01/sources.json" "${common[@]}" \
 --interleave-source-categories --output "$run_root/poster_sources_05"
"$ATLAS_ENV_DIR/bin/python" "$run_root/registration_runtime_03/register_research_recovery.py" \
 --sources "$run_root/sources_04/sources.json" "${common[@]}" \
 --candidates "$run_root/poster_paper_sources_01/poster_paper_candidates.json" \
 --output "$run_root/sources_05"

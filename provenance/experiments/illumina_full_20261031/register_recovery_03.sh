#!/usr/bin/env bash
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Run inside Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
run_root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
export ATLAS_REPOSITORY="$run_root/runtime_07"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
mkdir "$run_root/registration_runtime_02"
tar -xzf "$run_root/staging/registration_runtime_02.tar.gz" -C "$run_root/registration_runtime_02"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q "$run_root/registration_runtime_02/test_register_research_recovery.py" > "$run_root/logs/registration-tests-$SLURM_JOB_ID.txt"
"$ATLAS_ENV_DIR/bin/python" "$run_root/registration_runtime_02/register_research_recovery.py" \
 --sources "$run_root/poster_sources_01/sources.json" \
 --overlay "$run_root/staging/recovery_overlay_03.json" \
 --path-map "/Users/mihajlostojkovic/Documents/qiu_lab/agents_snRNA/sequencing-technology-atlas/provenance/experiments/illumina_full_20261031/sources/poster_citation_review_02=$run_root/poster_citation_recovery_02" \
 --interleave-source-categories --output "$run_root/poster_sources_04"
"$ATLAS_ENV_DIR/bin/python" "$run_root/registration_runtime_02/register_research_recovery.py" \
 --sources "$run_root/sources_04/sources.json" \
 --overlay "$run_root/staging/recovery_overlay_03.json" \
 --candidates "$run_root/poster_paper_sources_01/poster_paper_candidates.json" \
 --path-map "/Users/mihajlostojkovic/Documents/qiu_lab/agents_snRNA/sequencing-technology-atlas/provenance/experiments/illumina_full_20261031/sources/poster_citation_review_02=$run_root/poster_citation_recovery_02" \
 --output "$run_root/sources_05"

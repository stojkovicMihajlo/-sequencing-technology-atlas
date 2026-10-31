#!/usr/bin/env bash
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Run inside Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
run_root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
export ATLAS_REPOSITORY="$run_root/runtime_08"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
printf '%s  %s\n' ba120cd269f3cc584d39c6a67dd3b66f48be33b3473a6a95ba1e78d06357c64e "$run_root/staging/research_recovery_01.tar.gz" | sha256sum -c -
test ! -e "$run_root/research_recovery_01"
test ! -e "$run_root/research_recovery_02"
tar -xzf "$run_root/staging/research_recovery_01.tar.gz" -C "$run_root"
tar -xzf "$run_root/staging/research_recovery_02.tar.gz" -C "$run_root"
old_prefix="/Users/mihajlostojkovic/Documents/qiu_lab/agents_snRNA/sequencing-technology-atlas/provenance/experiments/illumina_full_20261031/sources"
common=(--overlay "$run_root/research_recovery_02/recovery_overlay.json"
 --path-map "$old_prefix/research_recovery_01=$run_root/research_recovery_01"
 --path-map "$old_prefix/research_recovery_02=$run_root/research_recovery_02")
"$ATLAS_ENV_DIR/bin/python" "$run_root/registration_runtime_03/register_research_recovery.py" \
 --sources "$run_root/sources_05/sources.json" "${common[@]}" --output "$run_root/sources_06"
"$ATLAS_ENV_DIR/bin/python" "$run_root/registration_runtime_03/register_research_recovery.py" \
 --sources "$run_root/poster_sources_05/sources.json" "${common[@]}" --output "$run_root/poster_sources_06"

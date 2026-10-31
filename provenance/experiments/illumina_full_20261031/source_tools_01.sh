#!/usr/bin/env bash
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Run inside allocation}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
source "$root/pilot_runtime_02/scripts/sherlock/environment.sh"
mkdir -p "$root/source_tools_01"
tar -xzf "$root/staging/source_tools_01.tar.gz" -C "$root/source_tools_01"
"$ATLAS_ENV_DIR/bin/python" "$root/source_tools_01/illumina_poster_inventory.py" \
  --sources "$root/sources_03/sources.json" --inventory "$root/sources_03/inventory.json" \
  --output "$root/poster_index_01"
"$ATLAS_ENV_DIR/bin/python" "$root/source_tools_01/illumina_cached_sources.py" \
  --sources "$root/sources_03/sources.json" --workspace "$ATLAS_PROJECT_ROOT" \
  --root "$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/migration_to_xiaojie_20261019/validation_48532750" \
  --root "$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/deployment_20261018/source_acquisition_bulk_01" \
  --root "$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/deployment_20261018/source_recovery_doi_01" \
  --output "$root/cache_recovery_01"

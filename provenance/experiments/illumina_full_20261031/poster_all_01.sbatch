#!/usr/bin/env bash
#SBATCH --job-name=illumina-all-posters
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=06:00:00
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Submit through Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
run_root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
export ATLAS_REPOSITORY="$run_root/runtime_07"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export OMP_NUM_THREADS=1
export CODEX_SQLITE_HOME="$SCRATCH/atlas-codex-runtime-20261031/sqlite"
export TMPDIR="${L_SCRATCH_JOB:?}/poster-all-tmp"
mkdir -p "$TMPDIR"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import hashlib,json,os
from pathlib import Path
root=Path(os.environ['ATLAS_REPOSITORY'])
receipt=json.loads((root.parent/'staging/runtime_07.json').read_text())
assert all(hashlib.sha256((root/p).read_bytes()).hexdigest()==h for p,h in receipt['files'].items())
pilot=json.loads((root.parent/'poster_pilot_01/state.json').read_text())
assert pilot['counts']=={'supported':2},'Poster source transport/author/reviewer pilot did not pass'
prepared=json.loads((root.parent/'poster_sources_01/preparation_receipt.json').read_text())
assert prepared['status']=='prepared' and prepared['statistics']['source_rows_prepared']==285
PY
codex --version
codex login status
# The independently validated initial sources can be reviewed while supplemental
# bibliography recovery proceeds in a separate version. No running input mutates.
if "$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.illumina_batch run \
 "$run_root/poster_sources_01/sources.json" --output "$run_root/poster_all_01" --workers 16 --max-revisions 1; then
 batch_exit=0
else
 batch_exit=$?
fi
printf '%s\n' "$batch_exit" > "$run_root/logs/poster-all-native-exit-$SLURM_JOB_ID.txt"
"$ATLAS_ENV_DIR/bin/python" -m genomics_discovery.curation.illumina_graph build \
 "$run_root/poster_all_01" "$run_root/poster_sources_01/inventory.json" --output "$run_root/poster_architecture_atlas_01.json"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import json,os
from pathlib import Path
from genomics_discovery.curation.illumina_graph import protocol_text
root=Path(os.environ['ATLAS_REPOSITORY']).parent
state=json.loads((root/'poster_all_01/state.json').read_text())
output=root/'poster_protocol_views_01'
output.mkdir()
for ident,row in state['cards'].items():
 path=Path(row.get('attempt_directory',''))/'graph.json'
 if path.is_file(): (output/(ident+'.txt')).write_text(protocol_text(json.loads(path.read_text())))
summary={'source_rows':285,'counts':state['counts'],'model':state['policy']['model'],
 'effort':state['policy']['effort'],'finished_at':state.get('finished_at'),
 'scope':'Main depicted row groups on three posters, not a count of unique assays. Bibliography leads and source disagreements remain qualified.'}
(root/'poster_batch_summary_01.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary))
PY

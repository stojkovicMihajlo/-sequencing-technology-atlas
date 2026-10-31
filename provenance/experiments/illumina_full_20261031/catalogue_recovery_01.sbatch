#!/usr/bin/env bash
#SBATCH --job-name=illumina-held-review
#SBATCH --partition=xiaojie
#SBATCH --constraint=NO_GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=03:00:00
set -euo pipefail
hostname
printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:?Submit through Slurm}"
source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh
run_root="$ATLAS_PROJECT_ROOT/technology_atlas/v2/work/illumina_full_20261031"
printf '%s  %s\n' 6d72ae0278d2609061e1c04bf12e5f392f83f9b9ebfd7951f80421adef63cd6c "$run_root/staging/runtime_09.tar.gz" | sha256sum -c -
mkdir "$run_root/runtime_09"
tar -xzf "$run_root/staging/runtime_09.tar.gz" -C "$run_root/runtime_09"
export ATLAS_REPOSITORY="$run_root/runtime_09"
source "$ATLAS_REPOSITORY/scripts/sherlock/environment.sh"
export PYTHONPATH="$ATLAS_REPOSITORY/src:$GROUP_HOME/mstojkov/software/atlas/illumina-20261031/python"
export OMP_NUM_THREADS=1
export CODEX_SQLITE_HOME="$SCRATCH/atlas-codex-runtime-20261031/sqlite"
export TMPDIR="${L_SCRATCH_JOB:?}/catalogue-recovery-tmp"
mkdir -p "$TMPDIR"
"$ATLAS_ENV_DIR/bin/python" -m pytest -q "$ATLAS_REPOSITORY/tests" > "$run_root/logs/runtime09-tests-$SLURM_JOB_ID.txt"
"$ATLAS_ENV_DIR/bin/python" - <<'PY'
import asyncio,hashlib,json,os
from pathlib import Path
from genomics_discovery.curation.illumina_batch import run
from genomics_discovery.curation.illumina_graph import build_graph,protocol_text
root=Path(os.environ['ATLAS_REPOSITORY']).parent
runtime=Path(os.environ['ATLAS_REPOSITORY'])
manifest=json.loads((root/'staging/runtime_09.json').read_bytes())
assert all(hashlib.sha256((runtime/p).read_bytes()).hexdigest()==h for p,h in manifest['files'].items())
sources=root/'sources_06/sources.json'
registration=json.loads((sources.parent/'recovery_registration.json').read_bytes())
assert registration['recovery_overlay_sha256']=='83542556b1c37d5c36d05e0a95fd4e00c7e51560e220185b5d5bf87fc7988047'
prior_path=root/'all_cards_01/state.json'
prior=json.loads(prior_path.read_bytes())
assert prior.get('finished_at'), 'Predecessor extraction has not reached a final checkpoint'
pilot=json.loads((root/'scope_repair_pilot_01/state.json').read_bytes())
assert pilot['counts']=={'supported':3}, 'Targeted general-fix pilot did not pass'
already_repaired={k for k,v in pilot['cards'].items() if v['status']=='supported'}
selected=[k for k,v in prior['cards'].items() if v['status'] in {'research_needed','needs_revision'} and k not in already_repaired]
assert selected, 'No held cards require this bounded recovery batch'
plan={'parent_state':str(prior_path),'parent_state_sha256':hashlib.sha256(prior_path.read_bytes()).hexdigest(),
 'source_snapshot':str(sources),'source_sha256':hashlib.sha256(sources.read_bytes()).hexdigest(),
 'runtime':str(runtime),'record_ids':selected,'successful_unchanged_cards_repeated':False,
 'reason':'Recorded scope/review contract defects or new primary source evidence; fresh image author and independent reviewer. No previous result is edited or assumed correct.',
 'already_repaired_pilot_records':sorted(already_repaired),'max_revisions':1,'workers':10}
(root/'catalogue_recovery_plan_01.json').write_text(json.dumps(plan,indent=2)+'\n')
state=asyncio.run(run(sources,root/'catalogue_recovery_01',workers=10,records=selected,max_revisions=1))
graph=build_graph(root/'catalogue_recovery_01',sources.parent/'inventory.json')
(root/'catalogue_recovery_graph_01.json').write_text(json.dumps(graph,indent=2,ensure_ascii=False)+'\n')
out=root/'catalogue_recovery_views_01';out.mkdir()
for rid,row in state['cards'].items():
 path=Path(row.get('attempt_directory',''))/'graph.json'
 if path.is_file():(out/(rid+'.txt')).write_text(protocol_text(json.loads(path.read_bytes())))
print(json.dumps({'selected':len(selected),'counts':state['counts'],'scope':'Named held source depictions; no forced admission or new ancestry assertions.'}))
PY

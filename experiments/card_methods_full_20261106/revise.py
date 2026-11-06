"""Continue source-complete revisions without re-extracting accepted methods."""
import argparse
import asyncio
import copy
from collections import Counter
import os
from pathlib import Path
import shutil

from genomics_discovery.execution.codex_worker import CodexWorker
from genomics_discovery.illumina.extraction import read, write, sha, now, checkpoint, extract_registered_workflow
from genomics_discovery.illumina.graph import _load_card, protocol_text
from scripts.illumina.review_site.export_batch import export
from .audit import actions


def revision_targets(state):
    result=[]
    for mid,row in state['cards'].items():
        if row['status']!='needs_revision':continue
        review=read(Path(row['attempt_directory'])/'review.json')
        requirement=actions(row,review)
        if requirement['needs_correction'] and not requirement['needs_source_investigation']:
            result.append(mid)
    return result


async def execute(previous, output, workers):
    if not os.environ.get('SLURM_JOB_ID'):raise ValueError('Native revisions require Qiu Slurm')
    if not 1<=workers<=10:raise ValueError('Select 1–10 method workers')
    output.mkdir(parents=True,exist_ok=False)
    state=copy.deepcopy(read(previous/'state.json'))
    cards=read(previous/'inventory.json')['cards'];card_by_id={c['record_id']:c for c in cards}
    selected=revision_targets(state)
    state.update(started_at=now(),finished_at=None,slurm_job_id=os.environ['SLURM_JOB_ID'],
        followup={'previous_batch':str(previous),'previous_state_sha256':sha(previous/'state.json'),
            'selected_methods':selected,'max_correction_rounds':1,
            'reason':'Correct errors identified by independent review using already registered evidence. '
                'Mixed evidence requests are routed to source recovery first. Accepted methods remain at their original attempts.'})
    for name in ('inventory.json','selection.json'):shutil.copyfile(previous/name,output/name)
    state['policy'].update(inventory_sha256=sha(output/'inventory.json'),max_revisions=1,workers=workers)
    checkpoint(output,state,cards)
    # Produce the requested inspection app before the new native work. This is
    # an immutable view of the finished baseline, not a false live-result display.
    export(previous,output/'inspection_initial')
    shutil.make_archive(str(output/'inspection_initial'),'zip',output/'inspection_initial')
    gate=asyncio.Semaphore(workers)

    async def one(mid):
        async with gate:
            original=copy.deepcopy(state['cards'][mid]); directory=Path(original['attempt_directory'])
            card=read(directory/'card.json');_load_card(original,card)
            registry=read(directory/'source_registry.json')
            images=[(sid,Path(s['path'])) for sid,s in registry.items() if s['kind']=='image']
            seed={'directory':str(directory),'pins':{n:sha(directory/n) for n in
                ('card.json','source_registry.json','extraction.json','review.json')}}
            destination=output/'cards'/mid/'attempt_001'
            row={'record_id':mid,'title':card['source_title'],'status':'running',
                'attempt_directory':str(destination),'previous_outcome':original}
            state['cards'][mid]=row;checkpoint(output,state,cards)
            worker=CodexWorker(model='gpt-6.1-sol',effort='xhigh',timeout_seconds=None,
                web_search=False,network_access=False,pin_shell_environment=True)
            try:
                result=await extract_registered_workflow(card,registry,images,destination,worker,
                    max_revisions=1,revision_seed=seed)
                row.update(result,finished_at=now())
                graph,_=_load_card(row,card)
                (destination/'workflow.txt').write_text(protocol_text(graph,sources=registry))
            except Exception as exc:
                row.update(status='failed',reason=f'{type(exc).__name__}: {exc}',finished_at=now())
            checkpoint(output,state,cards)

    await asyncio.gather(*(one(mid) for mid in selected))
    state['finished_at']=now();checkpoint(output,state,cards)
    export(output,output/'inspection_final')
    shutil.make_archive(str(output/'inspection_final'),'zip',output/'inspection_final')
    write(output/'RESULT.json',{'counts':state['counts'],'revised_methods':selected,
        'unchanged_accepted_methods':sum(r['status']=='supported' for m,r in read(previous/'state.json')['cards'].items()),
        'remaining_source_work':'Separate recovery of missing primary articles, supplements and incorporated protocols.'})
    return state


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--previous',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--workers',type=int,default=10);a=p.parse_args()
    asyncio.run(execute(a.previous.resolve(),a.output.resolve(),a.workers))

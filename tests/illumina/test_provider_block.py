"""A provider refusal remains a stop decision, not an extraction revision."""
import asyncio
import json

import pytest

from genomics_discovery.execution.codex_worker import (
    WorkerResult, assess_archived_attempt, classify_archived_terminal_failure,
)
from genomics_discovery.illumina import extraction as batch
from tests.illumina.test_illumina_batch_controls import acquired


# Exact terminal message; thread/input details are synthetic, not copied papers.
BLOCK = ("This content was flagged for possible biological risk. If this seems wrong, "
         "try rephrasing your request. We are continuously refining our work in detecting "
         "biological risk, and you can read more about our approach in our blog post: "
         "https://openai.com/index/preparing-for-future-ai-capabilities-in-biology")


def terminal_archive(path, events):
    path.mkdir(parents=True, exist_ok=True)
    (path / 'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
    batch.write(path / 'result.json', {'exit_code':1,'thread_id':'synthetic-policy-thread',
                'usage':{},'elapsed_seconds':1.,'error':'Codex exited with code 1'})
    (path / 'final.txt').write_text('')


def test_exact_terminal_policy_block_is_diagnosed_without_becoming_complete(tmp_path):
    events=[{'type':'thread.started','thread_id':'synthetic-policy-thread'},
            {'type':'turn.started'}, {'type':'error','message':BLOCK},
            {'type':'turn.failed','error':{'message':BLOCK}}]
    terminal_archive(tmp_path,events)
    before=(tmp_path/'events.jsonl').read_bytes()
    diagnostic=classify_archived_terminal_failure(tmp_path)
    assert diagnostic['category']=='provider_biological_risk_block'
    assert diagnostic['event']['line']==4
    assert BLOCK not in json.dumps(diagnostic)
    assert not assess_archived_attempt(tmp_path).complete
    assert (tmp_path/'events.jsonl').read_bytes()==before


@pytest.mark.parametrize('events',[
    [{'type':'item.completed','item':{'type':'agent_message','text':BLOCK}},
     {'type':'turn.failed','error':{'message':'Different error'}}],
    [{'type':'turn.failed','error':{'message':BLOCK}}, {'type':'turn.started'}],
    [{'type':'error','message':BLOCK}, {'type':'turn.completed','usage':{}}],
    [{'type':'turn.failed','error':{'code':'unobserved_code','message':BLOCK}}],
])
def test_model_text_recovered_turn_and_unobserved_coded_errors_are_not_policy_classification(tmp_path,events):
    terminal_archive(tmp_path,events)
    assert classify_archived_terminal_failure(tmp_path)['category']=='unknown'


def test_illumina_stage_stops_after_policy_block_and_preserves_diagnostic(acquired,tmp_path):
    root,card,sources,_=acquired
    class BlockedWorker:
        calls=0
        async def run(self,prompt,attempt_dir,**kwargs):
            self.calls+=1
            terminal_archive(attempt_dir,[{'type':'thread.started','thread_id':'synthetic-policy-thread'},
                {'type':'turn.started'},{'type':'turn.failed','error':{'message':BLOCK}}])
            return WorkerResult(1,'synthetic-policy-thread',{},1.,'Codex exited with code 1')
    worker=BlockedWorker()
    output=tmp_path/'policy-case'
    with pytest.raises(batch.StageFailure) as error:
        asyncio.run(batch.extract_card(card,sources,root,output,worker,max_revisions=2))
    assert error.value.category=='provider_biological_risk_block'
    stages=batch.read(output/'stages.json')
    assert len(stages)==1 and worker.calls==1
    assert stages[0]['failure_category']=='provider_biological_risk_block'
    assert stages[0]['terminal_failure']['category']=='provider_biological_risk_block'
    assert stages[0]['status']=='failed'
    assert not (output/'extraction.json').exists()

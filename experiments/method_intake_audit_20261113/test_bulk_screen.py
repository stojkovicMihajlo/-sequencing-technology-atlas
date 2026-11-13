"""Offline batch/transport/custody checks; no scientific model judgments."""
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bulk_screen as bulk
from genomics_discovery.execution.codex_worker import WorkerResult


def paper(pmid,text='We experimentally improved DNA library preparation using an enzyme.'):
    return {'pmid':str(pmid),'title':'Synthetic study','identifiers':{'pubmed':[str(pmid)]},
        'abstract':([] if text is None else [{'label':'METHODS','text':text}]),
        'publication_types':['Journal Article'],'publication_dates':[]}


class Worker:
    model='gpt-6.1-sol';effort='xhigh'
    def __init__(self,fail=()):self.calls=[];self.fail=set(fail)
    async def run(self,prompt,attempt,*,response_schema_path):
        papers=json.loads(prompt.split('SUPPLIED_PRIMARY_METADATA:\n',1)[1])['papers']
        self.calls.append([p['pmid'] for p in papers]);thread='mock-'+attempt.name
        (attempt/'prompt.txt').write_text(prompt)
        if any(p['pmid'] in self.fail for p in papers):
            result=WorkerResult(1,thread,{},0.1,'Synthetic provider failure')
            (attempt/'events.jsonl').write_text(json.dumps({'type':'error','message':'Synthetic provider failure'})+'\n')
        else:
            rows=[{'pmid':p['pmid'],'classification':'assay','publication_role':'primary_methods','methodological_claim_quote':p['abstract'][0]['text'],
                   'rationale':'Synthetic first-pass result.','focused_question':''} for p in papers]
            final=json.dumps({'papers':rows});usage={'input_tokens':100,'output_tokens':30}
            events=[{'type':'thread.started','thread_id':thread},{'type':'turn.started'},
                {'type':'item.completed','item':{'type':'agent_message','text':final}},
                {'type':'turn.completed','usage':usage}]
            (attempt/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
            (attempt/'final.txt').write_text(final);result=WorkerResult(0,thread,usage,0.1,None)
        (attempt/'result.json').write_text(json.dumps(asdict(result)))
        return result


class BulkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'SLURM_JOB_ID':'offline-no-model'});self.env.start()
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def pool(self,rows):
        p=self.root/'pool.json';p.write_text(json.dumps({r['pmid']:r for r in rows}));return p
    def run_screen(self,p,worker,**kw):
        return asyncio.run(bulk.screen(p,self.root/'screen',worker,pool_sha256=bulk.pilot.sha(p),**kw))

    def test_one_call_per_batch_with_missing_abstract_not_negative(self):
        p=self.pool([paper(1),paper(2),paper(3),paper(4,None)]);w=Worker()
        result=self.run_screen(p,w,batch_size=2,agents=2)
        self.assertEqual(w.calls,[['1','2'],['3']]);self.assertEqual(result['batches'],2)
        missing=result['papers'][-1]
        self.assertEqual(missing['status'],'unscreenable_missing_abstract');self.assertIsNone(missing['classification'])
        self.assertEqual(missing['route'],'metadata_recovery')
        self.assertTrue(all(r['route']=='acquisition_review_candidate' for r in result['papers'][:3]))

    def test_full_abstracts_dynamic_batching_and_single_oversize_refusal(self):
        rows=[paper(i,'X'*700) for i in (1,2,3)]
        cap=len(bulk.prompt_for(rows[:1]).encode())+100
        batches=bulk.batches_for(rows,20,cap)
        self.assertEqual([len(b) for b in batches],[1,1,1]);self.assertEqual(batches[0][0]['abstract'],rows[0]['abstract'])
        with self.assertRaisesRegex(ValueError,'without truncation'):bulk.batches_for(rows,20,cap-800)

    def test_quote_own_id_class_and_unclear_question_are_checked(self):
        row=paper(1);base={'pmid':'1','classification':'unresolved','publication_role':'unresolved','methodological_claim_quote':row['abstract'][0]['text'],
             'rationale':'An assay dependency needs inspection.','focused_question':'Does the Methods demonstrate improved library preparation?'}
        bulk.validate({'papers':[base]},[row])
        for change in [{'pmid':'2'},{'classification':'new_method'},{'methodological_claim_quote':'Invented quote'},
                       {'focused_question':''},{'rationale':''}]:
            with self.assertRaises(ValueError):bulk.validate({'papers':[{**base,**change}]},[row])
        with self.assertRaises(ValueError):bulk.validate({'papers':[base,base]},[row])

    def test_provider_failure_isolated_and_not_retried_on_resume(self):
        p=self.pool([paper(1),paper(2)]);w=Worker(fail={'1'})
        result=self.run_screen(p,w,batch_size=1)
        self.assertEqual(result['counts'],{'screening_batch_failed':1,'screened':1})
        self.assertEqual(len(w.calls),2)
        resumed=self.run_screen(p,w,batch_size=1,resume=True)
        self.assertEqual(len(w.calls),2);self.assertEqual(resumed['counts'],result['counts'])

    def test_same_complete_archive_reused_and_changed_final_rejected(self):
        p=self.pool([paper(1)]);w=Worker();self.run_screen(p,w)
        self.run_screen(p,w,resume=True);self.assertEqual(len(w.calls),1)
        attempt=next((self.root/'screen').glob('00001-*'))
        (attempt/'final.txt').write_text('{}')
        result=self.run_screen(p,w,resume=True)
        self.assertEqual(result['counts'],{'screening_batch_failed':1});self.assertEqual(len(w.calls),1)

    def test_changed_pool_prompt_schema_or_runtime_cannot_reuse(self):
        p=self.pool([paper(1)]);w=Worker();self.run_screen(p,w)
        with self.assertRaisesRegex(ValueError,'same-input'):self.run_screen(p,w,batch_size=1,resume=True)
        with patch.object(bulk,'RUBRIC',bulk.RUBRIC+'New instruction.'):
            result=self.run_screen(p,w,resume=True)
            self.assertEqual(result['counts'],{'screening_batch_failed':1})
        self.assertEqual(len(w.calls),1)
        with self.assertRaisesRegex(ValueError,'hash'):bulk.load_pool(p,'0'*64)

    def test_overlay_keeps_own_ids_and_full_sections(self):
        p=self.pool([paper(1,None)]);overlay=self.root/'overlay.json'
        overlay.write_text(json.dumps({'base_pool':{'sha256':bulk.pilot.sha(p)},'metadata_overlay':{'1':paper(1)}}))
        rows,missing,_=bulk.load_pool(p,bulk.pilot.sha(p),[(overlay,bulk.pilot.sha(overlay))])
        self.assertEqual(rows[0]['abstract'],paper(1)['abstract']);self.assertEqual(missing,[])
        bad=paper(1);bad['pmid']='2';p.write_text(json.dumps({'1':bad}))
        with self.assertRaisesRegex(ValueError,'Own PMID'):bulk.load_pool(p,bulk.pilot.sha(p))

    def test_runtime_manifest_replays_all_listed_pins(self):
        source=self.root/'support.py';source.write_text('supported')
        manifest=self.root/'PAYLOAD_MANIFEST.json';manifest.write_text(json.dumps({'files':{'support.py':bulk.pilot.sha(source)}}))
        bulk.runtime_pins(Worker(),manifest)
        source.write_text('changed')
        with self.assertRaisesRegex(ValueError,'Runtime manifest'):bulk.runtime_pins(Worker(),manifest)

    def test_no_slurm_stops_before_model(self):
        p=self.pool([paper(1)]);w=Worker()
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'Slurm'):self.run_screen(p,w)
        self.assertEqual(w.calls,[])

    def test_queue_visible_before_first_native_call(self):
        p=self.pool([paper(1),paper(2),paper(3)])
        parent=self
        class InspectQueue(Worker):
            async def run(self,prompt,attempt,**kwargs):
                if not self.calls:
                    state=bulk.read(attempt.parent/'STATE.json')
                    parent.assertEqual(state['counts'],{'running':1,'queued':2})
                    parent.assertEqual(state['planned_batches'],3)
                    parent.assertEqual(state['max_concurrent_native_calls'],1)
                return await super().run(prompt,attempt,**kwargs)
        result=self.run_screen(p,InspectQueue(),batch_size=1,agents=1)
        self.assertEqual(result['counts'],{'screened':3})

    def test_prior_planned_roster_excluded_including_unscreenable_and_queued(self):
        p=self.pool([paper(1),paper(2),paper(3,None),paper(4,None)]);w=Worker()
        result=self.run_screen(p,w,batch_size=1,exclude_pmids={'1','3'})
        self.assertEqual(w.calls,[['2']]);self.assertEqual(result['excluded_planned_papers'],2)
        self.assertEqual([r['pmid'] for r in result['papers']],['2','4'])
        self.assertEqual(result['papers'][-1]['route'],'metadata_recovery')

    def test_bounded_complete_transport_repair_reuses_valid_rows_and_keeps_old_attempt(self):
        p=self.pool([paper(1),paper(2,'A protocol for Saccharomyce cerevisiae sequencing.'),paper(3)])
        class TypoCorrectingWorker(Worker):
            async def run(self,prompt,attempt,**kwargs):
                result=await super().run(prompt,attempt,**kwargs)
                value=json.loads((attempt/'final.txt').read_text())
                if len(value['papers'])>1:value['papers'][1]['methodological_claim_quote']='A protocol for Saccharomyces cerevisiae sequencing.'
                final=json.dumps(value);(attempt/'final.txt').write_text(final)
                events=[json.loads(line) for line in (attempt/'events.jsonl').read_text().splitlines()]
                next(e for e in events if e['type']=='item.completed')['item']['text']=final
                (attempt/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
                return result
        failed=self.run_screen(p,TypoCorrectingWorker(),batch_size=2)
        self.assertEqual(failed['counts'],{'screening_batch_failed':2,'screened':1})
        original=next((self.root/'screen').glob('00001-*'));before=(original/'final.txt').read_bytes()
        config={'original_attempt':str(original),'source_pins':{name:bulk.pilot.sha(original/name) for name in ['papers.json','final.txt','events.jsonl','result.json','EXPECTED.json']},
                'reserved_repair_slots':1,'bulk_source_slots':25,'extraction_reserved_max':24,'max_repair_papers':1,
                'source_inputs':{'pool':{'path':str(p),'sha256':bulk.pilot.sha(p)},'overlays':[]}}
        config_path=self.root/'repair.json';config_path.write_text(json.dumps(config));repair_worker=Worker()
        result=asyncio.run(bulk.repair_complete_transport(config_path,self.root/'repair',repair_worker))
        self.assertEqual(repair_worker.calls,[['2']]);self.assertEqual(result['reused_valid_papers'],1)
        self.assertEqual((original/'final.txt').read_bytes(),before)
        patched=bulk.read(self.root/'repair/VALIDATION_REPAIR.json')['papers']
        self.assertEqual(patched[0]['methodological_claim_quote'],paper(1)['abstract'][0]['text'])
        self.assertEqual(patched[1]['methodological_claim_quote'],'A protocol for Saccharomyce cerevisiae sequencing.')
        consolidated=bulk.read(self.root/'repair/CONSOLIDATED_SCREENING.json')['papers']
        self.assertEqual([r['status'] for r in consolidated],['screened_validation_repair','screened_validation_repair','screened'])
        self.assertEqual(consolidated[2]['methodological_claim_quote'],paper(3)['abstract'][0]['text'])
        bad_patch=bulk.read(self.root/'repair/VALIDATION_REPAIR.json');bad_patch['papers']=[consolidated[2]]
        bad_out=self.root/'bad-merge';bad_out.mkdir()
        with self.assertRaisesRegex(ValueError,'valid/running/queued'):
            bulk.consolidate_repair(original,bad_patch,config['source_inputs'],bad_out)

    def test_validation_repair_refuses_incomplete_provider_transport(self):
        p=self.pool([paper(1)]);self.run_screen(p,Worker(fail={'1'}));original=next((self.root/'screen').glob('00001-*'))
        config={'original_attempt':str(original),'source_pins':{name:bulk.pilot.sha(original/name) for name in ['papers.json','events.jsonl','result.json','EXPECTED.json']},
                'reserved_repair_slots':1,'bulk_source_slots':25,'extraction_reserved_max':24,'max_repair_papers':1}
        path=self.root/'repair.json';path.write_text(json.dumps(config));worker=Worker()
        with self.assertRaisesRegex(ValueError,'complete successful native transport'):
            asyncio.run(bulk.repair_complete_transport(path,self.root/'repair',worker))
        self.assertEqual(worker.calls,[])

    def test_rubric_includes_improvements_and_has_no_seq_name_requirement(self):
        self.assertIn('library-preparation, enzyme',bulk.RUBRIC)
        self.assertIn("need not introduce a wholly new architecture or a named '-seq'",bulk.RUBRIC)
        self.assertIn('one screen',bulk.RUBRIC)

    def test_negative_classes_remain_inspectable_but_any_unclear_question_advances(self):
        for classification in ('application','software_only'):
            self.assertEqual(bulk.route_for({'classification':classification,'focused_question':''}),'negative_inspectable')
            self.assertEqual(bulk.route_for({'classification':classification,'focused_question':'Is there an original workflow?'}),
                             'acquisition_review_candidate')
        for classification in ('assay','component_only','unresolved','review'):
            self.assertEqual(bulk.route_for({'classification':classification,'focused_question':''}),
                             'acquisition_review_candidate')
        self.assertEqual(bulk.route_for({'classification':'application','publication_role':'later_protocol','focused_question':''}),'acquisition_review_candidate')


if __name__=='__main__':unittest.main()

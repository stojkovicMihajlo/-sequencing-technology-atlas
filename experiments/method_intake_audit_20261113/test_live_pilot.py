import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from genomics_discovery.execution.codex_worker import CodexWorker, WorkerResult
from live_pilot import (AbstractReadOnlyWorker, acquire_metadata, final_from_stdout,
                        load_metadata, prompt_for, schema_probe, screen,
                        validate_decisions, write_new)


def targets():
    return [{'pmid':str(1000+i),'doi':f'10.9999/test{i}','title':f'Synthetic title {i}'} for i in range(7)]


def xml():
    return ('<PubmedArticleSet>'+''.join(
        f'<PubmedArticle><MedlineCitation><PMID>{p["pmid"]}</PMID><Article><ArticleTitle>{p["title"]}</ArticleTitle>'
        '<Abstract><AbstractText>We introduced and experimentally validated a sequencing assay.</AbstractText></Abstract>'
        '</Article></MedlineCitation><PubmedData><ArticleIdList>'
        f'<ArticleId IdType="doi">{p["doi"]}</ArticleId></ArticleIdList></PubmedData></PubmedArticle>' for p in targets())+'</PubmedArticleSet>').encode()


def fetched(url, path, timeout):
    path.write_bytes(xml())
    return {'request_url':url,'final_url':url,'http_status':200,'sha256':hashlib.sha256(xml()).hexdigest()}


def decisions(papers):
    return {'papers':[{'pmid':p['pmid'],'classification':'assay','publication_role':'primary_methods',
        'criteria':{key:{'status':'yes','abstract_quote':'experimentally validated a sequencing assay',
            'rationale':'Synthetic mock positive.'} for key in ('sequence_based_measurement','assay_workflow_contribution','experimental_demonstration')},
        'rationale':'Synthetic mock only.','focused_question':''} for p in papers]}


class MockWorker:
    model = 'gpt-6.1-sol'; effort = 'xhigh'
    def __init__(self): self.prompts = []
    async def run(self, prompt, attempt, *, response_schema_path):
        self.prompts.append(prompt)
        schema = json.loads(response_schema_path.read_text())
        if 'ok' in schema['properties']:
            value = {'ok':True}
        else:
            papers = json.loads(prompt.split('SUPPLIED_PRIMARY_METADATA:\n',1)[1])['papers']
            value = decisions(papers)
        final = json.dumps(value)
        events = [{'type':'thread.started','thread_id':'mock-thread'}, {'type':'turn.started'},
                  {'type':'item.completed','item':{'type':'agent_message','text':final}},
                  {'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':50,'output_tokens':100}}]
        (attempt/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        (attempt/'final.txt').write_text(final)
        result = WorkerResult(0,'mock-thread',events[-1]['usage'],0.1,None)
        write_new(attempt/'result.json',asdict(result))
        return result


class LivePilotTests(unittest.TestCase):
    def prepare(self, root):
        target_path = root/'targets.json'; write_new(target_path,{'papers':targets()})
        manifest = acquire_metadata(target_path,root/'metadata',fetcher=fetched)
        return root/'metadata/METADATA_MANIFEST.json',manifest

    def test_raw_metadata_and_own_identifiers(self):
        with tempfile.TemporaryDirectory() as temp:
            path,manifest=self.prepare(Path(temp)); papers,_=load_metadata(path)
            self.assertEqual(len(papers),7);self.assertEqual(len(manifest['identity_checks']),7)
            self.assertNotIn('screening',papers[0]);self.assertNotIn('expected',papers[0])

    def test_conflicting_own_doi_stops_before_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);rows=targets();rows[0]['doi']='10.9999/wrong'
            write_new(root/'targets.json',{'papers':rows})
            with self.assertRaisesRegex(ValueError,'mismatch'):
                acquire_metadata(root/'targets.json',root/'metadata',fetcher=fetched)

    def test_changed_raw_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path,manifest=self.prepare(Path(temp));Path(manifest['raw_path']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'custody'):load_metadata(path)

    def test_readonly_changes_only_sandbox_preserves_policy(self):
        attempt=Path('/tmp/abstract-probe');schema=attempt/'schema.json'
        args=dict(model='gpt-6.1-sol',effort='xhigh',web_search=False,network_access=False)
        baseline=CodexWorker(**args)._command(attempt,response_schema_path=schema)
        actual=AbstractReadOnlyWorker(**args)._command(attempt,response_schema_path=schema)
        baseline[baseline.index('--sandbox')+1]='read-only'
        self.assertEqual(actual,baseline)
        self.assertIn('approvals_reviewer="auto_review"',actual)
        self.assertIn('approval_policy="on-request"',actual);self.assertIn('--output-schema',actual)

    def test_two_blinded_batches_controller_writes_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,_=self.prepare(root);worker=MockWorker()
            result=asyncio.run(screen(path,root/'run',worker))
            self.assertEqual(result['native_calls'],2);self.assertEqual(result['usage_tokens'],400)
            self.assertEqual(len(worker.prompts),2)
            self.assertNotIn('Synthetic mock positive',worker.prompts[1])
            self.assertTrue((root/'run/author/artifact.json').is_file())
            self.assertTrue((root/'run/independent_reviewer/artifact.json').is_file())

    def test_host_token_gate_stops_after_first_over_budget_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,_=self.prepare(root);worker=MockWorker()
            with self.assertRaisesRegex(ValueError,'budget exceeded'):
                asyncio.run(screen(path,root/'run',worker,token_budget=100))
            self.assertEqual(len(worker.prompts),1)

    def test_fabricated_quote_fails(self):
        papers=[{'pmid':'1','abstract':[{'text':'Real abstract text.'}]}]
        value=decisions(papers)
        with self.assertRaisesRegex(ValueError,'abstract quote'):validate_decisions(value,papers)

    def test_actual_structured_probe_shape_with_mock_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt=asyncio.run(schema_probe(Path(temp)/'probe',MockWorker()))
            self.assertTrue(receipt['passed'])


if __name__ == '__main__': unittest.main()

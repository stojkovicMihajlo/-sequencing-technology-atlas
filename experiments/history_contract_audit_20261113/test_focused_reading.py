import copy
import json
from pathlib import Path
import tempfile
import unittest
import sys
import os
import shutil
import subprocess

from focused_reading import (
    check_focused_reads, check_policy, context_ranges, deliver_outline, obligations,
    read_source, focused_instructions, outline, blocks,
)
from genomics_discovery.illumina.paper_reads import record_read


def proposal():
    return {'claims': [{'id': 'c', 'reference_id': 'R1', 'relation': 'documented_reuse',
        'unresolved': [], 'change_records': [{'id': 'd', 'before': 'Old route',
        'after': 'Changed route', 'retained': [], 'evidence': {
            'before': [{'source_id': 'R1', 'quote': 'The old circularization route.'}],
            'after': [{'source_id': 'seed', 'quote': 'Direct adapter ligation replaces it.'}],
            'inheritance': [{'source_id': 'seed', 'quote': 'We adapt the earlier method.'}],
            'retained': []}, 'consequence_and_limits': {
                'reported_effects': [], 'limit_evidence': [], 'unresolved': []}}]}]}


class FocusedReadingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.registry = self.root / 'sources.json'
        self.log = self.root / 'reads.jsonl'
        self.sources = {
            'R1': {'content_level': 'fulltext', 'text': 'Scope qualification.\n\nThe old circularization route.\n\nVariant exception.\n\n' + 'Unused result. ' * 1200},
            'seed': {'content_level': 'fulltext', 'text': 'We adapt the earlier method.\n\nDirect adapter ligation replaces it.\n\nImportant qualification.\n\n' + 'Unrelated result. ' * 1200}}
        self.registry.write_text(json.dumps(self.sources)); self.analysis = proposal()

    def deliver(self):
        for sid in self.sources:
            deliver_outline(self.log, self.registry, self.sources, sid)
            read_source(self.log, self.registry, self.sources, sid,
                question='Which route and its context?', query='route' if sid == 'R1' else 'adapter', limit=500)

    def check(self, review=None):
        return check_focused_reads(self.log, self.registry, self.sources, self.analysis, review)

    def test_focused_passes_bounded_delivery_full_default_rejects(self):
        self.deliver(); report = self.check()
        self.assertEqual(len(report['evidence_delivery']), 3)
        legacy = self.root / 'legacy.jsonl'
        for sid in self.sources:
            record_read(legacy, self.registry, self.sources,
                        {'source_id': sid, 'start': 0, 'text': self.sources[sid]['text'][:500]})
        with self.assertRaisesRegex(ValueError, 'Full paper'):
            check_policy(legacy, self.registry, self.sources, ['seed', 'R1'],
                         role='analyse', analysis=self.analysis)
        self.assertEqual(check_policy(self.log, self.registry, self.sources, [],
            role='analyse', analysis=self.analysis, reading_policy='focused')['reading_policy'], 'focused')

    def test_query_miss_logged_with_no_invented_range(self):
        result = read_source(self.log, self.registry, self.sources, 'R1',
            question='Is this mechanism present?', query='missing-mechanism')
        self.assertIsNone(result['start']); row=json.loads(self.log.read_text())
        self.assertFalse(row['found']); self.assertIsNone(row['end'])
        with self.assertRaisesRegex(ValueError, 'outline'):
            self.check()

    def test_exact_quote_alone_without_surrounding_context_fails(self):
        for sid in self.sources:
            deliver_outline(self.log, self.registry, self.sources, sid)
        quote = self.analysis['claims'][0]['change_records'][0]['evidence']['before'][0]['quote']
        read_source(self.log, self.registry, self.sources, 'R1', question='Route?',
            start=self.sources['R1']['text'].index(quote), limit=len(quote), reason='Open quoted text')
        with self.assertRaisesRegex(ValueError, 'surrounding block context'):
            self.check()

    def test_hash_and_range_tampering_rejected(self):
        self.deliver(); rows=self.log.read_text().splitlines(); row=json.loads(rows[1]); row['end']+=1
        rows[1]=json.dumps(row); self.log.write_text('\n'.join(rows)+'\n')
        with self.assertRaisesRegex(ValueError, 'range'):
            self.check()
        self.log.unlink(); self.deliver(); self.registry.write_text(self.registry.read_text()+' ')
        with self.assertRaisesRegex(ValueError, 'pin changed'):
            self.check()

    def test_inheritance_required_only_for_actual_use(self):
        change=self.analysis['claims'][0]['change_records'][0]
        change['evidence']['inheritance']=[]
        with self.assertRaisesRegex(ValueError, 'inheritance'):
            obligations(self.analysis)
        self.analysis['claims'][0]['relation']='comparison'
        self.deliver(); self.assertEqual(len(self.check()['evidence_delivery']),2)

    def test_optional_effect_evidence_becomes_delivery_obligation(self):
        self.deliver()
        self.analysis['claims'][0]['change_records'][0]['consequence_and_limits']['reported_effects']=[{
            'evidence':[{'source_id':'seed','quote':'Not present in this source.'}]}]
        with self.assertRaisesRegex(ValueError, 'reported_effects'):
            self.check()

    def test_missing_before_and_research_lead_remain_explicit(self):
        self.deliver(); change=self.analysis['claims'][0]['change_records'][0]
        change['before']=None; change['evidence']['before']=[]
        change['consequence_and_limits']['unresolved']=['Original supplement unavailable; route is unknown.']
        review={'findings':[], 'coverage_findings':[{'kind':'research_lead',
            'requested_evidence':'Acquire the exact earlier supplement.',
            'evidence':[{'source_id':'seed','quote':'We adapt the earlier method.'}]}]}
        result=self.check(review)
        self.assertEqual(len(result['unresolved_questions']),2)

    def test_abstract_delivery_does_not_establish_accepted_methods(self):
        self.sources['R1']['content_level']='abstract'; self.registry.write_text(json.dumps(self.sources))
        self.deliver(); self.assertTrue(self.check()['source_limitations'])
        with self.assertRaisesRegex(ValueError, 'Accepted claim'):
            self.check({'findings':[{'claim_id':'c','decision':'accept'}]})

    def test_manual_broadening_requires_reason_and_full_nomination_retained(self):
        with self.assertRaisesRegex(ValueError, 'reason'):
            read_source(self.log,self.registry,self.sources,'R1',question='Context?',start=0)
        legacy=self.root/'nominate.jsonl'
        record_read(legacy,self.registry,self.sources,{'source_id':'seed','start':0,'text':self.sources['seed']['text']})
        result=check_policy(legacy,self.registry,self.sources,['seed'],role='nominate',reading_policy='focused')
        self.assertTrue(result['papers']['seed']['complete_text_delivered'])

    def test_multiple_occurrences_can_use_second_context(self):
        text='First scope.\n\nSame quote.\n\nException.\n\nSecond scope.\n\nSame quote.\n\nValid context.'
        self.assertEqual(len(context_ranges(text,'Same quote.')),2)

    def test_opt_in_prompt_replaces_full_delivery_only_after_nomination(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'citation_lineage_pilot'))
        from prompts import CHANGE_COMMON, CHANGE_ROLES
        for role in ('analyse','review'):
            old=CHANGE_COMMON+'\n'+CHANGE_ROLES[role]
            new=focused_instructions(role,old)
            self.assertNotIn('Tool paging\nreceipts are checked for full-text coverage.',new)
            self.assertNotIn('Read the full seed paper and every acquired earlier full paper.',new)
            self.assertIn('An author is not its reviewer.',new)
            self.assertIn('If focused retrieval cannot resolve',new)
        old=CHANGE_COMMON+'\n'+CHANGE_ROLES['nominate']
        self.assertEqual(focused_instructions('nominate',old),old)

    def test_compact_default_has_no_paragraph_previews_and_reports_more_rows(self):
        self.sources['seed']['text']='\n\n'.join(
            f'[b{i:05}] Heading {i}' if i%3==0 else f'Paragraph {i}. See Fig. 1 and Supplementary Table 2.'
            for i in range(900))
        result=outline(self.sources,'seed')
        self.assertLess(len(json.dumps(result)),4096)
        self.assertNotIn('blocks',result)
        self.assertGreater(result['sections']['total'],len(result['sections']['items']))
        self.assertIsNotNone(result['sections']['next_start'])
        self.assertEqual(result['objects']['total'],2)
        self.assertGreater(result['mention_count'],result['objects']['total'])

    def test_all_blocks_and_deduplicated_mentions_have_lossless_paging(self):
        text='\n\n'.join(f'Paragraph {i}. Fig. 1; Table 2; Fig. 1.' for i in range(35))
        self.sources['seed']['text']=text
        for detail in ('blocks','mentions'):
            rows=[]; start=0
            while True:
                result=outline(self.sources,'seed',detail=detail,start=start,limit=7)
                page=result['page'];rows+=page['items']
                if page['next_start'] is None:
                    break
                start=page['next_start']
            self.assertEqual(len(rows),page['total'])
            if detail=='blocks':
                self.assertEqual([(r['start'],r['end']) for r in rows],blocks(text))
            else:
                self.assertEqual(len(rows),105)
                self.assertTrue(all(text[r['start']:r['end']]==r['text'] for r in rows))
                groups=outline(self.sources,'seed')['objects']['items']
                self.assertEqual(sum(g['count'] for g in groups),len(rows))

    def test_registered_manifest_used_before_flat_heading_heuristic(self):
        self.sources['seed']['document_manifest']={'methods_sections':[
            {'heading':'Methods','locator':'./body/sec[2]'}],
            'supplements':[{'element_id':'S1','description':'Exact registered supplement'}]}
        result=outline(self.sources,'seed')
        self.assertEqual(result['navigation_basis'],'existing JATS manifest')
        self.assertEqual(result['sections']['items'][0]['locator'],'./body/sec[2]')
        self.assertEqual(result['supplements']['total'],1)

    def test_review_only_patch_prompt_library_and_supported_script_entrypoint(self):
        root=Path(__file__).resolve().parents[2]
        candidate=Path(__file__).resolve().parent
        for name in ('run.py','models.py','change_models.py','prompts.py'):
            dest=self.root/'experiments/citation_lineage_pilot'/name
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(root/'experiments/citation_lineage_pilot'/name,dest)
        dest=self.root/'experiments/history_contract_audit_20261113/focused_reading.py'
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(candidate/'focused_reading.py',dest)
        for name in ('integration.patch','native_integration.patch'):
            patch=candidate/name
            if not patch.is_file():
                continue
            pending=subprocess.run(['git','apply','--check',str(patch)],cwd=self.root,capture_output=True,text=True)
            if pending.returncode==0:
                subprocess.run(['git','apply',str(patch)],cwd=self.root,check=True,capture_output=True,text=True)
                break
            applied=subprocess.run(['git','apply','--reverse','--check',str(patch)],cwd=self.root,capture_output=True,text=True)
            if applied.returncode==0:
                break
        else:
            self.fail('Neither staged development nor native adaptation patch matches the runtime')
        env={**os.environ,'PYTHONPATH':str(root/'src')}
        code="""from experiments.citation_lineage_pilot.prompts import build_prompt
import sys
before=list(sys.path)
p=build_prompt('review',{'analysis_format':'citation_analysis/4','reading_policy':'focused'})
assert 'FOCUSED READING POLICY' in p
assert 'Independently read the full seed and the full earlier papers' not in p
assert sys.path==before
"""
        subprocess.run([sys.executable,'-c',code],cwd=self.root,env=env,check=True,capture_output=True,text=True)
        # Runtime environment must include repo+src when invoked as a script.
        env['PYTHONPATH']=str(self.root)+os.pathsep+str(root/'src')
        result=subprocess.run([sys.executable,str(self.root/'experiments/citation_lineage_pilot/run.py'),'read',
            '--sources',str(self.registry),'--coverage-file',str(self.root/'cli_reads.jsonl'),
            '--id','seed','--reading-policy','focused','--outline'],
            cwd=self.root,env=env,check=True,capture_output=True,text=True)
        self.assertEqual(json.loads(result.stdout)['source_id'],'seed')


if __name__ == '__main__':
    unittest.main()

"""Method-level evidence remains visible when a donor has no extracted workflow."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from genomics_discovery.illumina.history_view import build, view_data, change_text_pin
from genomics_discovery.illumina.history import technology_references


def example():
    return {'nodes': {
        'earlier': {'name': 'Earlier assay', 'kind': 'assay', 'reference_id': 'R1'},
        'component': {'name': 'Earlier enzyme', 'kind': 'experimental_component'},
        'later': {'name': 'Later assay', 'kind': 'bound_workflow', 'record_id': 'later'}},
        'edges': [{'id': 'edge', 'source': 'earlier', 'target': 'later',
            'relation': 'documented_reuse', 'review': {'decision': 'accept'},
            'claim': {'change_records': []}, 'artifact': {'source_registry': {
                'sources': {'R1': {'pmid': '123', 'title': 'Earlier paper'}}}}}]}


class HistoryViewTests(unittest.TestCase):
    def test_missing_workflow_does_not_remove_reviewed_edge(self):
        source = example(); before = copy.deepcopy(source)
        value = view_data(source)
        self.assertEqual(value['edges'][0]['source'], 'earlier')
        self.assertEqual(value['edges'][0]['target'], 'later')
        self.assertEqual(value['nodes']['earlier']['workflow_status'], 'not_extracted_or_not_matched')
        self.assertEqual(source, before)

    def test_component_is_not_displayed_as_unextracted_assay(self):
        value = view_data(example())
        self.assertEqual(value['nodes']['component']['workflow_status'], 'component_or_other_contribution')
        self.assertEqual(value['nodes']['later']['workflow_status'], 'extracted')

    def test_same_names_do_not_merge_methods(self):
        source = example(); source['nodes']['second'] = dict(source['nodes']['earlier'])
        self.assertEqual(len(view_data(source)['nodes']), 4)

    def test_review_hold_cannot_appear_as_accepted_edge(self):
        source = example(); source['edges'][0]['review']['decision'] = 'needs_evidence'
        with self.assertRaisesRegex(ValueError, 'reviewed accepted'):
            view_data(source)

    def test_missing_endpoint_is_rejected(self):
        source = example(); del source['nodes']['earlier']
        with self.assertRaisesRegex(ValueError, 'endpoint missing'):
            view_data(source)

    def test_export_preserves_json_and_escapes_source_html(self):
        source = example(); source['nodes']['earlier']['name'] = '</script><script>bad()</script>'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); p = root/'input.json'
            p.write_text(json.dumps(source)); result = build(p, root/'view')
            self.assertEqual((root/'view/history_index.json').read_bytes(), p.read_bytes())
            self.assertNotIn('</script><script>bad()', (root/'view/index.html').read_text())
            self.assertFalse(result['scientific_records_changed'])

    def test_editorial_copy_is_pinned_and_keeps_scientific_record_unchanged(self):
        source=example();source['edges'][0]['claim']['id']='c1'
        change={'id':'d1','compared_feature':'cDNA construction','before':'Circularized cDNA',
            'after':'Direct adapter ligation','evidence':{'before':[],'after':[],'inheritance':[]},
            'consequence_and_limits':{'reported_effects':[{'description':'Protocol result',
                'attribution_scope':'whole_assay','comparator':'Local old protocol','reported_value':'5 ng','evidence':[]}],
                'limits':['Sparse coverage'],'unresolved':['Exact release reagent unavailable']}}
        source['edges'][0]['claim']['change_records']=[change]
        edits={'edits':[{'record_id':'later','claim_id':'c1','change_id':'d1',
            'source_change_sha256':change_text_pin(change),'text':{
                'compared_feature':'The later assay replaces circularization with adapter ligation',
                'after':'The later assay directly ligates the adapter.',
                'effect_descriptions':['The complete assay yielded 5 ng against the local protocol.'],
                'scope_notes':['Coverage is sparse; the exact release reagent remains unavailable.']}}]}
        before=copy.deepcopy(source);value=view_data(source,edits)
        self.assertEqual(source,before)
        self.assertEqual(value['edges'][0]['claim'],before['edges'][0]['claim'])
        reader=value['edges'][0]['reader_changes'][0]
        original=change['consequence_and_limits']['reported_effects'][0]
        rendered=reader['consequence_and_limits']['reported_effects'][0]
        for key in ('attribution_scope','comparator','reported_value','evidence'):
            self.assertEqual(original[key],rendered[key])
        self.assertIn('exact release reagent',reader['reader_scope_notes'][0])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);original=root/'input.json';original.write_text(json.dumps(source))
            edit_file=root/'edits.json';edit_file.write_text(json.dumps(edits,indent=1)+'\n')
            receipt=build(original,root/'reader',edit_file)
            self.assertEqual((root/'reader/reader_edits.json').read_bytes(),edit_file.read_bytes())
            self.assertEqual(receipt['reader_edits_sha256'],hashlib.sha256(edit_file.read_bytes()).hexdigest())
            self.assertTrue(receipt['canonical_record_bytes_preserved'])
            self.assertTrue(receipt['editorial_projection_requires_review'])
        edits['edits'][0]['source_change_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'exact saved change'):view_data(source,edits)

    def test_reference_query_does_not_turn_context_into_ancestry(self):
        source=example();source['nodes']['later']['id']='later'
        source['reference_ledgers']=[{'target':'later','artifact':{'sha256':'source'},'ledger':{
            'references':[{'reference_id':'R1','classification':'comparison'},
                          {'reference_id':'R2','classification':'background'}]}}]
        before=copy.deepcopy(source)
        result=technology_references(source,'later',reference_id='R2')
        self.assertEqual(result['reference_ledgers'][0]['ledger']['references'],[
            {'reference_id':'R2','classification':'background'}])
        self.assertEqual(source,before)

    def test_reader_has_no_injected_empty_result_or_component_lecture(self):
        from genomics_discovery.illumina.history_view import HTML
        self.assertNotIn('No separate performance benefit',HTML)
        self.assertNotIn('not a demonstrated evolutionary relationship',HTML)
        self.assertIn('Whole-assay results',HTML)

    def test_real_editorial_examples_keep_release_uncertainty_and_assay_scope(self):
        root=Path(__file__).resolve().parents[2]
        source=json.loads((root/'experiments/history_contract_audit_20261113/reader_example_index.json').read_text())
        edits=json.loads((root/'docs/illumina/history_reader_edits.json').read_text())
        before=copy.deepcopy(source);data=view_data(source,edits)
        self.assertEqual(source,before)
        changes={c['id']:c for edge in data['edges'] for c in edge['reader_changes']}
        self.assertEqual(changes['R6_capture']['compared_feature'],'Retained RBP capture and purification')
        self.assertEqual(changes['R6_capture']['consequence_and_limits']['reported_effects'],[])
        self.assertIn('replaces cDNA circularization',changes['R6_truncated_library']['compared_feature'])
        effect=changes['R6_truncated_library']['consequence_and_limits']['reported_effects'][0]
        self.assertEqual(effect['attribution_scope'],'whole_assay')
        self.assertIn('RBFOX2',effect['comparator'])
        release=changes['R13.post_transposition_handling']
        self.assertNotIn('figure caption',release['after'])
        self.assertTrue(any('final on-chip release reagent' in x for x in release['reader_scope_notes']))
        self.assertTrue(any('condition-specific fold gains' in x for x in release['reader_scope_notes']))


if __name__ == '__main__':
    unittest.main()

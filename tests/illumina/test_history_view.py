"""Method-level evidence remains visible when a donor has no extracted workflow."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from genomics_discovery.illumina.history_view import build, view_data


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


if __name__ == '__main__':
    unittest.main()

"""Controls for intake routing that could otherwise trigger unwanted acquisition."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from intake import run, stage, validate_publication


ROOT = Path(__file__).resolve().parent


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads((ROOT / 'fixtures.json').read_text())['publications']

    def test_real_abstract_controls(self):
        result = run(ROOT / 'fixtures.json')
        self.assertEqual(result['publications_screened'], 7)
        routes = result['routes']
        self.assertEqual(routes[0]['methods'][0]['route'], 'candidate_for_bounded_acquisition')
        self.assertEqual(routes[1]['methods'][0]['route'], 'candidate_for_bounded_acquisition')
        self.assertEqual(routes[2]['route'], 'component_source_candidate')
        self.assertEqual(routes[3]['route'], 'context_only')
        self.assertEqual(routes[4]['route'], 'context_only')
        self.assertEqual(routes[5]['route'], 'focused_methods_review')
        self.assertEqual(routes[6]['methods'][0]['route'], 'existing_method_source_update')
        self.assertTrue(all(r['extraction_authorized'] is False for r in routes))

    def test_fabricated_passage_fails(self):
        row = copy.deepcopy(self.rows[0]); row['evidence'][0]['passage'] = 'invented target facts'
        with self.assertRaisesRegex(ValueError, 'passage absent'):
            validate_publication(row, ROOT)

    def test_conflicting_doi_fails(self):
        row = copy.deepcopy(self.rows[0]); row['identity']['doi'] = '10.1/wrong'
        with self.assertRaisesRegex(ValueError, 'identifier'):
            validate_publication(row, ROOT)

    def test_changed_receipt_fails(self):
        row = copy.deepcopy(self.rows[0]); row['metadata_receipt']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'changed pinned input'):
            validate_publication(row, ROOT)

    def test_no_experiment_no_assay(self):
        row = copy.deepcopy(self.rows[0]); row['screening']['criteria']['experimental_demonstration']['status'] = 'unclear'
        with self.assertRaisesRegex(ValueError, 'three positive criteria'):
            validate_publication(row, ROOT)

    def test_unresolved_needs_focused_question(self):
        row = copy.deepcopy(self.rows[5]); row['screening'].pop('focused_review_question')
        with self.assertRaisesRegex(ValueError, 'focused methods'):
            validate_publication(row, ROOT)

    def test_unknown_identity_stays_out_of_acquisition(self):
        row = copy.deepcopy(self.rows[0]); row['method_proposals'][0]['identity_review']['status'] = 'pending'
        validate_publication(row, ROOT)
        self.assertEqual(stage(row)['methods'][0]['route'], 'identity_review_required')

    def test_homonym_stays_out_of_acquisition(self):
        row = copy.deepcopy(self.rows[0]); row['method_proposals'][0]['identity_review']['status'] = 'homonym_conflict'
        validate_publication(row, ROOT)
        self.assertEqual(stage(row)['methods'][0]['route'], 'identity_review_required')

    def test_absence_requires_original_inventory_pin(self):
        row = copy.deepcopy(self.rows[0]); row['method_proposals'][0]['identity_review'].pop('inventory_pin')
        with self.assertRaisesRegex(ValueError, 'Absence needs'):
            validate_publication(row, ROOT)

    def test_older_predecessor_cannot_enter_date_filter(self):
        row = copy.deepcopy(self.rows[0]); row['identity']['publication_year'] = 2016
        # Change the matched year observation as well, isolating the scope gate.
        with tempfile.TemporaryDirectory() as directory:
            receipt = json.loads((ROOT / row['metadata_receipt']['path']).read_text())
            receipt['identity']['publication_year'] = 2016
            path = Path(directory) / 'metadata.json'; path.write_text(json.dumps(receipt))
            from intake import digest
            row['metadata_receipt'] = {'path': str(path), 'sha256': digest(path)}
            with self.assertRaisesRegex(ValueError, 'Older work'):
                validate_publication(row, ROOT)

    def test_predecessor_requires_verified_need(self):
        row = copy.deepcopy(self.rows[0]); row['method_proposals'][0]['scope_track'] = 'verified_missing_predecessor'
        with self.assertRaisesRegex(ValueError, 'specific supported role'):
            validate_publication(row, ROOT)

    def test_pending_predecessor_is_not_acquisition_ready(self):
        row = copy.deepcopy(self.rows[0]); row['method_proposals'][0]['scope_track'] = 'missing_predecessor_candidate'
        validate_publication(row, ROOT)
        self.assertEqual(stage(row)['methods'][0]['route'], 'predecessor_verification_required')

    def test_multiple_methods_one_publication(self):
        row = copy.deepcopy(self.rows[0]); second = copy.deepcopy(row['method_proposals'][0])
        second['candidate_id'] = 'second-scoped-method'; second['name'] = 'Second scoped method'
        row['method_proposals'].append(second)
        validate_publication(row, ROOT)
        self.assertEqual(len(stage(row)['methods']), 2)

    def test_version_change_needs_actual_difference(self):
        row = copy.deepcopy(self.rows[6]); row['method_proposals'][0]['identity_review']['status'] = 'distinct_version'
        with self.assertRaisesRegex(ValueError, 'supported assay-level difference'):
            validate_publication(row, ROOT)

    def test_multi_domain_classification_is_not_duplicate_identity(self):
        row = self.rows[1]
        self.assertEqual(len(row['method_proposals'][0]['domain_tags']), 2)
        self.assertEqual(len(stage(row)['methods']), 1)


if __name__ == '__main__':
    unittest.main()

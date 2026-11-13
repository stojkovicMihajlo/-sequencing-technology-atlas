import copy
import json
from pathlib import Path
import unittest

from intake import run,validate_publication
from live_pilot import output_schema

OWN=Path(__file__).resolve().parent


class LiveImportTests(unittest.TestCase):
    def test_both_actual_screens_roundtrip_without_reclassification(self):
        for role in ('author','independent_reviewer'):
            native=json.loads((OWN/'observed_52176596/screens'/role/'artifact.json').read_text())['papers']
            manifest=json.loads((OWN/'imported_52176596'/f'{role}.json').read_text())
            self.assertEqual([r['native_judgment_unchanged'] for r in manifest['publications']],native)
            for row in manifest['publications']:
                self.assertEqual(row['screening']['classification'],row['native_judgment_unchanged']['classification'])
                self.assertEqual(row['screening']['publication_role'],row['native_judgment_unchanged']['publication_role'])
            self.assertEqual(run(OWN/'imported_52176596'/f'{role}.json')['publications_screened'],7)

    def test_original_software_paper_role_is_independent_of_assay_class(self):
        row=json.loads((OWN/'fixtures.json').read_text())['publications'][3]
        row=copy.deepcopy(row);row['screening']['publication_role']='primary_methods'
        validate_publication(row,OWN)
        self.assertEqual(row['screening']['classification'],'software_only')

    def test_existing_scnmt_and_pending_proposals_cannot_dispatch_new_acquisition(self):
        result=run(OWN/'imported_52176596/author.json')
        for route in result['routes']:
            self.assertFalse(route['extraction_authorized'])
            for proposal in route.get('methods',[]):
                expected='existing_method_source_update' if proposal['candidate_id']=='scnmt-seq' else 'identity_review_required'
                self.assertEqual(proposal['route'],expected)

    def test_component_source_role_removed_from_publication_schema(self):
        roles=output_schema()['properties']['papers']['items']['properties']['publication_role']['enum']
        self.assertIn('primary_methods',roles);self.assertNotIn('component_dependency',roles)

    def test_tn5_assay_screen_and_component_source_use_both_retained(self):
        rows=json.loads((OWN/'imported_52176596/author.json').read_text())['publications']
        tn5=next(r for r in rows if r['identity']['pmid']=='28095828')
        self.assertEqual(tn5['screening']['classification'],'assay')
        self.assertTrue(any(a['role']=='component_dependency' and a['target']=='nextera-library-preparation' for a in tn5['source_associations']))
        self.assertEqual(tn5['method_proposals'][0]['identity_review']['status'],'pending')


if __name__=='__main__':unittest.main()

import json
from pathlib import Path
import tempfile
import unittest
from import_data import publish


class SnapshotTests(unittest.TestCase):
    def test_new_cards_and_revisions_use_same_ui_without_overwriting_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / 'source', root / 'output'
            source.mkdir()
            def save(names, label):
                rows = []
                for name in names:
                    record = {'record_id':name, 'candidate':{'states':[{'id':'s','label':label}], 'operations':[], 'flows':[]}}
                    (source / f'{name}.json').write_text(json.dumps(record))
                    rows.append({'record_id':name,'file':f'{name}.json','title':name,'status':'supported'})
                (source / 'index.json').write_text(json.dumps({'records':rows}))
            save(['a'], 'Earlier material')
            first = publish(source, output, 'first', 'First run')
            save(['a','b'], 'Revised material')
            second = publish(source, output, 'second', 'Second run')
            self.assertEqual(len(second['records']), 2)
            self.assertEqual(json.loads((output/'current.json').read_text())['manifest'], 'second/manifest.json')
            self.assertNotEqual(first['records'][0]['sha256'],second['records'][0]['sha256'])
            self.assertIn('Earlier material',(output/'first'/first['records'][0]['file']).read_text())
            with self.assertRaises(ValueError): publish(source,output,'second','Overwrite')
            # A bad newer import must not replace the working snapshot.
            broken=json.loads((source/'a.json').read_text())
            broken['candidate']['flows']=[{'id':'f','source':'s','target':'missing'}]
            (source/'a.json').write_text(json.dumps(broken))
            with self.assertRaises(ValueError): publish(source,output,'third','Broken')
            self.assertEqual(json.loads((output/'current.json').read_text())['manifest'], 'second/manifest.json')

    def test_duplicate_method_identity_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'a.json').write_text(json.dumps({'record_id':'a','candidate':None}))
            row={'record_id':'a','file':'a.json','status':'failed'}
            (root/'index.json').write_text(json.dumps({'records':[row,row]}))
            with self.assertRaisesRegex(ValueError,'Duplicate method identity'):
                publish(root,root/'out','test','Test')


if __name__ == '__main__': unittest.main()

import json
from pathlib import Path
import tempfile
import unittest
from import_data import publish
from export_evolution import export
import hashlib


class SnapshotTests(unittest.TestCase):
    def test_context_references_export_without_manufacturing_evolution_edges(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);index=root/'index.json';receipt=root/'receipt.json'
            value={'nodes':{'method':{'kind':'bound_workflow','record_id':'m','name':'m'}},'edges':[],
                   'reference_ledgers':[{'target':'method','artifact':{'sha256':'source'},'ledger':{
                       'references':[{'reference_id':'R1','classification':'background','citation_contexts':['Motivation.']}]
                   }}]}
            index.write_text(json.dumps(value));pin=hashlib.sha256(index.read_bytes()).hexdigest()
            receipt.write_text(json.dumps({'scientific_records_changed':False,'view':{'history_index_sha256':pin}}))
            counts=export(index,receipt,root/'out','refs','References')
            manifest=json.loads((root/'out/refs/manifest.json').read_text())
            self.assertEqual(counts['experimental_relationships'],0)
            self.assertEqual(manifest['edges'],[])
            ledger=json.loads((root/'out/refs'/manifest['reference_ledgers'][0]['file']).read_text())
            self.assertEqual(ledger['ledger']['references'][0]['classification'],'background')
            self.assertEqual(json.loads(index.read_text()),value)

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

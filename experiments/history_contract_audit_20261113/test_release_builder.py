"""Contents-only release adaptation tests; no models or remote writes."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from experiments.history_contract_audit_20261113 import build_pilot_release as builder

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReleaseBuilderTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.base=self.root/'old';self.payload=self.root/'payload'
        for rel in ('experiments/citation_lineage_pilot/run.py','experiments/citation_lineage_pilot/prompts.py'):
            out=self.base/rel;out.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(HERE/'native_base'/rel,out)
        (self.base/'unchanged.txt').write_bytes(b'untouched baseline bytes\n')
        self.exp=self.payload/'experiments/history_contract_audit_20261113';self.exp.mkdir(parents=True)
        for name in ('focused_reading.py','native_integration.patch','native_integration_expected_pins.json','native_base_receipt.json'):
            shutil.copyfile(HERE/name,self.exp/name)
        helper=self.payload/'experiments/method_intake_audit_20261113';helper.mkdir(parents=True)
        shutil.copyfile(ROOT/'experiments/method_intake_audit_20261113/preserve_bytes.py',helper/'preserve_bytes.py')
        self.source=self.root/'synthetic_prior';self.source.mkdir();(self.source/'sources.json').write_text('{"original":"source bytes"}\n')
        plan={'reuse':{'synthetic':{'previous_run':str(self.source),'pins':{'sources.json':sha(self.source/'sources.json')}}}}
        (self.exp/'pilot_plan.json').write_text(json.dumps(plan))

    def test_adapts_exact_old_two_files_and_preserves_other_bytes(self):
        before={str(p.relative_to(self.base)):sha(p) for p in self.base.rglob('*') if p.is_file()}
        with patch.object(builder,'HERE',self.exp):
            receipt=builder.build(self.base,self.root/'new',self.payload)
        self.assertEqual({str(p.relative_to(self.base)):sha(p) for p in self.base.rglob('*') if p.is_file()},before)
        self.assertEqual((self.root/'new/unchanged.txt').read_bytes(),(self.base/'unchanged.txt').read_bytes())
        self.assertEqual(receipt['status'],'software_release_prepared_no_models')
        self.assertEqual(set(receipt['changed_baseline_code_files']),{
            'experiments/citation_lineage_pilot/run.py','experiments/citation_lineage_pilot/prompts.py'})
        self.assertTrue((self.root/'new.base-copy.json').is_file())

    def test_changed_input_refused_before_release_copy(self):
        (self.source/'sources.json').write_text('changed')
        with patch.object(builder,'HERE',self.exp),self.assertRaisesRegex(ValueError,'input changed'):
            builder.build(self.base,self.root/'new',self.payload)
        self.assertFalse((self.root/'new').exists())


if __name__=='__main__':
    unittest.main()

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from preserve_bytes import preserve_tree


class PreservationTests(unittest.TestCase):
    def source(self, root):
        src=root/'source';src.mkdir();(src/'empty').mkdir();(src/'nested').mkdir()
        (src/'nested/data.bin').write_bytes(b'\x00scientific artifact\xff')
        (src/'events.jsonl').write_text('{"unchanged":"native record"}\n')
        return src

    def test_copy_no_attribute_operations_and_all_hashes_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=self.source(root)
            with patch('os.chmod',side_effect=PermissionError('unsupported')),patch('os.chown',side_effect=PermissionError('unsupported')),patch('os.utime',side_effect=PermissionError('unsupported')):
                result=preserve_tree(src,root/'durable')
            self.assertEqual(result['file_count'],2);self.assertTrue(result['all_destination_hashes_verified'])
            self.assertTrue((root/'durable/empty').is_dir())
            self.assertEqual((src/'events.jsonl').read_bytes(),(root/'durable/events.jsonl').read_bytes())

    def test_resume_matching_partial_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=self.source(root);dst=root/'durable';dst.mkdir()
            (dst/'events.jsonl').write_bytes((src/'events.jsonl').read_bytes())
            first=preserve_tree(src,dst);second=preserve_tree(src,dst)
            self.assertEqual(first,second)

    def test_differing_destination_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=self.source(root);dst=root/'durable';dst.mkdir()
            (dst/'events.jsonl').write_text('prior preserved bytes')
            with self.assertRaisesRegex(ValueError,'Differing destination'):preserve_tree(src,dst)
            self.assertEqual((dst/'events.jsonl').read_text(),'prior preserved bytes')

    def test_symlink_source_rejected_without_dereference(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=self.source(root);(src/'link').symlink_to(src/'events.jsonl')
            with self.assertRaisesRegex(ValueError,'Symlink/special'):preserve_tree(src,root/'durable')

    def test_original_manifest_preserved_with_external_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=self.source(root);(src/'PRESERVATION_MANIFEST.json').write_text('original manifest')
            with self.assertRaisesRegex(ValueError,'replace original source'):preserve_tree(src,root/'bad')
            preserve_tree(src,root/'durable',manifest_path=root/'receipt.json')
            self.assertEqual((root/'durable/PRESERVATION_MANIFEST.json').read_text(),'original manifest')

    def test_recursive_copy_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=self.source(root)
            with self.assertRaisesRegex(ValueError,'separate'):preserve_tree(src,src/'recursive')


if __name__ == '__main__':unittest.main()

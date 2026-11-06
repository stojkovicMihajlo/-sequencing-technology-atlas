"""History publication cache keeps exact paper identity and changed-byte holds."""
import json
from pathlib import Path

from experiments.history_changes_20261106.run import reference_cache
from genomics_discovery.illumina.extraction import sha
from genomics_discovery.illumina.sources import jats_text


def test_reference_cache_reuses_registered_full_paper_and_records_corrupt_source(tmp_path):
    snapshot, library, vault = (tmp_path / p for p in ('snapshot', 'library', 'vault'))
    for directory in (snapshot, library, vault): directory.mkdir()
    article = tmp_path / 'primary.xml'
    article.write_text('<article><front><article-meta><article-id pub-id-type="pmid">123</article-id>'
        '<title-group><article-title>Earlier technique</article-title></title-group></article-meta></front>'
        '<body><sec><title>Methods</title><p>Reported experimental transformation.</p></sec></body></article>')
    text, _ = jats_text(article.read_bytes())
    source = {'kind': 'text', 'content_level': 'fulltext', 'pmid': '123', 'text': text,
              'original': {'path': str(article), 'sha256': sha(article)}}
    broken = {**source, 'pmid': '456', 'original': {**source['original'], 'sha256': '0' * 64}}
    attempt = tmp_path / 'attempt'; attempt.mkdir()
    (attempt / 'source_registry.json').write_text(json.dumps({'valid': source, 'changed': broken}))
    (snapshot / 'state.json').write_text(json.dumps({'cards': {'method': {'attempt_directory': str(attempt)}}}))
    (library / 'canonical_inventory.json').write_text(json.dumps({'methods': {}}))
    output = reference_cache(snapshot, library, vault, tmp_path / 'cache.json')
    papers = json.loads(output.read_text())['papers']
    assert list(papers) == ['123'] and papers['123']['text'] == text
    receipt = json.loads((tmp_path / 'reference_cache_receipt.json').read_text())
    assert receipt['skipped'][0]['pmid'] == '456' and receipt['publications'] == 1
    assert not (attempt / 'state.json').exists()  # cache construction does not edit a workflow

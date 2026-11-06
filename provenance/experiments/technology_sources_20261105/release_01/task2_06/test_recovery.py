"""Reject abstract-only and wrong-article responses before source admission."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import pytest

spec = importlib.util.spec_from_file_location('remaining_recovery', Path(__file__).with_name('recover_remaining.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('pmid,body,expected', [
    ('1', '<body><p>' + 'Procedure and material evidence. ' * 150 + '</p></body>', True),
    ('2', '<body><p>' + 'Procedure and material evidence. ' * 150 + '</p></body>', False),
    ('1', '', False),
    ('1', '<body/>', False),
])
def test_full_article_identity_and_substantial_body(tmp_path, pmid, body, expected):
    raw = ('<pmc-articleset><article><front><article-meta><article-id pub-id-type="pmid">' + pmid +
           '</article-id><article-id pub-id-type="doi">10.1/example</article-id>'
           '<title-group><article-title>Assigned method</article-title></title-group>'
           '</article-meta></front>' + body + '</article></pmc-articleset>').encode()
    def fake_fetch(url, path, **kwargs):
        path.write_bytes(raw)
        return {'request_url': url, 'sha256': module.sha(path)}
    paper = {'id': 'pmid-1', 'pmid': '1', 'title': 'Assigned method', 'status': 'metadata_only',
             'files': [], 'metadata': {'identifiers': {'doi': ['10.1/example']}}}
    state = {'files': {}}
    with patch.object(module, 'fetch', fake_fetch):
        result = module.recover_xml(tmp_path, state, paper, 'PMC1', tmp_path)
    assert result['admitted'] is expected
    assert (paper['status'] == 'fulltext_acquired') is expected
    if expected:
        assert any(f['kind'] == 'original-api-response' for f in paper['files'])
        assert len(paper['files']) == 4
    else:
        assert paper['files'] == [] and state['files'] == {}

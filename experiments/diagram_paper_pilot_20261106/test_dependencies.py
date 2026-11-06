import copy

import pytest

from experiments.diagram_paper_pilot_20261106 import recover_dependencies as mod
from genomics_discovery.illumina.extraction import read, write, verify_registry_files


@pytest.mark.parametrize('defect', [None, 'parent_quote', 'article_identity', 'abstract_only'])
def test_dependency_registration_requires_delegation_own_identity_and_full_body(tmp_path, monkeypatch, defect):
    old = tmp_path / 'previous'
    write(old / 'cards/method/attempt_001/source_registry.json', {
        'paper:1': {'kind': 'text', 'content_level': 'fulltext',
                    'text': 'The assay follows Earlier et al. (2011).'}})
    request = {'method_id': 'method', 'parent_source_id': 'paper:1',
               'parent_quote': 'follows Earlier et al. (2011)', 'requested_pmid': '2',
               'requested_pmcid': 'PMC2', 'requested_doi': '10.1/earlier',
               'association_scope': 'Delegated method dependency, not a new target'}
    if defect == 'parent_quote':
        request['parent_quote'] = 'An invented delegation'
    request_path = tmp_path / 'requests.json'
    write(request_path, {'format_version': 'method_source_requests/1', 'requests': [request]})
    calls = []

    def acquire(publication, output):
        calls.append(copy.deepcopy(publication))
        output.mkdir(parents=True)
        body = '' if defect == 'abstract_only' else '<body><p>Reported method.</p></body>'
        pmid = '999' if defect == 'article_identity' else '2'
        xml = ('<article><front><article-meta><article-id pub-id-type="pmid">' + pmid +
               '</article-id><article-id pub-id-type="doi">10.1/earlier</article-id>' +
               '<title-group><article-title>Earlier assay</article-title></title-group>' +
               '</article-meta></front>' + body + '</article>')
        (output / 'article.xml').write_text(xml)
        (output / 'text.txt').write_text('Reported method.')
        write(output / 'blocks.json', {})
        (output / 'oai.xml').write_text(xml)
        return {'status': 'fulltext_acquired', 'path': 'article.xml', 'text_path': 'text.txt',
                'blocks_path': 'blocks.json', 'original_response': {'path': 'oai.xml'}}

    monkeypatch.setattr(mod, 'acquire_pmc_fulltext', acquire)
    monkeypatch.setenv('SLURM_JOB_ID', 'fake-dependency-test')
    if defect:
        with pytest.raises(ValueError):
            mod.recover(request_path, old, tmp_path / 'recovery')
        if defect == 'parent_quote':
            assert not calls
    else:
        result = mod.recover(request_path, old, tmp_path / 'recovery')
        assert set(result['method']) == {'paper:2'}
        verify_registry_files(result['method'])
        assert read(tmp_path / 'recovery/registered_additions.json') == result

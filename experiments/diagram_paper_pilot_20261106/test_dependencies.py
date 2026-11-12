import copy
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZipFile

import pytest

from experiments.diagram_paper_pilot_20261106 import recover_dependencies as mod
from genomics_discovery.illumina.extraction import read, write, verify_registry_files
from genomics_discovery.illumina.extraction import sha


def test_archive_acquisition_is_shared_between_concurrent_attachment_requests(tmp_path, monkeypatch):
    calls = []
    def fetch(url, destination):
        calls.append(url)
        with ZipFile(destination, 'w') as archive:
            archive.writestr('one.pdf', b'%PDF-first')
            archive.writestr('two.pdf', b'%PDF-second')
        return {'url': url, 'sha256': sha(destination)}
    monkeypatch.setattr(mod, 'fetch', fetch)
    cache = tmp_path / 'cache'
    with ThreadPoolExecutor(max_workers=3) as workers:
        rows = list(workers.map(lambda _: mod.cached_supplement_archive('https://example.org/PMC1', cache), range(3)))
    assert calls == ['https://example.org/PMC1']
    assert sum(not row[2]['cache_reused'] for row in rows) == 1
    assert len({row[0] for row in rows}) == 1
    assert mod.selected_pdf(rows[0][0].read_bytes(), 'one.pdf')[0] == b'%PDF-first'
    assert mod.selected_pdf(rows[1][0].read_bytes(), 'two.pdf')[0] == b'%PDF-second'
    other, _, _ = mod.cached_supplement_archive('https://example.org/PMC2', cache)
    assert other != rows[0][0]
    assert len(calls) == 2


@pytest.mark.parametrize('failure', ['timeout', 'html', 'truncated_zip'])
def test_failed_archive_attempt_is_not_repeated_for_each_attachment(tmp_path, monkeypatch, failure):
    calls = []
    def fetch(url, destination):
        calls.append(url)
        if failure == 'timeout':
            raise RuntimeError('Timed out')
        destination.write_bytes(b'<html>Not an archive</html>' if failure == 'html' else b'PKtruncated')
        return {'url': url}
    monkeypatch.setattr(mod, 'fetch', fetch)
    for _ in range(3):
        with pytest.raises(ValueError, match='not repeated'):
            mod.cached_supplement_archive('https://example.org/PMC1', tmp_path)
    assert len(calls) == 1


def test_cached_archive_custody_is_checked_before_reuse(tmp_path, monkeypatch):
    def fetch(url, destination):
        with ZipFile(destination, 'w') as archive:
            archive.writestr('one.pdf', b'%PDF-first')
        return {'url': url}
    monkeypatch.setattr(mod, 'fetch', fetch)
    archive, _, _ = mod.cached_supplement_archive('https://example.org/PMC1', tmp_path)
    archive.write_bytes(b'Changed bytes')
    with pytest.raises(ValueError, match='differ from their receipt'):
        mod.cached_supplement_archive('https://example.org/PMC1', tmp_path)


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


@pytest.mark.parametrize('defect', [None, 'wrong_parent', 'wrong_attachment', 'public_copy', 'public_copy_badcover', 'archive'])
def test_supplement_is_bound_to_parent_identity_and_exact_attachment(tmp_path, monkeypatch, defect):
    original = tmp_path / 'parent.xml'
    original.write_text('<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>'
        '<article-id pub-id-type="pmid">2</article-id><article-id pub-id-type="pmcid">PMC2</article-id>'
        '<article-id pub-id-type="doi">10.1/example</article-id>'
        '<title-group><article-title>Unrelated method</article-title></title-group></article-meta></front>'
        '<body><p>Reported workflow.</p><supplementary-material id="S1">'
        '<media xlink:href="methods.pdf"/></supplementary-material></body></article>')
    source = {'pmid': '999' if defect == 'wrong_parent' else '2', 'title': 'Unrelated method',
              'custody_files': [{'path': str(original), 'sha256': sha(original)}]}
    request = {'attachment_id': 'absent' if defect == 'wrong_attachment' else 'S1',
               'parent_source_id': 'paper:2'}
    if defect in {'public_copy', 'public_copy_badcover'}:
        request['public_copy_url'] = 'https://example.org/public-supplement.pdf'
    calls = []
    def fetch(url, destination):
        calls.append(url); destination.parent.mkdir(parents=True, exist_ok=True)
        if defect == 'archive' and url.endswith('/supplementaryFiles'):
            with ZipFile(destination, 'w') as archive:
                archive.writestr('methods.pdf', b'%PDF-fixture')
        else:
            destination.write_bytes(b'<error>Not a downloadable document</error>'
                if defect == 'archive' or (defect in {'public_copy', 'public_copy_badcover'}
                    and 'example.org' not in url) else b'%PDF-fixture')
        return {'final_url': url, 'path': str(destination), 'sha256': sha(destination)}
    def converted(folder, pmid, output):
        output.mkdir(parents=True)
        path = output / 'text.txt'; path.write_text('Wrong publication' if defect == 'public_copy_badcover'
            else 'Supporting information for Unrelated method. DOI 10.1/example. Synthetic converter output.')
        return [{'path': str(path)}], [{'original': str(folder / 'supplements/methods.pdf')}], []
    monkeypatch.setattr(mod, 'fetch', fetch)
    monkeypatch.setattr(mod, 'supplements', converted)
    if defect in {'wrong_parent', 'wrong_attachment', 'public_copy_badcover'}:
        with pytest.raises(ValueError):
            mod.recover_supplement(request, source, tmp_path / 'supp')
        if defect != 'public_copy_badcover':
            assert not calls
    else:
        result = mod.recover_supplement(request, source, tmp_path / 'supp')
        assert result['document_role'] == 'supplement'
        assert result['parent_source_id'] == 'paper:2'
        assert result['pmid'] == '2'
        verify_registry_files({'supplement:2:S1': result})

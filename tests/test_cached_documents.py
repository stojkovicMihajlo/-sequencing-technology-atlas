"""Source acquisition receipts are not extraction answers or semantic evidence."""

import json

import pytest

from genomics_discovery.curation.cached_documents import primary_html, supplements
from genomics_discovery.curation.run_state import sha


def test_html_requires_actual_publication_identity_and_preserves_table_boundaries(tmp_path):
    folder = tmp_path / "paper"
    folder.mkdir()
    original = folder / "article.html"
    original.write_text('''<meta name="citation_pmid" content="123">
      <meta name="citation_title" content="Protocol">
      <script>not evidence</script><p>MgCl<sub>2</sub> at 10<sup>3</sup>.</p>
      <table><tr><td>Reagent</td><td>Amount</td></tr><tr><td>A</td><td>5</td></tr></table>''')
    source, receipt = primary_html(folder, "123", tmp_path / "rendition.txt")
    text = (tmp_path / "rendition.txt").read_text()
    assert "MgCl_(2)" in text and "10^(3)" in text
    assert "Reagent | Amount" in text and "not evidence" not in text
    assert source["role"] == "primary_paper"
    assert receipt["original_sha256"] == sha(original)
    assert receipt["rendition_sha256"] == sha(tmp_path / "rendition.txt")
    with pytest.raises(ValueError, match="citation_pmid"):
        primary_html(folder, "456", tmp_path / "bad.txt")
    assert not (tmp_path / "bad.txt").exists()


def test_only_registered_matching_supplements_become_sources(tmp_path):
    folder = tmp_path / "paper"
    supp = folder / "supplements"
    supp.mkdir(parents=True)
    original = supp / "procedure.txt"
    original.write_text("Wash with supplied buffer.")
    unregistered = supp / "model_answer.txt"
    unregistered.write_text("Invented buffer composition.")
    register = supp / "acquisition.json"
    register.write_text(json.dumps({"pmid": "123", "files": [
        {"status": "cached", "path": str(original), "sha256": sha(original), "url": "https://example.org/supp"},
        {"status": "unavailable", "url": "https://example.org/missing.pdf"}]}))
    sources, receipts, leads = supplements(folder, "123", tmp_path / "renditions")
    assert len(sources) == len(receipts) == 1
    assert len(leads) == 1 and leads[0]["status"] == "unavailable"
    assert receipts[0]["register_sha256"] == sha(register)
    original.write_text("tampered")
    sources, receipts, leads = supplements(folder, "123", tmp_path / "changed")
    assert not sources and not receipts
    assert leads[0]["status"] == "source_hash_mismatch"
    with pytest.raises(ValueError, match="another publication"):
        supplements(folder, "456", tmp_path / "bad")


def test_invalid_pdf_and_foreign_path_do_not_abort_other_source_jobs(tmp_path):
    folder = tmp_path / "paper"
    supp = folder / "supplements"
    supp.mkdir(parents=True)
    invalid = supp / "bad.pdf"
    invalid.write_text("<html>challenge</html>")
    foreign = tmp_path / "foreign.txt"
    foreign.write_text("Other paper")
    (supp / "acquisition.json").write_text(json.dumps({"pmid": "123", "files": [
        {"status": "cached", "path": str(invalid), "url": "https://example.org/a"},
        {"status": "cached", "path": str(foreign), "url": "https://example.org/b"}]}))
    sources, receipts, leads = supplements(folder, "123", tmp_path / "renditions")
    assert not sources
    assert {r["status"] for r in leads} == {"rendition_failed", "registration_outside_publication_folder"}


def test_html_preserves_dependency_links_and_does_not_flatten_math_fractions(tmp_path):
    folder = tmp_path / 'paper'; folder.mkdir()
    (folder / 'article.html').write_text('''<meta name="citation_pmid" content="123">
    <meta name="citation_pmid" content="123"><meta name="citation_title" content="Protocol">
    <p>First step<br>Second step <a href="supplement.pdf">Supplemental protocol</a></p>
    <math><mfrac><mi>a</mi><mi>b</mi></mfrac><mspace/></math><img alt="reaction scheme">''')
    source, receipt = primary_html(folder, '123', tmp_path / 'rendered.txt')
    text = (tmp_path / 'rendered.txt').read_text()
    assert 'First step\n\nSecond step' in text
    assert '[link: supplement.pdf]' in text
    assert '[MathML source; unrendered]' in text
    assert '<mfrac><mi>a</mi><mi>b</mi></mfrac><mspace/>' in text
    assert '</mspace>' not in text
    assert 'Image not rendered: reaction scheme' in text
    assert 'Rendition provenance, not publication content' in text
    assert receipt['original_sha256'] in text
    assert 'supplied_metadata' not in source


def test_rendition_never_overwrites_original_or_prior_output(tmp_path):
    folder = tmp_path / 'paper'; folder.mkdir()
    original = folder / 'article.html'
    original.write_text('<meta name="citation_pmid" content="123"><meta name="citation_title" content="P"><p>Original</p>')
    before = original.read_bytes()
    with pytest.raises(FileExistsError):
        primary_html(folder, '123', original)
    assert original.read_bytes() == before
    output = tmp_path / 'prior.txt'; output.write_text('Frozen prior rendition')
    with pytest.raises(FileExistsError):
        primary_html(folder, '123', output)
    assert output.read_text() == 'Frozen prior rendition'


def test_relative_registered_path_and_bad_first_duplicate_do_not_hide_valid_source(tmp_path):
    folder = tmp_path / 'paper'; supp = folder / 'supplements'; supp.mkdir(parents=True)
    original = supp / 'protocol.txt'; original.write_text('Published protocol text.')
    (supp / 'acquisition.json').write_text(json.dumps({'pmid': '123', 'files': [
        {'status': 'cached', 'path': 'protocol.txt', 'sha256': '0'*64, 'url': 'https://example.org/a'},
        {'status': 'cached', 'path': 'protocol.txt', 'sha256': sha(original), 'url': 'https://example.org/a'},
        None]}))
    sources, receipts, leads = supplements(folder, '123', tmp_path / 'rendered')
    assert len(sources) == len(receipts) == 1
    assert {lead['status'] for lead in leads} == {'source_hash_mismatch', 'invalid_registration'}
    assert receipts[0]['original'] == str(original.resolve())
    assert 'Original cached file:' in (tmp_path / 'rendered/supplement_001.txt').read_text()

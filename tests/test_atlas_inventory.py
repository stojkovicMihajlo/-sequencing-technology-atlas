"""Full-catalogue accounting, publication identity and source preparation boundaries."""

import json

import pytest

from genomics_discovery.curation.atlas_inventory import prepare
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet


def article(cache, pmid, actual=None):
    path = cache / pmid / "article.xml"
    path.parent.mkdir(parents=True)
    path.write_text(f'''<article><front><article-meta>
      <article-id pub-id-type="pmid">{actual or pmid}</article-id>
      <title-group><article-title>Paper {pmid}</article-title></title-group>
      </article-meta></front><body><sec id="methods"><title>Methods</title>
      <p>Wash material; see supplementary protocol.</p>
      <supplementary-material><media href="supplement.pdf"/></supplementary-material>
      </sec></body></article>''')
    return path


def atlas(tmp_path, methods, **extra):
    path = tmp_path / "atlas.json"
    path.write_text(json.dumps({"methods": methods, **extra}))
    return path


def test_inventory_keeps_every_method_and_missing_supplements_visible(tmp_path):
    cache = tmp_path / "cache"
    article(cache, "1")
    article(cache, "2", actual="999")
    article(cache, "3").write_text("<article>malformed")
    methods = [{"id": mid, "name": mid, "origin_paper_ids": papers} for mid, papers in (
        ("good", ["pmid:1"]), ("mismatch", ["pmid:2"]), ("broken", ["pmid:3"]),
        ("missing", ["pmid:4"]), ("variants", ["pmid:1", "pmid:2"]))]
    out = tmp_path / "prepared"
    report = prepare(atlas(tmp_path, methods), cache, out)
    jobs = json.loads((out / "inventory.json").read_text())["jobs"]
    assert [j["id"] for j in jobs] == [m["id"] for m in methods]
    assert report["counts"] == {"ready_for_source_inspection": 1, "needs_source_preparation": 4}
    assert all(j["status"] == "missing_sources" for j in jobs[1:])
    assert "mismatch" in jobs[1]["reason"].lower()
    packet = load_packet(out / jobs[0]["packet"])
    assert len(packet["sources"]) == 1
    assert report["methods"][0]["document_structure"]["supplements"]
    assert "not complete evidence" in report["boundary"]


def test_explicit_packet_binding_preserves_scope_and_avoids_name_matching(tmp_path):
    source = tmp_path / "verified.txt"
    source.write_text("General protocol under explicit source registration.")
    packet = build_packet(method_id="original-paper-implementation", title="Published implementation",
                          scope="Original only", variant="v1", sources=[{
                              "source_id": "primary", "title": "Title", "url": "https://example.org/paper",
                              "role": "primary_paper", "path": str(source)}])
    saved = tmp_path / "packet.json"
    saved.write_text(json.dumps(packet))
    path = atlas(tmp_path, [{"id": "canonical-method", "name": "Method"}])
    out = tmp_path / "prepared"
    result = prepare(path, tmp_path / "cache", out, packets={"canonical-method": saved})
    job = json.loads((out / "inventory.json").read_text())["jobs"][0]
    assert job["id"] == "canonical-method"
    assert job["method_id"] == "original-paper-implementation"
    assert load_packet(out / job["packet"]) == packet
    assert result["methods"][0]["source_basis"] == "explicit_registered_packet"
    source.write_text("Changed evidence")
    changed = prepare(path, tmp_path / "cache", tmp_path / "changed", packets={"canonical-method": saved})
    assert changed["counts"] == {"needs_source_preparation": 1}


def test_multiple_registered_variants_require_choice_and_input_errors_do_not_create_run(tmp_path):
    path = atlas(tmp_path, [{"id": "method", "name": "Method"}], workflows={
        "one": {"atlas_method_id": "method", "packet_path": "/one/packet.json"},
        "two": {"atlas_method_id": "method", "packet_path": "/two/packet.json"}})
    report = prepare(path, tmp_path, tmp_path / "prepared")
    assert "Multiple registered variants" in report["methods"][0]["problems"][0]
    with pytest.raises(ValueError, match="absent methods"):
        prepare(path, tmp_path, tmp_path / "bad", packets={"unknown": "/packet.json"})
    assert not (tmp_path / "bad").exists()


def test_integrated_graph_node_identity_is_preserved(tmp_path):
    article(tmp_path / "cache", "1")
    path = tmp_path / "integrated.json"
    path.write_text(json.dumps({"format_version": "integrated_technology_graph/1.0",
        "nodes": [{"kind": "operation", "id": "irrelevant"},
                  {"kind": "technology", "record": {"id": "assay", "name": "Assay",
                                                       "origin_paper_ids": ["pmid:1"]}}]}))
    report = prepare(path, tmp_path / "cache", tmp_path / "prepared")
    assert len(report["methods"]) == 1
    assert report["methods"][0]["id"] == "assay"


def test_invalid_jats_falls_back_to_independently_matching_html(tmp_path):
    cache = tmp_path / 'cache'; article(cache, '1').write_text('<article>broken')
    (cache / '1/article.html').write_text('<meta name="citation_pmid" content="1"><meta name="citation_title" content="Paper"><p>Usable methods.</p>')
    report = prepare(atlas(tmp_path, [{'id': 'assay', 'name': 'Assay', 'origin_paper_ids': ['pmid:1']}]),
                     cache, tmp_path / 'prepared')
    method = report['methods'][0]
    assert method['status'] == 'ready_for_source_inspection'
    assert method['source_basis'] == 'atlas_origin_and_exact_html_identity'
    assert method['primary_format_failures'][0]['format'] == 'jats'
    assert len(load_packet(tmp_path / 'prepared/packets/assay/packet.json')['sources']) == 1


@pytest.mark.parametrize('register', ['not json', '{"pmid":"other","files":[]}',
                                     '{"pmid":"1","files":null}'])
def test_bad_supplement_register_does_not_hide_readable_primary(tmp_path, register):
    cache = tmp_path / 'cache'; article(cache, '1')
    supp = cache / '1/supplements'; supp.mkdir()
    (supp / 'acquisition.json').write_text(register)
    report = prepare(atlas(tmp_path, [{'id': 'assay', 'name': 'Assay', 'origin_paper_ids': ['pmid:1']}]),
                     cache, tmp_path / 'prepared')
    method = report['methods'][0]
    assert method['status'] == 'ready_for_source_inspection'
    assert method['supplement_leads'][0]['status'] == 'supplement_register_invalid'
    assert not method['problems']


def test_atlas_registered_packet_paths_are_relative_to_atlas_not_process(tmp_path):
    source = tmp_path / 'verified.txt'; source.write_text('Verified protocol.')
    packet = build_packet(method_id='assay', title='Assay', scope='Original', variant='v1', sources=[{
        'source_id': 'primary', 'title': 'P', 'url': 'https://example.org/p',
        'role': 'primary_paper', 'path': str(source)}])
    (tmp_path / 'packet.json').write_text(json.dumps(packet))
    a = atlas(tmp_path, [{'id': 'assay', 'name': 'Assay'}], workflows={
        'original': {'atlas_method_id': 'assay', 'packet_path': 'packet.json'}})
    report = prepare(a, tmp_path / 'cache', tmp_path / 'prepared')
    assert report['methods'][0]['status'] == 'ready_for_source_inspection'
    assert report['methods'][0]['registered_packet'] == str(tmp_path / 'packet.json')


def test_pending_binary_supplement_is_pinned_without_claiming_conversion(tmp_path):
    from genomics_discovery.curation.run_state import sha
    cache = tmp_path / 'cache'; article(cache, '1')
    supp = cache / '1/supplements'; supp.mkdir()
    document = supp / 'protocol.docx'; document.write_bytes(b'unconverted registered binary')
    (supp / 'acquisition.json').write_text(json.dumps({'pmid': '1', 'files': [
        {'status': 'cached', 'path': 'protocol.docx', 'sha256': sha(document),
         'url': 'https://example.org/protocol.docx'}]}))
    report = prepare(atlas(tmp_path, [{'id': 'assay', 'name': 'Assay', 'origin_paper_ids': ['pmid:1']}]),
                     cache, tmp_path / 'prepared')
    method = report['methods'][0]
    assert method['status'] == 'ready_for_source_inspection'
    assert method['source_count'] == 1
    assert method['supplement_leads'][0]['status'] == 'original_requires_format_specific_inspection'
    pins = json.loads((tmp_path / 'prepared/source_receipts.json').read_text())['files']
    assert pins[str(document.resolve())] == sha(document)

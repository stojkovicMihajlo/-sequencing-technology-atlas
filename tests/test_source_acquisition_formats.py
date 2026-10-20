"""Host wrapper/DOCX/association cases, with no model-created publication text."""

from io import BytesIO
import json
import os
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from genomics_discovery.curation.document_manifest import inspect_jats
from genomics_discovery.curation.source_acquisition.formats import select_jats, docx_rendition
from genomics_discovery.curation.source_acquisition.associations import biostudies_record
from genomics_discovery.curation.source_acquisition.models import Identity
from genomics_discovery.curation.source_acquisition.runtime import _match, acquire
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet
from genomics_discovery.curation.run_state import sha
from test_source_acquisition import Finder, document, fetcher, findings, jats, worklist


def docx():
    stream=BytesIO()
    with ZipFile(stream,"w") as archive:
        archive.writestr("word/document.xml",'''<w:document
          xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
          xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><w:body>
          <w:p><w:r><w:t>Wash MgCl</w:t></w:r><w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>2</w:t></w:r>
          <w:r><w:t> at 4 C.</w:t></w:r><w:r><w:instrText> ADDIN EN.CITE EndNote hidden-base64-metadata </w:instrText></w:r></w:p>
          <w:p><w:hyperlink r:id="link1"><w:r><w:t>Original protocol</w:t></w:r></w:hyperlink></w:p>
          <w:p><w:r><w:instrText> HYPERLINK "https://example.org/field" </w:instrText></w:r><w:r><w:t>Field link</w:t></w:r></w:p>
          <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Primer</w:t></w:r></w:p></w:tc>
          <w:tc><w:p><w:r><w:t>Sequence</w:t></w:r></w:p></w:tc></w:tr>
          <w:tr><w:tc><w:p><w:r><w:t>Modified primer</w:t></w:r></w:p></w:tc>
          <w:tc><w:p><w:r><w:t>5'-ACGT-/3ddC/</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
          <w:p><w:r><w:drawing/></w:r><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></w:p>
          </w:body></w:document>''')
        archive.writestr("word/_rels/document.xml.rels",'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="link1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
          Target="https://example.org/protocol" TargetMode="External"/></Relationships>''')
    return stream.getvalue()


def matches(identity):_match(identity,Identity(pmid="123",title="Published protocol"))


def test_wrapper_selects_exactly_one_matching_article_without_importing_other_body():
    wrapped=b"<pmc-articleset>"+jats(pmid="999",title="Other article")+jats()+b"</pmc-articleset>"
    raw,identity,receipt=select_jats(wrapped,matches)
    assert ET.fromstring(raw).tag=="article" and identity["identifiers"]["pmid"]==["123"]
    assert b"Other article" not in raw
    assert receipt["direct_article_count"]==2 and receipt["selected_article_index"]==1


@pytest.mark.parametrize("articles",[[],[jats(pmid="999")],[jats(),jats()]])
def test_wrapper_missing_or_ambiguous_article_never_selects_by_position(articles):
    with pytest.raises(ValueError,match="exactly one matching"):
        select_jats(b"<pmc-articleset>"+b"".join(articles)+b"</pmc-articleset>",matches)


def test_docx_preserves_visible_paragraphs_table_cells_scripts_and_links_without_endnote_metadata(tmp_path):
    original=tmp_path/"supp.docx";original.write_bytes(docx())
    output=tmp_path/"supp.txt"
    receipt=docx_rendition(original,output)
    text=output.read_text()
    assert "Wash MgCl_(2)" in text and "at 4 C." in text
    assert "Primer | Sequence" in text and "Modified primer | 5'-ACGT-/3ddC/" in text
    assert "[link: https://example.org/protocol]" in text and "[link: https://example.org/field]" in text
    assert "ADDIN EN.CITE" not in text and "hidden-base64" not in text
    assert "Image/object/equation not rendered" in text
    assert receipt["original_sha256"]==sha(original) and receipt["rendition_sha256"]==sha(output)
    assert receipt["omitted_field_instruction_elements"]==2
    assert "merged-cell" in receipt["limits"] and "headers/footers" in receipt["limits"]


def api_record(size):
    return {"accno":"S-EPMC456","type":"submission","section":{"type":"Study",
        "files":[{"path":"supp.docx","size":size,"type":"file"}],
        "subsections":[{"type":"Publication","links":[[
            {"url":"123","attributes":[{"name":"Type","value":"PMID"}]},
            {"url":"10.1234/protocol","attributes":[{"name":"Type","value":"DOI"}]}]]}]}}


async def test_wrapped_jats_and_docx_enter_real_packet_resolve_with_original_and_selection_pins(tmp_path):
    tasks=worklist(tmp_path)
    raw=b"<pmc-articleset>"+jats(supplement="https://example.org/supp.docx")+b"</pmc-articleset>"
    docs=[document(),document("docx",url="https://example.org/supp.docx",role="supplement",fmt="docx",
                             linked_from="https://example.org/article.xml")]
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":findings("assay",docs)}),
        downloader=fetcher({"https://example.org/article.xml":raw,"https://example.org/supp.docx":docx()}))
    packet=load_packet(Path(result["methods"]["assay"]["packet"]))
    assert {s["role"] for s in packet["sources"]}=={"primary_paper","supplement"}
    assert any("5'-ACGT-/3ddC/" in p["text"] for s in packet["sources"] for p in s["passages"])
    ledger=json.loads((tmp_path/"out/methods/assay/originals_ledger.json").read_bytes())
    primary=next(r for r in ledger if r.get("container")=="pmc-articleset")
    assert primary["original"]!=primary["rendition"] and Path(primary["original"]).read_bytes()==raw
    receipts=json.loads((tmp_path/"out/source_receipts.json").read_bytes())["files"]
    assert receipts[primary["original"]]==primary["original_sha256"]
    assert receipts[primary["rendition"]]==primary["rendition_sha256"]
    assert result["registration"]["scientific_resolution"] is False


async def test_biostudies_mirror_requires_host_publication_attachment_and_canonical_delivery(tmp_path):
    tasks=worklist(tmp_path)
    metadata_url="https://www.ebi.ac.uk/biostudies/api/v1/studies/S-EPMC456"
    mirror="https://www.ebi.ac.uk/biostudies/files/S-EPMC456/supp.docx"
    docs=[document(),document("docx",url=mirror,role="supplement",fmt="docx",linked_from="https://example.org/article.xml",
        association_record={"format":"biostudies","url":metadata_url,"file_path":"supp.docx"})]
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":findings("assay",docs)}),downloader=fetcher({
        "https://example.org/article.xml":jats(supplement="supp.docx"),mirror:docx(),
        metadata_url:json.dumps(api_record(len(docx()))).encode()}))
    row=result["methods"]["assay"]["documents"][1]
    assert row["status"]=="registered_for_fresh_source_review"
    assert row["association"]["publication_identity"]["pmid"]==["123"]
    record=Path(row["association"]["record"])
    receipts=json.loads((tmp_path/"out/source_receipts.json").read_bytes())["files"]
    assert receipts[str(record)]==sha(record)
    assert row["association"]["canonical_download_url"]==mirror


@pytest.mark.parametrize("change",["publication","file","size","citation","endpoint"])
def test_filename_alone_or_wrong_repository_identity_never_proves_association(change):
    original=docx();record=api_record(len(original));links={"https://example.org/supp.docx"}
    url="https://www.ebi.ac.uk/biostudies/api/v1/studies/S-EPMC456"
    if change=="publication":record["section"]["subsections"][0]["links"][0][0]["url"]="999"
    elif change=="file":record["section"]["files"][0]["path"]="other.docx"
    elif change=="size":record["section"]["files"][0]["size"]+=1
    elif change=="citation":links={"https://example.org/other.docx"}
    else:url="https://example.org/metadata.json"
    with pytest.raises(ValueError):
        biostudies_record(json.dumps(record).encode(),url,"supp.docx",
            {"identifiers":{"pmid":["123"],"doi":["10.1234/protocol"]}},links,len(original))


async def test_unassociated_docx_still_retains_original_and_rendition(tmp_path):
    tasks=worklist(tmp_path)
    docs=[document(),document("docx",url="https://example.org/other.docx",role="supplement",fmt="docx",
                             linked_from="https://example.org/article.xml")]
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":findings("assay",docs)}),downloader=fetcher({
        "https://example.org/article.xml":jats(),"https://example.org/other.docx":docx()}))
    row=result["methods"]["assay"]["documents"][1]
    assert row["status"]=="retrieved_unregistered"
    assert Path(row["path"]).is_file() and Path(row["rendition"]).is_file()
    packet=load_packet(Path(result["methods"]["assay"]["packet"]))
    assert len(packet["sources"])==1


def test_already_downloaded_jats_wrapper_regression(tmp_path):
    path=os.environ.get("ACQUISITION_JATS_REGRESSION_PATH")
    if not path:pytest.skip("Set path to an already downloaded, immutable JATS source")
    original=Path(path);raw=original.read_bytes();root=ET.fromstring(raw)
    articles=[e for e in root if e.tag.rsplit("}",1)[-1]=="article"]
    assert root.tag=="pmc-articleset" and len(articles)==1
    expected=inspect_jats(ET.tostring(articles[0]))["identity"]
    binding=Identity(pmid=expected["identifiers"]["pmid"][0],doi=expected["identifiers"]["doi"][0],title=expected["title"])
    selected,actual,receipt=select_jats(raw,lambda identity:_match(identity,binding))
    assert actual==expected and receipt["selected_article_index"]==0
    assert any(e.tag.rsplit("}",1)[-1]=="supplementary-material" for e in ET.fromstring(selected).iter())
    rendered=tmp_path/"selected.xml";rendered.write_bytes(selected)
    packet=build_packet(method_id="source_regression",title="Source regression",scope="Registered source only",variant="original",
        sources=[{"source_id":"primary","title":actual["title"],"role":"primary_paper", "url":"https://example.org/original","path":str(rendered)}])
    assert packet["sources"][0]["passages"] and packet["sources"][0]["document_identity"]==actual


async def test_bad_docx_signature_keeps_original_without_blocking_readable_primary(tmp_path):
    tasks=worklist(tmp_path)
    docs=[document(),document("bad",url="https://example.org/supp.docx",role="supplement",fmt="docx",
                             linked_from="https://example.org/article.xml")]
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":findings("assay",docs)}),downloader=fetcher({
        "https://example.org/article.xml":jats(supplement="supp.docx"),"https://example.org/supp.docx":b"<html>challenge</html>"}))
    row=result["methods"]["assay"]["documents"][1]
    assert row["status"]=="retrieved_unregistered" and "DOCX ZIP" in row["error"]
    assert Path(row["path"]).is_file() and result["registration"]
    assert len(load_packet(Path(result["methods"]["assay"]["packet"]))["sources"])==1


async def test_noncanonical_mirror_must_replay_same_host_download_bytes(tmp_path):
    tasks=worklist(tmp_path)
    metadata_url="https://www.ebi.ac.uk/biostudies/api/v1/studies/S-EPMC456"
    mirror="https://mirror.example.org/supp.docx"
    canonical="https://www.ebi.ac.uk/biostudies/files/S-EPMC456/supp.docx"
    docs=[document(),document("docx",url=mirror,role="supplement",fmt="docx",linked_from="https://example.org/article.xml",
        association_record={"format":"biostudies","url":metadata_url,"file_path":"supp.docx"})]
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":findings("assay",docs)}),downloader=fetcher({
        "https://example.org/article.xml":jats(supplement="supp.docx"),mirror:docx(),canonical:b"Other bytes",
        metadata_url:json.dumps(api_record(len(docx()))).encode()}))
    row=result["methods"]["assay"]["documents"][1]
    assert row["status"]=="retrieved_unregistered" and "canonical BioStudies delivery" in row["error"]
    assert result["scientific_resolution"] is False

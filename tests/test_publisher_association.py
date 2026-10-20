"""Citation-matched link-page evidence never promotes the page to full paper."""

import hashlib
import json
from pathlib import Path

import pytest

from genomics_discovery.curation.extraction_batch.storage import check_pins, PinDrift
from genomics_discovery.curation.source_acquisition import acquire
from genomics_discovery.curation.source_acquisition.associations import citation_html_record
from genomics_discovery.curation.source_acquisition.runtime import _identity, _match, _links
from genomics_discovery.curation.workflow_extraction.sources import load_packet

from test_source_acquisition import Finder, document, findings, worklist, fetcher, jats


PAGE = "https://publisher.example.org/article"
ATTACHMENT = "https://cdn.example.org/actual-supplement.txt"
PRIMARY = {"title":"Published protocol", "identifiers":{"doi":["10.1234/protocol"], "pmid":["123"]}}
BODY = b"Actual supplemental procedure."


def html(doi="10.1234/protocol", title="Published protocol", url=ATTACHMENT, extra=""):
    return (f'<html><head><meta name="citation_doi" content="{doi}">'
            f'<meta name="citation_title" content="{title}">{extra}</head>'
            f'<body>LANDING_METADATA_ONLY<a href="{url}">SI</a></body></html>').encode()


def verify(raw, original=BODY, expected_sha=None, primary=PRIMARY):
    return citation_html_record(raw, PAGE, primary, [ATTACHMENT], original,
        expected_sha or hashlib.sha256(BODY).hexdigest(), identify=_identity, match=_match, links=_links)


def test_exact_citation_link_and_original_bytes_are_qualified_evidence():
    result = verify(html())
    assert result["association_strength"] == "citation_matched_landing_page_exact_attachment"
    assert result["expected_registered_primary_identity"] == PRIMARY
    assert result["exact_attachment_urls"] == [ATTACHMENT]
    assert result["original_sha256"] == hashlib.sha256(BODY).hexdigest()
    assert "does not prove official publisher authority" in result["limits"]


@pytest.mark.parametrize("raw,error", [
    (html(doi="10.9999/later-protocol"), "doi mismatch"),
    (html(title="A different assay implementation"), "title mismatch"),
    (html(extra='<meta name="citation_doi" content="10.9999/conflict">'), "doi mismatch"),
    (html(extra='<meta name="citation_title" content="Different paper">'), "unique citation_title"),
    (html(url="https://other.example.org/actual-supplement.txt"), "exact retrieved attachment URL"),
    (html(url=ATTACHMENT+"?different=bytes"), "exact retrieved attachment URL"),
    (html(extra='<meta name="citation_pmid" content="999">'), "PMID conflicts"),
])
def test_wrong_or_ambiguous_identity_and_filename_only_links_reject(raw,error):
    with pytest.raises(ValueError, match=error):
        verify(raw)


def test_missing_primary_identity_and_changed_attachment_bytes_reject():
    with pytest.raises(ValueError,match="unique DOI and title"):
        verify(html(),primary={"title":"Published protocol","identifiers":{}})
    with pytest.raises(ValueError,match="acquisition SHA256"):
        verify(html(),original=BODY+b"changed")


async def test_unregistered_landing_bridge_caches_and_pins_without_registering_page(tmp_path):
    tasks=worklist(tmp_path)
    second=ATTACHMENT.replace("supplement.txt","supplement2.txt")
    landing=html()+f'<a href="{second}">Another SI</a>'.encode()
    docs=[document(),document("s1",url=ATTACHMENT,role="supplement",fmt="text",linked_from=PAGE),
          document("s2",url=second,role="supplement",fmt="text",linked_from=PAGE)]
    fetch=fetcher({"https://example.org/article.xml":jats(supplement="https://unrelated.example.org/x"),
                   PAGE:landing,ATTACHMENT:BODY,second:b"Second supplemental procedure."})
    output=tmp_path/"out"
    result=await acquire(tasks,output,worker=Finder({"assay":findings("assay",docs)}),downloader=fetch)
    packet=load_packet(Path(result["methods"]["assay"]["packet"]))
    assert [s["role"] for s in packet["sources"]] == ["primary_paper","supplement","supplement"]
    assert fetch.calls.count(PAGE)==1
    assert not any("LANDING_METADATA_ONLY" in p["text"] for s in packet["sources"] for p in s["passages"])
    supplement=result["methods"]["assay"]["documents"][1]
    association=supplement["association"]
    assert association["registered_primary_urls"] == ["https://example.org/article.xml"]
    assert association["original_sha256"] == hashlib.sha256(BODY).hexdigest()
    receipts=json.loads((output/"source_receipts.json").read_bytes())
    for key in ("landing_original","landing_retrieval_receipt","record"):
        assert str(Path(association[key])) in receipts["files"]
    check_pins(receipts["files"])
    Path(association["landing_original"]).write_bytes(b"changed host page")
    with pytest.raises(PinDrift):check_pins(receipts["files"])


async def test_wrong_landing_retains_supplement_as_unregistered(tmp_path):
    tasks=worklist(tmp_path)
    docs=[document(),document("s1",url=ATTACHMENT,role="supplement",fmt="text",linked_from=PAGE)]
    fetch=fetcher({"https://example.org/article.xml":jats(supplement="https://unrelated.example.org/x"),
                   PAGE:html(doi="10.9999/wrong"),ATTACHMENT:BODY})
    output=tmp_path/"out"
    result=await acquire(tasks,output,worker=Finder({"assay":findings("assay",docs)}),downloader=fetch)
    packet=load_packet(Path(result["methods"]["assay"]["packet"]))
    assert len(packet["sources"])==1
    row=result["methods"]["assay"]["documents"][1]
    assert row["status"]=="retrieved_unregistered" and "doi mismatch" in row["error"]
    assert Path(row["path"]).read_bytes()==BODY
    assert (output/"methods/assay/associations/landing_001.html").exists()


async def test_failed_landing_fetch_is_not_repeated_for_other_supplements(tmp_path):
    tasks=worklist(tmp_path)
    second=ATTACHMENT+"2"
    docs=[document(),document("s1",url=ATTACHMENT,role="supplement",fmt="text",linked_from=PAGE),
          document("s2",url=second,role="supplement",fmt="text",linked_from=PAGE)]
    fetch=fetcher({"https://example.org/article.xml":jats(supplement="https://unrelated.example.org/x"),
                   PAGE:OSError("HTTP403"),ATTACHMENT:BODY,second:BODY})
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":findings("assay",docs)}),downloader=fetch)
    assert fetch.calls.count(PAGE)==1
    assert all(row["status"]=="retrieved_unregistered" for row in result["methods"]["assay"]["documents"][1:])


@pytest.mark.parametrize("declared_format", ["doc", "docx"])
async def test_publisher_bridge_preserves_combined_word_sniff_rendition_and_table_pins(tmp_path, declared_format):
    from test_source_acquisition_formats import docx

    tasks=worklist(tmp_path)
    attachment="https://cdn.example.org/retained."+declared_format
    docs=[document(),document("word",url=attachment,role="supplement",fmt=declared_format,linked_from=PAGE)]
    raw=docx()
    fetch=fetcher({"https://example.org/article.xml":jats(supplement="https://unrelated.example.org/x"),
                   PAGE:html(url=attachment),attachment:raw})
    output=tmp_path/"out"
    result=await acquire(tasks,output,worker=Finder({"assay":findings("assay",docs)}),downloader=fetch)
    row=result["methods"]["assay"]["documents"][1]
    assert row["status"]=="registered_for_fresh_source_review"
    assert row["association"]["association_strength"]=="citation_matched_landing_page_exact_attachment"
    packet=load_packet(Path(result["methods"]["assay"]["packet"]))
    assert len(packet["sources"])==2
    assert any("Modified primer |" in passage["text"] for passage in packet["sources"][1]["passages"])
    ledger=json.loads((output/"methods/assay/originals_ledger.json").read_bytes())
    word=next(item for item in ledger if item.get("actual_container"))
    assert word["actual_container"]=="docx_zip"
    assert word["format_discrepancy"] is (declared_format=="doc")
    pins=json.loads((output/"source_receipts.json").read_bytes())["files"]
    assert pins[word["original"]]==hashlib.sha256(raw).hexdigest()
    assert str(Path(row["association"]["landing_original"])) in pins
    check_pins(pins)

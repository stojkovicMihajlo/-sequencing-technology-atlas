"""Exercise native archive, public retrieval and real packet/resolve boundaries."""

from dataclasses import asdict
import json
from pathlib import Path

import pytest

from genomics_discovery.curation.codex_worker import WorkerResult
from genomics_discovery.curation.extraction_batch.storage import check_pins, PinDrift
from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.source_acquisition import acquire
from genomics_discovery.curation.source_acquisition.downloads import Download, public_url
from genomics_discovery.curation.source_worklist import build_worklist
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def jats(pmid="123", title="Published protocol", supplement="https://example.org/supp.txt"):
    return f'''<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>
    <article-id pub-id-type="pmid">{pmid}</article-id>
    <article-id pub-id-type="doi">10.1234/protocol</article-id>
    <title-group><article-title>{title}</article-title></title-group></article-meta></front>
    <body><sec id="methods"><title>Methods</title><p>Wash nuclei with buffer.</p>
    <supplementary-material><media xlink:href="{supplement}"/></supplementary-material>
    </sec></body></article>'''.encode()


def document(did="primary", *, url="https://example.org/article.xml", role="primary_paper", fmt="jats", **extra):
    return {"id":did,"url":url,"role":role,"format":fmt,
            "identity":{"pmid":"123","doi":"10.1234/protocol","title":"Published protocol"},
            "rationale":"Public original document location; applicability needs fresh inspection.", **extra}


def findings(mid, documents):
    return {"method_id":mid,"documents":documents,"searches":[{
        "query":"PMID 123 protocol original full text and supplements", "status":"found",
        "note":"Candidate locations only; no completeness assertion."}]}


def worklist(tmp_path, *, mids=("assay",), existing=False, ambiguous=False):
    rows=[]
    for mid in mids:
        row={"id":mid,"title":f"Assay {mid}","status":"needs_source_preparation",
             "origin_paper_ids":["pmid:123","pmid:456"] if ambiguous else ["pmid:123"],
             "problems":["Missing source"], "source_leads":[{"url":"https://pubmed.ncbi.nlm.nih.gov/123/"}]}
        if existing:
            original=tmp_path/mid/"old.xml";original.parent.mkdir();original.write_bytes(jats())
            packet=build_packet(method_id=f"implementation_{mid}",title="Scoped implementation",scope="Nuclei only",
                variant="Original variant",sources=[{"source_id":"original_primary","title":"Published protocol",
                  "role":"primary_paper","url":"https://example.org/article.xml","path":str(original)}])
            packet_path=save(tmp_path/mid/"packet.json",packet)
            row.update(packet=str(packet_path),packet_sha256=sha(packet_path),status="ready_for_source_inspection",
                supplement_leads=[{"status":"unavailable","record":{"url":"https://example.org/supp.txt"}}])
        rows.append(row)
    readiness=save(tmp_path/"readiness.json",{"format_version":"atlas_source_readiness/1.0","methods":rows})
    return save(tmp_path/"worklist.json",build_worklist(readiness=readiness))


class Finder:
    def __init__(self, reports, *, mutate=None, bad_archive=False):
        self.reports=reports;self.mutate=mutate;self.bad_archive=bad_archive;self.calls=[]

    async def run(self, prompt, attempt):
        request=json.loads((attempt/"request.json").read_text());mid=request["method"]["id"]
        self.calls.append(mid)
        save(attempt/"artifact.json",self.reports[mid])
        (attempt/"prompt.txt").write_text(prompt)
        thread=f"finder-{mid}"
        events=[{"type":"thread.started","thread_id":thread},{"type":"turn.started"}]
        if not self.bad_archive:
            events.append({"type":"turn.completed","usage":{}})
        (attempt/"events.jsonl").write_text("".join(json.dumps(e)+"\n" for e in events))
        (attempt/"final.txt").write_text("Saved structured candidate locations.")
        result=WorkerResult(0,thread,{},.01,None)
        save(attempt/"result.json",asdict(result))
        if self.mutate:
            self.mutate(attempt)
        return result


def fetcher(values):
    calls=[]
    def fetch(url):
        calls.append(url)
        value=values[url]
        if isinstance(value,Exception):
            raise value
        return Download(value,url,"application/octet-stream")
    fetch.calls=calls
    return fetch


async def test_host_bytes_identity_pins_and_real_resolve_not_generated_prose(tmp_path):
    tasks=worklist(tmp_path)
    report=findings("assay",[document(),document("supplement",url="https://example.org/supp.txt",role="supplement",
        fmt="text",linked_from="https://example.org/article.xml")])
    worker=Finder({"assay":report})
    fetched=fetcher({"https://example.org/article.xml":jats(),"https://example.org/supp.txt":b"Rotate nuclei at 4 C."})
    output=tmp_path/"acquisition"
    result=await acquire(tasks,output,worker=worker,downloader=fetched)
    assert result["scientific_resolution"] is False
    assert result["registration"]["scientific_resolution"] is False
    assert result["registration"]["status"]=="registered_for_fresh_source_review"
    packet=load_packet(Path(result["methods"]["assay"]["packet"]))
    assert {s["role"] for s in packet["sources"]}=={"primary_paper","supplement"}
    assert any("Rotate nuclei at 4 C." in p["text"] for s in packet["sources"] for p in s["passages"])
    assert not any("Candidate locations" in p["text"] for s in packet["sources"] for p in s["passages"])
    receipts=json.loads((output/"source_receipts.json").read_bytes())
    check_pins(receipts["files"])
    original=output/"methods/assay/supplements/supplement.txt"
    assert receipts["files"][str(original)]==sha(original)
    assert str(output/"methods/assay/finder/artifact.json") in receipts["files"]
    assert str(output/"methods/assay/supplements/acquisition.json") in receipts["files"]
    mapping=json.loads((output/"replacements.json").read_bytes())
    assert mapping["worklist_sha256"]==sha(tasks)
    registered=json.loads((output/"registration/inventory.json").read_bytes())
    assert registered["jobs"][0]["id"]=="assay"
    original.write_text("Changed original")
    with pytest.raises(PinDrift):check_pins(receipts["files"])


@pytest.mark.parametrize("url",[None,"", "file:///tmp/source", "http://127.0.0.1/a", "https://u:p@example.org/a"])
async def test_missing_invalid_private_or_credential_urls_remain_unavailable(tmp_path,url):
    tasks=worklist(tmp_path)
    fetch=fetcher({})
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":findings("assay",[document(url=url)])}),downloader=fetch)
    assert not fetch.calls
    assert result["registration"] is None
    assert result["methods"]["assay"]["documents"][0]["status"]=="unavailable"
    assert result["scientific_resolution"] is False


async def test_wrong_primary_identity_retains_original_and_isolates_other_method(tmp_path):
    tasks=worklist(tmp_path,mids=("good","wrong"))
    bad=document(url="https://example.org/wrong.xml")
    worker=Finder({"good":findings("good",[document()]),"wrong":findings("wrong",[bad])})
    result=await acquire(tasks,tmp_path/"out",worker=worker,downloader=fetcher({
        "https://example.org/article.xml":jats(),"https://example.org/wrong.xml":jats(pmid="999")}))
    assert result["methods"]["good"]["packet"]
    wrong=result["methods"]["wrong"]["documents"][0]
    assert wrong["status"]=="retrieved_unregistered" and "pmid mismatch" in wrong["error"]
    assert Path(wrong["path"]).read_bytes()==jats(pmid="999")
    assert result["registration"]["affected_methods"]==["good"]


async def test_partial_retrieval_and_search_failures_are_retained(tmp_path):
    tasks=worklist(tmp_path)
    docs=[document(),document("missing",url="https://example.org/missing.pdf",role="supplement",fmt="pdf",
                              linked_from="https://example.org/article.xml")]
    report=findings("assay",docs)
    report["searches"].append({"query":"Exact supplement S3","status":"access_limited",
                               "note":"Public endpoint denied access; no bypass attempted."})
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":report}),downloader=fetcher({
        "https://example.org/article.xml":jats(),"https://example.org/missing.pdf":OSError("HTTP 403")}))
    assert result["methods"]["assay"]["packet"]
    missing=result["methods"]["assay"]["documents"][1]
    assert missing["status"]=="unavailable" and "403" in missing["error"]
    register=json.loads((tmp_path/"out/methods/assay/acquisition.json").read_bytes())
    assert register["searches"][1]["status"]=="access_limited"
    assert result["scientific_resolution"] is False


async def test_augment_preserves_scope_variant_source_ids_and_passages(tmp_path):
    tasks=worklist(tmp_path,existing=True)
    old=load_packet(tmp_path/"assay/packet.json")
    doc=document("extra",url="https://example.org/supp.txt",role="supplement",fmt="text",
                 linked_from="https://example.org/article.xml")
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":findings("assay",[doc])}),
        downloader=fetcher({"https://example.org/supp.txt":b"Sequence the washed nuclei."}))
    packet=load_packet(Path(result["methods"]["assay"]["packet"]))
    assert all(packet[k]==old[k] for k in ("method_id","title","scope","variant","format_version","rendition_version"))
    assert packet["sources"][0]==old["sources"][0]
    mapping=json.loads((tmp_path/"out/replacements.json").read_bytes())
    assert mapping["replacements"]["assay"]["previous_packet_sha256"]==sha(tmp_path/"assay/packet.json")


async def test_ambiguous_scope_is_caller_decision_and_never_sent_to_finder(tmp_path):
    tasks=worklist(tmp_path,ambiguous=True)
    worker=Finder({})
    result=await acquire(tasks,tmp_path/"out",worker=worker,downloader=fetcher({}))
    assert not worker.calls and result["methods"]["assay"]["status"]=="needs_scope_decision"
    explicit={"assay":{"method_id":"assay","title":"Scoped original","scope":"Original nuclei workflow",
        "variant":"Chosen original","primary_identity":{"pmid":"123","title":"Published protocol"}}}
    result=await acquire(tasks,tmp_path/"explicit",scopes=explicit,worker=Finder({"assay":findings("assay",[document()])}),
        downloader=fetcher({"https://example.org/article.xml":jats()}))
    assert result["registration"]


async def test_stale_worklist_or_origin_rejected_before_finding_and_during_work(tmp_path):
    tasks=worklist(tmp_path)
    original=json.loads(tasks.read_bytes())["inputs"][0]["path"]
    Path(original).write_text("changed")
    worker=Finder({})
    with pytest.raises(PinDrift):await acquire(tasks,tmp_path/"before",worker=worker)
    assert not worker.calls and not (tmp_path/"before").exists()
    fresh=tmp_path/"fresh";fresh.mkdir();tasks=worklist(fresh)
    worker=Finder({"assay":findings("assay",[document()])},mutate=lambda _:tasks.write_text("changed"))
    with pytest.raises(PinDrift):await acquire(tasks,fresh/"out",worker=worker)
    assert not (fresh/"out/registration").exists()


async def test_pdf_primary_and_unlinked_supplement_remain_unregistered(tmp_path):
    tasks=worklist(tmp_path)
    docs=[document(fmt="pdf",url="https://example.org/article.pdf"),
          document("foreign",url="https://example.org/foreign.txt",role="supplement",fmt="text",
                   linked_from="https://example.org/article.pdf")]
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":findings("assay",docs)}),downloader=fetcher({
        "https://example.org/article.pdf":b"%PDF-1.7 original", "https://example.org/foreign.txt":b"Other content."}))
    assert result["registration"] is None
    assert all(d["status"]=="retrieved_unregistered" for d in result["methods"]["assay"]["documents"])


async def test_unfinished_native_turn_cannot_supply_download_locations(tmp_path):
    tasks=worklist(tmp_path);fetch=fetcher({})
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":findings("assay",[document()])},bad_archive=True),downloader=fetch)
    assert not fetch.calls and result["methods"]["assay"]["status"]=="failed"
    assert result["registration"] is None


def test_redirects_cannot_change_to_nonpublic_or_authenticated_url():
    for value in ("https://192.168.1.1/a", "http://[::1]/a", "https://localhost/a", "https://example.org:22/a"):
        with pytest.raises(ValueError):public_url(value)


async def test_unrelated_batch_state_advancement_does_not_invalidate_readiness_task(tmp_path):
    tasks=worklist(tmp_path)
    state=save(tmp_path/"unrelated_state.json",{"status":"running"})
    work=json.loads(tasks.read_bytes())
    work["inputs"].append({"path":str(state),"sha256":sha(state)})
    save(tasks,work)
    save(state,{"status":"completed"})
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":findings("assay",[document()])}),
        downloader=fetcher({"https://example.org/article.xml":jats()}))
    assert result["registration"] and result["scientific_resolution"] is False
    receipts=json.loads((tmp_path/"out/source_receipts.json").read_bytes())
    assert str(state) not in receipts["files"]


@pytest.mark.parametrize("name",["request.json","schema.json","instruction.json"])
async def test_changed_finder_inputs_cannot_supply_retrieval_locations(tmp_path,name):
    tasks=worklist(tmp_path);fetch=fetcher({})
    worker=Finder({"assay":findings("assay",[document()])},mutate=lambda attempt:(attempt/name).write_text("changed"))
    result=await acquire(tasks,tmp_path/"out",worker=worker,downloader=fetch)
    assert not fetch.calls and result["methods"]["assay"]["status"]=="failed"
    assert "Pinned file changed" in result["methods"]["assay"]["error"]
    assert result["registration"] is None


@pytest.mark.parametrize("title",["Published protocol","Another publication"])
async def test_citation_html_identity_checked_before_rendition_registration(tmp_path,title):
    tasks=worklist(tmp_path)
    html=(f'<meta name="citation_pmid" content="123"><meta name="citation_doi" content="10.1234/protocol">'
          f'<meta name="citation_title" content="{title}"><p>Original HTML: MgCl<sub>2</sub>.</p>').encode()
    doc=document(fmt="html",url="https://example.org/article.html")
    result=await acquire(tasks,tmp_path/"out",worker=Finder({"assay":findings("assay",[doc])}),
        downloader=fetcher({doc["url"]:html}))
    if title=="Published protocol":
        packet=load_packet(Path(result["methods"]["assay"]["packet"]))
        assert any("MgCl_(2)" in p["text"] for p in packet["sources"][0]["passages"])
        assert result["registration"]
    else:
        assert result["registration"] is None
        assert "title mismatch" in result["methods"]["assay"]["documents"][0]["error"]


async def test_production_worker_configuration_has_public_search_and_no_session_deadline(tmp_path,monkeypatch):
    from genomics_discovery.curation.source_acquisition import runtime
    tasks=worklist(tmp_path)
    configurations=[]
    class ConfiguredFinder(Finder):
        def __init__(self,**kwargs):
            configurations.append(kwargs)
            self.web_search=kwargs["web_search"];self.network_access=kwargs["network_access"]
            super().__init__({"assay":findings("assay",[document()])})
    monkeypatch.setattr(runtime,"CodexWorker",ConfiguredFinder)
    result=await acquire(tasks,tmp_path/"out",downloader=fetcher({"https://example.org/article.xml":jats()}))
    assert configurations==[{"model":"gpt-6.1-sol","effort":"xhigh","timeout_seconds":None,
                              "web_search":True,"network_access":True}]
    assert result["registration"]

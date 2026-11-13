"""Inventory and acquire Illumina catalogue diagrams and publication identities.

The inventory preserves source cards, including grouped and empty cards. Neither
a listed reference nor verified PubMed metadata establishes method ancestry or
scientific support for an extracted operation. Model extraction is separate.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree as ET


FORMAT = "illumina_source_inventory/1.0"
ACQUIRED_FORMAT = "illumina_source_acquisition/1.0"
BASE = "https://www.illumina.com"
_NCBI_LOCK = threading.Lock()
_OAI_LOCK = threading.Lock()
_NCBI_NEXT = 0.0
RESOURCE_PATHS = (
    ("rna_poster", "/content/dam/illumina-marketing/documents/applications/ngs-library-prep/for-all-you-seq-rna.pdf"),
    ("dna_poster", "/content/dam/illumina-marketing/documents/applications/ngs-library-prep/for-all-you-seq-dna.pdf"),
    ("single_cell_poster", "/content/dam/illumina-marketing/documents/applications/ngs-library-prep/for-all-you-seq-single-cell.pdf"),
    ("dna_methods_collection", "/content/dam/illumina-marketing/documents/products/research_reviews/dna-sequencing-methods-review-web.pdf"),
    ("rna_methods_collection", "/content/dam/illumina-marketing/documents/products/research_reviews/rna-sequencing-methods-review-web.pdf"),
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: Path, value) -> None:
    temporary = path.with_name(path.name + ".writing")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def https_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Expected a public HTTP(S) URL without credentials")
    return urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))


def asset_id(url: str) -> str:
    return "asset_" + sha(url.encode())[:24]


def _pmid(reference: dict) -> str | None:
    value = str(reference.get("pmid") or "")
    if value.isdigit():
        return value
    for key in ("canonical_url", "url"):
        match = re.search(r"(?:pubmed/|pubmed\.ncbi\.nlm\.nih\.gov/)(\d+)", reference.get(key, ""))
        if match:
            return match.group(1)
    return None


def build_inventory(catalogue: Path, selector: Path | None = None) -> dict:
    raw = catalogue.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    cards, assets, ids, publications = [], {}, set(), {}
    for row in rows:
        ident = row.get("record_id")
        if not isinstance(ident, str) or not ident or ident in ids:
            raise ValueError("Every catalogue card requires a unique record_id")
        ids.add(ident)
        card = {key: row.get(key) for key in (
            "record_id", "source_title", "source_page_url", "source_url", "explorer_url",
            "retrieved_at_utc", "source_category", "source_category_path", "description_verbatim",
            "advantages_verbatim", "limitations_verbatim", "source_sections",
            "cms_modified_at_not_invention_date", "curation_status", "missing_evidence",
        )}
        card["diagram_asset_ids"] = []
        card["source_id"] = "card:" + ident
        card["references"] = []
        card["source_record_status"] = ("substantive" if row.get("source_title") and
                                         row.get("description_verbatim") else "incomplete_source_record")
        for original_url in row.get("source_image_urls", []):
            url = https_url(original_url)
            aid = asset_id(url)
            item = assets.setdefault(aid, {"asset_id": aid, "url": url, "kind": "method_diagram",
                                          "source_id": "diagram:" + aid,
                                          "card_ids": [], "source_urls": []})
            if ident not in item["card_ids"]:
                item["card_ids"].append(ident)
            if original_url not in item["source_urls"]:
                item["source_urls"].append(original_url)
            if aid not in card["diagram_asset_ids"]:
                card["diagram_asset_ids"].append(aid)
        for role in ("references", "related_publications"):
            for index, reference in enumerate(row.get(role, [])):
                pmid = _pmid(reference)
                ref = {"reference_id": f"{ident}:{role}:{index}", "role": role,
                       "listed_by": "illumina", "source_reference": reference, "pmid": pmid,
                       "identity_status": "metadata_unverified",
                       "scientific_support_status": "listed_reference_not_claim_verification"}
                card["references"].append(ref)
                if pmid:
                    pub = publications.setdefault(pmid, {"pmid": pmid, "source_id": "paper:" + pmid,
                                                         "reference_ids": [], "card_ids": [], "reference_roles": []})
                    pub["reference_ids"].append(ref["reference_id"])
                    if ident not in pub["card_ids"]:
                        pub["card_ids"].append(ident)
                    if role not in pub["reference_roles"]:
                        pub["reference_roles"].append(role)
        cards.append(card)
    selector_raw = selector.read_bytes() if selector else None
    selector_text = selector_raw.decode("utf-8") if selector_raw is not None else ""
    resource_links = []
    for role, path in RESOURCE_PATHS:
        if selector is not None and path not in selector_text:
            resource_links.append({"role": role, "url": BASE + path,
                                   "status": "not_found_in_supplied_selector"})
            continue
        url = BASE + path
        aid = asset_id(url)
        assets[aid] = {"asset_id": aid, "url": url, "kind": role, "card_ids": [],
                       "source_id": "collection:" + aid,
                       "source_urls": [path], "discovery_basis": "selector_link" if selector else
                       "known_illumina_resource_path_unverified"}
        resource_links.append({"role": role, "url": url, "asset_id": aid,
                               "status": "inventoried"})
    return {"format_version": FORMAT, "catalogue": {"path": str(catalogue), "sha256": sha(raw)},
            "selector": ({"path": str(selector), "sha256": sha(selector_raw)} if selector else None),
            "cards": cards, "assets": list(assets.values()), "publications": list(publications.values()),
            "resources": resource_links,
            "statistics": {"cards": len(cards), "substantive_cards": sum(
                c["source_record_status"] == "substantive" for c in cards),
                "cards_with_diagrams": sum(bool(c["diagram_asset_ids"]) for c in cards),
                "unique_method_diagrams": sum(a["kind"] == "method_diagram" for a in assets.values()),
                "collection_resources": sum(a["kind"] != "method_diagram" for a in assets.values()),
                "reference_occurrences": sum(len(c["references"]) for c in cards),
                "unique_pmids": len(publications)},
            "scope": "Cached source catalogue inventory; no inference of one-card/one-method identity, publication priority, or scientific claim support."}


def fetch(url: str, destination: Path, *, timeout: int = 90, preserve_http_error: bool = False) -> dict:
    """Use system TLS trust and preserve only completed download bodies."""
    url = https_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    temporary = destination.with_name(destination.name + ".partial")
    if temporary.exists():
        raise FileExistsError(temporary)
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is required for acquisition with system TLS trust")
    if (urlsplit(url).hostname or "").endswith("ncbi.nlm.nih.gov"):
        global _NCBI_NEXT
        with _NCBI_LOCK:
            time.sleep(max(0.0, _NCBI_NEXT - time.monotonic()))
            _NCBI_NEXT = time.monotonic() + 0.35
    command = [
        curl, "--silent", "--show-error", "--location", "--proto", "=https",
        "--proto-redir", "=https", "--max-redirs", "5", "--max-time", str(timeout),
        "--max-filesize", "200000000", "--output", str(temporary),
        "--write-out", "%{http_code}\n%{url_effective}", url,
    ]
    if not preserve_http_error:
        command.insert(3, "--fail")
    result = subprocess.run(command, capture_output=True, timeout=timeout + 10)
    if result.returncode:
        if temporary.exists():
            temporary.unlink()
        raise RuntimeError(f"curl exit {result.returncode}: {result.stderr.decode(errors='replace')[:1200]}")
    lines = result.stdout.decode().splitlines()
    metadata = {"request_url": url, "http_status": int(lines[0]), "final_url": lines[1]}
    temporary.rename(destination)
    data = destination.read_bytes()
    metadata.update(path=str(destination), sha256=sha(data), bytes=len(data))
    return metadata


def parse_pubmed(raw: bytes) -> dict[str, dict]:
    root = ET.fromstring(raw)
    if root.tag != "PubmedArticleSet":
        raise ValueError("Response is not a PubMed article set")
    publications = {}
    def txt(element):
        return " ".join("".join(element.itertext()).split()) if element is not None else None
    for record in root:
        citation = record.find("MedlineCitation")
        if citation is None:
            citation = record.find("BookDocument")
        if citation is None:
            continue
        pmid = txt(citation.find("PMID"))
        if not pmid or not pmid.isdigit():
            continue
        article = citation.find("Article")
        if article is None:
            article = citation
        title = txt(article.find("ArticleTitle"))
        identifiers = {}
        # ArticleId also occurs inside PubmedData/ReferenceList. Those identify
        # cited papers, not this paper, and must never drive fulltext retrieval.
        own_identifiers = record.findall("./PubmedData/ArticleIdList/ArticleId")
        own_identifiers += record.findall("./PubmedBookData/ArticleIdList/ArticleId")
        own_identifiers += citation.findall("./ArticleIdList/ArticleId")
        for ident in own_identifiers:
            identifiers.setdefault(ident.get("IdType", "unspecified"), []).append(txt(ident))
        dates = [{"kind": "journal_pub_date", "text": txt(e)} for e in article.findall("Journal/JournalIssue/PubDate")]
        dates.extend({"kind": e.get("DateType", "article_date"), "text": txt(e)}
                     for e in article.findall("ArticleDate"))
        dates.extend({"kind": e.get("PubStatus", "publication_history"), "text": txt(e)}
                     for e in record.findall(".//History/PubMedPubDate"))
        abstracts = [{"label": e.get("Label"), "text": txt(e)} for e in article.findall("Abstract/AbstractText")]
        publications[pmid] = {"pmid": pmid, "identity_status": "pubmed_record_retrieved",
                              "source_id": "metadata:" + pmid,
                              "title": title, "journal": txt(article.find("Journal/Title")),
                              "publication_dates": dates, "identifiers": identifiers,
                              "abstract": abstracts,
                              "authors": [txt(e) for e in article.findall("AuthorList/Author")],
                              "publication_types": [txt(e) for e in article.findall("PublicationTypeList/PublicationType")],
                              "scientific_support_status": "metadata_and_abstract_only_not_protocol_or_claim_review",
                              "origin_date_status": "not_established_by_publication_metadata"}
    return publications


def jats_text(raw: bytes) -> tuple[str, list[dict]]:
    """Render bounded blocks with paragraph/heading boundaries, retaining JATS.

    This deterministic reading rendition is not a claim of layout equivalence.
    The original article XML remains the authoritative source artifact.
    """
    article = ET.fromstring(raw)
    if article.tag.rsplit("}", 1)[-1] != "article":
        raise ValueError("Expected JATS article")
    blocks = []
    def visit(element, location):
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in {"article-title", "title", "p", "table-wrap", "fig", "ref"}:
            text = " ".join("".join(element.itertext()).split())
            if text:
                blocks.append({"block_id": f"b{len(blocks) + 1:05d}", "kind": tag,
                               "locator": location, "text": text})
            return
        counts = {}
        for child in element:
            child_tag = child.tag.rsplit("}", 1)[-1]
            counts[child_tag] = counts.get(child_tag, 0) + 1
            visit(child, f"{location}/{child_tag}[{counts[child_tag]}]")
    visit(article, "/article")
    if not blocks:
        raise ValueError("JATS contains no readable text blocks")
    rendition = "\n\n".join(f"[{b['block_id']}] {b['text']}" for b in blocks) + "\n"
    return rendition, blocks


def acquire_pmc_fulltext(publication: dict, output: Path) -> dict:
    """Recover identity-bound JATS through the current official PMC OAI API.

    PMC retired the former OA package endpoints in 2026. Preserve the complete
    OAI response, then serialize its article subtree into a separate rendition.
    OAI requests are serialized, following its published no-concurrency guidance.
    """
    pmid = publication["pmid"]
    pmcs = publication.get("identifiers", {}).get("pmc", [])
    pmcid = next((p for p in pmcs if p and re.fullmatch(r"PMC\d+", p)), None)
    if not pmcid:
        return {"source_id": "paper:" + pmid, "status": "no_pmc_identifier_in_pubmed_metadata"}
    directory = output / "papers" / pmid
    directory.mkdir(parents=True, exist_ok=False)
    oa_url = "https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/?" + urlencode({
        "verb": "GetRecord", "identifier": "oai:pubmedcentral.nih.gov:" + pmcid.removeprefix("PMC"),
        "metadataPrefix": "pmc"})
    oa_path = directory / "original_oai_response.xml"
    try:
        with _OAI_LOCK:
            # OAI returns typed protocol errors with HTTP400. Keep those original
            # bodies for availability diagnosis; they are never article sources.
            oa_receipt = fetch(oa_url, oa_path, preserve_http_error=True)
        root = ET.fromstring(oa_path.read_bytes())
        local = lambda e: e.tag.rsplit("}", 1)[-1]
        errors = [e for e in root.iter() if local(e) == "error"]
        if errors:
            error = errors[0]
            status = ("fulltext_not_available_via_pmc_oai" if error.get("code") in
                      {"cannotDisseminateFormat", "idDoesNotExist"} else "failed")
            return {"source_id": "paper:" + pmid, "pmcid": pmcid, "status": status,
                    "original_response": {**oa_receipt, "path": str(oa_path.relative_to(output))},
                    "error_code": error.get("code"), "reason": " ".join(error.itertext()).strip()}
        if oa_receipt.get('http_status', 200) >= 400:
            raise ValueError('HTTP error response lacks a recognized OAI error; retain original response for inspection')
        candidates = []
        for article in (e for e in root.iter() if local(e) == "article"):
            front = next((e for e in article if local(e) == "front"), None)
            meta = next((e for e in front if local(e) == "article-meta"), None) if front is not None else None
            if meta is None:
                continue
            ids = {e.get("pub-id-type"): (e.text or "").strip() for e in meta if local(e) == "article-id"}
            declared_pmid, declared_pmc = ids.get("pmid"), ids.get("pmcid") or ids.get("pmc")
            if declared_pmid and declared_pmid != pmid:
                continue
            if declared_pmc and declared_pmc not in {pmcid, pmcid.removeprefix("PMC")}:
                continue
            if declared_pmid or declared_pmc:
                candidates.append((article, ids))
        if len(candidates) != 1:
            raise ValueError(f"Expected exactly one identity-matching article XML; found {len(candidates)}")
        article, identifiers = candidates[0]
        raw = ET.tostring(article, encoding="utf-8", xml_declaration=True)
        article_path = directory / "article.xml"
        article_path.write_bytes(raw)
        text, blocks = jats_text(raw)
        text_path = directory / "article.txt"
        text_path.write_text(text, encoding="utf-8")
        blocks_path = directory / "blocks.json"
        write(blocks_path, {"format_version": "jats_reading_blocks/1.0", "source_id": "paper:" + pmid,
                            "article_sha256": sha(raw), "blocks": blocks})
        return {"source_id": "paper:" + pmid, "pmcid": pmcid, "status": "fulltext_acquired",
                "path": str(article_path.relative_to(output)), "sha256": sha(raw), "bytes": len(raw),
                "text_path": str(text_path.relative_to(output)), "text_sha256": sha(text.encode("utf-8")),
                "blocks_path": str(blocks_path.relative_to(output)), "block_count": len(blocks),
                "rendition": "jats_reading_blocks/1.0; paragraph boundaries preserved; not original layout",
                "media_type": "application/xml", "format": "JATS", "identifiers": identifiers,
                "original_response": {**oa_receipt, "path": str(oa_path.relative_to(output))},
                "derivation": "Article subtree serialized from preserved official PMC OAI response",
                "license": [" ".join(e.itertext()).strip() for e in article.iter() if local(e) == "license"],
                "scientific_support_status": "fulltext_available_not_content_reviewed"}
    except Exception as exc:
        return {"source_id": "paper:" + pmid, "pmcid": pmcid, "status": "failed",
                "error": f"{type(exc).__name__}: {exc}"}


def acquire(inventory_path: Path, output: Path, *, workers: int = 4,
            metadata: bool = False, fulltext: bool = False, batch_size: int = 100,
            fulltext_workers: int = 3) -> dict:
    if workers < 1 or workers > 16 or batch_size < 1 or batch_size > 200:
        raise ValueError("Use 1-16 acquisition workers and 1-200 PubMed records per request")
    if fulltext and not metadata:
        raise ValueError("Full-text acquisition requires --metadata for verified PMID/PMCID links")
    if fulltext_workers < 1 or fulltext_workers > 8:
        raise ValueError("Use 1-8 full-text acquisition workers")
    raw = inventory_path.read_bytes()
    inventory = json.loads(raw)
    if inventory.get("format_version") != FORMAT:
        raise ValueError("Unknown source inventory format")
    output.mkdir(parents=True, exist_ok=False)
    write(output / "inventory.json", inventory)
    state = {"format_version": ACQUIRED_FORMAT, "inventory_sha256": sha(raw), "status": "running",
             "assets": [], "publication_batches": [], "publications": {}, "fulltexts": {},
             "scientific_review": False}
    write(output / "sources.json", state)
    def one(asset):
        kind = asset["kind"]
        suffix = ".png" if kind == "method_diagram" else ".pdf"
        destination = output / "assets" / (asset["asset_id"] + suffix)
        try:
            receipt = fetch(asset["url"], destination)
            head = destination.read_bytes()[:8]
            valid = head.startswith(b"\x89PNG\r\n\x1a\n") if suffix == ".png" else head.startswith(b"%PDF-")
            if not valid:
                return {**asset, **receipt, "status": "invalid_media", "error": "Response signature differs from required image/PDF"}
            return {**asset, **receipt, "path": str(destination.relative_to(output)), "status": "downloaded"}
        except Exception as exc:
            return {**asset, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # map preserves inventory order; write a durable checkpoint after each result.
        for receipt in pool.map(one, inventory["assets"]):
            state["assets"].append(receipt)
            write(output / "sources.json", state)
    if metadata:
        pmids = sorted((p["pmid"] for p in inventory["publications"]), key=int)
        for offset in range(0, len(pmids), batch_size):
            batch = pmids[offset:offset + batch_size]
            url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urlencode(
                {"db": "pubmed", "id": ",".join(batch), "retmode": "xml", "tool": "sequencing_technology_atlas"})
            destination = output / "pubmed" / f"batch_{offset // batch_size + 1:04d}.xml"
            try:
                receipt = fetch(url, destination)
                found = parse_pubmed(destination.read_bytes())
                for pmid, record in found.items():
                    if pmid not in batch:
                        raise ValueError("PubMed returned an unrequested PMID")
                    record["metadata_source"] = str(destination.relative_to(output))
                state["publications"].update(found)
                state["publication_batches"].append({**receipt, "path": str(destination.relative_to(output)),
                    "requested_pmids": batch, "returned_pmids": list(found), "status": "downloaded"})
                for pmid in batch:
                    state["publications"].setdefault(pmid, {"pmid": pmid, "identity_status": "not_returned_by_pubmed"})
            except Exception as exc:
                state["publication_batches"].append({"request_url": url, "requested_pmids": batch,
                    "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            write(output / "sources.json", state)
            time.sleep(0.4)  # No API key; remain below NCBI's three-requests/second limit.
    fulltext_expected = []
    if fulltext:
        # Acquire papers listed as method references, preserving related-publication
        # metadata separately rather than silently treating all related papers as protocols.
        fulltext_expected = [p["pmid"] for p in inventory["publications"] if "references" in p["reference_roles"]]
        def fulltext_one(pmid):
            publication = state["publications"].get(pmid)
            if publication is None or publication.get("identity_status") != "pubmed_record_retrieved":
                return {"source_id": "paper:" + pmid, "status": "metadata_unavailable_no_fulltext_attempt"}
            return acquire_pmc_fulltext(publication, output)
        with ThreadPoolExecutor(max_workers=fulltext_workers) as pool:
            for pmid, receipt in zip(fulltext_expected, pool.map(fulltext_one, fulltext_expected)):
                state["fulltexts"][pmid] = receipt
                write(output / "sources.json", state)
    successful = sum(a["status"] == "downloaded" for a in state["assets"])
    metadata_count = sum(p["identity_status"] == "pubmed_record_retrieved" for p in state["publications"].values())
    fulltext_count = sum(p["status"] == "fulltext_acquired" for p in state["fulltexts"].values())
    complete = (successful == len(inventory["assets"]) and
                (not metadata or metadata_count == len(inventory["publications"])) and
                (not fulltext or fulltext_count == len(fulltext_expected)))
    state["status"] = "completed" if complete else "completed_with_failures"
    state["statistics"] = {"cards": len(inventory["cards"]), "assets_expected": len(inventory["assets"]),
        "assets_downloaded": successful, "assets_failed": len(inventory["assets"]) - successful,
        "pmids_expected": len(inventory["publications"]), "pmids_retrieved": metadata_count,
        "fulltexts_expected": len(fulltext_expected), "fulltexts_acquired": fulltext_count}
    write(output / "sources.json", state)
    return state


def recover_fulltext(sources_path: Path, output: Path, *, fulltext_workers: int = 3) -> dict:
    """Register a new recovery attempt without rewriting the prior acquisition."""
    if not 1 <= fulltext_workers <= 8:
        raise ValueError("Use 1-8 full-text acquisition workers")
    sources_path = sources_path.resolve()
    raw = sources_path.read_bytes()
    parent = json.loads(raw)
    if parent.get("format_version") != ACQUIRED_FORMAT:
        raise ValueError("Unknown source acquisition format")
    prior_root = sources_path.parent
    inventory_path = prior_root / "inventory.json"
    inventory_raw = inventory_path.read_bytes()
    inventory = json.loads(inventory_raw)
    if inventory.get("format_version") != FORMAT:
        raise ValueError("Unknown source inventory format")
    # Rebase custody links in the new descriptor; leave all prior bytes alone.
    path_keys = {"path", "text_path", "blocks_path", "metadata_source"}
    def rebase(value):
        if isinstance(value, list):
            return [rebase(v) for v in value]
        if not isinstance(value, dict):
            return value
        result = {k: rebase(v) for k, v in value.items()}
        for key in path_keys:
            if isinstance(result.get(key), str):
                p = Path(result[key])
                result[key] = str((prior_root / p).resolve() if not p.is_absolute() else p.resolve())
        if "path" in result and "sha256" in result:
            p = Path(result["path"])
            if not p.is_file() or sha(p.read_bytes()) != result["sha256"]:
                raise ValueError(f"Prior source custody mismatch: {p}")
        return result
    assets = rebase(parent.get("assets", []))
    publications = rebase(parent.get("publications", {}))
    prior_fulltexts = rebase(parent.get("fulltexts", {}))
    output.mkdir(parents=True, exist_ok=False)
    (output / "inventory.json").write_bytes(inventory_raw)
    state = {"format_version": ACQUIRED_FORMAT, "inventory_sha256": sha(inventory_raw), "status": "running",
             "parent_acquisition": {"path": str(sources_path), "sha256": sha(raw)},
             "assets": assets, "publications": publications,
             "publication_batches": rebase(parent.get("publication_batches", [])),
             "fulltexts": {k: v for k, v in prior_fulltexts.items() if v.get("status") == "fulltext_acquired"},
             "scientific_review": False}
    expected = [p["pmid"] for p in inventory["publications"] if "references" in p["reference_roles"]]
    pending = [p for p in expected if p not in state["fulltexts"]]
    write(output / "sources.json", state)
    def one(pmid):
        publication = publications.get(pmid)
        if publication is None or publication.get("identity_status") != "pubmed_record_retrieved":
            return {"source_id": "paper:" + pmid, "status": "metadata_unavailable_no_fulltext_attempt"}
        return acquire_pmc_fulltext(publication, output)
    with ThreadPoolExecutor(max_workers=fulltext_workers) as pool:
        for pmid, receipt in zip(pending, pool.map(one, pending)):
            state["fulltexts"][pmid] = receipt
            write(output / "sources.json", state)
    successful = sum(v.get("status") == "fulltext_acquired" for v in state["fulltexts"].values())
    assets_ok = sum(a.get("status") == "downloaded" for a in assets)
    metadata_ok = sum(p.get("identity_status") == "pubmed_record_retrieved" for p in publications.values())
    state["status"] = ("completed" if successful == len(expected) and assets_ok == len(inventory["assets"])
                       and metadata_ok == len(inventory["publications"]) else "completed_with_failures")
    state["statistics"] = {"cards": len(inventory["cards"]), "fulltexts_expected": len(expected),
        "fulltexts_acquired": successful, "fulltexts_reused": len(expected) - len(pending),
        "assets_expected": len(inventory["assets"]), "assets_downloaded": assets_ok,
        "pmids_expected": len(inventory["publications"]), "pmids_retrieved": metadata_ok}
    if sha(sources_path.read_bytes()) != sha(raw):
        raise ValueError("Prior acquisition changed while registering recovery")
    write(output / "sources.json", state)
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("catalogue", type=Path)
    inventory.add_argument("--selector", type=Path)
    inventory.add_argument("--output", required=True, type=Path)
    acquisition = commands.add_parser("acquire")
    acquisition.add_argument("inventory", type=Path)
    acquisition.add_argument("--output", required=True, type=Path)
    acquisition.add_argument("--workers", type=int, default=4)
    acquisition.add_argument("--metadata", action="store_true")
    acquisition.add_argument("--fulltext", action="store_true",
                             help="Acquire PMC OA article XML for method references, with explicit unavailable statuses")
    acquisition.add_argument("--batch-size", type=int, default=100)
    acquisition.add_argument("--fulltext-workers", type=int, default=3)
    recovery = commands.add_parser("recover-fulltext")
    recovery.add_argument("sources", type=Path)
    recovery.add_argument("--output", required=True, type=Path)
    recovery.add_argument("--fulltext-workers", type=int, default=3)
    args = parser.parse_args(argv)
    if args.command == "inventory":
        result = build_inventory(args.catalogue, args.selector)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps(result["statistics"]))
    elif args.command == "acquire":
        result = acquire(args.inventory, args.output, workers=args.workers,
                         metadata=args.metadata, fulltext=args.fulltext, batch_size=args.batch_size,
                         fulltext_workers=args.fulltext_workers)
        print(json.dumps(result["statistics"]))
        return 0 if result["status"] == "completed" else 1
    else:
        result = recover_fulltext(args.sources, args.output, fulltext_workers=args.fulltext_workers)
        print(json.dumps(result["statistics"]))
        return 0 if result["status"] == "completed" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Recover non-PMID method references and the empty MDA-family source card.

Read existing identity-bound registrations before requesting public metadata.
This prepares evidence; it does not inherit a historical scientific verdict.
"""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import quote, urlencode, unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from genomics_discovery.curation.illumina_sources import (  # noqa: E402
    acquire_pmc_fulltext, asset_id, fetch, parse_pubmed, sha, write,
)


def doi_from_reference(reference: dict) -> str | None:
    url = unquote(reference.get("url") or reference.get("canonical_url") or "")
    found = re.search(r"10\.\d{4,9}/[^\s<>]+", url)
    if found:
        return re.sub(r"/(?:abstract|full|pdf)$", "", found.group()).rstrip(".,;").lower()
    preprint = re.search(r"biorxiv\.org/content/(?:early/\d{4}/\d{2}/\d{2}/)?(\d{6})", url)
    return "10.1101/" + preprint.group(1) if preprint else None


def cached_registration(doi: str, roots: list[Path]) -> tuple[Path, dict] | None:
    matches = []
    for root in roots:
        for path in sorted(root.glob("*/registration*/registration.json")):
            record = json.loads(path.read_text())
            if (record.get("identity", {}).get("doi") or "").lower() != doi:
                continue
            if record.get("status") != "registered_for_fresh_source_review":
                continue
            if not record.get("article_selection"):
                continue
            for key in ("original", "rendition", "metadata"):
                source = Path(record[key])
                if not source.is_file():
                    raise ValueError(f"Cached registration custody mismatch: {source}")
                if not materialized(source):
                    if key == "original" and record.get("public_url"):
                        continue  # Redownload the exact public original and check its pinned hash below.
                    raise ValueError(f"Registered {key} is an unmaterialized cloud placeholder: {source}")
                if sha(source.read_bytes()) != record[key + "_sha256"]:
                    raise ValueError(f"Cached registration custody mismatch: {source}")
            matches.append((path, record))
    # More recent explicit article-boundary registrations supersede earlier
    # identity-only records without modifying either historical registration.
    return matches[-1] if matches else None


def materialized(path: Path) -> bool:
    if sys.platform != "darwin":
        return True
    # Some FileProvider volumes do not expose SF_DATALESS through Python's
    # st_flags. ls uses the platform metadata API without opening file contents.
    probe = subprocess.run(["/bin/ls", "-ldO", str(path)], capture_output=True, text=True, timeout=5)
    return "dataless" not in probe.stdout


def copy_registration(reference_id: str, registration_path: Path, record: dict, output: Path,
                      *, network: bool = False) -> dict:
    directory = output / "cached" / sha(reference_id.encode())[:16]
    directory.mkdir(parents=True)
    copies = {}
    for kind, name in (("original", "original.pdf"), ("rendition", "article.txt"), ("metadata", "metadata.json")):
        source = Path(record[kind])
        target = directory / name
        redownload = None
        if not materialized(source):
            if not network or kind != "original" or not record.get("public_url"):
                raise ValueError(f"Cannot read registered cloud placeholder without explicit recovery: {source}")
            redownload = fetch(record["public_url"], target)
        else:
            shutil.copyfile(source, target)
        expected = record[kind + "_sha256"]
        if sha(target.read_bytes()) != expected:
            raise ValueError("Copied cached source differs from registered bytes")
        copies[kind] = {"path": str(target.resolve()), "sha256": expected,
                        "parent_path": str(source), "bytes": target.stat().st_size}
        if redownload:
            copies[kind]["public_redownload"] = redownload
    target_registration = directory / "registration.json"
    shutil.copyfile(registration_path, target_registration)
    return {"source_id": "reference:" + reference_id, "kind": "text", "content_level": "fulltext",
            "identity_status": "cached_registration_identity_and_custody_verified",
            "status": "available", "title": record["identity"]["title"],
            "doi": record["identity"]["doi"], "text_path": copies["rendition"]["path"],
            "text_sha256": copies["rendition"]["sha256"], "original_custody": copies,
            "public_url": record.get("public_url"), "parent_registration": {
                "path": str(target_registration.resolve()), "sha256": sha(target_registration.read_bytes()),
                "original_path": str(registration_path)},
            "article_selection": record["article_selection"],
            "scientific_support_status": "fulltext_available_for_fresh_review; no prior scientific verdict inherited"}


class PMCArticleHTML(HTMLParser):
    """Read explicit PMC citation identity and its article-body section only."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.citations = {}
        self.section_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("name", "").startswith("citation_"):
            self.citations.setdefault(attributes["name"], []).append(attributes.get("content", ""))
        if tag == "section":
            if self.section_depth:
                self.section_depth += 1
            elif "main-article-body" in attributes.get("class", "").split():
                self.section_depth = 1
        if self.section_depth and tag in {"section", "p", "h1", "h2", "h3", "h4", "li", "tr", "br"}:
            self.parts.append("\n\n")
        if self.section_depth and tag in {"td", "th"}:
            self.parts.append("\t")

    def handle_endtag(self, tag):
        if self.section_depth and tag in {"section", "p", "h1", "h2", "h3", "h4", "li", "tr"}:
            self.parts.append("\n\n")
        if tag == "section" and self.section_depth:
            self.section_depth -= 1

    def handle_data(self, data):
        if self.section_depth:
            self.parts.append(data)

    def text(self):
        return "\n\n".join(" ".join(part.split()) for part in
                            re.split(r"\n\s*\n", "".join(self.parts)) if part.strip()) + "\n"


def register_existing_pmc_html(publication: dict, receipt_path: Path, output: Path) -> dict:
    """Register an explicitly provided prior HTML recovery; never bulk-scrape PMC.

    PMC permits systematic acquisition through its sanctioned APIs. A public
    HTML page is not an automatic fallback when its OAI format is unavailable.
    """
    pmcid = next((value for value in publication.get("identifiers", {}).get("pmc", [])
                  if value and re.fullmatch(r"PMC\d+", value)), None)
    if not pmcid:
        return {"status": "no_pmc_identifier"}
    directory = output / "public_html" / pmcid
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("request_url") != f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
            or receipt.get("http_status") != 200):
        raise ValueError("Existing PMC HTML receipt does not identify the requested public article")
    source = Path(receipt["path"])
    if sha(source.read_bytes()) != receipt["sha256"]:
        raise ValueError("Existing PMC HTML recovery differs from its receipt")
    directory.mkdir(parents=True)
    shutil.copyfile(source, directory / "original.html")
    parser = PMCArticleHTML()
    parser.feed((directory / "original.html").read_text())
    if parser.citations.get("citation_pmid") != [publication["pmid"]]:
        raise ValueError("Public PMC HTML citation PMID does not match requested publication")
    titles = parser.citations.get("citation_title", [])
    normalized = lambda value: re.sub(r"\W+", "", value).lower()
    if len(titles) != 1 or normalized(titles[0]) != normalized(publication["title"]):
        raise ValueError("Public PMC HTML citation title does not match PubMed metadata")
    body = parser.text()
    if len(body) < 1000:
        raise ValueError("Public PMC HTML has no substantive main-article-body section")
    text = "Title: " + titles[0] + "\nPMID: " + publication["pmid"] + "\n\n" + body
    path = directory / "article.txt"
    path.write_text(text)
    return {"status": "fulltext_acquired", "format": "PMC_HTML", "original_response": receipt,
            "path": str((directory / "original.html").resolve()), "sha256": receipt["sha256"],
            "text_path": str(path.resolve()), "text_sha256": sha(text.encode()),
            "derivation": "HTMLParser extracts only the explicit main-article-body section; paragraph boundaries retained",
            "identity_status": "pubmed_pmid_and_title_match_pmc_html_citation_metadata",
            "registration_basis": "explicit_existing_recovery_not_automated_HTML_fallback",
            "parent_receipt": {"path": str(receipt_path.resolve()), "sha256": sha(receipt_path.read_bytes())},
            "reuse_status": "publicly_accessible_html_not_an_OA_license_assertion"}


def recover_pubmed_leads(leads: list[dict], output: Path,
                        existing_html_receipts: list[Path] = ()) -> tuple[dict, dict]:
    """Recover explicitly registered leads without replacing original listings."""
    path = output / "recovery_pubmed_metadata.xml"
    receipt = fetch("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urlencode({
        "db": "pubmed", "id": ",".join(sorted({lead["pmid"] for lead in leads})), "retmode": "xml"}), path)
    publications = parse_pubmed(path.read_bytes())
    registered_html = {}
    for receipt_path in existing_html_receipts:
        registered = json.loads(receipt_path.read_text())
        parser = PMCArticleHTML()
        parser.feed(Path(registered["path"]).read_text())
        ids = parser.citations.get("citation_pmid", [])
        if len(ids) != 1 or ids[0] in registered_html:
            raise ValueError("Prior HTML recovery must identify one unique PMID")
        registered_html[ids[0]] = receipt_path
    recovered, associations = {}, {}
    for lead in leads:
        ident, pmid, card_id = lead["reference_id"], lead["pmid"], lead["card_id"]
        associations.setdefault(card_id, []).append(lead)
        entry = {"source_id": "reference:" + ident, "kind": "text", "reference_id": ident,
                 "card_id": card_id, "pmid": pmid, "recovery_reference": lead,
                 "metadata_custody": receipt, "scientific_support_status": "source_available_for_fresh_review"}
        publication = publications.get(pmid)
        if publication is None:
            entry.update(status="metadata_unavailable", content_level="unavailable", identity_status="unverified")
        else:
            expected_title = lead.get("expected_title")
            if expected_title and (re.sub(r"\W+", "", expected_title).lower() !=
                                   re.sub(r"\W+", "", publication["title"]).lower()):
                raise ValueError(f"Recovered PubMed title differs from registered identity lead: {ident}")
            entry.update(title=publication["title"], identity_status="pubmed_record_retrieved")
            fulltext = acquire_pmc_fulltext(publication, output)
            if fulltext["status"] != "fulltext_acquired" and pmid in registered_html:
                try:
                    html = register_existing_pmc_html(publication, registered_html[pmid], output)
                    html["prior_oai_attempt"] = fulltext
                    fulltext = html
                except Exception as exc:
                    fulltext["public_html_recovery_error"] = f"{type(exc).__name__}: {exc}"
            if fulltext["status"] == "fulltext_acquired":
                text_path = Path(fulltext["text_path"])
                if not text_path.is_absolute():
                    text_path = output / text_path
                entry.update(status="available", content_level="fulltext", fulltext_custody=fulltext,
                             text_path=str(text_path.resolve()), text_sha256=fulltext["text_sha256"])
            else:
                text = "Title: " + publication["title"] + "\nPMID: " + pmid + "\n\n" + "\n\n".join(
                    block["text"] for block in publication.get("abstract", []))
                text_path = output / ("metadata_" + pmid + ".txt")
                text_path.write_text(text)
                level = "abstract" if publication.get("abstract") else "metadata"
                entry.update(status=level + "_available_fulltext_not_recovered", content_level=level,
                             text_path=str(text_path.resolve()), text_sha256=sha(text.encode()), fulltext_custody=fulltext)
        recovered[ident] = entry
    return recovered, associations


def recovery_bundle(inventory: dict, external_sources: dict, diagram_receipt_path: Path | None) -> dict:
    """An explicit overlay; the empty original card remains byte-for-byte representable."""
    result = {"format_version": "illumina_source_recovery_bundle/1.0", "scientific_review": False,
              "external_publications": external_sources["external_publications"],
              "recovered_card_references": external_sources["recovered_card_references"], "card_overlays": {}}
    if diagram_receipt_path:
        receipt = json.loads(diagram_receipt_path.read_text())
        path = Path(receipt["path"])
        if sha(path.read_bytes()) != receipt["sha256"]:
            raise ValueError("Recovered diagram bytes differ from acquisition receipt")
        aid = asset_id(receipt["url"])
        original = next(card for card in inventory["cards"] if card["record_id"] == receipt["card_id"])
        result["card_overlays"][receipt["card_id"]] = {
            "original_card": original, "original_title": original["source_title"],
            "recovered_title": "MDA / IMS-MDA / MIDAS",
            "title_basis": "Abbreviations transcribed from the recovered public diagram caption; original card title remains null",
            "caption_verbatim": "Multiple displacement amplification (MDA). Immunomagnetic separation for targeted bacterial enrichment for MDA (IMS-MDA). Microwell displacement amplification system (MIDAS).",
            "diagram_asset_ids": [aid], "assets": [{"asset_id": aid, "source_id": "diagram:" + aid,
                "kind": "method_diagram", "url": receipt["url"], "card_ids": [receipt["card_id"]],
                "path": str(path.resolve()), "sha256": receipt["sha256"], "bytes": receipt["bytes"],
                "status": "downloaded", "discovery_basis": receipt["discovery_basis"],
                "parent_receipt": {"path": str(diagram_receipt_path.resolve()),
                                   "sha256": sha(diagram_receipt_path.read_bytes())}}],
            "scope_note": "Grouped family diagram; depicted operations must not automatically be attributed to every named variant",
            "source_content_restored": False}
    return result


def run(inventory_path: Path, output: Path, registration_roots: list[Path], *, network: bool,
        include_mda: bool, recovery_manifest: Path | None = None, diagram_receipt: Path | None = None,
        existing_html_receipts: list[Path] = ()) -> dict:
    inventory_raw = inventory_path.read_bytes()
    inventory = json.loads(inventory_raw)
    output.mkdir(parents=True, exist_ok=False)
    results = {"format_version": "illumina_external_publications/1.0", "inventory_sha256": sha(inventory_raw),
               "external_publications": {}, "recovered_card_references": {}, "scientific_review": False}
    for card in inventory["cards"]:
        for ref in card["references"]:
            if ref["pmid"] or ref["role"] != "references":
                continue
            ident = ref["reference_id"]
            original = ref["source_reference"]
            doi = doi_from_reference(original)
            entry = {"source_id": "reference:" + ident, "kind": "text", "card_id": card["record_id"],
                     "reference_id": ident, "listed_reference": original, "doi": doi,
                     "content_level": "unavailable", "identity_status": "unverified"}
            try:
                cached = cached_registration(doi, registration_roots) if doi else None
                if cached:
                    entry.update(copy_registration(ident, *cached, output, network=network))
                elif not original.get("text") and (original.get("url") or "").rstrip("/") == "https://www.illumina.com":
                    entry.update(status="malformed_homepage_link_not_publication", identity_status="no_publication_identified")
                elif doi and network:
                    directory = output / "metadata" / sha(ident.encode())[:16]
                    path = directory / "crossref.json"
                    receipt = fetch("https://api.crossref.org/works/" + quote(doi, safe=""), path)
                    message = json.loads(path.read_text())["message"]
                    if message.get("DOI", "").lower() != doi:
                        raise ValueError("Crossref returned a different DOI")
                    titles = message.get("title", [])
                    text = "Title: " + " / ".join(titles) + "\nDOI: " + doi + "\n"
                    if message.get("abstract"):
                        text += "Abstract: " + re.sub(r"<[^>]+>", " ", message["abstract"]) + "\n"
                    text_path = directory / "metadata.txt"
                    text_path.write_text(text)
                    entry.update(status="metadata_available", content_level="abstract" if message.get("abstract") else "metadata",
                                 identity_status="crossref_doi_verified", title=" / ".join(titles),
                                 text_path=str(text_path.resolve()), text_sha256=sha(text.encode()), original_custody=receipt)
                else:
                    entry["status"] = "supporting_or_unresolved_reference_without_recovered_text"
            except Exception as exc:
                entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            results["external_publications"][ident] = entry
            write(output / "external_sources.json", results)
    leads = []
    if include_mda:
        # Recovery leads are not references present in the empty live card.
        for label, pmid in [("mda", "11959976"), ("midas", "24213699"),
                            ("ims_mda_protocol", "24202554"), ("ims_mda_original", "23525359")]:
            leads.append({"reference_id": "mda-ims-mda-midas:recovery:" + label,
                "card_id": "mda-ims-mda-midas", "pmid": pmid,
                "listed_by": "recovered_from_illumina_collection_and_scoped_worklist",
                "recovery_basis": "Illumina DNA Methods Collection MDA-family section; existing source worklist supplies identifiers; fresh PubMed metadata verifies publication identity"})
    if recovery_manifest:
        raw = recovery_manifest.read_bytes()
        manifest = json.loads(raw)
        leads.extend(manifest["pubmed_leads"])
        results["recovery_manifest"] = {"path": str(recovery_manifest.resolve()), "sha256": sha(raw)}
    if leads and network:
        recovered, associations = recover_pubmed_leads(leads, output, existing_html_receipts)
        # A recovered identity may augment an original non-PMID reference. The
        # original listed_reference remains attached and is never rewritten.
        for ident, recovered_entry in recovered.items():
            if ident in results["external_publications"]:
                recovered_entry["original_recovery_attempt"] = results["external_publications"][ident]
            results["external_publications"][ident] = recovered_entry
        results["recovered_card_references"] = associations
    results["statistics"] = {"external_reference_records": len(results["external_publications"]),
        "fulltexts": sum(v.get("content_level") == "fulltext" for v in results["external_publications"].values()),
        "abstracts": sum(v.get("content_level") == "abstract" for v in results["external_publications"].values()),
        "metadata_only": sum(v.get("content_level") == "metadata" for v in results["external_publications"].values())}
    write(output / "external_sources.json", results)
    write(output / "recovery_bundle.json", recovery_bundle(inventory, results, diagram_receipt))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--registration-root", type=Path, action="append", default=[])
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--include-mda", action="store_true")
    parser.add_argument("--pubmed-recovery-manifest", type=Path)
    parser.add_argument("--diagram-receipt", type=Path)
    parser.add_argument("--existing-pmc-html-receipt", type=Path, action="append", default=[])
    args = parser.parse_args()
    result = run(args.inventory, args.output, args.registration_root, network=args.network,
                 include_mda=args.include_mda, recovery_manifest=args.pubmed_recovery_manifest,
                 diagram_receipt=args.diagram_receipt, existing_html_receipts=args.existing_pmc_html_receipt)
    print(json.dumps(result["statistics"]))


if __name__ == "__main__":
    main()

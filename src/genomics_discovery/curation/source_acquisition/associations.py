"""Narrow host validation of public BioStudies PageTab attachment records.

Contract: https://www.ebi.ac.uk/biostudies/RH3R/help (API/download sections),
https://github.com/EBIBioStudies/page-tab-specification/blob/master/examples/AllInOneExample.md.
No external implementation is copied. Unknown record formats fail closed.
"""

import json
import hashlib
import re
from urllib.parse import quote, unquote, urlsplit


def _objects(value):
    if isinstance(value, dict):yield value
    elif isinstance(value, list):
        for item in value:yield from _objects(item)


def _sections(section):
    yield section
    for child in _objects(section.get("subsections", [])):yield from _sections(child)


def _doi(value):return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value.lower()).strip()


def biostudies_record(raw, record_url, file_path, primary_identity, primary_links, size):
    url = urlsplit(record_url)
    match = re.fullmatch(r"/biostudies/api/v1/studies/([A-Za-z0-9_.-]+)", url.path)
    if (url.scheme != "https" or url.netloc.lower() != "www.ebi.ac.uk" or not match
            or url.query or url.fragment):
        raise ValueError("Association record must be an anonymous official BioStudies study API URL")
    record = json.loads(raw)
    accession = match[1]
    if record.get("accno") != accession or record.get("type", "").lower() != "submission":
        raise ValueError("BioStudies accession/type does not match its API URL")
    section = record.get("section", {})
    if section.get("type") != "Study":raise ValueError("Unknown BioStudies study record shape")
    expected = {key:set(primary_identity.get("identifiers", {}).get(key, [])) for key in ("pmid","doi","pmcid")}
    expected["doi"] = {_doi(v) for v in expected["doi"]}
    publications = []
    for candidate in _sections(section):
        if candidate.get("type") != "Publication":continue
        ids = {"pmid":set(), "doi":set(), "pmcid":set()}
        for link in _objects(candidate.get("links", [])):
            kinds = {a.get("value", "").lower() for a in link.get("attributes", []) if a.get("name", "").lower() == "type"}
            for key, aliases in (("pmid",{"pmid"}),("doi",{"doi"}),("pmcid",{"pmc","pmcid"})):
                if kinds & aliases:
                    value = link.get("url", "")
                    ids[key].add(_doi(value) if key == "doi" else value)
        comparable = [key for key in expected if expected[key] and ids[key]]
        if comparable and all(ids[key] == expected[key] for key in comparable):publications.append(ids)
    if len(publications) != 1:
        raise ValueError("BioStudies record requires exactly one matching publication identity")
    if not file_path or file_path.startswith("/") or any(p in {"", ".", ".."} for p in file_path.split("/")):
        raise ValueError("BioStudies attachment path must be an exact safe relative file path")
    files = [file for part in _sections(section) for file in _objects(part.get("files", [])) if file.get("path") == file_path]
    if len(files) != 1 or files[0].get("type", "file") != "file":
        raise ValueError("BioStudies record lacks one unambiguous exact attachment path")
    reported_size = files[0].get("size")
    if reported_size is not None and (type(reported_size) is not int or reported_size != size):
        raise ValueError("BioStudies attachment size differs from retrieved original")
    name = file_path.rsplit("/", 1)[-1]
    cited = sorted(link for link in primary_links if unquote(urlsplit(link).path).rsplit("/",1)[-1] == name)
    if not cited:
        raise ValueError("Registered primary does not cite this BioStudies attachment name")
    canonical = f"https://www.ebi.ac.uk/biostudies/files/{accession}/{quote(file_path,safe='/')}"
    return canonical, {"adapter":"source_acquisition.biostudies/1", "accession":accession,
        "publication_identity":{k:sorted(v) for k,v in publications[0].items()},
        "file_path":file_path, "reported_size":reported_size, "primary_attachment_links":cited,
        "canonical_download_url":canonical,
        "basis":"Host-retrieved official publication-bound API record, exact attachment path and size, primary attachment citation and canonical file delivery; filename alone is insufficient"}


def citation_html_record(raw, page_url, primary_identity, attachment_urls, original,
                         original_sha256, *, identify, match, links):
    """Citation-matched landing-page attachment evidence, not publisher authority.

    The caller supplies an actual registered primary's host identity and retained
    supplement bytes. Parser callbacks reuse the acquisition identity/link rules
    without an associations/runtime import cycle. No scientific applicability or
    official-publisher status follows from citation metadata alone.
    """
    from .models import Identity

    ids = primary_identity.get("identifiers", {})
    dois = {_doi(value) for value in ids.get("doi", []) if value}
    title = primary_identity.get("title")
    if len(dois) != 1 or not isinstance(title, str) or not title.strip():
        raise ValueError("Landing association requires a registered primary's unique DOI and title")
    page_identity = identify(raw, "html")
    match(page_identity, Identity(doi=next(iter(dois)), title=title))
    page_pmids = set(page_identity.get("identifiers", {}).get("pmid", []))
    primary_pmids = set(ids.get("pmid", []))
    if page_pmids and primary_pmids and page_pmids != primary_pmids:
        raise ValueError("Landing citation PMID conflicts with registered primary")
    exact = sorted({url for url in attachment_urls if isinstance(url, str) and url in links(raw, page_url)})
    if not exact:
        raise ValueError("Citation-matched landing page lacks the exact retrieved attachment URL")
    if (not isinstance(original, bytes) or not original or not isinstance(original_sha256, str)
            or hashlib.sha256(original).hexdigest() != original_sha256):
        raise ValueError("Retrieved attachment bytes differ from their acquisition SHA256")
    return {"adapter":"source_acquisition.citation_html/1",
            "association_strength":"citation_matched_landing_page_exact_attachment",
            "expected_registered_primary_identity":primary_identity,
            "landing_identity":page_identity, "landing_final_url":page_url,
            "exact_attachment_urls":exact, "original_sha256":original_sha256,
            "original_bytes":len(original),
            "basis":"Host-retrieved landing citation DOI/title match the registered primary; exact linked URL matches the retrieved attachment and its retained byte hash",
            "limits":"Citation metadata does not prove official publisher authority, scientific applicability, completeness, or equivalence to another protocol/version; fresh source-first review is required"}

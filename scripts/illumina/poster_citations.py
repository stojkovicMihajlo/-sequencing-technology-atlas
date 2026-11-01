"""Resolve poster citation candidates with auditable bibliographic comparisons.

Uses NCBI's official batch citation matcher, then PubMed/Crossref metadata for
unresolved fragments. A returned identifier is a candidate, never an automatic
paper-to-method or evolutionary assertion. Bulk --network runs belong in Slurm.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import urlencode, quote
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from genomics_discovery.illumina.sources import acquire_pmc_fulltext, fetch, parse_pubmed, sha, write


def normalized(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"[^a-z0-9]+", "", value.encode("ascii", "ignore").decode().lower())


def doi_key(value):
    # DOI punctuation is significant. Bibliographic display normalization must
    # not collapse different identifiers such as10.1234/a-b and10.1234/ab.
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", str(value or "").strip().lower())


def pages(value):
    """Expand abbreviated numeric end pages, preserving genuine discrepancies."""
    value = str(value or "").strip().replace("–", "-").replace("—", "-")
    match = re.match(r"([A-Za-z]*)(\d+)(?:-([A-Za-z]*)(\d+))?", value)
    if not match:
        return {"first": None, "last": None, "raw": value, "unparsed_suffix": value}
    prefix, first, end_prefix, last = match.groups()
    if last and len(last) < len(first):
        last = first[:len(first) - len(last)] + last
    return {"first": (prefix + first).lower(),
            "last": ((end_prefix or prefix) + last).lower() if last else None,
            "raw": value, "unparsed_suffix": value[match.end():].strip()}


def parse_citation(fragment):
    text = " ".join(unicodedata.normalize("NFKC", fragment).split())
    year_match = re.search(r"\(((?:19|20)\d{2})\)", text) or re.search(r"\b((?:19|20)\d{2})\b", text)
    year = int(year_match[1]) if year_match else None
    author_split = re.split(r"\bet\s+al\.?", text, maxsplit=1, flags=re.I)
    if len(author_split) == 2:
        author, remainder = author_split
    elif year_match:
        author, remainder = text[:year_match.start()], text[year_match.end():]
    else:
        author, remainder = "", text
    tokens = author.strip(" ,.").split()
    initials = []
    while tokens and re.fullmatch(r"[A-Z]{1,4}\.?", tokens[-1]):
        initials.insert(0, tokens.pop().strip("."))
    surname = " ".join(tokens).strip(" ,.") or None
    # Remove the actual parenthesized publication year once. Globally deleting
    # four-digit numbers would corrupt perfectly valid pages such as2078-2090.
    if year is not None:
        remainder = re.sub(r"\(" + str(year) + r"\)", "", remainder, count=1)
        remainder = re.sub(r"^\s*" + str(year) + r"\b", "", remainder, count=1)
    remainder = remainder.strip(" ,.;")
    volume_match = re.search(r"\b(\d+)(?:\([^)]*\))?\s*[:,]\s*(.*)", remainder)
    volume, page_text = (volume_match[1], volume_match[2]) if volume_match else (None, None)
    journal = remainder[:volume_match.start()] if volume_match else remainder
    journal = re.split(r"\b(?:advance|ahead|see also)\b", journal, flags=re.I)[0].strip(" ,.;")
    # A chapter/unit citation is retained as partial rather than pretending its
    # chapter number is the journal's volume or its unit is a page range.
    chapter = bool(re.search(r"\b(?:Chapter|Unit)\b", remainder, re.I))
    if chapter:
        journal = re.split(r"\bChapter\b", journal, flags=re.I)[0].strip()
        volume, page_text = None, None
    page = pages(page_text) if page_text else pages("")
    preprint = re.search(r"bioRxiv\s*:\s*(\d{6})", remainder, re.I)
    if preprint:
        journal = "bioRxiv"
    doi_match = re.search(r"10\.\d{4,9}/[^\s<>]+", text)
    doi = doi_match[0].rstrip(".,;").lower() if doi_match else ("10.1101/" + preprint[1] if preprint else None)
    return {"fragment": fragment, "first_author_surname": surname,
            "first_author_initials": "".join(initials), "year": year, "journal": journal or None,
            "volume": volume, "pages": page, "doi_as_printed_or_biorxiv_id": doi,
            "partial_citation": chapter or not all((surname, year, journal, volume, page["first"])),
            "journal_fragment_truncated": bool(re.search(r"\b(?:and|of|in)$", journal, re.I)),
            "chapter_or_unit_citation": chapter}


def structured_pubmed(raw):
    """Own bibliographic fields, without relying on concatenated display text."""
    base = parse_pubmed(raw)
    root = ET.fromstring(raw)
    text = lambda element: " ".join("".join(element.itertext()).split()) if element is not None else None
    for node in root.findall("PubmedArticle"):
        citation = node.find("MedlineCitation")
        pmid = text(citation.find("PMID"))
        article = citation.find("Article")
        if article is None or pmid not in base:
            continue
        first = article.find("AuthorList/Author")
        journal = article.find("Journal")
        years = set()
        dates = article.findall("Journal/JournalIssue/PubDate") + article.findall("ArticleDate")
        for date in dates:
            # Joining Year/Month/Day itertext would produce e.g2015Jun10,
            # obscuring the year boundary. Read explicit Year/MedlineDate.
            for field in date.findall("Year") + date.findall("MedlineDate"):
                years.update(int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", text(field) or ""))
        author = {"surname": text(first.find("LastName")), "initials": text(first.find("Initials")),
                  "collective_name": text(first.find("CollectiveName"))} if first is not None else {}
        page_text = text(article.find("Pagination/MedlinePgn"))
        if not page_text:
            page_text = next((text(e) for e in article.findall("ELocationID") if e.get("EIdType") == "pii"), None)
        base[pmid]["bibliography"] = {"first_author": author, "publication_years": sorted(years),
            "journal_titles": list(dict.fromkeys(filter(None, [text(journal.find("Title")) if journal is not None else None,
                text(journal.find("ISOAbbreviation")) if journal is not None else None,
                text(citation.find("MedlineJournalInfo/MedlineTA"))]))),
            "volume": text(article.find("Journal/JournalIssue/Volume")),
            "issue": text(article.find("Journal/JournalIssue/Issue")), "pages": pages(page_text)}
    return base


def journal_agreement(left, right):
    if normalized(left) == normalized(right):
        return True
    words = lambda text: [normalized(word) for word in re.findall(r"[\w-]+", text or "")
                          if word.lower() not in {"of", "in", "and", "the"}]
    a, b = words(left), words(right)
    # Abbreviated/full journal words can be corresponding prefixes. The word
    # count must agree; truncated journal names stay unverified.
    return bool(a and b and len(a) == len(b) and all(x.startswith(y) or y.startswith(x) for x, y in zip(a, b)))


def compare(citation, publication):
    bib = publication.get("bibliography", {})
    check = lambda a, b, predicate=lambda x, y: x == y: None if not a or not b else predicate(a, b)
    checks = {
        "first_author": check(citation["first_author_surname"], bib.get("first_author", {}).get("surname"),
                              lambda a, b: normalized(a) == normalized(b)),
        "first_author_initials": check(citation.get("first_author_initials"), bib.get("first_author", {}).get("initials"),
                                       lambda a, b: normalized(b).startswith(normalized(a))),
        "year": check(citation["year"], bib.get("publication_years"), lambda year, years: year in years),
        "journal": check(citation["journal"], bib.get("journal_titles"),
                         lambda name, names: any(journal_agreement(name, candidate) for candidate in names)),
        "volume": check(citation["volume"], bib.get("volume"), lambda a, b: normalized(a) == normalized(b)),
        "first_page": check(citation["pages"]["first"], bib.get("pages", {}).get("first")),
        "last_page": check(citation["pages"]["last"], bib.get("pages", {}).get("last")),
        "printed_doi": check(citation.get("doi_as_printed_or_biorxiv_id"), publication.get("doi") or
                             publication.get("identifiers", {}).get("doi"), lambda doi, candidates:
                             doi_key(doi) in {doi_key(item) for item in (candidates if isinstance(candidates, list) else [candidates])}),
    }
    if citation.get("journal_fragment_truncated") and checks["journal"] is False:
        checks["journal"] = None
    core = (checks["first_author"] is True and checks["year"] is True and
            (checks["journal"] is True or checks["printed_doi"] is True))
    conflicts = [field for field, value in checks.items() if value is False]
    unknowns = [field for field, value in checks.items() if value is None]
    return {"field_checks": checks, "conflicting_fields": conflicts, "unknown_fields": unknowns,
            "all_observed_fields_agree": core and not conflicts,
            "comparison_status": ("bibliographic_candidate_requires_review" if core and not conflicts else
                "bibliographic_conflicts_require_review" if core else "weak_or_conflicting_candidate"),
            "paper_identity_verified": False,
            "unparsed_poster_pagination": citation["pages"]["unparsed_suffix"]}


def ecit_line(citation, key):
    values = [citation.get("journal"), citation.get("year"), citation.get("volume"),
              citation.get("pages", {}).get("first"),
              " ".join(filter(None, [citation.get("first_author_surname"), citation.get("first_author_initials")])), key]
    return "|".join(str(value or "").replace("|", " ") for value in values) + "|"


def parse_ecit(raw, requested_keys):
    results = {key: {"candidate_pmids": [], "responses": []} for key in requested_keys}
    for line in raw.decode("utf-8").splitlines():
        columns = line.strip().split("|")
        if len(columns) < 7 or columns[5] not in results:
            if line.strip():
                raise ValueError("Citation matcher response does not preserve the requested keys")
            continue
        key, answer = columns[5], columns[6].strip()
        results[key]["responses"].append(line)
        if re.fullmatch(r"\d+", answer):
            results[key]["candidate_pmids"].append(answer)
        # NOT_FOUND/AMBIGUOUS/error text is retained, never parsed for numbers.
    return results


def citation_inventory(proposals):
    citations, rows = {}, []
    for row in proposals["rows"]:
        record = {"row_id": row["row_id"], "source_row_caption": row["source_row_caption"], "members": []}
        for member in row["members"]:
            refs = []
            for listed in member.get("source_listed_citations", []):
                fragment = listed["citation_fragment_as_listed"]
                key = "citation_" + sha(fragment.encode())[:24]
                citations.setdefault(key, {"citation_id": key, "parsed": parse_citation(fragment), "occurrences": [],
                                           "catalogue_reference_candidate_pmids": []})
                citation = citations[key]
                citation["occurrences"].append({"row_id": row["row_id"], "source_method_label": member["source_method_label"],
                    "source_pdf_sha256": row["source_pdf_sha256"], "listed_citation": listed})
                for pmid in member.get("catalogue_reference_candidate_pmids", []):
                    if pmid not in citation["catalogue_reference_candidate_pmids"]:
                        citation["catalogue_reference_candidate_pmids"].append(pmid)
                refs.append(key)
            record["members"].append({"source_method_label": member["source_method_label"], "citation_ids": refs,
                "identity_status": "source_citation_candidates_only" if refs else "no_source_listed_citation_recovered",
                "paper_identity_verified": False})
        rows.append(record)
    return citations, rows


def build_candidates(citations, publications, origin_pmids, remote_ids):
    for key, item in citations.items():
        candidates = {}
        for pmid, pub in publications.items():
            comparison = compare(item["parsed"], pub)
            is_hint = pmid in item["catalogue_reference_candidate_pmids"]
            is_remote = pmid in remote_ids.get(key, [])
            author_year = comparison["field_checks"]["first_author"] is True and comparison["field_checks"]["year"] is True
            if not (author_year or is_hint or is_remote):
                continue
            origins = []
            if author_year:
                origins.append("structured_author_year_comparison_of_acquired_pubmed_metadata")
            if is_hint:
                origins.append("original_catalogue_reference_hint_from_label_candidate; not_identity_evidence")
            if is_remote:
                origins.append("public_citation_lookup_response_candidate")
            candidates[pmid] = {"pmid": pmid, "title": pub["title"], "bibliography": pub.get("bibliography"),
                "identifiers": pub.get("identifiers"), "candidate_origins": origins, "comparison": comparison,
                "metadata_origin": "original_source_acquisition" if pmid in origin_pmids else "new_pubmed_metadata_lookup"}
        item["pubmed_candidates"] = sorted(candidates.values(), key=lambda c: (
            -sum(value is True for value in c["comparison"]["field_checks"].values()),
            len(c["comparison"]["conflicting_fields"]), int(c["pmid"])))
        item["has_compatible_pubmed_candidate"] = any(
            c["comparison"]["all_observed_fields_agree"] for c in candidates.values())
        item["paper_identity_verified"] = False


def read_initial_publications(source_snapshot, sources, output):
    publications, receipts = {}, []
    for batch in sources["publication_batches"]:
        if batch.get("status") != "downloaded":
            continue
        path = Path(batch["path"])
        if not path.is_absolute():
            path = source_snapshot.parent / path
        raw = path.read_bytes()
        if sha(raw) != batch["sha256"]:
            raise ValueError("Original PubMed metadata batch custody mismatch")
        found = structured_pubmed(raw)
        if set(found) - set(batch["requested_pmids"]):
            raise ValueError("Original PubMed metadata contains an unrequested record")
        for record in found.values():
            record["metadata_source"] = str(path.resolve())
            record["metadata_source_sha256"] = sha(raw)
        publications.update(found)
        receipts.append({"path": str(path.resolve()), "sha256": sha(raw), "records": len(found)})
    write(output / "original_metadata_receipts.json", receipts)
    return publications


def acquire_metadata(pmids, publications, output, label):
    new = sorted(set(pmids) - set(publications), key=int)
    failures = []
    for offset in range(0, len(new), 100):
        ids = new[offset:offset + 100]
        path = output / "pubmed" / f"{label}_{offset//100+1:04d}.xml"
        try:
            receipt = fetch("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urlencode({
                "db": "pubmed", "id": ",".join(ids), "retmode": "xml"}), path)
            found = structured_pubmed(path.read_bytes())
            if set(found) - set(ids):
                raise ValueError("New PubMed batch contains an unrequested record")
            for record in found.values():
                record["metadata_source"], record["metadata_source_sha256"] = str(path.resolve()), receipt["sha256"]
            publications.update(found)
            write(path.with_suffix(".receipt.json"), {**receipt, "requested_pmids": ids, "returned_pmids": list(found)})
        except Exception as exc:
            failed = {"stage": "pubmed_efetch", "requested_pmids": ids, "error": f"{type(exc).__name__}: {exc}"}
            failures.append(failed)
            write(path.with_suffix(".receipt.json"), failed)
    return failures


def crossref_publication(message):
    author = (message.get("author") or [{}])[0]
    years = set()
    for kind in ("published", "published-print", "published-online", "issued"):
        for parts in message.get(kind, {}).get("date-parts", []):
            if parts:
                years.add(parts[0])
    given = author.get("given") or ""
    return {"title": " / ".join(message.get("title", [])), "doi": message.get("DOI"),
            "bibliography": {"first_author": {"surname": author.get("family"), "given": given,
                "initials": "".join(part[0] for part in re.findall(r"\w+", given))},
                "publication_years": sorted(years), "journal_titles": message.get("container-title", []) +
                message.get("short-container-title", []), "volume": message.get("volume"), "pages": pages(message.get("page"))},
            "abstract": message.get("abstract"), "type": message.get("type")}


def bind_receipt(value, root):
    """Make candidate source paths explicit and verify any advertised byte pins."""
    if isinstance(value, list):
        return [bind_receipt(item, root) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: copy.deepcopy(item) if key == "cache_provenance" else bind_receipt(item, root)
              for key, item in value.items()}
    for key, hash_key in (("path", "sha256"), ("text_path", "text_sha256"), ("blocks_path", "blocks_sha256")):
        if isinstance(result.get(key), str):
            path = Path(result[key])
            path = path if path.is_absolute() else root / path
            observed = sha(path.read_bytes())
            if result.get(hash_key) and result[hash_key] != observed:
                raise ValueError("Candidate fulltext custody mismatch: " + str(path))
            result[key], result[hash_key] = str(path.resolve()), observed
    return result


def run(proposals_path, source_snapshot, output, *, network=False, fulltext=False):
    proposal_raw, source_raw = proposals_path.read_bytes(), source_snapshot.read_bytes()
    proposals, source = json.loads(proposal_raw), json.loads(source_raw)
    output.mkdir(parents=True, exist_ok=False)
    citations, rows = citation_inventory(proposals)
    publications = read_initial_publications(source_snapshot, source, output)
    original_pmids, remote_ids, failures = set(publications), {}, []
    build_candidates(citations, publications, original_pmids, remote_ids)
    write(output / "citation_candidates.json", citations)
    pending = [key for key, value in citations.items() if not value["has_compatible_pubmed_candidate"]]
    if network:
        for offset in range(0, len(pending), 20):
            keys = pending[offset:offset+20]
            bdata = "\r".join(ecit_line(citations[key]["parsed"], key) for key in keys)
            path = output / "ecitmatch" / f"batch_{offset//20+1:04d}.txt"
            try:
                receipt = fetch("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/ecitmatch.cgi?" + urlencode({
                    "db": "pubmed", "retmode": "xml", "bdata": bdata}), path)
                found = parse_ecit(path.read_bytes(), keys)
                write(path.with_suffix(".receipt.json"), receipt)
                for key, response in found.items():
                    citations[key]["ecitmatch_response"] = response
                    remote_ids.setdefault(key, []).extend(response["candidate_pmids"])
            except Exception as exc:
                failures.append({"stage": "ecitmatch", "citation_ids": keys, "error": f"{type(exc).__name__}: {exc}"})
        failures.extend(acquire_metadata([pmid for values in remote_ids.values() for pmid in values], publications, output, "ecitmatch"))
        build_candidates(citations, publications, original_pmids, remote_ids)
        # The official matcher can miss incomplete/typoed journal citations.
        # Search a stated first author/year plus available bibliographic fields;
        # returned IDs still undergo the same field-by-field verification.
        for key, value in citations.items():
            if value["has_compatible_pubmed_candidate"]:
                continue
            parsed = value["parsed"]
            if not parsed["first_author_surname"] or not parsed["year"]:
                continue
            clauses = [parsed["first_author_surname"] + "[1au]", str(parsed["year"]) + "[dp]"]
            if parsed["journal"] and "biorxiv" not in normalized(parsed["journal"]):
                clauses.append('"' + parsed["journal"].replace('"', '') + '"[ta]')
            if parsed["volume"]:
                clauses.append(parsed["volume"] + "[vi]")
            if parsed["pages"]["first"]:
                clauses.append(parsed["pages"]["first"] + "[pg]")
            query = " AND ".join(clauses)
            path = output / "esearch" / (key + ".json")
            try:
                receipt = fetch("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urlencode({
                    "db": "pubmed", "term": query, "retmode": "json", "retmax": 100}), path)
                found = json.loads(path.read_text())["esearchresult"]
                ids = found.get("idlist", [])
                if not all(re.fullmatch(r"\d+", str(pmid)) for pmid in ids):
                    raise ValueError("PubMed search returned a nonnumeric identifier")
                value["pubmed_search"] = {"query": query, "receipt": receipt, "candidate_pmids": ids,
                    "reported_count": int(found["count"]), "truncated": int(found["count"]) > len(ids)}
                remote_ids.setdefault(key, []).extend(ids)
            except Exception as exc:
                failures.append({"stage": "esearch", "citation_id": key, "error": f"{type(exc).__name__}: {exc}"})
        failures.extend(acquire_metadata([pmid for values in remote_ids.values() for pmid in values], publications, output, "esearch"))
        build_candidates(citations, publications, original_pmids, remote_ids)
        for key, value in citations.items():
            if value["has_compatible_pubmed_candidate"]:
                continue
            parsed = value["parsed"]
            doi = parsed["doi_as_printed_or_biorxiv_id"]
            url = ("https://api.crossref.org/works/" + quote(doi, safe="") if doi else
                   "https://api.crossref.org/works?" + urlencode({"query.bibliographic": parsed["fragment"], "rows": 5}))
            path = output / "crossref" / (key + ".json")
            try:
                receipt = fetch(url, path)
                message = json.loads(path.read_text())["message"]
                if doi and doi_key(message.get("DOI")) != doi_key(doi):
                    raise ValueError("Crossref returned a different DOI for the printed identifier")
                items = [message] if doi else message["items"]
                value["crossref_candidates"] = [{"publication": crossref_publication(item),
                    "comparison": compare(parsed, crossref_publication(item)), "metadata_receipt": receipt,
                    "candidate_origin": "crossref_exact_printed_doi" if doi else "crossref_bibliographic_query",
                    "paper_identity_verified": False} for item in items]
            except Exception as exc:
                failures.append({"stage": "crossref", "citation_id": key, "error": f"{type(exc).__name__}: {exc}"})
    evidence = {}
    for key, value in citations.items():
        for candidate in value["pubmed_candidates"]:
            if not candidate["comparison"]["all_observed_fields_agree"]:
                continue
            pmid = candidate["pmid"]
            if pmid in evidence:
                continue
            existing = source.get("fulltexts", {}).get(pmid)
            if existing and existing.get("status") == "fulltext_acquired":
                evidence[pmid] = bind_receipt(existing, source_snapshot.parent)
                evidence[pmid].update(parent_source_snapshot_sha256=sha(source_raw),
                    candidate_reference_status="bibliographic_candidate_not_method_applicability_reviewed", scientific_review=False)
            elif network and fulltext:
                evidence[pmid] = bind_receipt(acquire_pmc_fulltext(publications[pmid], output), output)
    for row in rows:
        for member in row["members"]:
            member["candidate_summaries"] = [{"citation_id": key,
                "compatible_pubmed_candidate_pmids": [candidate["pmid"] for candidate in citations[key]["pubmed_candidates"]
                    if candidate["comparison"]["all_observed_fields_agree"]],
                "compatible_crossref_candidate_dois": [candidate["publication"]["doi"] for candidate in
                    citations[key].get("crossref_candidates", []) if candidate["comparison"]["all_observed_fields_agree"]],
                "bibliographic_conflict_candidates": [candidate["pmid"] for candidate in citations[key]["pubmed_candidates"]
                    if candidate["comparison"]["comparison_status"] == "bibliographic_conflicts_require_review"],
                "paper_identity_verified": False} for key in member["citation_ids"]]
    resolutions = []
    for row in rows:
        for member in row["members"]:
            references, pmids = [], []
            for key in member["citation_ids"]:
                item = citations[key]
                for candidate in item["pubmed_candidates"]:
                    if candidate["comparison"]["all_observed_fields_agree"] and candidate["pmid"] not in pmids:
                        pmids.append(candidate["pmid"])
                references.append({"citation_id": key, "original_citation_fragment": item["parsed"]["fragment"],
                    "candidate_publications": item["pubmed_candidates"],
                    "crossref_candidates": item.get("crossref_candidates", []), "source_occurrences": item["occurrences"]})
            resolutions.append({"row_id": row["row_id"], "source_method_label": member["source_method_label"],
                "candidate_pmids": pmids, "references": references, "paper_identity_verified": False})
    statistics = {"poster_rows": len(rows), "unique_citation_fragments": len(citations),
        "citation_occurrences": sum(len(value["occurrences"]) for value in citations.values()),
        "original_pubmed_records": len(original_pmids), "new_pubmed_records": len(publications) - len(original_pmids),
        "citations_with_compatible_pubmed_candidate": sum(value["has_compatible_pubmed_candidate"] for value in citations.values()),
        "citations_with_compatible_crossref_candidate": sum(any(c["comparison"]["all_observed_fields_agree"]
            for c in value.get("crossref_candidates", [])) for value in citations.values()), "failures": len(failures)}
    result = {"format_version": "illumina_poster_paper_candidates/1.0", "scientific_review": False,
        "paper_identity_verified": False, "proposals": {"path": str(proposals_path.resolve()), "sha256": sha(proposal_raw)},
        "source_snapshot": {"path": str(source_snapshot.resolve()), "sha256": sha(source_raw)},
        "statistics": statistics, "rows": rows, "citations": citations, "resolutions": resolutions,
        "publications": publications, "fulltexts": evidence, "candidate_fulltext_sources": evidence,
        "failures": failures, "limitations": ["Bibliographic matches do not establish paper-to-method applicability or historical priority.",
            "Truncated/ambiguous citations remain candidates; conflicting pagination is retained.",
            "Search results and catalogue label hints never authorize automatic graph admission."],
        "method_sources": ["https://www.ncbi.nlm.nih.gov/books/NBK25500/", "https://pubmed.ncbi.nlm.nih.gov/help/",
                           "https://pmc.ncbi.nlm.nih.gov/tools/oai/"]}
    write(output / "bibliographic_publications.json", publications)
    write(output / "citation_candidates.json", citations)
    write(output / "poster_paper_candidates.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--fulltext", action="store_true")
    args = parser.parse_args()
    result = run(args.proposals, args.sources, args.output, network=args.network, fulltext=args.fulltext)
    print(json.dumps(result["statistics"]))


if __name__ == "__main__":
    main()

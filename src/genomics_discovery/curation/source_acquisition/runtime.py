"""Execute a source worklist through existing native, rendition and registration tools.

The native finder supplies untrusted locations. Only host-retrieved originals
can become packet sources. Unsupported formats and failures remain auditable
retrievals; neither registration nor a successful download resolves a source gap.
"""

import asyncio
from collections import Counter
import copy
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import urljoin
from xml.etree.ElementTree import ParseError

from ..cached_documents import ArticleHTML, _publish_rendition, primary_html, supplements
from ..codex_worker import CodexWorker, assess_archived_attempt, DEFAULT_MODEL, DEFAULT_EFFORT
from ..document_manifest import MAX_BYTES, inspect_jats
from ..extraction_batch.storage import atomic_bytes, check_pins, sha, write
from ..source_worklist import resolve
from ..workflow_extraction.models import digest
from ..workflow_extraction.sources import build_packet, load_packet
from .downloads import Download, download, public_url
from .formats import jats_rendition
from .legacy_word import word_rendition
from .associations import biostudies_record, citation_html_record
from .models import Findings, Identity, Scope
from .html_identity import assess_primary_html
from .failures import SourceFinderFailure, host_storage_failure, native_failure, output_failure

ACTIONABLE = {"acquire_document", "inspect_original_or_rendition", "decide_variant_scope"}
BOUNDARY = ("Public source finding and host byte retrieval only. Registered inputs require fresh "
            "source-first inspection. No scientific gap is certified resolved. PDF-only primaries, "
            "unverified supplement associations, cited/manufacturer/other documents and unsupported "
            "formats remain explicit retrieved originals for manual scoped registration/inspection.")


def _normal(value):
    return " ".join(re.sub(r"[^\w]+", " ", unescape(value).casefold()).split())


def _doi(value):
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value.lower()).strip()


def _identity(raw, kind):
    if kind == "jats":
        return inspect_jats(raw)["identity"]
    if kind != "html":
        raise ValueError("Primary identity is supported for JATS and citation HTML only")
    parser = ArticleHTML(); parser.feed(raw.decode("utf-8")); parser.close()
    metadata = parser.metadata
    titles = {v.strip() for v in metadata.get("citation_title", []) if v.strip()}
    if len(titles) != 1:
        raise ValueError("Primary HTML needs a unique citation_title")
    return {"title": next(iter(titles)), "identifiers": {
        key: sorted({v.strip() for v in metadata.get(field, []) if v.strip()})
        for key, field in (("pmid", "citation_pmid"), ("doi", "citation_doi"))}}


def _match(actual, expected):
    for key in ("pmid", "doi"):
        value = getattr(expected, key)
        if value is not None:
            normalize = _doi if key == "doi" else str
            if {normalize(v) for v in actual["identifiers"].get(key, [])} != {normalize(value)}:
                raise ValueError(f"Primary publication {key} mismatch")
    if expected.title and _normal(actual["title"]) != _normal(expected.title):
        raise ValueError("Primary publication title mismatch")
    if not actual.get("title"):
        raise ValueError("Primary publication title is missing")


def _links(raw, base):
    text = raw.decode("utf-8", errors="replace")
    values = re.findall(r'''(?:href\s*=\s*["']([^"']+)["']|\[link: ([^\]]+)\])''', text, re.I)
    return {urljoin(base, unescape(a or b)) for a, b in values}


async def _landing_association(document, record, identities, cache, directory, local_pins,
                               downloader, check):
    """Fetch an unregistered link page once per method acquisition; never register it."""
    page_url = public_url(document.linked_from)
    if not identities:
        raise ValueError("Landing association requires an already registered primary")
    check()
    if page_url not in cache:
        index = len(cache) + 1
        receipt_path = directory / "associations" / f"landing_{index:03d}_retrieval.json"
        try:
            fetched = await asyncio.to_thread(downloader, page_url)
            if not isinstance(fetched, Download) or not isinstance(fetched.content, bytes):
                raise ValueError("Landing downloader must return host bytes and response metadata")
            public_url(fetched.final_url)
            if not fetched.content or len(fetched.content) > MAX_BYTES:
                raise ValueError("Empty or oversized landing-page original")
            raw_path = directory / "associations" / f"landing_{index:03d}.html"
            atomic_bytes(raw_path, fetched.content)
            local_pins[str(raw_path)] = sha(raw_path)
            retrieval = {"status":"downloaded", "requested_url":page_url, "final_url":fetched.final_url,
                         "content_type":fetched.content_type, "path":str(raw_path), "sha256":sha(raw_path),
                         "retriever":"host downloader; anonymous public HTTP(S), unchanged TLS checks",
                         "role":"association evidence only; not registered as primary full text"}
            write(receipt_path, retrieval)
            cache[page_url] = {"fetched":fetched, "raw_path":raw_path, "receipt_path":receipt_path}
        except (OSError, ValueError) as exc:
            if host_storage_failure(exc):
                raise
            write(receipt_path, {"status":"retrieval_failed", "requested_url":page_url,
                                 "error":type(exc).__name__ + ": " + str(exc)})
            cache[page_url] = {"error":str(exc), "receipt_path":receipt_path}
        local_pins[str(receipt_path)] = sha(receipt_path)
    cached = cache[page_url]
    if "error" in cached:
        raise ValueError("Landing-page retrieval unavailable: " + cached["error"])
    fetched = cached["fetched"]
    matches, errors = {}, []
    original = Path(record["path"]).read_bytes()
    for primary_url, identity in identities.items():
        try:
            association = citation_html_record(fetched.content, fetched.final_url, identity,
                (document.url, record["final_url"]), original, record["original_sha256"],
                identify=_identity, match=_match, links=_links)
            key = (_doi(association["landing_identity"]["identifiers"]["doi"][0]),
                   _normal(association["landing_identity"]["title"]))
            if key not in matches:
                matches[key] = {**association, "registered_primary_urls":[]}
            matches[key]["registered_primary_urls"].append(primary_url)
        except (ValueError, KeyError) as exc:
            errors.append(str(exc))
    if len(matches) != 1:
        raise ValueError("Landing association needs one matching registered publication: " + "; ".join(sorted(set(errors))))
    association = next(iter(matches.values()))
    association.update(landing_requested_url=page_url, landing_original=str(cached["raw_path"]),
                       landing_original_sha256=sha(cached["raw_path"]),
                       landing_retrieval_receipt=str(cached["receipt_path"]),
                       landing_retrieval_receipt_sha256=sha(cached["receipt_path"]))
    association_path = directory / "associations" / (document.id + "_association.json")
    write(association_path, association)
    local_pins[str(association_path)] = sha(association_path)
    association.update(record=str(association_path), record_sha256=sha(association_path))
    check()
    return association


def _single_origin(method):
    origins = method.get("origin_paper_ids", [])
    if len(origins) == 1 and re.fullmatch(r"pmid:\d+", origins[0]):
        return origins[0].split(":", 1)[1]
    packet = method.get("source_packet")
    if packet:
        values = set()
        for source in load_packet(Path(packet["path"]))["sources"]:
            if source["role"] == "primary_paper":
                values.update(source["document_identity"].get("identifiers", {}).get("pmid", []))
                match = re.fullmatch(r"https?://pubmed\.ncbi\.nlm\.nih\.gov/(\d+)/?", source["url"])
                if match:
                    values.add(match[1])
        if len(values) == 1:
            return next(iter(values))
    return None


def _scope(method, override):
    previous = method.get("source_packet")
    pmid = _single_origin(method)
    if previous:
        packet = load_packet(Path(previous["path"]))
        if sha(Path(previous["path"])) != previous["sha256"] or digest(packet) != previous["document_sha256"]:
            raise ValueError("Prior packet binding changed")
        if any(packet[k] != previous[k] for k in ("method_id", "title", "scope", "variant")):
            raise ValueError("Prior packet metadata differs from bound bytes")
        options = {k:packet[k] for k in ("method_id", "title", "scope", "variant")}
        if override is not None:
            explicit = Scope.model_validate(override)
            if any(getattr(explicit, k) != v for k,v in options.items()):
                raise ValueError("Acquisition cannot change an existing packet's scope or identity")
            identity = explicit.primary_identity
        else:
            identity = Identity(pmid=pmid)
        return options, identity, packet
    if override is not None:
        explicit = Scope.model_validate(override)
        if explicit.method_id != method["id"]:
            raise ValueError("New source scope must preserve the worklist method ID")
        if pmid and explicit.primary_identity.pmid != pmid:
            raise ValueError("Explicit scope differs from the worklist's single origin PMID")
        return {k:getattr(explicit,k) for k in ("method_id", "title", "scope", "variant")}, explicit.primary_identity, None
    if pmid and not any(t["kind"] == "decide_variant_scope" for t in method["tasks"]):
        title = method["title"]
        return {"method_id": method["id"], "title": title,
                "scope": (f"Describe the general {title} assay implementation in PMID {pmid}, "
                          "from its stated starting material to its intended molecular/data readout. "
                          "Preserve source-defined alternatives separately and distinguish optional "
                          "evaluation experiments. Check applicable supplements and cited procedural "
                          "dependencies; a missing required source is a source acquisition task. "
                          "Do not reconstruct every historical experiment or assume omitted chemistry."),
                "variant": f"Implementation attributed by atlas to PMID {pmid}; verify scope against source"}, Identity(pmid=pmid), None
    raise ValueError("Explicit source scope and expected PMID/DOI are required for an absent or ambiguous origin")


def _primary(document, record, path, expected, output):
    if not (expected.pmid or expected.doi):
        raise ValueError("No independently bound expected primary PMID/DOI; retain original for explicit registration")
    if document.format == "jats":
        def matches(actual):
            _match(actual, expected); _match(actual, document.identity)
        selected, actual, receipt = jats_rendition(path, output.with_suffix(".xml"), matches)
        source = {"path": str(selected), "title": actual["title"]}
    else:
        actual = _identity(path.read_bytes(), document.format)
        record["host_identity"] = actual
        if document.format == "html":
            record["identity_assessment"] = assess_primary_html(path.read_bytes(), actual, expected,
                document.identity, record["final_url"], normal=_normal, doi=_doi)
        else:
            _match(actual, expected); _match(actual, document.identity)
        pmids = actual["identifiers"].get("pmid", [])
        if len(pmids) == 1:
            source, receipt = primary_html(path.parent, pmids[0], output)
        else:
            parser = ArticleHTML(); parser.feed(path.read_text()); parser.close()
            limits = "Linear citation HTML rendition; inspect originals for images, layout and unrendered mathematics."
            _publish_rendition(output, parser.text, path, sha(path), "cached_documents.ArticleHTML/2", limits)
            source = {"path": str(output), "title": actual["title"]}
            receipt = {"original": str(path), "original_sha256": sha(path),
                       "rendition": str(output), "rendition_sha256": sha(output),
                       "converter": "cached_documents.ArticleHTML/2", "limits": limits}
    record["host_identity"] = actual
    source.update(source_id=f"acquired_{document.id}_{sha(path)[:8]}", url=record["final_url"], role=document.role)
    receipt["source_id"] = source["source_id"]
    if "identity_assessment" in record:
        receipt["identity_assessment"] = record["identity_assessment"]
    return source, receipt


async def _method(method, directory, options, expected, previous, worker, downloader, check):
    directory.mkdir(parents=True, exist_ok=False)
    attempt = directory / "finder"; attempt.mkdir()
    request = {"method": method, "scope": options, "expected_primary_identity": expected.model_dump(),
               "worklist_binding_policy": "Frozen inputs; locations are unaccepted source leads."}
    write(attempt / "request.json", request)
    write(attempt / "schema.json", Findings.model_json_schema())
    prompt = (f"ROLE: public_source_finder\nRead {attempt / 'request.json'} and {attempt / 'schema.json'}.\n"
              f"Save exactly one schema-conforming JSON object to {attempt / 'artifact.json'}.\n"
              "Find PUBLIC original full text and applicable supplemental/procedural documents for these source tasks. "
              "Return candidate direct HTTP(S) document URLs, roles, formats, publication PMID/DOI/title, "
              "and the exact primary document URL linking each supplement. Include Word supplements as format docx or doc (legacy binary); host byte sniff verifies the container. "
              "For a BioStudies mirror, also supply association_record {format:biostudies,url:official study API URL,file_path:exact attachment path}; "
              "prefer the canonical /biostudies/files/ACCESSION/FILE_PATH download URL. Host verification requires the record's publication IDs, "
              "attachment path/size, primary attachment citation and actual canonical file delivery; a filename alone is insufficient. "
              "Record searched queries and unavailable/access-limited sources. "
              "Use public publisher, PubMed/PMC and official repository pages; do not use account credentials, private data, "
              "signed/authenticated URLs or bypass subscription/captcha/access controls. Do not launch agents. "
              "Do not write publication bodies or renditions: the HOST retrieves original bytes. Generated prose is never a source. "
              "Do not read extraction candidates, reviews or other run outputs. Missing documents may stay unavailable; "
              "do not invent URLs, identifiers, source text or scientific gap resolutions. "
              "Read the supplied task descriptions only to locate sources; do not evaluate or repair an extraction.\n")
    write(attempt / "instruction.json", {"prompt": prompt})
    local_pins = {str(attempt / name):sha(attempt / name)
                  for name in ("request.json", "schema.json", "instruction.json")}
    check_inputs = check

    def check():
        check_inputs()
        check_pins(local_pins)

    check()
    try:
        result = await worker.run(prompt, attempt)
    except Exception as exc:
        if host_storage_failure(exc):
            raise
        raise SourceFinderFailure(native_failure(attempt, worker_raised=True)) from None
    archive = assess_archived_attempt(attempt)
    try:
        prompt_changed = (attempt / "prompt.txt").read_text() != prompt
    except (OSError, UnicodeDecodeError):
        prompt_changed = True
    if (not archive.complete or result.exit_code != 0 or result.error is not None
            or result.thread_id != archive.thread_id or result.usage != archive.usage
            or prompt_changed):
        raise SourceFinderFailure(native_failure(attempt, result, archive=archive, prompt_changed=prompt_changed))
    check()
    try:
        findings = Findings.model_validate_json((attempt / "artifact.json").read_bytes())
    except (OSError, ValueError):
        raise SourceFinderFailure(output_failure(attempt)) from None
    if findings.method_id != method["id"]:
        raise SourceFinderFailure(output_failure(attempt, method_mismatch=True))
    if any(path.is_symlink() for path in directory.rglob("*")):
        raise ValueError("Source-finder output cannot redirect host writes through symlinks")
    local_pins.update({str(path):sha(path) for path in attempt.iterdir() if path.is_file()})
    documents, new_sources, ledger, known_links, known_identities, word_receipts = [], [], [], {}, {}, {}
    landing_cache = {}
    if previous:
        for source in previous["sources"]:
            if source["role"] == "primary_paper":
                known_links[source["url"]] = _links(Path(source["original_path"]).read_bytes(), source["url"])
                known_identities[source["url"]] = source["document_identity"]
    suffixes = {"jats": ".xml", "html": ".html", "pdf": ".pdf", "text": ".txt", "docx": ".docx", "doc": ".doc"}
    for document in findings.documents:
        record = {**document.model_dump(mode="json"), "status": "unavailable"}
        documents.append(record)
        try:
            url = public_url(document.url)
            check()
            fetched = await asyncio.to_thread(downloader, url)
            if not isinstance(fetched, Download) or not isinstance(fetched.content, bytes):
                raise ValueError("Downloader must return retrieved bytes and response metadata")
            public_url(fetched.final_url)
            if not fetched.content or len(fetched.content) > MAX_BYTES:
                raise ValueError("Empty or oversized original")
            if document.role == "supplement":
                path = directory / "supplements" / (document.id + suffixes[document.format])
            else:
                filename = "article" if document.format == "html" else document.id
                path = directory / "originals" / document.id / (filename + suffixes[document.format])
            atomic_bytes(path, fetched.content)
            local_pins[str(path)] = sha(path)
            record.update(status="retrieved_unregistered", path=str(path), original_sha256=sha(path),
                          final_url=fetched.final_url, content_type=fetched.content_type)
            if document.format in {"docx", "doc"}:
                receipt = word_rendition(path, directory / "renditions" / (document.id + ".txt"), document.format)
                receipt["source_id"] = f"acquired_{document.id}_{sha(path)[:8]}"
                record.update(rendition=receipt["rendition"], rendition_sha256=receipt["rendition_sha256"])
                word_receipts[document.id] = receipt
                for intermediate in receipt.get("intermediates", []):
                    local_pins[intermediate["path"]] = intermediate["sha256"]
                if receipt.get("executable"):
                    local_pins[receipt["executable"]] = receipt["executable_sha256"]
                local_pins.update(receipt.get("conversion_assets", {}))
                ledger.append(receipt)
                local_pins[receipt["rendition"]] = receipt["rendition_sha256"]
            if document.role != "primary_paper":
                if document.role != "supplement":
                    record["registration_limit"] = "Cited/manufacturer/other documents require explicit scoped registration; no automatic dependency recovery"
                continue
            source, receipt = _primary(document, record, path, expected, directory / "renditions" / (document.id + ".txt"))
            # Registration validates actual rendition bytes before marking it usable.
            build_packet(**options, sources=[source])
            new_sources.append(source); ledger.append(receipt)
            record["status"] = "registered_for_fresh_source_review"
            # Wrapper selection must not inherit attachment links from another article.
            known_links[url] = known_links[fetched.final_url] = _links(Path(source["path"]).read_bytes(), fetched.final_url)
            known_identities[url] = known_identities[fetched.final_url] = record["host_identity"]
            local_pins[receipt["rendition"]] = receipt["rendition_sha256"]
        except (OSError, ValueError, ParseError) as exc:
            if host_storage_failure(exc):
                raise
            record["error"] = f"{type(exc).__name__}: {exc}"
    register_rows = []
    pmid = expected.pmid
    for document, record in zip(findings.documents, documents):
        if document.role != "supplement" or "path" not in record:
            continue
        linked = known_links.get(document.linked_from, set())
        associated = document.url in linked or record["final_url"] in linked
        if associated:
            record["association"] = {"basis":"Exact URL present in the host-registered primary", "primary":document.linked_from}
        if (not associated and document.association_record is None and document.linked_from
                and document.linked_from not in known_links):
            try:
                record["association"] = await _landing_association(document, record, known_identities,
                    landing_cache, directory, local_pins, downloader, check)
                associated = True
            except (OSError, ValueError, KeyError) as exc:
                if host_storage_failure(exc):
                    raise
                record["error"] = f"Landing association verification failed: {exc}"
        if not associated and document.association_record is not None:
            try:
                if document.linked_from not in known_links:
                    raise ValueError("Association requires a host-registered primary document URL")
                binding = document.association_record
                metadata_url = public_url(binding.url)
                fetched_record = await asyncio.to_thread(downloader, metadata_url)
                if not isinstance(fetched_record, Download) or fetched_record.final_url != metadata_url:
                    raise ValueError("Association API retrieval changed its official endpoint")
                if not fetched_record.content or len(fetched_record.content) > MAX_BYTES:
                    raise ValueError("Empty or oversized association record")
                association_path = directory / "associations" / (document.id + ".json")
                atomic_bytes(association_path, fetched_record.content)
                local_pins[str(association_path)] = sha(association_path)
                primary_identity = copy.deepcopy(known_identities.get(document.linked_from, {}))
                ids = primary_identity.setdefault("identifiers", {})
                for key in ("pmid", "doi"):
                    if getattr(expected,key) and not ids.get(key):ids[key] = [getattr(expected,key)]
                canonical, association = biostudies_record(fetched_record.content, metadata_url, binding.file_path,
                    primary_identity, linked, Path(record["path"]).stat().st_size)
                if document.url != canonical and record["final_url"] != canonical:
                    verified = await asyncio.to_thread(downloader, canonical)
                    if (not isinstance(verified, Download) or verified.final_url != record["final_url"]
                            or verified.content != Path(record["path"]).read_bytes()):
                        raise ValueError("Candidate mirror differs from host canonical BioStudies delivery")
                    association["canonical_final_url"] = verified.final_url
                association.update(record=str(association_path), record_sha256=sha(association_path), record_url=metadata_url)
                record["association"] = association
                associated = True
                check()
            except (OSError, ValueError, KeyError) as exc:
                if host_storage_failure(exc):
                    raise
                record["error"] = f"Association verification failed: {exc}"
        if not associated:
            record.setdefault("error", "Supplement association is not host-linked to a registered primary")
            continue
        if document.format in {"docx", "doc"}:
            if document.id not in word_receipts:continue
            receipt = word_receipts[document.id]
            receipt["association"] = record["association"]
            source = {"source_id":receipt["source_id"], "title":Path(record["path"]).name,
                      "url":record["final_url"], "role":"supplement", "path":receipt["rendition"]}
            try:
                build_packet(**options,sources=[source])
                new_sources.append(source)
                record["status"] = "registered_for_fresh_source_review"
                register_rows.append({"status":"cached", "path":record["path"], "sha256":record["original_sha256"],
                                      "url":record["final_url"], "document_id":document.id, "converter":receipt["converter"]})
            except (OSError, ValueError) as exc:
                if host_storage_failure(exc):
                    raise
                record["error"] = str(exc)
            continue
        if not pmid:
            record["error"] = "Existing PDF/text supplement converter requires a bound PMID"
            continue
        if document.format not in {"pdf", "text"}:
            record["error"] = "Supplement format requires explicit original inspection/registration"
            continue
        if document.format == "text" and re.search(br"<(?:!doctype|html|script)\b", Path(record["path"]).read_bytes(), re.I):
            record["error"] = "HTML response cannot be registered as a plain-text supplement"
            continue
        register_rows.append({"status": "cached", "path": record["path"], "sha256": record["original_sha256"],
                              "url": record["final_url"], "document_id": document.id})
    if register_rows:
        write(directory / "supplements/acquisition.json", {"pmid": pmid, "files": register_rows})
        sources, receipts, leads = supplements(directory, pmid, directory / "renditions")
        for source, receipt in zip(sources, receipts):
            record = next(r for r in documents if r.get("path") == receipt["original"])
            source["source_id"] = receipt["source_id"] = f"acquired_{record['id']}_{record['original_sha256'][:8]}"
            try:
                build_packet(**options, sources=[source])
                new_sources.append(source); ledger.append(receipt)
                record["status"] = "registered_for_fresh_source_review"
            except (OSError, ValueError) as exc:
                if host_storage_failure(exc):
                    raise
                record["error"] = str(exc)
        for lead in leads:
            path = lead.get("record", {}).get("path")
            for record in documents:
                if record.get("path") == path:
                    if record["status"] == "registered_for_fresh_source_review":continue
                    record["error"] = lead.get("error", lead["status"])
    write(directory / "acquisition.json", {"method_id": method["id"], "documents": documents,
                                          "searches": [s.model_dump(mode="json") for s in findings.searches],
                                          "scientific_resolution": False})
    write(directory / "originals_ledger.json", ledger)
    check()
    if not new_sources:
        return {"status": "sources_retained_unregistered", "documents": documents, "packet": None}
    descriptors = ([{**{k:s[k] for k in ("source_id", "title", "url", "role")}, "path":s["original_path"]}
                    for s in previous["sources"]] if previous else [])
    version = ({k:previous[k] for k in ("format_version", "rendition_version") if k in previous} if previous else {})
    packet = build_packet(**options, **version, sources=[*descriptors, *new_sources])
    check()
    write(directory / "packet.json", packet)
    return {"status": "registered_for_fresh_source_review", "documents": documents,
            "packet": str(directory / "packet.json")}


async def acquire(worklist: Path, output: Path, *, method_ids: set[str] | None = None,
                  scopes: dict | None = None, worker=None, downloader=None, concurrency=2) -> dict:
    """Find/retrieve a worklist subset; emit explicit fresh-registration inputs.

    Existing packet identity, variant, source IDs and passages are preserved. A
    single declared origin can seed the same provisional scope as atlas-sources;
    ambiguous/absent origins require a caller-supplied Scope, never a model guess.
    """
    worklist, output = Path(worklist).resolve(), Path(output).resolve()
    if type(concurrency) is not int or concurrency < 1:
        raise ValueError("concurrency must be a positive integer")
    document = json.loads(worklist.read_bytes())
    if document.get("format_version") != "source_worklist/1.0":
        raise ValueError("Expected source_worklist/1.0")
    methods = {row["id"]:row for row in document["methods"]}
    if len(methods) != len(document["methods"]) or any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", mid) for mid in methods):
        raise ValueError("Worklist method IDs must be unique safe identifiers")
    selected = set(method_ids) if method_ids is not None else {mid for mid,m in methods.items()
        if any(t["kind"] in ACTIONABLE for t in m["tasks"])}
    if not selected or selected - methods.keys() or set(scopes or {}) - selected:
        raise ValueError("Select known acquisition methods; scope choices must belong to the selected subset")
    pins = {str(worklist):sha(worklist)}
    for mid in selected:
        for task in methods[mid]["tasks"]:
            if task["kind"] not in ACTIONABLE:
                continue
            origin = task.get("origin", {})
            if origin.get("path") and origin.get("sha256"):
                pins[str(Path(origin["path"]).resolve())] = origin["sha256"]
        previous = methods[mid].get("source_packet")
        if previous:
            pins[str(Path(previous["path"]).resolve())] = previous["sha256"]
            check_pins(pins)
            packet = load_packet(Path(previous["path"]))
            pins.update({s["original_path"]:s["original_sha256"] for s in packet["sources"]})
    check_pins(pins)
    package = Path(__file__).resolve().parent
    curation = package.parent
    implementation = {str(path):sha(path) for path in [*package.glob("*.py"),
        *(curation / name for name in ("codex_worker.py", "cached_documents.py", "document_manifest.py", "source_worklist.py")),
        *(curation / "workflow_extraction" / name for name in ("sources.py", "jats_text.py"))]}

    def check():
        check_pins(pins)
        check_pins(implementation)

    output.mkdir(parents=True, exist_ok=False)
    atomic_bytes(output / "worklist.json", worklist.read_bytes())
    write(output / "scope_choices.json", scopes or {})
    write(output / "implementation.json", {"files":implementation,
        "boundary":"Code hashes replayed during acquisition; not source-document dependencies of later extraction."})
    states, semaphore = {}, asyncio.Semaphore(concurrency)
    fetch = downloader or download

    async def one(mid):
        method = copy.deepcopy(methods[mid])
        method["tasks"] = [t for t in method["tasks"] if t["kind"] in ACTIONABLE]
        method["retained_unknowns"] = []
        try:
            options, expected, previous = _scope(method, (scopes or {}).get(mid))
        except (OSError, ValueError, KeyError) as exc:
            states[mid] = {"status": "needs_scope_decision", "error": str(exc), "packet": None}
            return
        async with semaphore:
            try:
                native = worker or CodexWorker(model=DEFAULT_MODEL, effort=DEFAULT_EFFORT,
                    timeout_seconds=None, web_search=True, network_access=True)
                if isinstance(native, CodexWorker) and not (native.web_search and native.network_access):
                    raise ValueError("Source finder requires public web search and network access")
                states[mid] = await _method(method, output / "methods" / mid, options, expected, previous,
                    native, fetch, check)
            except Exception as exc:
                states[mid] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "packet": None}
                if isinstance(exc, SourceFinderFailure):
                    states[mid]["failure"] = exc.detail
                elif failure := host_storage_failure(exc):
                    states[mid]["failure"] = failure
            finally:
                write(output / "state.json", {"methods": states, "scientific_resolution": False})
    await asyncio.gather(*(one(mid) for mid in sorted(selected)))
    check()
    # Originals, failed retrievals, finder artifacts, conversion registers and
    # receipts are all pinned, not merely the text paths inside source packets.
    files = {**pins, **{str(p):sha(p) for p in output.rglob("*") if p.is_file()}}
    for mid in selected:
        previous = methods[mid].get("source_packet")
        if previous:
            files[previous["path"]] = previous["sha256"]
            packet = load_packet(Path(previous["path"]))
            files.update({s["original_path"]:s["original_sha256"] for s in packet["sources"]})
    write(output / "source_receipts.json", {"format_version": "public_source_acquisition/1.0", "files": files,
          "scientific_resolution": False, "boundary": BOUNDARY})
    replacements = {}
    for mid, state in states.items():
        if not state.get("packet"):
            continue
        packet_path = Path(state["packet"]); packet = load_packet(packet_path)
        replacements[mid] = {"packet": str(packet_path), "packet_sha256":sha(packet_path),
            "previous_packet_sha256": (methods[mid].get("source_packet") or {}).get("sha256"),
            **{k:packet[k] for k in ("method_id", "scope", "variant")},
            "source_receipts": str(output / "source_receipts.json")}
    mapping = {"format_version": "source_replacements/1.0", "worklist_sha256":sha(worklist), "replacements":replacements}
    write(output / "replacements.json", mapping)
    check()
    check_pins(files)
    registration = (resolve(worklist, output / "replacements.json", output / "registration") if replacements else None)
    receipt = {"format_version": "public_source_acquisition_result/1.0", "worklist_sha256":sha(worklist),
               "methods": states, "counts":dict(Counter(s["status"] for s in states.values())),
               "registration":registration, "scientific_resolution":False, "boundary":BOUNDARY}
    write(output / "result.json", receipt)
    return receipt

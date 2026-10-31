#!/usr/bin/env python3
"""Prepare source-depiction rows for the normal Illumina extraction batch.

Rows stay separate from canonical technologies. Render each PDF page once under
Slurm, retain broad target context, and preserve literal citations independently
of candidate publication IDs. No network, models, semantic merges or history.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import copy
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess

INVENTORY_FORMAT = "illumina_source_inventory/1.0"
SOURCES_FORMAT = "illumina_source_acquisition/1.0"
PATH_PINS = {"path": "sha256", "text_path": "text_sha256", "blocks_path": "blocks_sha256",
             "metadata_source": "metadata_source_sha256", "cache_registration_path": "cache_registration_sha256"}
DOCUMENT_IDENTITY_STATUSES = {
    "cached_registration_identity_and_custody_verified", "crossref_doi_verified",
    "pubmed_record_retrieved", "pubmed_pmid_and_title_match_pmc_html_citation_metadata",
}


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(part)
    return result.hexdigest()


def object_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def row_record_id(row_id):
    return "poster_" + hashlib.sha256(row_id.encode("utf-8")).hexdigest()[:24]


def doi_key(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value if re.fullmatch(r"10\.\d{4,9}/\S+", value) else None


def external_document_index(external):
    """Index already identified DOCUMENTS, not method-to-paper associations."""
    by_doi, by_card = defaultdict(list), defaultdict(set)
    for ident, source in external.items():
        doi = doi_key(source.get("doi"))
        if (not doi or source.get("identity_status") not in DOCUMENT_IDENTITY_STATUSES or
                source.get("content_level") not in {"metadata", "abstract", "fulltext"} or
                not source.get("text_path") or not source.get("text_sha256")):
            continue
        by_doi[doi].append((ident, source))
        if source.get("card_id"):
            by_card[source["card_id"]].add(doi)
    return by_doi, by_card


def crossref_document(candidate, doi, root, output, custody):
    """Make metadata/abstract text from the actual pinned Crossref response."""
    receipt = candidate.get("metadata_receipt", {})
    if not receipt.get("path") or not receipt.get("sha256"):
        raise ValueError("Crossref document candidate lacks a pinned response")
    path = Path(receipt["path"])
    path, _ = custody.verify(path if path.is_absolute() else root / path, receipt["sha256"])
    message = json.loads(path.read_bytes()).get("message", {})
    works = message.get("items", []) if "items" in message else [message]
    matching = [w for w in works if doi_key(w.get("DOI")) == doi]
    if len(matching) != 1:
        raise ValueError("Candidate DOI is not unique in its pinned Crossref response")
    work = matching[0]
    title = " / ".join(work.get("title", []))
    abstract = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", work.get("abstract") or "")).split())
    text = f"Title: {title}\nDOI: {doi}\n" + ("Abstract: " + abstract + "\n" if abstract else "")
    directory = output / "candidate_documents"
    directory.mkdir(exist_ok=True)
    text_path = directory / (hashlib.sha256(text.encode()).hexdigest() + ".txt")
    if not text_path.exists():
        text_path.write_text(text, encoding="utf-8")
    _, pin = custody.verify(text_path)
    return {"doi": doi, "title": title, "content_level": "abstract" if abstract else "metadata",
        "identity_status": "crossref_doi_verified", "status": "metadata_available",
        "text_path": str(text_path), "text_sha256": pin, "public_url": "https://doi.org/" + doi,
        "original_custody": {**copy.deepcopy(receipt), "path": str(path)},
        "scientific_support_status": "metadata_or_abstract_available_not_fulltext_or_method_applicability_review"}


def member_external_candidates(member, resolutions, citation_ids, *, rid, by_doi, by_card, root, output, custody):
    """Expose DOI documents as optional candidates, preserving literal refs."""
    requests = {}
    def request(doi, basis, refids=(), crossref=None):
        item = requests.setdefault(doi, {"basis": set(), "reference_ids": set(), "crossref": []})
        item["basis"].add(basis)
        item["reference_ids"].update(refids)
        if crossref is not None:
            item["crossref"].append(crossref)
    hints = set(member.get("exact_current_card_label_candidates", [])) | set(member.get("historical_card_ids_for_exact_existing_entity_candidates", []))
    for card in sorted(hints):
        for doi in sorted(by_card.get(card, [])):
            request(doi, "existing_source_card_document_hint_not_poster_identity")
    for resolution in resolutions:
        for reference in resolution.get("references", []):
            cid = reference["citation_id"]
            if cid not in citation_ids:
                raise ValueError("External candidate refers to another member's literal citation")
            for candidate in reference.get("crossref_candidates", []):
                if candidate.get("comparison", {}).get("all_observed_fields_agree") is not True:
                    continue
                doi = doi_key(candidate.get("publication", {}).get("doi"))
                if doi:
                    request(doi, "bibliographic_crossref_candidate_not_method_applicability", citation_ids[cid], candidate)
    references, documents = [], {}
    for doi, request in sorted(requests.items()):
        # Pin lookup evidence even when a full-text cache already supplies the
        # document; cached availability must not hide a changed lookup response.
        crossref_sources = [crossref_document(candidate, doi, root, output, custody)
                            for candidate in request["crossref"]]
        available = by_doi.get(doi, [])
        if available:
            rank = {"metadata": 1, "abstract": 2, "fulltext": 3}
            highest = max(rank[s["content_level"]] for _, s in available)
            available = [(key, s) for key, s in available if rank[s["content_level"]] == highest]
        else:
            available = [("pinned_crossref_metadata", document) for document in crossref_sources]
        seen = set()
        for original_id, source in available:
            signature = (source["content_level"], source["text_sha256"])
            if signature in seen:
                continue
            seen.add(signature)
            ident = rid + ":candidate_publications:doi_" + object_hash({"member": member["source_method_label"],
                "doi": doi, "content_level": signature[0], "text_sha256": signature[1]})[:24]
            sources = sorted(request["basis"])
            links = sorted(request["reference_ids"])
            references.append({"reference_id": ident, "role": "candidate_publications", "listed_by": "candidate_document_lookup",
                "pmid": None, "source_method_label": member["source_method_label"], "association_status": "unverified_candidate",
                "linked_literal_reference_ids": links, "source_reference": {"doi": doi, "title": source.get("title"),
                    "candidate_basis": sources, "paper_identity_verified": False}})
            documents[ident] = {**copy.deepcopy(source), "reference_id": ident, "source_id": "reference:" + ident, "card_id": rid,
                "doi": doi, "candidate_basis": sources, "original_external_reference_id": original_id,
                "source_method_label": member["source_method_label"], "linked_literal_reference_ids": links,
                "candidate_bibliographic_sources": [s["original_custody"] for s in crossref_sources],
                "association_status": "unverified_candidate", "paper_method_association_verified": False}
    return references, documents


def finite_box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("Expected four PDF coordinates")
    box = tuple(float(x) for x in value)
    if not all(math.isfinite(x) for x in box) or box[0] < 0 or box[1] < 0 or box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError("Invalid PDF coordinate box")
    return box


def pixel_box(box, *, dpi, width, height, point_width, point_height):
    box = finite_box(box)
    if box[2] > point_width + 0.02 or box[3] > point_height + 0.02:
        raise ValueError("PDF region extends beyond the source page")
    scale = dpi / 72
    result = (math.floor(box[0] * scale), math.floor(box[1] * scale),
              min(width, math.ceil(box[2] * scale)), min(height, math.ceil(box[3] * scale)))
    if result[0] >= result[2] or result[1] >= result[3]:
        raise ValueError("Empty pixel crop")
    return result


class Custody:
    """Pin working files once; historical provenance is retained without rebasing."""
    def __init__(self):
        self.files = {}

    def verify(self, path, expected=None):
        path = Path(path)
        if path.is_symlink():
            raise ValueError("Source symlinks require an explicit prior registration: " + str(path))
        path = path.resolve()
        if not path.is_file():
            raise ValueError("Expected materialized source file: " + str(path))
        if getattr(path.stat(), "st_flags", 0) & 0x40000000:
            raise ValueError("Dataless source must be materialized remotely: " + str(path))
        if expected is not None and not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("Invalid source SHA256")
        observed = self.files.get(str(path)) or file_hash(path)
        if expected and observed != expected:
            raise ValueError("Source custody mismatch: " + str(path))
        self.files[str(path)] = observed
        return path, observed

    def absolute_records(self, value, base):
        if isinstance(value, list):
            return [self.absolute_records(v, base) for v in value]
        if not isinstance(value, dict):
            return value
        result = {k: copy.deepcopy(v) if k in {"cache_provenance", "prior_fulltext_attempt", "snapshot_file_imports"}
                  else self.absolute_records(v, base) for k, v in value.items()}
        for key, pin_key in PATH_PINS.items():
            if isinstance(result.get(key), str):
                pin = result.get(pin_key)
                if not pin:
                    raise ValueError("Unpinned current source-file field: " + key)
                path = Path(result[key])
                resolved, _ = self.verify(path if path.is_absolute() else base / path, pin)
                result[key] = str(resolved)
        return result

    def recheck(self):
        for path, expected in self.files.items():
            if file_hash(path) != expected:
                raise ValueError("Source changed during poster preparation: " + path)


def render_page(pdf, destination, *, page, dpi):
    """Poppler rasterization with explicit page geometry and no OCR."""
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Poster rasterization must run in a Sherlock Slurm allocation")
    info_tool, raster_tool = shutil.which("pdfinfo"), shutil.which("pdftoppm")
    if not info_tool or not raster_tool:
        raise ValueError("Poppler pdfinfo and pdftoppm are required")
    info_command = [info_tool, "-f", str(page), "-l", str(page), "-box", str(pdf)]
    info = subprocess.run(info_command, capture_output=True, check=True, timeout=120).stdout.decode("utf-8")
    size = re.search(r"Page\s+(?:\d+\s+)?size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts", info)
    rotation = re.search(r"Page\s+(?:\d+\s+)?rot:\s+(-?\d+)", info)
    if not size or not rotation or int(rotation[1]) != 0:
        raise ValueError("Unverified page geometry or rotated PDF; explicit coordinate adaptation required")
    point_width, point_height = float(size[1]), float(size[2])
    media = re.search(r"(?:Page\s+\d+\s+)?MediaBox:\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)", info)
    crop = re.search(r"(?:Page\s+\d+\s+)?CropBox:\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)", info)
    if not media or not crop or media.groups() != crop.groups() or tuple(float(x) for x in media.groups()[:2]) != (0., 0.):
        raise ValueError("Nontrivial PDF boxes require an explicit coordinate adaptation")
    command = [raster_tool, "-f", str(page), "-l", str(page), "-r", str(dpi), "-singlefile", "-png", str(pdf), str(destination.with_suffix(""))]
    subprocess.run(command, capture_output=True, check=True, timeout=600)
    from PIL import Image
    with Image.open(destination) as image:
        width, height = image.size
    return {"path": str(destination.resolve()), "sha256": file_hash(destination), "page": page, "dpi": dpi,
            "width_pixels": width, "height_pixels": height, "page_width_points": point_width,
            "page_height_points": point_height, "renderer": "Poppler pdftoppm PNG, no OCR",
            "command": command, "geometry_command": info_command, "geometry_output": info}


def validate_overlay(overlay):
    rows = overlay.get("rows", [])
    ids = [r.get("row_id") for r in rows]
    if not rows or any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)):
        raise ValueError("Overlay requires nonempty distinct source-row IDs")
    if len({row_record_id(x) for x in ids}) != len(ids):
        raise ValueError("Source-row record-ID hash collision")
    for row in rows:
        if not row.get("source_row_caption") or not row.get("members"):
            raise ValueError("Every row requires its literal caption and source members")
        names = [m.get("source_method_label") for m in row["members"]]
        if any(not isinstance(n, str) or not n for n in names) or len(names) != len(set(names)):
            raise ValueError("Every source member needs a nonempty distinct label within its row")
        box = finite_box(row["diagram_region_proposal"])
        if not row.get("exact_label_bboxes"):
            raise ValueError("Target-label bounding boxes are required for crop verification")
        for label in row.get("exact_label_bboxes", []):
            label = finite_box(label)
            if label[0] < box[0] or label[1] < box[1] or label[2] > box[2] or label[3] > box[3]:
                raise ValueError("Context crop does not preserve the target label")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", row.get("resource_id", "")):
            raise ValueError("Unsafe poster resource ID")
        if not isinstance(row.get("page"), int) or row["page"] < 1:
            raise ValueError("Explicit positive PDF page required")
        if not re.fullmatch(r"[a-f0-9]{64}", row.get("source_pdf_sha256", "")):
            raise ValueError("Poster original SHA256 required")
    return rows


def prepare(overlay_path, base_sources_path, asset_root, output, *, resolutions_path=None, renderer=None):
    overlay_path, base_sources_path, asset_root, output = map(lambda p: Path(p).resolve(), (overlay_path, base_sources_path, asset_root, output))
    custody = Custody()
    overlay_file, overlay_sha = custody.verify(overlay_path)
    source_file, source_sha = custody.verify(base_sources_path)
    overlay, base = json.loads(overlay_file.read_bytes()), json.loads(source_file.read_bytes())
    if base.get("format_version") != SOURCES_FORMAT:
        raise ValueError("Expected an Illumina acquired source snapshot")
    if not base.get("inventory_sha256"):
        raise ValueError("Base source snapshot must pin its own inventory")
    custody.verify(base_sources_path.parent / "inventory.json", base["inventory_sha256"])
    rows = validate_overlay(overlay)
    resolutions = {}
    resolution_pin = None
    if resolutions_path:
        resolution_path, resolution_sha = custody.verify(resolutions_path)
        resolutions = json.loads(resolution_path.read_bytes())
        if resolutions.get("proposals", {}).get("sha256") != overlay_sha:
            raise ValueError("Citation candidates refer to a different poster overlay")
        resolution_pin = {"path": str(resolution_path), "sha256": resolution_sha}
    row_names = {(r["row_id"], m["source_method_label"]) for r in rows for m in r["members"]}
    by_resolution = defaultdict(list)
    for item in resolutions.get("resolutions", []):
        key = item.get("row_id"), item.get("source_method_label")
        if key not in row_names or item.get("paper_identity_verified") is not False:
            raise ValueError("Resolution must be an unverified candidate for an existing source member")
        by_resolution[key].append(item)
    # A source pool is reused only after current custody links are pinned. No
    # publication is promoted to an assayed method by appearing in this pool.
    pool = {k: custody.absolute_records(base.get(k, {} if k != "publication_batches" else []), base_sources_path.parent)
            for k in ("publications", "fulltexts", "external_publications", "publication_batches")}
    resolution_root = Path(resolutions_path).resolve().parent if resolutions_path else output
    for pmid, pub in resolutions.get("publications", {}).items():
        if str(pub.get("pmid")) != str(pmid) or not str(pmid).isdecimal():
            raise ValueError("Publication metadata map has a conflicting PMID")
        verified = custody.absolute_records(pub, resolution_root)
        if pmid in pool["publications"]:
            prior = pool["publications"][pmid]
            if prior.get("title") and pub.get("title") and prior["title"] != pub["title"]:
                raise ValueError("Conflicting publication titles for one PMID")
        else:
            pool["publications"][pmid] = verified
    for pmid, full in resolutions.get("fulltexts", {}).items():
        if pmid not in pool["publications"]:
            raise ValueError("Candidate fulltext lacks identity-bound publication metadata")
        verified = custody.absolute_records(full, resolution_root)
        if pool["fulltexts"].get(pmid, {}).get("status") != "fulltext_acquired":
            pool["fulltexts"][pmid] = verified
    for pmid, pub in pool["publications"].items():
        if str(pub.get("pmid")) != pmid or not pmid.isdecimal():
            raise ValueError("Source publication map must use its own numeric PMID")
    external_by_doi, external_by_card = external_document_index(pool["external_publications"])
    pdfs = {}
    parent_assets = {a["asset_id"]: a for a in base.get("assets", [])}
    for row in rows:
        parent_asset = parent_assets.get(row["resource_id"], {})
        if (parent_asset.get("status") != "downloaded" or parent_asset.get("sha256") != row["source_pdf_sha256"]
                or parent_asset.get("kind") != row["resource_kind"] or parent_asset.get("url") != row["source_url"]):
            raise ValueError("Poster overlay PDF kind/URL/hash is not bound to the acquired parent resource")
        pdf, _ = custody.verify(asset_root / (row["resource_id"] + ".pdf"), row["source_pdf_sha256"])
        pdfs[(row["resource_id"], row["page"])] = pdf
    if renderer is None and not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Poster rendering must run under Sherlock Slurm")
    output.mkdir(parents=True, exist_ok=False)
    rasters, assets, cards, coverage = {}, [], [], []
    active_image, active_page, decoded_pages = None, None, 0
    try:
        from PIL import Image
        (output / "rasters").mkdir()
        (output / "assets").mkdir()
        for (resource_id, page), pdf in pdfs.items():
            path = output / "rasters" / f"{resource_id}_p{page:04d}_144dpi.png"
            raster = (renderer or render_page)(pdf, path, page=page, dpi=144)
            if Path(raster["path"]).resolve() != path.resolve() or raster.get("dpi") != 144 or raster.get("page") != page:
                raise ValueError("Renderer returned a different raster target/page/DPI")
            custody.verify(path, raster["sha256"])
            if any(not isinstance(raster.get(k), (int, float)) or not math.isfinite(raster[k]) or raster[k] <= 0
                   for k in ("width_pixels", "height_pixels", "page_width_points", "page_height_points")):
                raise ValueError("Renderer geometry must be positive and finite")
            if any(not isinstance(raster[k], int) for k in ("width_pixels", "height_pixels")):
                raise ValueError("Raster pixel dimensions must be integers")
            with Image.open(path) as image:
                if image.size != (raster["width_pixels"], raster["height_pixels"]):
                    raise ValueError("Raster dimensions differ from render receipt")
            if any(abs(raster[a] - raster[b] * 2) > 1.01 for a, b in (("width_pixels", "page_width_points"), ("height_pixels", "page_height_points"))):
                raise ValueError("Raster geometry is not the declared 144dpi PDF transform")
            rasters[(resource_id, page)] = raster
        # PNG cropping otherwise decompresses an entire ~70MP poster for every
        # row. Group by source page, retain one decoded raster, and restore the
        # declared overlay order before publishing the inventory.
        for row in sorted(rows, key=lambda r: (r["resource_id"], r["page"], r["row_id"])):
            rid = row_record_id(row["row_id"])
            raster = rasters[(row["resource_id"], row["page"])]
            box = pixel_box(row["diagram_region_proposal"], dpi=144, width=raster["width_pixels"], height=raster["height_pixels"],
                            point_width=raster["page_width_points"], point_height=raster["page_height_points"])
            image_path = output / "assets" / (rid + ".png")
            page_key = row["resource_id"], row["page"]
            if active_page != page_key:
                if active_image is not None:
                    active_image.close()
                active_image = Image.open(raster["path"])
                active_image.load()
                active_page = page_key
                decoded_pages += 1
            active_image.crop(box).convert("RGB").save(image_path)
            _, image_sha = custody.verify(image_path)
            aid = "poster_crop_" + object_hash({"row_id": row["row_id"], "parent_pdf_sha256": row["source_pdf_sha256"],
                                              "pixel_box": box, "crop_sha256": image_sha})[:24]
            provenance = {"format_version": "illumina_poster_crop/1.0", "source_row_id": row["row_id"],
                "parent_pdf": {"path": str(pdfs[(row["resource_id"], row["page"])]), "sha256": row["source_pdf_sha256"], "page": row["page"]},
                "raster": raster, "overlay": {"path": str(overlay_path), "sha256": overlay_sha},
                "crop": {"pdf_point_box": row["diagram_region_proposal"], "pixel_box": box,
                         "transform": "pixel=PDFpoint*144/72; floorleft/top, ceilright/bottom", "resampling": "none"},
                "target_labels": [m["source_method_label"] for m in row["members"]],
                "context_scope_warning": row.get("context_scope_warning", "Adjacent rows and chemical legend may be visible; extract only the named target workflow."),
                "scientific_applicability_verified": False}
            assets.append({"asset_id": aid, "source_id": "diagram:" + aid, "kind": "method_diagram", "card_ids": [rid],
                "url": row["source_url"], "source_urls": [row["source_url"]], "path": str(image_path), "sha256": image_sha,
                "bytes": image_path.stat().st_size, "status": "downloaded", "acquisition_basis": "derived_from_hash_verified_poster_pdf", "original": provenance})
            references, candidate_publications, external_references = [], [], []
            for member in row["members"]:
                name = member["source_method_label"]
                citation_ids = defaultdict(list)
                for listed in member.get("source_listed_citations", []):
                    fragment = listed["citation_fragment_as_listed"]
                    cid = "citation_" + hashlib.sha256(fragment.encode()).hexdigest()[:24]
                    refid = f"{rid}:references:{len(references)}"
                    citation_ids[cid].append(refid)
                    references.append({"reference_id": refid, "role": "references", "listed_by": "illumina_poster", "pmid": None,
                        "identity_status": "unverified_source_listed_citation", "scientific_support_status": "listed_reference_not_claim_verification",
                        "source_reference": {**copy.deepcopy(listed), "source_method_label": name, "source_row_id": row["row_id"],
                                             "source_pdf_sha256": row["source_pdf_sha256"], "citation_id": cid}})
                for pmid in member.get("catalogue_reference_candidate_pmids", []):
                    candidate_publications.append({"pmid": str(pmid), "candidate_basis": "historical_source_card_or_entity_label_hint_not_poster_identity",
                        "reference_ids": [], "source_method_label": name, "association_status": "unverified_candidate"})
                for resolution in by_resolution[(row["row_id"], name)]:
                    if any(ref["citation_id"] not in citation_ids for ref in resolution.get("references", [])):
                        raise ValueError("Citation resolution references a different member's literal citation")
                    for pmid in resolution.get("candidate_pmids", []):
                        linked = [refid for ref in resolution.get("references", [])
                            if any(str(c.get("pmid")) == str(pmid) and c.get("comparison", {}).get("all_observed_fields_agree") is True
                                   for c in ref.get("candidate_publications", []))
                            for refid in citation_ids.get(ref["citation_id"], [])]
                        candidate_publications.append({"pmid": str(pmid), "candidate_basis": "bibliographic_resolver_candidate_not_method_applicability",
                            "reference_ids": sorted(set(linked)), "source_method_label": name, "association_status": "unverified_candidate",
                            "resolution_source": resolution_pin})
                proposed_refs, proposed_docs = member_external_candidates(member, by_resolution[(row["row_id"], name)], citation_ids,
                    rid=rid, by_doi=external_by_doi, by_card=external_by_card, root=resolution_root, output=output, custody=custody)
                external_references.extend(proposed_refs)
                pool["external_publications"].update(proposed_docs)
            if any(not re.fullmatch(r"\d+", c["pmid"]) for c in candidate_publications):
                raise ValueError("Publication candidate PMID must be numeric")
            candidate_publications = list({object_hash(c): c for c in candidate_publications}.values())
            literal_reference_count = len(references)
            references.extend(external_references)
            cards.append({"record_id": rid, "source_id": "card:" + rid, "source_title": row["source_row_caption"],
                "source_page_url": row["source_url"], "source_url": row["source_url"], "explorer_url": "https://www.illumina.com/science/sequencing-method-explorer.html",
                "source_category": "Illumina poster > " + row["resource_kind"], "source_category_path": ["Illumina poster", row["resource_kind"]],
                "description_verbatim": row["source_row_caption"], "source_sections": [], "advantages_verbatim": None, "limitations_verbatim": None,
                "diagram_asset_ids": [aid], "references": references, "candidate_publications": candidate_publications,
                "require_paper_matches": True, "source_record_status": "substantive", "scope_kind": "source_depiction_row_not_canonical_technology",
                "canonical_technology_id": None, "source_row_id": row["row_id"], "source_method_labels": provenance["target_labels"],
                "poster_scope": {"grouped_source_row": row.get("grouped_source_row", len(row["members"]) > 1),
                    "literal_label_fragments": row["source_label_fragments"], "line_wrap_reconstruction": row.get("line_wrap_reconstruction"),
                    "context_scope_warning": provenance["context_scope_warning"], "source_overlay_row": copy.deepcopy(row)},
                "missing_evidence": ([] if literal_reference_count else ["No source-listed citation recovered; paper association remains unresolved"]),
                "curation_status": "source_prepared_not_scientifically_reviewed"})
            coverage.append({"source_row_id": row["row_id"], "record_id": rid, "source_title": row["source_row_caption"], "asset_id": aid,
                             "source_member_labels": provenance["target_labels"], "reference_occurrences": len(references),
                             "literal_reference_occurrences": literal_reference_count, "external_candidate_documents": len(external_references),
                             "candidate_publications": len(candidate_publications), "status": "prepared_not_extracted"})
        if len(cards) != len(rows) or {c["source_row_id"] for c in cards} != {r["row_id"] for r in rows}:
            raise ValueError("Poster row coverage changed during preparation")
        order = {r["row_id"]: i for i, r in enumerate(rows)}
        cards.sort(key=lambda c: order[c["source_row_id"]])
        coverage.sort(key=lambda c: order[c["source_row_id"]])
        card_order = {c["record_id"]: i for i, c in enumerate(cards)}
        assets.sort(key=lambda a: card_order[a["card_ids"][0]])
        publication_inventory = []
        for pmid in sorted({c["pmid"] for card in cards for c in card["candidate_publications"]}):
            publication_inventory.append({"pmid": pmid, "source_id": "paper:" + pmid, "reference_ids": [],
                "card_ids": [card["record_id"] for card in cards if any(c["pmid"] == pmid for c in card["candidate_publications"])],
                "reference_roles": ["candidate_publication_not_assigned_method_reference"]})
        inventory = {"format_version": INVENTORY_FORMAT, "catalogue": {"path": str(overlay_path), "sha256": overlay_sha}, "selector": None,
            "cards": cards, "assets": [{k: v for k, v in a.items() if k not in {"path", "sha256", "bytes", "status"}} for a in assets],
            "publications": publication_inventory, "resources": [], "scope": "One card per source depiction row; no canonical method merging or ancestry.",
            "statistics": {"cards": len(cards), "substantive_cards": len(cards), "cards_with_diagrams": len(cards),
                "unique_method_diagrams": len(assets), "collection_resources": 0,
                "reference_occurrences": sum(len(c["references"]) for c in cards), "unique_pmids": len(publication_inventory)}}
        write(output / "inventory.json", inventory)
        custody.verify(output / "inventory.json")
        snapshot = {"format_version": SOURCES_FORMAT, "inventory_sha256": file_hash(output / "inventory.json"),
            "status": "prepared_with_unverified_paper_associations", "scientific_review": False,
            "parent_acquisition": {"path": str(base_sources_path), "sha256": source_sha}, "poster_overlay": {"path": str(overlay_path), "sha256": overlay_sha},
            "candidate_resolution": resolution_pin, "assets": assets, **pool,
            "statistics": {**inventory["statistics"], "fulltexts_acquired": sum(v.get("status") == "fulltext_acquired" for v in pool["fulltexts"].values()),
                "publication_metadata_pool": len(pool["publications"]), "rendered_pdf_pages": len(rasters),
                "decoded_raster_pages": decoded_pages, "source_rows_prepared": len(rows)}}
        custody.recheck()
        write(output / "sources.json", snapshot)
        write(output / "preparation_receipt.json", {"format_version": "illumina_poster_source_preparation/1.0", "status": "prepared",
            "scientific_review": False, "overlay_sha256": overlay_sha, "base_sources_sha256": source_sha, "resolution": resolution_pin,
            "inventory_sha256": snapshot["inventory_sha256"], "sources_sha256": file_hash(output / "sources.json"), "row_coverage": coverage,
            "custody_files": [{"path": p, "sha256": h} for p, h in sorted(custody.files.items())],
            "statistics": snapshot["statistics"], "render_receipts": list(rasters.values())})
        return snapshot
    except BaseException as exc:
        write(output / "PREPARATION_FAILED.json", {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "source_rows": len(rows),
            "completed_row_ids": [r["source_row_id"] for r in coverage], "scientific_review": False})
        raise
    finally:
        if active_image is not None:
            active_image.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--base-sources", type=Path, required=True)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolutions", type=Path)
    args = parser.parse_args(argv)
    result = prepare(args.overlay, args.base_sources, args.asset_root, args.output, resolutions_path=args.resolutions)
    print(json.dumps(result["statistics"], indent=2))


if __name__ == "__main__":
    main()

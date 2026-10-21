"""Prepare and explicitly inspect one publication-bound Europe PMC ZIP attachment.

The public ZIP URL and exact member are distinct locators. Registration follows
an explicit content/version inspection; neither retrieval nor this helper certifies
scientific applicability or completeness. Active acquisition runtimes are unchanged.
"""

import argparse
from io import BytesIO
import json
from pathlib import Path
import re
import stat
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from pydantic import Field

from ...core.artifacts import StrictModel
from ..cached_documents import supplements
from ..document_manifest import MAX_BYTES, inspect_jats
from ..extraction_batch.storage import atomic_bytes, check_pins, sha, write
from ..source_originals import bind_originals
from ..workflow_extraction.models import digest
from ..workflow_extraction.sources import build_packet, load_packet
from .downloads import Download, download, public_url

PREPARATION = "supplement_archive_preparation/1.0"
MAX_ENTRIES = 4096
BOUNDARY = ("Publication association and exact-byte custody only. Explicit inspection is an "
            "authored content/version judgment, not automatic scientific certification. "
            "Fresh source-first extraction and independent review remain required.")


class Inspection(StrictModel):
    format_version: str = Field(pattern=r"^supplement_archive_inspection/1\.0$",
        description="Exact inspection-record format identifier.")
    preparation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$",
        description="SHA256 of the exact supplied preparation.json bytes; copy the frozen request binding.")
    method_id: str = Field(min_length=1,
        description="Exact method_id from the supplied preparation and source packet.")
    expected_variant: str = Field(min_length=1,
        description="Exact declared packet variant, including its version qualification; do not paraphrase.")
    decision: str = Field(pattern=r"^(consistent|uncertain|conflict)$",
        description="Your content/version judgment. Preserve uncertain or conflict honestly; structural validity does not require a positive judgment.")
    content_basis: str = Field(min_length=1,
        description="Explain publication/content association. Put commentary, page labels, additional quotations and relevant primary-source grounds here, separately from the single content_witness.")
    version_basis: str = Field(min_length=1,
        description="Explain applicability to the declared version and any uncertainty. Primary JATS/XML grounds or other version evidence belong here, not in content_witness.")
    content_witness: str = Field(min_length=1,
        description="ONE contiguous raw verbatim excerpt from the selected PDF text rendition BODY only. Preserve exact whitespace/newlines as JSON escapes. No labels, commentary, joined excerpts, ellipses, primary XML or provenance-header text. Additional evidence belongs in content_basis/version_basis. The host checks literal substring presence, not scientific entailment.")
    inspected_files: dict[str, str] = Field(
        description="Exact inspected file paths mapped to SHA256 strings. Must include both the selected PDF and its text rendition, with the frozen request's bindings.")


def _association(packet, source_id, url, member):
    endpoint = urlsplit(public_url(url))
    route = re.fullmatch(r"/europepmc/webservices/rest/(PMC[0-9]+)/supplementaryFiles", endpoint.path)
    if (endpoint.scheme != "https" or endpoint.netloc != "www.ebi.ac.uk" or not route
            or endpoint.query or endpoint.fragment):
        raise ValueError("Use the exact official HTTPS Europe PMC supplementaryFiles endpoint")
    selected = [source for source in packet["sources"] if source["source_id"] == source_id]
    if len(selected) != 1 or selected[0]["role"] != "primary_paper":
        raise ValueError("Select exactly one registered primary source")
    primary = selected[0]
    structure = inspect_jats(Path(primary["original_path"]).read_bytes())
    identity = structure["identity"]
    if identity != primary["document_identity"]:
        raise ValueError("Registered primary JATS identity changed")
    pmc_ids = set()
    for key in ("pmcid", "pmc"):
        for value in identity["identifiers"].get(key, []):
            match = re.fullmatch(r"(?:PMC)?([0-9]+)", value)
            if match is None:
                raise ValueError("Registered primary contains an invalid PMCID")
            pmc_ids.add("PMC" + match[1])
    if pmc_ids != {route[1]}:
        raise ValueError("Archive endpoint PMCID differs from the registered primary")
    pmids = identity["identifiers"].get("pmid", [])
    if len(pmids) != 1 or not re.fullmatch(r"[0-9]+", pmids[0]):
        raise ValueError("Existing supplement converter requires one registered primary PMID")
    # Exact basename association is conditional on this official publication-bound
    # endpoint, a unique full member name, and subsequent explicit content review.
    name = member.rsplit("/", 1)[-1]
    citations = [{"locator": row["locator"], "href": href}
                 for row in structure["supplements"] for href in row["hrefs"]
                 if unquote(urlsplit(href).path).rsplit("/", 1)[-1] == name]
    if not citations:
        raise ValueError("Registered primary does not cite this exact supplemental member name")
    return {"pmcid": route[1], "pmid": pmids[0], "primary_source_id": source_id,
            "primary_identity": identity, "primary_citations": citations,
            "archive_url": url, "archive_member": member,
            "basis": "Official PMCID-bound archive, unique exact member and primary supplemental citation; content/version inspection is separate"}


def _safe_name(name, directory=False):
    candidate = name[:-1] if directory and name.endswith("/") else name
    if (not candidate or candidate.startswith("/") or "\\" in candidate or ":" in candidate
            or any(ord(char) < 32 for char in candidate)
            or any(part in {"", ".", ".."} for part in candidate.split("/"))):
        raise ValueError("Unsafe ZIP member path")


def selected_pdf(raw, member):
    """Read one bounded member by its exact name; never extract archive paths."""
    if not raw or len(raw) > MAX_BYTES or not raw.startswith(b"PK"):
        raise ValueError("Empty, oversized or non-ZIP archive response")
    _safe_name(member)
    try:
        with ZipFile(BytesIO(raw)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ENTRIES:
                raise ValueError("ZIP member count exceeds supported bounds")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("Duplicate ZIP member names are ambiguous")
            expanded = 0
            for info in infos:
                _safe_name(info.filename, info.is_dir())
                kind = stat.S_IFMT(info.external_attr >> 16)
                if info.orig_filename != info.filename or kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ValueError("Unsafe ZIP member type or truncated name")
                if info.flag_bits & 1:
                    raise ValueError("Encrypted ZIP members are unsupported")
                expanded += info.file_size
                if info.file_size > MAX_BYTES or expanded > MAX_BYTES:
                    raise ValueError("Expanded ZIP exceeds the source size limit")
            matches = [info for info in infos if info.filename == member and not info.is_dir()]
            if len(matches) != 1:
                raise ValueError("ZIP lacks one exact unambiguous selected member")
            if sum(not info.is_dir() and info.filename.rsplit("/", 1)[-1] == member.rsplit("/", 1)[-1]
                   for info in infos) != 1:
                raise ValueError("Multiple ZIP members share the cited basename; association is ambiguous")
            info = matches[0]
            with archive.open(info) as handle:
                content = handle.read(MAX_BYTES + 1)  # EOF also checks CRC.
            if len(content) != info.file_size or len(content) > MAX_BYTES:
                raise ValueError("Truncated or oversized ZIP member")
            if not member.lower().endswith(".pdf") or not content.startswith(b"%PDF-"):
                raise ValueError("Selected member must contain an actual PDF")
            return content, {"name": member, "bytes": len(content), "crc32": info.CRC}
    except (BadZipFile, NotImplementedError, RuntimeError, EOFError, OSError) as exc:
        raise ValueError("Unreadable, truncated or CRC-invalid ZIP archive/member") from exc


def prepare(packet_path, primary_source_id, archive_url, member, output, *, fetch=download):
    """Retrieve, select and convert; leave pending until explicit inspection."""
    packet_path, output = Path(packet_path).resolve(), Path(output).resolve()
    packet = load_packet(packet_path)
    association = _association(packet, primary_source_id, archive_url, member)
    pins = {str(packet_path): sha(packet_path),
            **{source["original_path"]: source["original_sha256"] for source in packet["sources"]}}
    output.mkdir(parents=True, exist_ok=False)
    fetched = fetch(archive_url)
    if (not isinstance(fetched, Download) or not isinstance(fetched.content, bytes)
            or fetched.final_url != archive_url):
        raise ValueError("Archive retrieval must retain the exact official endpoint")
    # Preserve even an invalid response; it never becomes a registered source.
    archive_path = output / "supplementary_files.zip"
    atomic_bytes(archive_path, fetched.content)
    write(output / "retrieval.json", {"status": "downloaded", "requested_url": archive_url,
          "final_url": fetched.final_url, "content_type": fetched.content_type,
          "path": str(archive_path), "sha256": sha(archive_path),
          "retriever": "host anonymous public downloader; original bytes retained"})
    content, selection = selected_pdf(fetched.content, member)
    original = output / "supplements" / "selected.pdf"
    atomic_bytes(original, content)
    register = output / "supplements" / "acquisition.json"
    write(register, {"pmid": association["pmid"], "files": [{"status": "cached",
          "path": str(original), "sha256": sha(original), "url": archive_url,
          "archive_member": member}]})
    sources, receipts, leads = supplements(output, association["pmid"], output / "renditions")
    if len(sources) != 1 or len(receipts) != 1 or leads:
        raise ValueError("Selected PDF conversion failed: " + json.dumps(leads))
    source, receipt = sources[0], receipts[0]
    source["source_id"] = receipt["source_id"] = "epmc_supp_" + sha(original)[:16]
    source["title"] = member
    receipt["association"] = association
    receipt["intermediates"] = [{"path": str(archive_path), "sha256": sha(archive_path)}]
    write(output / "conversion_receipt.json", receipt)
    check_pins(pins)
    pins.update({str(path): sha(path) for path in output.rglob("*") if path.is_file()})
    result = {"format_version": PREPARATION, "status": "pending_content_and_version_inspection",
              "source_packet": {"path": str(packet_path), "sha256": sha(packet_path)},
              "source_packet_sha256": digest(packet), "method_id": packet["method_id"],
              "variant": packet["variant"], "association": association,
              "archive": {"path": str(archive_path), "sha256": sha(archive_path)},
              "member": {**selection, "path": str(original), "sha256": sha(original)},
              "source": source, "conversion": receipt, "files": pins,
              "scientific_resolution": False, "boundary": BOUNDARY}
    write(output / "preparation.json", result)
    return result


def _validated_inspection(preparation_path, inspection_path):
    """Replay read-only bindings and literal witness checks, without judging the decision."""
    preparation_path, inspection_path = (Path(path).resolve()
                                        for path in (preparation_path, inspection_path))
    prepared = json.loads(preparation_path.read_bytes())
    if prepared.get("format_version") != PREPARATION:
        raise ValueError("Expected supplement archive preparation")
    check_pins(prepared["files"])
    packet_path = Path(prepared["source_packet"]["path"])
    packet = load_packet(packet_path)
    if (sha(packet_path) != prepared["source_packet"]["sha256"]
            or digest(packet) != prepared["source_packet_sha256"]):
        raise ValueError("Preparation source packet changed")
    association = prepared["association"]
    if _association(packet, association["primary_source_id"], association["archive_url"],
                    association["archive_member"]) != association:
        raise ValueError("Preparation association does not replay")
    archive_path, original = Path(prepared["archive"]["path"]), Path(prepared["member"]["path"])
    receipt_path = preparation_path.parent / "conversion_receipt.json"
    receipt = prepared["conversion"]
    source = prepared["source"]
    required_pins = {str(archive_path): prepared["archive"]["sha256"],
                     str(original): prepared["member"]["sha256"],
                     source["path"]: receipt["rendition_sha256"]}
    if (not all(prepared["files"].get(path) == value for path, value in required_pins.items())
            or prepared["files"].get(str(receipt_path)) != sha(receipt_path)
            or json.loads(receipt_path.read_bytes()) != receipt
            or receipt["original"] != str(original)
            or receipt["original_sha256"] != prepared["member"]["sha256"]
            or receipt["rendition"] != source["path"]
            or receipt["source_id"] != source["source_id"]
            or source["source_id"] != "epmc_supp_" + sha(original)[:16]
            or source["title"] != association["archive_member"]
            or source["url"] != association["archive_url"] or source["role"] != "supplement"):
        raise ValueError("Preparation source/conversion custody does not replay")
    content, selection = selected_pdf(archive_path.read_bytes(), association["archive_member"])
    if (content != original.read_bytes() or selection != {key: prepared["member"][key]
            for key in ("name", "bytes", "crc32")}):
        raise ValueError("Archive-to-member extraction does not replay")
    inspection = Inspection.model_validate_json(inspection_path.read_bytes())
    if (inspection.preparation_sha256 != sha(preparation_path)
            or inspection.method_id != packet["method_id"]
            or inspection.expected_variant != packet["variant"]):
        raise ValueError("Content/version inspection is bound to another preparation or scope")
    if not inspection.content_basis.strip() or not inspection.version_basis.strip():
        raise ValueError("Explicit content and version inspection bases are required")
    rendition = Path(prepared["source"]["path"])
    required = {str(original): sha(original), str(rendition): sha(rendition)}
    if not all(inspection.inspected_files.get(path) == value for path, value in required.items()):
        raise ValueError("Inspection must pin both the selected PDF and its rendition")
    check_pins(inspection.inspected_files)
    text = rendition.read_text(encoding="utf-8").split("\n\n", 1)[-1]
    if not inspection.content_witness.strip() or inspection.content_witness not in text:
        raise ValueError("Inspection content witness is absent from the converted PDF body")
    return prepared, packet, inspection


def check_inspection(preparation_path, inspection_path):
    """Check an authored record without creating files, a packet or a positive judgment."""
    _, _, inspection = _validated_inspection(preparation_path, inspection_path)
    return {"format_version": "supplement_archive_inspection_check/1.0",
            "integrity_valid": True, "decision": inspection.decision,
            "registration_allowed": inspection.decision == "consistent",
            "witness_match": "raw_exact_contiguous_pdf_body",
            "scientific_resolution": False,
            "boundary": BOUNDARY + " Registration_allowed describes this inspection gate only; "
                        "registration must still validate its ledger inputs and fresh destination."}


def register(preparation_path, inspection_path, output, *, prior_ledger=None):
    """Register a fresh expanded packet after a separately authored inspection."""
    preparation_path, inspection_path, output = (Path(path).resolve()
                                               for path in (preparation_path, inspection_path, output))
    prepared, packet, inspection = _validated_inspection(preparation_path, inspection_path)
    if inspection.decision != "consistent":
        raise ValueError("Content/version inspection remains uncertain or conflicts; no registration")
    packet_path = Path(prepared["source_packet"]["path"])
    receipt_path = preparation_path.parent / "conversion_receipt.json"
    receipt, source = prepared["conversion"], prepared["source"]
    ledger, inherited = [], {}
    if prior_ledger is not None:
        prior_ledger = Path(prior_ledger).resolve()
        inherited = bind_originals(packet_path, prior_ledger)["input_files"]
        ledger = json.loads(prior_ledger.read_bytes())
    if any(row["source_id"] == source["source_id"] for row in packet["sources"]):
        raise ValueError("Selected supplement is already registered")
    options = {key: packet[key] for key in ("method_id", "title", "scope", "variant", "format_version")}
    if "rendition_version" in packet:
        options["rendition_version"] = packet["rendition_version"]
    descriptors = [{**{key: row[key] for key in ("source_id", "title", "url", "role")},
                    "path": row["original_path"]} for row in packet["sources"]]
    expanded = build_packet(**options, sources=[*descriptors, source])
    if expanded["sources"][:-1] != packet["sources"]:
        raise ValueError("Existing source records changed while adding the supplement")
    # The archiver follows conversion intermediates, not arbitrary source_receipts
    # mappings. Preserve the exact acquisition/inspection records even when only
    # this registration directory is selected for archival. Never rewrite the
    # earlier preparation conversion receipt or its already pinned ledger value.
    intermediates = {row["path"]: row["sha256"] for row in receipt.get("intermediates", [])}
    for path in (preparation_path, inspection_path, preparation_path.parent / "retrieval.json", receipt_path):
        intermediates[str(path)] = sha(path)
    intermediates.update({str(Path(path).resolve()): value
                          for path, value in inspection.inspected_files.items()})
    if prior_ledger is not None:
        intermediates[str(prior_ledger)] = sha(prior_ledger)
    check_pins(intermediates)
    registered_receipt = {**receipt, "intermediates": [
        {"path": path, "sha256": value} for path, value in sorted(intermediates.items())]}
    output.mkdir(parents=True, exist_ok=False)
    write(output / "packet.json", expanded)
    write(output / "conversion_receipt.json", registered_receipt)
    write(output / "originals_ledger.json", [*ledger, registered_receipt])
    bindings = bind_originals(output / "packet.json", output / "originals_ledger.json")
    write(output / "original_bindings.json", bindings)
    result = {"status": "registered_for_fresh_source_review", "scientific_resolution": False,
              "method_id": packet["method_id"], "packet": str(output / "packet.json"),
              "packet_sha256": sha(output / "packet.json"),
              "preparation": {"path": str(preparation_path), "sha256": sha(preparation_path)},
              "inspection": {"path": str(inspection_path), "sha256": sha(inspection_path),
                             "record": inspection.model_dump()},
              "original_bindings": {"path": str(output / "original_bindings.json"),
                                    "sha256": sha(output / "original_bindings.json")},
              "boundary": BOUNDARY}
    write(output / "registration.json", result)
    pins = {**prepared["files"], **inherited, **inspection.inspected_files,
            str(preparation_path): sha(preparation_path), str(inspection_path): sha(inspection_path)}
    check_pins(pins)
    pins.update({str(path): sha(path) for path in output.rglob("*") if path.is_file()})
    write(output / "source_receipts.json", {"files": pins, "scientific_resolution": False})
    load_packet(output / "packet.json")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("prepare", help="Download/convert one cited member; no registration")
    fetch.add_argument("--packet", type=Path, required=True)
    fetch.add_argument("--primary-source-id", required=True)
    fetch.add_argument("--archive-url", required=True)
    fetch.add_argument("--member", required=True)
    fetch.add_argument("--output", type=Path, required=True)
    admit = commands.add_parser("register", help="Require explicit content/version inspection")
    admit.add_argument("--preparation", type=Path, required=True)
    admit.add_argument("--inspection", type=Path, required=True)
    admit.add_argument("--prior-ledger", type=Path)
    admit.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("check-inspection", help="Validate exact bindings/witness without registration or writes")
    check.add_argument("--preparation", type=Path, required=True)
    check.add_argument("--inspection", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check-inspection":
            print(json.dumps(check_inspection(args.preparation, args.inspection)))
        else:
            result = (prepare(args.packet, args.primary_source_id, args.archive_url, args.member, args.output)
                      if args.command == "prepare" else register(args.preparation, args.inspection,
                          args.output, prior_ledger=args.prior_ledger))
            print(json.dumps({key: result[key] for key in ("status", "scientific_resolution")}))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        error = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        if args.command == "check-inspection":
            error.update(integrity_valid=False, registration_allowed=False)
        print(json.dumps(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

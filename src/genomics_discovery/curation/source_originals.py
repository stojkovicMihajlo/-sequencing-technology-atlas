"""Bind registered text renditions to cached originals, without guessing from titles.

The provenance ledger asserts how a rendition was produced. Byte checks verify
that assertion's inputs, not textual/visual equivalence or scientific entailment.
"""

import argparse
import json
import subprocess
from pathlib import Path

from .run_state import sha
from .workflow_extraction.models import digest
from .workflow_extraction.sources import load_packet

FORMAT = "workflow_source_originals/1.0"


def bind_originals(packet_path: Path, ledger_path: Path):
    packet_path, ledger_path = Path(packet_path).resolve(), Path(ledger_path).resolve()
    packet = load_packet(packet_path)
    ledger = json.loads(ledger_path.read_text())
    if not isinstance(ledger, list) or any(not isinstance(r, dict) for r in ledger):
        raise ValueError("Original provenance ledger must be a list of records")
    pins = {str(packet_path): sha(packet_path), str(ledger_path): sha(ledger_path)}
    bindings, unmapped = {}, []
    for source in packet["sources"]:
        matches = []
        rendition = Path(source["original_path"]).resolve()
        for index, row in enumerate(ledger):
            if (row.get("source_id") != source["source_id"]
                    or not row.get("rendition")
                    or Path(row["rendition"]).resolve() != rendition):
                continue
            if row.get("rendition_sha256") != source["original_sha256"]:
                raise ValueError("Provenance rendition digest disagrees with the registered source")
            original = Path(row["original"]).resolve()
            if not original.is_file() or sha(original) != row["original_sha256"]:
                raise ValueError("Cached original differs from the provenance ledger")
            item = {"source_id": source["source_id"], "original": str(original),
                    "original_sha256": row["original_sha256"], "rendition": str(rendition),
                    "rendition_sha256": row["rendition_sha256"],
                    "ledger_row": index, "parser": row.get("parser"),
                    "parser_version": row.get("parser_version"),
                    "provenance_boundary": row.get("boundary")}
            if not any((r["original"], r["original_sha256"]) ==
                       (item["original"], item["original_sha256"]) for r in matches):
                matches.append(item)
        if len(matches) > 1:
            raise ValueError("Ambiguous original association; reconcile the provenance ledger")
        if not matches:
            unmapped.append(source["source_id"])
            continue
        bindings[source["source_id"]] = matches[0]
        pins[matches[0]["original"]] = matches[0]["original_sha256"]
        pins[str(rendition)] = source["original_sha256"]
    result = {"format_version": FORMAT, "source_packet_sha256": digest(packet),
              "packet": str(packet_path), "ledger": str(ledger_path),
              "bindings": bindings, "unmapped_source_ids": unmapped, "input_files": pins,
              "boundary": "Original/rendition provenance only. Missing associations are "
                          "unmapped, not proof of unavailability. Inspect original layouts "
                          "when relevant. New image-derived facts require registered evidence "
                          "and fresh source review; a text rendition is not figure equivalence."}
    return {**result, "revision_id": digest(result)}


def load_originals(path):
    bound = json.loads(Path(path).read_text())
    if (bound.get("format_version") != FORMAT
            or bound.get("revision_id") != digest({k: v for k, v in bound.items()
                                                   if k != "revision_id"})):
        raise ValueError("Invalid or changed original binding artifact")
    if any(not Path(p).is_file() or sha(Path(p)) != expected
           for p, expected in bound["input_files"].items()):
        raise ValueError("Original, rendition, packet or provenance ledger changed")
    if bind_originals(Path(bound["packet"]), Path(bound["ledger"])) != bound:
        raise ValueError("Bindings do not replay from the exact packet and provenance ledger")
    return bound


def render_page(binding_path, source_id, page, output):
    bound = load_originals(binding_path)
    source = bound["bindings"][source_id]
    original, output = Path(source["original"]), Path(output).resolve()
    if type(page) is not int or page < 1:
        raise ValueError("PDF page must be a positive one-based integer")
    with original.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError("This renderer needs a PDF original; inspect other formats separately")
    output.mkdir(parents=True, exist_ok=False)
    prefix = output / "page"
    command = ["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-r", "140",
               "-png", str(original), str(prefix)]
    subprocess.run(command, check=True, capture_output=True, timeout=60)
    image = prefix.with_suffix(".png")
    if not image.is_file():
        raise ValueError("PDF renderer did not produce the requested page")
    load_originals(binding_path)  # Detect changes during rendering too.
    receipt = {"source_id": source_id, "page": page, "original": str(original),
               "original_sha256": source["original_sha256"], "image": str(image),
               "image_sha256": sha(image), "binding_revision_id": bound["revision_id"],
               "command": command, "boundary": "Rendered page for inspection, not a claim "
                                               "that an agent read or understood the image."}
    (output / "render_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def validate_inspection(inspection, bound, attempt):
    """Verify a claimed page's render receipt; this cannot prove visual understanding."""
    receipt_path = Path(inspection.render_receipt)
    if not receipt_path.is_absolute():
        receipt_path = attempt / receipt_path
    if not receipt_path.resolve().is_relative_to(attempt.resolve()):
        raise ValueError("Render receipt must belong to this auditor attempt")
    receipt = json.loads(receipt_path.read_text())
    binding = bound["bindings"][inspection.source_id]
    if (receipt.get("source_id"), receipt.get("page"), receipt.get("original_sha256"),
        receipt.get("binding_revision_id")) != (
            inspection.source_id, inspection.page, binding["original_sha256"],
            bound["revision_id"]):
        raise ValueError("Render receipt does not bind the requested original page")
    image = Path(receipt["image"]).resolve()
    if (not image.is_relative_to(attempt.resolve()) or not image.is_file()
            or sha(image) != receipt["image_sha256"]):
        raise ValueError("Rendered image is missing, changed or outside this attempt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    bind = sub.add_parser("bind")
    bind.add_argument("packet", type=Path)
    bind.add_argument("ledger", type=Path)
    bind.add_argument("output", type=Path)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("bindings", type=Path)
    inspect.add_argument("--source-id")
    render = sub.add_parser("render")
    render.add_argument("bindings", type=Path)
    render.add_argument("source_id")
    render.add_argument("page", type=int)
    render.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "bind":
        result = bind_originals(args.packet, args.ledger)
        with args.output.open("x") as out:
            out.write(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"bound_sources": len(result["bindings"]),
                          "unmapped_sources": result["unmapped_source_ids"]}))
    elif args.command == "inspect":
        result = load_originals(args.bindings)
        print(json.dumps(result["bindings"][args.source_id] if args.source_id else result,
                         indent=2))
    else:
        print(json.dumps(render_page(args.bindings, args.source_id, args.page, args.output),
                         indent=2))


if __name__ == "__main__":
    main()

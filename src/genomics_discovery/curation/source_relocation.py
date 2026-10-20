"""Register a copied source packet anew, verifying bytes and passages.

This does not relocate accepted runs, reviews, or graph publications. It creates
fresh source registration for fresh extraction; historical records stay intact.
"""

import argparse
import hashlib
import json
from pathlib import Path

from .workflow_extraction.sources import build_packet


def register_relocated_packet(packet_path: Path, *, old_root: Path,
                              new_root: Path, output: Path) -> dict:
    packet_path, output = Path(packet_path), Path(output)
    old_root, new_root = Path(old_root), Path(new_root)
    if not old_root.is_absolute() or not new_root.is_absolute():
        raise ValueError("Relocation roots must be explicit absolute paths")
    new_root = new_root.resolve(strict=True)
    if not new_root.is_dir():
        raise ValueError("Destination root must be a directory")
    if output.exists():
        raise FileExistsError(f"New registration already exists: {output}")
    raw = packet_path.read_bytes()
    original = json.loads(raw)
    sources, mappings = [], []
    for source in original["sources"]:
        previous = Path(source["original_path"])
        try:
            relative = previous.relative_to(old_root)
        except ValueError as exc:
            raise ValueError("A registered source is outside the declared old root") from exc
        relocated = (new_root / relative).resolve(strict=True)
        if not relocated.is_relative_to(new_root):
            raise ValueError("Relocated source escapes the declared new root")
        actual = hashlib.sha256(relocated.read_bytes()).hexdigest()
        if actual != source["original_sha256"]:
            raise ValueError(f"Copied source bytes differ: {source['source_id']}")
        sources.append({**{k: source[k] for k in ("source_id", "title", "url", "role")},
                        "path": str(relocated)})
        mappings.append({"source_id": source["source_id"], "old_path": str(previous),
                         "new_path": str(relocated), "sha256": actual})
    options = {k: original[k] for k in ("method_id", "title", "scope", "variant",
                                        "format_version")}
    if "rendition_version" in original:
        options["rendition_version"] = original["rendition_version"]
    packet = build_packet(**options, sources=sources)
    # Fresh rendition must reproduce the entire registered record, except paths.
    expected = {**original, "sources": [
        {**source, "original_path": mapping["new_path"]}
        for source, mapping in zip(original["sources"], mappings, strict=True)]}
    if packet != expected:
        raise ValueError("Copied source does not reproduce registered passages/metadata")
    content = (json.dumps(packet, indent=2, ensure_ascii=False) + "\n").encode()
    receipt = {"format_version": "source_relocation/1.0",
               "historical_packet": str(packet_path.resolve()),
               "historical_packet_sha256": hashlib.sha256(raw).hexdigest(),
               "old_root": str(old_root), "new_root": str(new_root),
               "packet": str(output.resolve() / "packet.json"),
               "packet_sha256": hashlib.sha256(content).hexdigest(),
               "sources": mappings,
               "boundary": "Verified copied bytes and fresh deterministic passages only. "
                           "No source-coverage, review, acceptance or run identity transfers."}
    output.mkdir(parents=True, exist_ok=False)
    (output / "packet.json").write_bytes(content)
    (output / "relocation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--new-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(register_relocated_packet(args.packet, old_root=args.old_root,
                     new_root=args.new_root, output=args.output), indent=2))


if __name__ == "__main__":
    main()

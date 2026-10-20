"""Archive one stopped benchmark phase and its explicitly declared source custody."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile

DENIED = {".ssh", ".aws", ".azure", ".git", ".codex", ".config", ".kube", ".gnupg",
          ".netrc", ".npmrc", ".pypirc", ".git-credentials", "auth.json", "credentials",
          "credentials.json", "secrets.json", "token.json", "id_rsa", "id_ed25519"}
RECEIPTS = ("originals_ledger.json", "conversion_receipt.json", "registration.json",
            "rendition_provenance.json", "text_rendition_provenance.json", "provenance.json")


def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def archive(experiment, output, project_root):
    root = Path(project_root).resolve()

    def checked(value, base=root):
        path = Path(value)
        path = path if path.is_absolute() else base / path
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Archive input escapes project: {path}")
        if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)):
            raise ValueError(f"Archive input is a symlink: {path}")
        if any(p.lower() in DENIED or p.lower() == ".env" or p.lower().startswith(".env.")
               for p in resolved.relative_to(root).parts):
            raise ValueError(f"Credential/auth path forbidden: {path}")
        return resolved

    phase, target = checked(experiment), checked(output)
    if not phase.is_dir() or target.is_relative_to(phase):
        raise ValueError("Archive output must be outside the experiment directory")
    if target.exists():
        raise FileExistsError(target)
    files, declared, documents, pending, gaps, receipts = {}, {}, [], {}, [], []

    def pin(value, expected, why, *, optional=False, base=root):
        path = checked(value, base)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"Missing/invalid SHA256 pin: {path}")
        if path in declared and declared[path] != expected:
            raise ValueError(f"Conflicting SHA256 pins: {path}")
        declared[path] = expected
        if not path.is_file():
            if optional:
                gaps.append({"path": str(path), "sha256": expected, "reason": "missing_binary_original"})
                return None
            raise ValueError(f"Missing declared source: {path}")
        if sha(path) != expected:
            raise ValueError(f"SHA256 changed: {path}")
        files.setdefault(path, {"sha256": expected, "why": []})["why"].append(why)
        return path

    def read_json(path):
        try:
            return json.loads(path.read_bytes())
        except (UnicodeError, json.JSONDecodeError):
            return None  # Incomplete native artifacts are still archived verbatim.

    def receipt_fields(row):
        if row.get("rendition"):
            return row.get("rendition"), row.get("rendition_sha256"), row.get("original"), row.get("original_sha256")
        if row.get("text_rendition_path"):
            return row.get("text_rendition_path"), row.get("text_rendition_sha256"), row.get("original_pdf_path"), row.get("original_pdf_sha256")
        return row.get("path"), row.get("sha256"), row.get("source_path"), row.get("source_sha256")

    def refers_to(value, base, source):
        # Compare before validating/reading: unrelated historical receipt rows are not inputs.
        return isinstance(value, str) and Path(os.path.abspath(base / value)) == source

    def packet_ref(path, expected):
        if path in pending and pending[path] != expected:
            raise ValueError(f"Conflicting SHA256 pins: {path}")
        pending[path] = expected

    for raw in sorted(phase.rglob("*")):
        path = checked(raw)
        if not path.is_file():
            continue
        pin(path, sha(path), "experiment_file")
        if path.suffix == ".json":
            documents.append((path, read_json(path)))
    for path, value in documents:
        if isinstance(value, dict) and value.get("format_version") in {"workflow_sources/1.0", "workflow_sources/1.1"}:
            packet_ref(path, files[path]["sha256"])
        for row in objects(value):
            if isinstance(row.get("packet"), str) and row.get("packet_sha256"):
                selected = checked(row["packet"], path.parent)
                packet_ref(selected, row["packet_sha256"])
            selected = row.get("source_packet")
            if isinstance(selected, dict) and selected.get("path") and selected.get("sha256"):
                selected_path = checked(selected["path"], path.parent)
                packet_ref(selected_path, selected["sha256"])
            if receipt_fields(row)[2]:
                receipts.append((path, row))
    selected_sources = {}
    for path, expected in pending.items():
        pin(path, expected, "declared_source_packet")
        packet = read_json(path)
        if not isinstance(packet, dict) or packet.get("format_version") not in {"workflow_sources/1.0", "workflow_sources/1.1"}:
            raise ValueError(f"Declared packet is not workflow_sources: {path}")
        for row in packet["sources"]:
            source = pin(row["original_path"], row["original_sha256"], f"packet_source:{path}")
            selected_sources[source] = row["original_sha256"]
    for source, expected in selected_sources.items():
        local_receipts = list(receipts)
        candidates = (directory / name for directory in (source.parent, source.parent.parent)
                      if directory.is_relative_to(root) for name in RECEIPTS)
        for candidate in candidates:
            candidate = checked(candidate)
            if candidate.is_file() and candidate not in files:
                value = read_json(candidate)
                for row in objects(value):
                    if refers_to(receipt_fields(row)[0], candidate.parent, source):
                        pin(candidate, sha(candidate), f"conversion_receipt:{source}")
                        local_receipts.append((candidate, row))
        with source.open("rb") as handle:
            header = handle.read(4096).decode("utf-8", errors="replace")
        match = re.match(r"\[Rendition provenance, not publication content\]\r?\nOriginal cached file: (.+)\r?\nSHA256: ([0-9a-f]{64})", header)
        found = False
        if match:
            pin(match[1].strip(), match[2], f"rendition_header:{source}", optional=True)
            found = True
        for receipt, row in local_receipts:
            rendition, rendition_hash, original, original_hash = receipt_fields(row)
            if not refers_to(rendition, receipt.parent, source):
                continue
            checked(rendition, receipt.parent)
            if rendition_hash != expected:
                raise ValueError(f"Rendition receipt SHA256 conflict: {source}")
            pin(original, original_hash, f"conversion_original:{receipt}", optional=True, base=receipt.parent)
            for intermediate in row.get("intermediates", []):
                pin(intermediate["path"], intermediate["sha256"], f"conversion_intermediate:{receipt}", optional=True, base=receipt.parent)
            found = True
        if not found:
            gaps.append({"path": str(source), "sha256": expected, "reason": "binary_provenance_not_declared_or_located"})
    members, inherited = {}, {}
    for path, record in sorted(files.items()):
        member = "phase/" + str(path.relative_to(phase)) if path.is_relative_to(phase) else inherited.setdefault(
            record["sha256"], "inherited_inputs/" + record["sha256"] + "/" + path.name)
        item = members.setdefault(member, {"sha256": record["sha256"], "original_paths": [], "why": []})
        item["original_paths"].append(str(path))
        item["why"] = sorted(set(item["why"] + record["why"]))
    manifest = {"format_version": "benchmark_archive/1.0", "experiment": str(phase),
        "members": members, "provenance_gaps": gaps,
        "status": "incomplete_binary_provenance" if gaps else "declared_inputs_preserved",
        "boundary": "Exact available phase/source bytes; not scientific source completeness or successful model execution."}
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".partial-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as destination, tarfile.open(fileobj=destination, mode="w:gz" if target.suffix in {".gz", ".tgz"} else "w") as tar:
            for member, record in members.items():
                path = Path(record["original_paths"][0])
                if sha(path) != record["sha256"]:
                    raise ValueError(f"SHA256 changed before archive: {path}")
                tar.add(path, arcname=member, recursive=False)
            data = json.dumps(manifest, indent=2).encode() + b"\n"
            info = tarfile.TarInfo("archive_manifest.json"); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for path, record in files.items():
            if sha(path) != record["sha256"]:
                raise ValueError(f"SHA256 changed during archive: {path}")
        with tarfile.open(temporary, "r:*") as tar:
            for member, record in members.items():
                with tar.extractfile(member) as handle:
                    if hashlib.file_digest(handle, "sha256").hexdigest() != record["sha256"]:
                        raise ValueError(f"Archived bytes differ from SHA256 pin: {member}")
        os.link(temporary, target)  # Exclusive publication; an existing archive is never replaced.
    finally:
        Path(temporary).unlink(missing_ok=True)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)
    result = archive(args.experiment, args.output, args.project_root)
    print(json.dumps({"archive": str(args.output), "status": result["status"], "members": len(result["members"])}))


if __name__ == "__main__":
    main()

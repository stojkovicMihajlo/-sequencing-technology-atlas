"""Build a read-only review site from a completed audit, without model calls.

This is a display export, not graph admission or scientific method consolidation.
The original candidate and review stay unchanged, including for rejected records.
"""

from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import html
import json
from pathlib import Path
import re
import shutil


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read(path: Path):
    return json.loads(path.read_text())


def write(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def standalone(record: dict, output: Path) -> None:
    """Render a saved workflow directly, with inline data and no local server."""
    assets = Path(__file__).parent
    data = json.dumps(record, ensure_ascii=False).replace('<', '\\u003c')
    style = (assets/'style.css').read_text()
    script = (assets/'app.js').read_text().replace('</script', '<\\/script')
    title = html.escape(record['title'])
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>{style}</style><header><div><h1>{title}</h1>
<p>Saved workflow inspection. Click a State, Operation or Flow to read its exact fields and evidence.</p>
<p>Supported means the recorded model review admitted this scope; it is not experimental validation.</p>
</div><a href="../../index.html">Technology atlas</a></header>
<main style="grid-template-columns:minmax(0,1fr)"><article id="detail" tabindex="-1"></article></main>
<script id="record-data" type="application/json">{data}</script><script>{script}</script></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page)


def refresh_saved_view(source: Path, output: Path) -> dict:
    """Publish current display code around an unchanged embedded scientific record."""
    if output.exists() or source.resolve() == output.resolve():
        raise FileExistsError("Use a new output; preserve the prior inspection page")
    page = source.read_text()
    pattern = r'<script id=[\'\"]record-data[\'\"][^>]*>(.*?)</script>'
    records = re.findall(pattern, page, re.S)
    if len(records) != 1:
        raise ValueError("Expected exactly one embedded workflow record")
    json.loads(records[0])
    scripts = list(re.finditer(r'<script>(.*?)</script>', page, re.S))
    scripts = [m for m in scripts if 'function drawGraph(' in m.group(1)]
    if len(scripts) != 1:
        raise ValueError("Expected exactly one known workflow viewer")
    assets = Path(__file__).parent
    script = (assets / 'app.js').read_text().replace('</script', '<\\/script')
    match = scripts[0]
    page = page[:match.start()] + '<script>' + script + '</script>' + page[match.end():]
    page, count = re.subn(r'<style>.*?</style>', lambda _: '<style>' +
                         (assets / 'style.css').read_text() + '</style>', page, count=1, flags=re.S)
    if count != 1 or re.findall(pattern, page, re.S) != records:
        raise ValueError("Display update changed or lost the embedded workflow record")
    # Keep relative links valid: this refresh is a sibling of the existing page.
    if output.parent.resolve() != source.parent.resolve():
        raise ValueError("Keep refreshed view beside its source so relative links remain valid")
    output.write_text(page)
    receipt = {"source": str(source), "source_sha256": sha(source),
               "output": str(output), "output_sha256": sha(output),
               "embedded_record_sha256": hashlib.sha256(records[0].encode()).hexdigest(),
               "embedded_record_bytes_unchanged": True,
               "viewer_files": {n: sha(assets / n) for n in ('app.js', 'style.css')}}
    write(output.with_suffix('.view.json'), receipt)
    return receipt


def build(audit: Path, output: Path, *, comparison: Path | None = None) -> dict:
    """Verify archived receipts, then expose every occurrence with its current status."""
    archive = audit / "records.jsonl.gz"
    status = read(audit / "AUDIT_STATUS.json")
    expected = status["raw_audit_receipts"][archive.name]
    if sha(archive) != expected["sha256"] or archive.stat().st_size != expected["bytes"]:
        raise ValueError("Archived records differ from the audit receipt")
    publication = read(audit / "GRAPH_PUBLICATION.json")
    with gzip.open(archive, "rt") as stream:
        rows = [json.loads(line) for line in stream]
    by_id = {row["record_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("Duplicate source record IDs; cannot safely display distinct occurrences")
    overrides = {}
    for finding in publication["new_admission_failures"]:
        ident = finding["record_id"]
        if ident in overrides or ident not in by_id:
            raise ValueError("Duplicate or unmatched revalidation finding")
        if by_id[ident]["status"] != finding["baseline_status"]:
            raise ValueError("Revalidation finding disagrees with archived baseline status")
        overrides[ident] = finding
    counts = Counter(overrides.get(r["record_id"], {}).get("current_status", r["status"]) for r in rows)
    if dict(counts) != publication["counts"] or len(rows) != publication["selected_source_occurrences"]:
        raise ValueError("Review inventory does not reproduce publication counts")

    # An export is immutable: use a new destination instead of leaving stale records.
    if output.exists():
        raise FileExistsError("Choose a new output directory; existing review exports are preserved")
    output.mkdir(parents=True)
    (output / "records").mkdir()
    index = []
    for row in rows:
        ident = row["record_id"]
        finding = overrides.get(ident, {})
        current_status = finding.get("current_status", row["status"])
        candidate = row.get("candidate") or {}
        filename = hashlib.sha256(ident.encode()).hexdigest()[:24] + ".json"
        item = {
            "record_id": ident, "title": row["title"], "batch": row["batch"],
            "status": current_status, "baseline_status": row["status"],
            "reason": finding.get("reason") or row.get("reason") or "",
            "method_names": candidate.get("method_names", []),
            "operations": len(candidate.get("operations", [])),
            "states": len(candidate.get("states", [])),
            "flows": len(candidate.get("flows", [])),
            "corrections": len(candidate.get("corrections", [])),
            "unknowns": len(candidate.get("unknowns", [])),
            "category": row["card"].get("source_category_path", []),
            "file": "records/" + filename,
        }
        detail = {
            **item, "candidate": row.get("candidate"), "review": row.get("review"),
            "sources": row.get("sources", {}), "card": row["card"],
            "research_questions": row.get("research_questions", []),
            "flags": row.get("flags", []), "errors": row.get("errors", []),
            "attempt_directory": row["attempt_directory"],
            "revalidation_finding": finding or None,
        }
        write(output / item["file"], detail)
        index.append(item)
    pins = {name: sha(audit / name) for name in (
        "records.jsonl.gz", "AUDIT_STATUS.json", "GRAPH_PUBLICATION.json")}
    manifest = {
        "format_version": "illumina_review_site/1.0", "counts": dict(counts),
        "source_occurrences": len(rows), "unique_method_count": None,
        "input_sha256": pins, "native_model_calls": 0,
        "boundary": "Read-only display of archived extractions and later admission decisions. "
                    "Source occurrences are not distinct methods. No new scientific review, "
                    "canonical identity, historical edge or compatibility claim is created.",
        "comparison": None,
    }
    if comparison:
        # Keep this separate: these exact workflows predate the November batch.
        target = output / "comparison"
        target.mkdir()
        files = ["comparison.html", "RESULTS.json", "integrity_check.json", "analysis.json", "review.json"]
        for name in files:
            shutil.copyfile(comparison / name, target / name)
        manifest["comparison"] = {
            "url": "comparison/comparison.html", "files": {n: sha(comparison / n) for n in files},
            "boundary": "Earlier ATAC-seq–ChIPmentation pilot; unchanged source graphs. "
                        "Not automatically attached to the November extraction records.",
        }
    write(output / "index.json", {"manifest": manifest, "records": index})
    write(output / "manifest.json", manifest)
    for name in ("index.html", "app.js", "style.css"):
        shutil.copyfile(Path(__file__).with_name(name), output / name)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--audit", type=Path)
    inputs.add_argument("--refresh-from", type=Path, help="Existing standalone HTML; its record stays byte-for-byte unchanged")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison", type=Path)
    args = parser.parse_args()
    if args.refresh_from:
        print(json.dumps(refresh_saved_view(args.refresh_from, args.output), indent=2))
        return
    print(json.dumps(build(args.audit, args.output, comparison=args.comparison), indent=2))


if __name__ == "__main__":
    main()

"""Preserve review-policy filename pins for the earlier profile extraction format."""

import hashlib
from pathlib import Path


def implementation_hashes() -> dict[str, str]:
    legacy = Path(__file__).resolve().parent
    package = legacy.parent
    paths = [p for p in legacy.rglob("*.py") if p.name != "__init__.py"]
    paths.extend(package / name for name in (
        "execution/codex_worker.py", "execution/state.py", "execution/accepted_transport.py",
        "sources/document_manifest.py", "sources/prepared_sources.py", "sources/originals.py",
        "sources/search.py", "protocols/contracts.py", "core/artifacts.py",
    ))
    result = {}
    for path in sorted(paths):
        if path.name in result:
            raise ValueError("Ambiguous legacy implementation filename: " + path.name)
        result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result

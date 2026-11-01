"""Files whose bytes are frozen by detailed protocol extraction and batch plans."""

from pathlib import Path


def implementation_files() -> list[Path]:
    extraction = Path(__file__).resolve().parent
    package = extraction.parents[1]
    dependencies = (
        "execution/codex_worker.py",
        "execution/state.py",
        "sources/document_manifest.py",
        "sources/evidence_views.py",
        "sources/packets.py",
        "sources/jats_text.py",
        "legacy/review/validation.py",
        "core/artifacts.py",
    )
    return sorted({*extraction.glob("*.py"), *(package / name for name in dependencies)})

"""Provider-neutral paper discovery and located evidence retrieval.

Search discovers works; read_more selects passages for a follow-up question. The
record reports what text was actually available, rather than implying full-text
access merely because a paper was found.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Protocol

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import SourceRef, StrictModel

# A transport/prompt budget, not a proxy for scientific query quality. Concision
# remains guidance; long method names and scoped comparisons can exceed 180 chars.
MAX_SEARCH_QUERY_CHARS = 1000


class LiteratureRecord(StrictModel):
    source_id: str
    title: str
    doi: str | None = None
    year: str | None = None
    first_publication_date: str | None = None
    url: str
    access_level: str
    # Discovery metadata is a lead, not evidence. No fabricated excerpt is needed
    # for a discovered paper whose text has not yet been acquired.
    source_refs: tuple[SourceRef, ...] = ()
    retrieval_source: str | None = None
    retrieved_via: tuple[str, ...] = ()
    retrieval_warnings: tuple[str, ...] = ()
    # Persist discovery routes across checkpoints, including papers not yet read.
    # These URLs remain unverified leads until text is independently fetched.
    full_text_urls: tuple[str, ...] = ()


class PaperSearcher(Protocol):
    async def search(self, query: str, *, limit: int = 4) -> list[LiteratureRecord]: ...


class EvidenceRetriever(PaperSearcher, Protocol):
    def can_read_more(self, record: LiteratureRecord) -> bool: ...

    async def read_more(
        self, record: LiteratureRecord, query: str, *, limit: int = 4
    ) -> tuple[SourceRef, ...]: ...


class RetrievalSourceError(RuntimeError):
    """A named backend failed; its absence must not be mistaken for a negative search."""


class DocumentAccessError(ValueError):
    """An access screen was delivered instead of the requested source document."""


def validate_document_content(body: bytes, *, suffix: str) -> None:
    """Reject recognizable access screens before indexing them under a paper title.

    HTTP 200 is not evidence that article text was delivered. Deliberately inspect
    document-level indicators; a research article mentioning CAPTCHA is still valid.
    PNG/JPEG originals must have the corresponding signature. This does not
    verify complete image decoding, scientific relevance or document identity.
    """
    suffix = suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg"}:
        signature = b"\x89PNG\r\n\x1a\n" if suffix == ".png" else b"\xff\xd8\xff"
        if not body.startswith(signature):
            raise ValueError("Source image bytes do not match the declared format")
        return
    if suffix not in {".html", ".htm", ".xhtml"}:
        return
    text = body.decode("utf-8", errors="replace")
    head = re.split(r"</head\s*>", text, maxsplit=1, flags=re.I)[0]
    match = re.search(r"<title\b[^>]*>(.*?)</title\s*>", head, flags=re.I | re.S)
    title = (
        " ".join(html.unescape(re.sub(r"<[^>]+>", " ", match[1])).split()).casefold()
        if match
        else ""
    )
    blocked_titles = {
        "access denied",
        "forbidden",
        "just a moment",
        "checking your browser",
        "verify you are human",
        "security check",
        "robot check",
        "captcha",
        "recaptcha",
        "cookies must be enabled",
        "enable cookies",
        "cookies required",
        "preparing to download",
    }
    challenge_base = re.search(
        r"<base\b[^>]*href\s*=\s*[\"']https://(?:www\.)?google\.com/recaptcha/challengepage/",
        head,
        flags=re.I,
    )
    # Some access screens keep the domain as their title and put the warning
    # inside a dedicated cookie-required panel instead. Inspect its heading,
    # rather than rejecting an article that happens to discuss cookies.
    heading_match = re.search(r"<h1\b[^>]*>(.*?)</h1\s*>", text, flags=re.I | re.S)
    heading = (
        " ".join(html.unescape(re.sub(r"<[^>]+>", " ", heading_match[1])).split()).casefold()
        if heading_match
        else ""
    )
    cookie_screen = heading.rstrip(".!… ") in {
        "cookies must be enabled", "enable cookies", "cookies required"
    } and re.search(r"cookies?-required(?:-message)?", text, flags=re.I)
    # Some widget-only challenges have no title and use a lower-level heading.
    # Require the access message plus widget, a short visible body and absence
    # of article markup. Do not reject substantive articles with CAPTCHA scripts.
    visible = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", text, flags=re.I | re.S)
    visible = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", visible)).split())
    challenge_heading = any(
        re.search(r"protecting\b.*\bfrom bots\b|verify you are human|checking your browser",
                  re.sub(r"<[^>]+>", " ", value), flags=re.I)
        for value in re.findall(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]\s*>", text, flags=re.I | re.S)
    )
    widget_screen = (challenge_heading and len(visible) < 2000
                     and re.search(r"turnstile\.render|challenges\.cloudflare\.com/turnstile",
                                   text, flags=re.I)
                     and not re.search(r"<article\b", text, flags=re.I))
    if (title.rstrip(".!… ") in blocked_titles or challenge_base or cookie_screen
            or widget_screen):
        raise DocumentAccessError("Access challenge received instead of document text")


def _work_keys(record: LiteratureRecord) -> tuple[tuple[str, str], ...]:
    """Match a work by any shared persistent identifier, never by title alone."""
    keys = [("source_id", record.source_id)]
    if record.doi:
        keys.append(("doi", record.doi.strip().lower().removeprefix("https://doi.org/")))
    return tuple(keys)


class FederatedRetriever:
    """Search named backends fairly and route deep reads to the originating backend."""

    def __init__(self, sources: Mapping[str, EvidenceRetriever]) -> None:
        if not sources or any(not name for name in sources):
            raise ValueError("At least one named retrieval source is required")
        self.sources = dict(sources)
        self._routes: dict[tuple[str, str], list[tuple[str, LiteratureRecord]]] = {}

    async def search(self, query: str, *, limit: int = 4) -> list[LiteratureRecord]:
        if not query.strip() or not 1 <= limit <= 20:
            raise ValueError("Search needs a nonempty query and limit from 1 to 20")
        names = tuple(self.sources)

        # A backend error must remain visible: a partial search cannot establish absence.
        async def search_one(name: str) -> list[LiteratureRecord]:
            try:
                return await self.sources[name].search(query, limit=limit)
            except Exception as error:
                raise RetrievalSourceError(f"Literature source {name!r} failed") from error

        outcomes = await asyncio.gather(
            *(search_one(name) for name in names), return_exceptions=True
        )
        warnings = tuple(str(value) for value in outcomes if isinstance(value, Exception))
        batches = [value if isinstance(value, list) else [] for value in outcomes]
        if warnings and not any(batches):
            raise RetrievalSourceError("; ".join(warnings))
        for name, batch in zip(names, batches, strict=True):
            for record in batch:
                for key in _work_keys(record):
                    routes = self._routes.setdefault(key, [])
                    if not any(n == name and r.source_id == record.source_id for n, r in routes):
                        routes.append((name, record))
        selected: list[LiteratureRecord] = []
        positions: dict[tuple[str, str], int] = {}
        for rank in range(max(map(len, batches), default=0)):
            for name, batch in zip(names, batches, strict=True):
                if rank >= len(batch):
                    continue
                record = batch[rank]
                keys = _work_keys(record)
                match = next((positions[key] for key in keys if key in positions), None)
                if match is not None:
                    index = match
                    existing = selected[index]
                    via = tuple(dict.fromkeys((*existing.retrieved_via, name)))
                    richer = (
                        record if len(record.source_refs) > len(existing.source_refs) else existing
                    )
                    selected[index] = richer.model_copy(
                        update={
                            "retrieval_source": name
                            if richer is record
                            else existing.retrieval_source,
                            "retrieved_via": via,
                            "full_text_urls": tuple(
                                dict.fromkeys((*existing.full_text_urls, *record.full_text_urls))
                            ),
                        }
                    )
                    for key in keys:
                        positions[key] = index
                    continue
                if len(selected) >= limit:
                    continue
                for key in keys:
                    positions[key] = len(selected)
                selected.append(
                    record.model_copy(update={"retrieval_source": name, "retrieved_via": (name,)})
                )
        return [record.model_copy(update={"retrieval_warnings": warnings}) for record in selected]

    def _read_routes(self, record: LiteratureRecord) -> list[tuple[str, LiteratureRecord]]:
        routes = [(record.retrieval_source or "", record)]
        for key in _work_keys(record):
            routes.extend(self._routes.get(key, ()))
        # Other backends can explicitly support this record (e.g. its DOI URL).
        routes.extend((name, record) for name in self.sources)
        unique = {}
        for name, candidate in routes:
            backend = self.sources.get(name)
            if backend is not None and backend.can_read_more(candidate):
                unique.setdefault((name, candidate.source_id), (name, candidate))
        return list(unique.values())

    def can_read_more(self, record: LiteratureRecord) -> bool:
        return bool(self._read_routes(record))

    async def read_more(
        self, record: LiteratureRecord, query: str, *, limit: int = 4
    ) -> tuple[SourceRef, ...]:
        errors = []
        for name, candidate in self._read_routes(record):
            try:
                passages = await self.sources[name].read_more(candidate, query, limit=limit)
            except Exception as error:
                errors.append(f"{name}: {type(error).__name__}")
                continue
            if passages:
                return passages
        if errors:
            raise RetrievalSourceError("All accessible routes failed: " + "; ".join(errors))
        return ()


class LocalDocument(StrictModel):
    """A pre-extracted paper or protocol; every passage has a stable locator."""

    source_id: str
    title: str
    doi: str | None = None
    year: str | None = None
    first_publication_date: str | None = None
    url: str
    source_refs: tuple[SourceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def located_passages(self) -> LocalDocument:
        if any(
            ref.source_id != self.source_id or not ref.locator or not ref.excerpt
            for ref in self.source_refs
        ):
            raise ValueError("Local passages need the document's source_id, locator, and text")
        return self


class LocalCorpus(StrictModel):
    documents: tuple[LocalDocument, ...]


def _score(query: str, text: str) -> int:
    terms = set(re.findall(r"[a-z0-9]{3,}", query.casefold()))
    words = set(re.findall(r"[a-z0-9]{3,}", text.casefold()))
    return len(terms & words)


def _passage_score(query: str, ref: SourceRef) -> int:
    return _score(query, f"{ref.locator or ''} {ref.excerpt or ''}")


class LocalCorpusRetriever:
    """Search a curated corpus of located passages without an external service."""

    def __init__(self, corpus: LocalCorpus, *, as_of: date | None = None) -> None:
        self.documents = {document.source_id: document for document in corpus.documents}
        if len(self.documents) != len(corpus.documents):
            raise ValueError("Local corpus source_id values must be unique")
        self.as_of = as_of

    @classmethod
    def from_json(cls, path: Path, *, as_of: date | None = None) -> LocalCorpusRetriever:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(LocalCorpus.model_validate(payload), as_of=as_of)

    def _eligible(self, document: LocalDocument) -> bool:
        if self.as_of is None:
            return True
        if document.first_publication_date:
            return date.fromisoformat(document.first_publication_date) <= self.as_of
        return bool(document.year and int(document.year) <= self.as_of.year)

    async def search(self, query: str, *, limit: int = 4) -> list[LiteratureRecord]:
        if not query.strip() or not 1 <= limit <= 20:
            raise ValueError("Search needs a nonempty query and limit from 1 to 20")
        ranked = []
        for document in self.documents.values():
            if not self._eligible(document):
                continue
            passages = sorted(
                document.source_refs,
                key=lambda ref: -_passage_score(query, ref),
            )
            score = 2 * _score(query, document.title) + _passage_score(query, passages[0])
            if score:
                ranked.append((score, document, passages))
        ranked.sort(key=lambda item: (-item[0], item[1].source_id))
        return [
            LiteratureRecord(
                source_id=document.source_id,
                title=document.title,
                doi=document.doi,
                year=document.year,
                first_publication_date=document.first_publication_date,
                url=document.url,
                access_level="local_located_passages",
                source_refs=tuple(passages[:4]),
                retrieval_source="local_corpus",
                retrieved_via=("local_corpus",),
            )
            for _, document, passages in ranked[:limit]
        ]

    def can_read_more(self, record: LiteratureRecord) -> bool:
        return record.source_id in self.documents

    async def read_more(
        self, record: LiteratureRecord, query: str, *, limit: int = 4
    ) -> tuple[SourceRef, ...]:
        if not query.strip() or not 1 <= limit <= 8:
            raise ValueError("Deep read needs a nonempty query and limit from 1 to 8")
        document = self.documents.get(record.source_id)
        if document is None:
            return ()
        ranked = sorted(
            document.source_refs,
            key=lambda ref: -_passage_score(query, ref),
        )
        return tuple(ref for ref in ranked if _passage_score(query, ref))[:limit]

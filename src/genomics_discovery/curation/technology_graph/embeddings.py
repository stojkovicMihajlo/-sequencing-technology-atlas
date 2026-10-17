"""Optional local embeddings with content/model-bound caching; never evidence."""

import json
import math
import sqlite3
from importlib.metadata import version
from pathlib import Path

from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.models import digest

MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class LocalEmbedder:
    def __init__(self, directory: Path, *, download=False):
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError("Install the optional semantic extra: uv sync --extra semantic") from exc
        self.model = TextEmbedding(model_name=MODEL, cache_dir=str(directory), threads=2,
                                   providers=["CPUExecutionProvider"], local_files_only=not download)
        # Version-bound adapter to FastEmbed's loaded model; fingerprint actual weights
        # and tokenizer instead of assuming a remote model name pins their contents.
        model_dir = Path(self.model.model._model_dir)
        self.metadata = {
            "model": MODEL, "fastembed_version": version("fastembed"),
            "onnxruntime_version": version("onnxruntime"),
            "artifacts": {str(p.relative_to(model_dir)): sha(p) for p in sorted(model_dir.rglob("*"))
                          if p.is_file() and ".cache" not in p.parts},
            "encoding": "symmetric document embeddings; exact text partition below tokenizer limit; "
                        "token-count-weighted mean of chunk vectors; L2 cosine; partition/1.0",
            "truncation": self.model.model.tokenizer.truncation,
        }
        from tokenizers import Tokenizer
        self.counter = Tokenizer.from_str(self.model.model.tokenizer.to_str())
        self.counter.no_truncation()

    def encode(self, texts):
        import numpy as np
        groups = [self._segments(text) for text in texts]
        chunks = [chunk for group in groups for chunk in group]
        values = iter(self.model.embed(chunks, batch_size=64))
        result = []
        for group in groups:
            weights = [len(self.counter.encode(chunk).ids) for chunk in group]
            vector = np.average([next(values) for _ in group], axis=0, weights=weights)
            norm = np.linalg.norm(vector)
            result.append((vector / norm if norm else vector).tolist())
        return result

    def _segments(self, text):
        """Partition all characters, checking the actual tokenizer including special tokens.

        Segments are retrieval units only, not new scientific operations or claims.
        No overlap or tail dropping; recursive whitespace cuts also handle long IDs.
        """
        limit = (self.metadata["truncation"] or {}).get("max_length")
        if not limit or len(self.counter.encode(text).ids) <= limit:
            return [text]
        middle = len(text) // 2
        if not middle:
            raise ValueError("A character cannot fit within the embedding token limit")
        boundary = text.rfind(" ", 0, middle + 1)
        boundary = boundary + 1 if boundary > 0 else middle
        return self._segments(text[:boundary]) + self._segments(text[boundary:])

    def segmentations(self, texts):
        return [{"text_sha256": digest(text), "token_count": len(self.counter.encode(text).ids),
                 "max_length": self.metadata["truncation"]["max_length"],
                 "segments": len(parts)} for text in texts if len(parts := self._segments(text)) > 1]


class EmbeddingCache:
    """The backend is injectable so cache integrity can be tested without model downloads."""

    def __init__(self, path: Path, backend):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.backend = path, backend
        self.metadata = {"cache_format": "text_vectors/1.0", **backend.metadata}
        self.namespace = digest(self.metadata)
        self.last_stats = {}

    def encode(self, texts):
        unique = list(dict.fromkeys(texts))
        vectors, missing = {}, []
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, value TEXT)")
            for text in unique:
                key = digest([self.namespace, text])
                row = db.execute("SELECT value FROM vectors WHERE key = ?", (key,)).fetchone()
                if row is None:
                    missing.append(text)
                else:
                    payload = json.loads(row[0])
                    if payload["sha256"] != digest(payload["vector"]):
                        raise ValueError("Embedding cache vector checksum mismatch")
                    vectors[text] = payload["vector"]
            produced = self.backend.encode(missing) if missing else []
            if len(produced) != len(missing):
                raise ValueError("Embedding backend returned the wrong number of vectors")
            vectors.update(zip(missing, produced))
            dimensions = {len(v) for v in vectors.values()}
            if len(dimensions) > 1 or (dimensions and min(dimensions) < 1) or any(
                not all(isinstance(x, (int, float)) and math.isfinite(x) for x in v)
                or sum(x*x for x in v) == 0 for v in vectors.values()
            ):
                raise ValueError("Invalid or inconsistent embedding vectors")
            for text in missing:
                vector = vectors[text]
                db.execute("INSERT OR REPLACE INTO vectors VALUES (?, ?)",
                           (digest([self.namespace, text]),
                            json.dumps({"vector": vector, "sha256": digest(vector)})))
        self.last_stats = {"unique_texts": len(unique), "cache_hits": len(unique) - len(missing),
                           "computed": len(missing)}
        return [vectors[text] for text in texts]

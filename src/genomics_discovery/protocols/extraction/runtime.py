"""Compatibility import for older callers of runtime.execute.

The extraction stages and acceptance gates live in extraction_pipeline.py.
Frozen releases preserve their original runtime implementation.
"""

from genomics_discovery.protocols.extraction.pipeline import execute

__all__ = ["execute"]

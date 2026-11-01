"""Small, versioned inventory and immutable execution configuration."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel

Text = Annotated[str, Field(min_length=1)]
Count = Annotated[int, Field(strict=True, ge=0)]


class Job(StrictModel):
    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    status: Literal["ready", "missing_sources"]
    packet: Path | None = None
    reason: Text | None = None
    next_evidence: Text | None = None
    method_id: Text | None = None
    title: Text | None = None
    seed_runtime: Path | None = None
    seed_mode: Literal["repair", "review_only"] | None = None

    @model_validator(mode="after")
    def coherent_boundary(self):
        if (self.seed_runtime is None) != (self.seed_mode is None):
            raise ValueError("seed_runtime and seed_mode must be provided together")
        if self.seed_runtime is not None and self.status != "ready":
            raise ValueError("Only ready jobs may name an explicit runtime seed")
        if self.status == "ready":
            if self.packet is None or self.reason is not None or self.next_evidence is not None:
                raise ValueError("ready jobs require packet, without missing-source fields")
        elif self.packet is not None or not self.reason or not self.next_evidence:
            raise ValueError("missing_sources jobs require reason/next_evidence and no packet")
        return self


class Inventory(StrictModel):
    format_version: Literal["extraction_inventory/1.0"] = "extraction_inventory/1.0"
    jobs: Annotated[tuple[Job, ...], Field(min_length=1)]
    source_receipts: Path | None = None

    @model_validator(mode="after")
    def unique_ids(self):
        if len({job.id for job in self.jobs}) != len(self.jobs):
            raise ValueError("Duplicate inventory job IDs")
        return self


class BatchConfig(StrictModel):
    model: Text = "gpt-6.1-sol"
    effort: Text = "xhigh"
    concurrency: Annotated[int, Field(strict=True, ge=1)] = 2
    timeout_seconds: float | None = None
    mode: Literal["baseline", "specialists"] = "baseline"
    max_revisions: Annotated[int, Field(strict=True, ge=0, le=1)] = 1
    transport_retries: Count = 1
    require_source_coverage: bool = False

    @model_validator(mode="before")
    @classmethod
    def valid_timeout(cls, values):
        if isinstance(values, dict) and (value := values.get("timeout_seconds")) is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not (
                    math.isfinite(value) and value > 0):
                raise ValueError("timeout_seconds must be None or finite and positive")
        return values

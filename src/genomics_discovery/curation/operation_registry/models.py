"""Stable concepts reuse the existing whole-transformation definition and review."""

import re
import unicodedata
from typing import Annotated, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.curation.workflow_extraction.models import Text
from genomics_discovery.curation.workflow_extraction.transformations import (
    Hash, Membership, MembershipCandidate,
)


def normalized_label(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


class Assignment(MembershipCandidate):
    """One new contextual use; creating a concept does not need two methods."""

    format_version: Literal["concept_assignment/1.0"] = "concept_assignment/1.0"
    members: Annotated[tuple[Membership, ...], Field(min_length=1, max_length=1)]
    registry_revision: Hash
    decision: Literal["reuse", "new"]
    aliases: tuple[Text, ...] = ()
    scope_note: Text
    lookup_queries: Annotated[tuple[Text, ...], Field(min_length=1)]
    distinction: Text = Field(description="Why this definition fits and nearby definitions do not; "
                             "state context differences without assuming interchangeability.")

    @model_validator(mode="after")
    def stable_labels(self):
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,159}", self.definition.id):
            raise ValueError("Concept ID must be a stable lowercase identifier")
        labels = [normalized_label(s) for s in (self.definition.name, *self.aliases)]
        if len(labels) != len(set(labels)):
            raise ValueError("Preferred label and aliases must be distinct")
        return self

    def concept(self) -> dict:
        return {"definition": self.definition.model_dump(mode="json"),
                "relation": self.relation, "aliases": list(self.aliases),
                "scope_note": self.scope_note}


class Decision(StrictModel):
    """Abstention is explicit; the worker cannot quietly omit its target."""

    assignment: Assignment | None
    explanation: Text

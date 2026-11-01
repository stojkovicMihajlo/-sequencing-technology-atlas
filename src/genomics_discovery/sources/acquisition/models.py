"""The finder supplies locations and identifiers, never publication text."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel

Text = Annotated[str, Field(min_length=1)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]


class Identity(StrictModel):
    pmid: Annotated[str, Field(pattern=r"^[0-9]+$")] | None = None
    doi: Text | None = None
    title: Text | None = None


class Scope(StrictModel):
    method_id: Text
    title: Text
    scope: Text
    variant: Text
    primary_identity: Identity

    @model_validator(mode="after")
    def identified(self):
        if not (self.primary_identity.pmid or self.primary_identity.doi):
            raise ValueError("Explicit source scope needs an expected PMID or DOI")
        return self


class AssociationRecord(StrictModel):
    format: Literal["biostudies"]
    url: Text
    file_path: Text


class Document(StrictModel):
    id: Slug
    url: str | None
    role: Literal["primary_paper", "supplement", "manufacturer_protocol", "cited_protocol", "other"]
    format: Literal["jats", "html", "pdf", "text", "docx", "doc"]
    identity: Identity
    linked_from: str | None = None
    association_record: AssociationRecord | None = None
    rationale: Text

    @model_validator(mode="after")
    def primary_identified(self):
        if self.role == "primary_paper" and (not self.identity.title or not (self.identity.pmid or self.identity.doi)):
            raise ValueError("Primary candidates need a declared publication title and PMID/DOI")
        return self


class Search(StrictModel):
    query: Text
    status: Literal["found", "unavailable", "access_limited"]
    url: str | None = None
    note: Text


class Findings(StrictModel):
    format_version: Literal["public_source_findings/1.0"] = "public_source_findings/1.0"
    method_id: Text
    documents: tuple[Document, ...] = ()
    searches: Annotated[tuple[Search, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique(self):
        if len({d.id for d in self.documents}) != len(self.documents):
            raise ValueError("Duplicate source-finder document ID")
        return self

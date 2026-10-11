"""Shared, IO-free document and evidence records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from career_os.evidence_contract import (
    Approximation,
    Confidence,
    EvidenceContractMetadata,
    EvidenceKind,
    EvidenceStatus,
    Ownership,
    PublicationBoundary,
)

DocumentFormat = Literal["text", "markdown", "pdf", "docx"]
LocationKind = Literal["line", "page", "paragraph"]
ReviewAction = Literal["approve", "reject", "defer", "revoke"]
ProposalActor = Literal["local_user", "agent", "deterministic_rule", "model", "synthetic_fixture"]
PROPOSAL_ACTORS = frozenset(
    {"local_user", "agent", "deterministic_rule", "model", "synthetic_fixture"}
)


@dataclass(frozen=True, slots=True)
class DocumentLocation:
    kind: LocationKind
    ordinal: int
    start_line: int
    end_line: int
    part: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"line", "page", "paragraph"}:
            raise ValueError("Document location kind is invalid")
        if self.ordinal < 1 or self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("Document location coordinates are invalid")
        if self.part is not None and (self.kind != "paragraph" or not self.part.strip()):
            raise ValueError("Document location part is invalid")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "kind": self.kind,
            "ordinal": self.ordinal,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }
        if self.part is not None:
            result["part"] = self.part
        return result


@dataclass(frozen=True, slots=True)
class ExtractedCandidateDocument:
    display_name: str
    input_format: DocumentFormat
    media_type: str
    original_sha256: str
    original_size_bytes: int
    original_content: bytes = field(repr=False)
    text: str
    extracted_sha256: str
    line_count: int
    locations: tuple[DocumentLocation, ...]
    page_count: int | None = None

    def provenance(self) -> dict[str, object]:
        """Return content-free source identity and location metadata."""
        return {
            "display_name": self.display_name,
            "input_format": self.input_format,
            "media_type": self.media_type,
            "original_sha256": self.original_sha256,
            "original_size_bytes": self.original_size_bytes,
            "extracted_sha256": self.extracted_sha256,
            "line_count": self.line_count,
            "page_count": self.page_count,
            "locations": [location.to_dict() for location in self.locations],
            "model_calls": False,
            "external_actions": False,
        }


@dataclass(frozen=True, slots=True)
class SourceDocument:
    source_id: str
    display_name: str
    media_type: str
    source_hash: str
    extracted_hash: str
    size_bytes: int
    line_count: int
    input_format: DocumentFormat
    locations: tuple[DocumentLocation, ...]
    page_count: int | None
    managed_copy: bool
    imported_at: str


@dataclass(frozen=True, slots=True)
class EvidenceRevision:
    evidence_id: str
    revision: int
    kind: EvidenceKind
    statement: str
    details: Mapping[str, str]
    source_id: str
    source_hash: str
    source_start_line: int
    source_end_line: int
    ownership: Ownership
    confidence: Confidence
    approximation: Approximation
    publication_boundary: PublicationBoundary
    status: EvidenceStatus
    content_hash: str
    created_at: str
    supersedes_revision: int | None = None
    metadata: EvidenceContractMetadata = EvidenceContractMetadata(origin_ownership="unknown")
    extracted_hash: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceReadiness:
    ready: bool
    approved: int
    unapproved: int
    rejected: int
    deferred: int
    revoked: int
    gaps: int
    forbidden_claims: int
    unknown_or_disputed: int
    blockers: tuple[str, ...]

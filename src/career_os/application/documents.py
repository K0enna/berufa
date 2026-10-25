"""Shared local imported-document contracts; no evidence or career-event authority."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Protocol, cast

FORMATS = {"md": "draft.md", "txt": "draft.txt", "pdf": "draft.pdf", "docx": "draft.docx"}
PURPOSES = {"cv", "letter", "answer", "interview", "follow_up", "other"}


def identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,99}", value):
        raise ValueError(f"Invalid {field}")
    return value


def digest(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Invalid document hash")
    return value


def bounded_text(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or "\x00" in value:
        raise ValueError("Invalid bounded document text")
    value.encode("utf-8")
    return value


class Serializable:
    def to_dict(self) -> dict[str, Any]:
        return asdict(cast(Any, self))


@dataclass(frozen=True, slots=True)
class DocumentRef(Serializable):
    document_id: str
    fingerprint: str

    def __post_init__(self) -> None:
        identifier(self.document_id, "document_id")
        digest(self.fingerprint)

    @classmethod
    def from_dict(cls, value: Any) -> DocumentRef:
        if not isinstance(value, dict) or set(value) != {"document_id", "fingerprint"}:
            raise ValueError("Invalid document ref")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class DocumentBasis(Serializable):
    evidence_id: str
    revision: int
    content_hash: str

    def __post_init__(self) -> None:
        identifier(self.evidence_id, "evidence_id")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("Invalid evidence revision")
        digest(self.content_hash)


@dataclass(frozen=True, slots=True)
class DocumentImportRequest:
    request_id: str
    label: str
    purpose: str
    provenance: str
    artifacts: dict[str, bytes]
    opportunity_id: str | None = None
    basis: tuple[DocumentBasis, ...] = ()
    document_id: str | None = None

    def __post_init__(self) -> None:
        identifier(self.request_id, "request_id")
        bounded_text(self.label, 200)
        bounded_text(self.provenance, 2000)
        if not isinstance(self.purpose, str) or self.purpose not in PURPOSES:
            raise ValueError("Invalid document purpose")
        if self.opportunity_id is not None:
            identifier(self.opportunity_id, "opportunity_id")
        if self.document_id is not None:
            identifier(self.document_id, "document_id")
        if (
            not isinstance(self.artifacts, dict)
            or not 1 <= len(self.artifacts) <= 4
            or set(self.artifacts) - FORMATS.keys()
        ):
            raise ValueError("Invalid document formats")
        if any(
            not isinstance(v, bytes) or not 1 <= len(v) <= 2 * 1024 * 1024
            for v in self.artifacts.values()
        ):
            raise ValueError("Invalid document artifact size")
        if (
            not isinstance(self.basis, tuple)
            or len(self.basis) > 100
            or any(not isinstance(b, DocumentBasis) for b in self.basis)
            or len({b.evidence_id for b in self.basis}) != len(self.basis)
        ):
            raise ValueError("Invalid document basis")


@dataclass(frozen=True, slots=True)
class DocumentReceipt(Serializable):
    ref: DocumentRef
    changed: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class DocumentEditRequest(Serializable):
    request_id: str
    ref: DocumentRef
    format: str
    selected_text: str
    replacement_text: str

    def __post_init__(self) -> None:
        identifier(self.request_id, "request_id")
        if (
            not isinstance(self.ref, DocumentRef)
            or not isinstance(self.format, str)
            or self.format not in {"md", "txt"}
        ):
            raise ValueError("Targeted edits require an exact Markdown or text version")
        bounded_text(self.selected_text, 128 * 1024)
        bounded_text(self.replacement_text, 128 * 1024)
        if self.selected_text == self.replacement_text:
            raise ValueError("The targeted edit makes no change")


@dataclass(frozen=True, slots=True)
class DocumentEditProposal(Serializable):
    request: DocumentEditRequest
    text: str
    change_summary: str
    confirmation_fingerprint: str


@dataclass(frozen=True, slots=True)
class DocumentInspection(Serializable):
    ref: DocumentRef
    label: str
    purpose: str
    opportunity_id: str | None
    intact: bool
    basis_current: bool | None
    artifacts: dict[str, dict[str, Any]]
    review_state: str
    review: dict[str, Any] | None = None
    created_at: str | None = None
    provenance: str = ""
    basis: tuple[DocumentBasis, ...] = ()
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentInventory(Serializable):
    versions: tuple[DocumentInspection, ...]


@dataclass(frozen=True, slots=True)
class DocumentReviewRequest(Serializable):
    ref: DocumentRef
    disposition: str
    note: str = ""
    page_inspected_hashes: tuple[str, ...] = ()
    basis_unknown_ack: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ref, DocumentRef)
            or not isinstance(self.disposition, str)
            or self.disposition
            not in {
                "accepted",
                "changes_requested",
            }
        ):
            raise ValueError("Invalid document review")
        if self.note:
            bounded_text(self.note, 2000)
        if (
            not isinstance(self.note, str)
            or not isinstance(self.page_inspected_hashes, tuple)
            or len(self.page_inspected_hashes) > 4
            or any(not isinstance(value, str) for value in self.page_inspected_hashes)
            or len(set(self.page_inspected_hashes)) != len(self.page_inspected_hashes)
        ):
            raise ValueError("Invalid review acknowledgement")
        for value in self.page_inspected_hashes:
            digest(value)
        if type(self.basis_unknown_ack) is not bool:
            raise ValueError("Invalid unknown-basis acknowledgement")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "page_inspected_hashes": list(self.page_inspected_hashes)}

    @classmethod
    def from_dict(cls, value: Any) -> DocumentReviewRequest:
        if not isinstance(value, dict) or set(value) != {
            "ref",
            "disposition",
            "note",
            "page_inspected_hashes",
            "basis_unknown_ack",
        }:
            raise ValueError("Invalid review request fields")
        if not isinstance(value["page_inspected_hashes"], list):
            raise ValueError("Invalid persisted review acknowledgements")
        return cls(
            DocumentRef.from_dict(value["ref"]),
            value["disposition"],
            value["note"],
            tuple(value["page_inspected_hashes"]),
            value["basis_unknown_ack"],
        )


@dataclass(frozen=True, slots=True)
class DocumentReviewProposal(Serializable):
    request: DocumentReviewRequest
    inspection: DocumentInspection
    confirmation_fingerprint: str
    previous_review_id: str | None


@dataclass(frozen=True, slots=True)
class DocumentReviewReceipt(Serializable):
    review: dict[str, Any]
    changed: bool

    @property
    def confirmation_fingerprint(self) -> str:
        return cast(str, self.review["confirmation_fingerprint"])

    @property
    def review_id(self) -> str:
        return cast(str, self.review["review_id"])


@dataclass(frozen=True, slots=True)
class DocumentExportRequest(Serializable):
    ref: DocumentRef
    formats: tuple[str, ...]
    review_id: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ref, DocumentRef)
            or not isinstance(self.formats, tuple)
            or not 1 <= len(self.formats) <= 4
            or any(not isinstance(value, str) for value in self.formats)
            or len(set(self.formats)) != len(self.formats)
            or set(self.formats) - FORMATS.keys()
        ):
            raise ValueError("Invalid export selection")
        digest(self.review_id)


@dataclass(frozen=True, slots=True)
class DocumentExportReceipt(Serializable):
    ref: DocumentRef
    review_id: str
    path: str
    artifacts: tuple[str, ...]


class DocumentRepository(Protocol):
    def recover_review_lock(self) -> bool: ...

    def import_version(self, request: DocumentImportRequest) -> DocumentReceipt: ...
    def edit(
        self, request: DocumentEditRequest, fingerprint: str | None
    ) -> DocumentEditProposal | DocumentReceipt: ...
    def list(self, *, opportunity_id: str | None = None) -> DocumentInventory: ...
    def inspect(self, ref: DocumentRef) -> DocumentInspection: ...
    def review(
        self, request: DocumentReviewRequest, fingerprint: str | None
    ) -> DocumentReviewProposal | DocumentReviewReceipt: ...
    def export(self, request: DocumentExportRequest) -> DocumentExportReceipt: ...


class DocumentService:
    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    @staticmethod
    def _actor(actor: str | None) -> None:
        if actor != "local_user":
            raise PermissionError("Only the local user may change local documents")

    def recover_review_lock(self, *, actor: str) -> bool:
        """Explicitly normalize a validated legacy numeric sidecar before reopening the UI."""
        self._actor(actor)
        return self._repository.recover_review_lock()

    def import_version(self, request: DocumentImportRequest, *, actor: str) -> DocumentReceipt:
        self._actor(actor)
        return self._repository.import_version(request)

    def edit(
        self,
        request: DocumentEditRequest,
        *,
        confirmation_fingerprint: str | None = None,
        actor: str | None = None,
    ) -> DocumentEditProposal | DocumentReceipt:
        if not isinstance(request, DocumentEditRequest):
            raise ValueError("Invalid document edit request")
        if confirmation_fingerprint is not None:
            self._actor(actor)
            digest(confirmation_fingerprint)
        return self._repository.edit(request, confirmation_fingerprint)

    def list(self, *, opportunity_id: str | None = None) -> DocumentInventory:
        if opportunity_id is not None:
            identifier(opportunity_id, "opportunity_id")
        return self._repository.list(opportunity_id=opportunity_id)

    def inspect(self, ref: DocumentRef) -> DocumentInspection:
        return self._repository.inspect(ref)

    def review(
        self,
        request: DocumentReviewRequest,
        *,
        confirmation_fingerprint: str | None = None,
        actor: str | None = None,
    ) -> DocumentReviewProposal | DocumentReviewReceipt:
        if confirmation_fingerprint is not None:
            self._actor(actor)
            digest(confirmation_fingerprint)
        return self._repository.review(request, confirmation_fingerprint)

    def export(self, request: DocumentExportRequest, *, actor: str) -> DocumentExportReceipt:
        self._actor(actor)
        return self._repository.export(request)

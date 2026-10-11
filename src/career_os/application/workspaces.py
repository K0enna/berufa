"""Discovery and exact local review of prepared content, not career-event authority."""

from __future__ import annotations

import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Protocol, TypeVar

from career_os.application.applications import application_fingerprint
from career_os.application.opportunities import _identifier, _require_utf8
from career_os.application_records import _hash_payload
from career_os.outcome_records import OutcomeLearningEvent, _timestamp_instant

MAX_WORKSPACE_REQUEST_BYTES = 64 * 1024
MAX_WORKSPACE_REVIEW_BYTES = 2 * 1024 * 1024
MAX_WORKSPACE_REVIEWS = 5000


@dataclass(frozen=True, slots=True)
class WorkspaceRef:
    kind: str
    opportunity_id: str
    fingerprint: str
    round_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in {"application", "interview"}:
            raise ValueError("Unknown workspace kind")
        if _identifier(self.opportunity_id, "opportunity_id") != self.opportunity_id:
            raise ValueError("Workspace identity must be canonical")
        application_fingerprint(self.fingerprint)
        if self.kind == "interview":
            if _identifier(self.round_id, "round_id") != self.round_id:
                raise ValueError("Interview round identity must be canonical")
        elif self.round_id is not None:
            raise ValueError("Application workspaces have no round ID")

    @classmethod
    def from_dict(cls, payload: object) -> WorkspaceRef:
        if not isinstance(payload, dict) or set(payload) != {
            "kind",
            "opportunity_id",
            "fingerprint",
            "round_id",
        }:
            raise ValueError("Workspace reference fields are invalid")
        return cls(**payload)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def key(self) -> str:
        return _hash_payload(self.to_dict())


@dataclass(frozen=True, slots=True)
class WorkspaceReviewRequest:
    workspace: WorkspaceRef
    disposition: str
    note: str = ""
    interview_event_id: str | None = None

    def __post_init__(self) -> None:
        WorkspaceRef.from_dict(self.workspace.to_dict())
        if not isinstance(self.disposition, str) or self.disposition not in {
            "accepted",
            "changes_requested",
        }:
            raise ValueError("Review disposition is invalid")
        if not isinstance(self.note, str) or len(self.note) > 2000 or "\x00" in self.note:
            raise ValueError("Review note must be bounded text")
        _require_utf8(self.note, "note")
        if self.interview_event_id is not None and (
            self.workspace.kind != "interview"
            or not isinstance(self.interview_event_id, str)
            or not re.fullmatch(r"olv2-[0-9a-f]{32}", self.interview_event_id)
        ):
            raise ValueError("Interview association requires an exact V2 event ID")

    @classmethod
    def from_dict(cls, payload: object) -> WorkspaceReviewRequest:
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "workspace",
            "disposition",
            "note",
            "interview_event_id",
        }:
            raise ValueError("Workspace review requires exactly the documented fields")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValueError("Workspace review requires schema version 1")
        return cls(
            WorkspaceRef.from_dict(payload["workspace"]),
            payload["disposition"],
            payload["note"],
            payload["interview_event_id"],
        )

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 1, **asdict(self)}


def review_basis(
    request: WorkspaceReviewRequest,
    manifest_sha256: str,
    previous_review_id: str | None,
    event_record_sha256: str | None,
) -> dict[str, object]:
    application_fingerprint(manifest_sha256)
    if previous_review_id is not None:
        application_fingerprint(previous_review_id)
    if event_record_sha256 is not None:
        application_fingerprint(event_record_sha256)
    if (request.interview_event_id is None) != (event_record_sha256 is None):
        raise ValueError("Review event binding is incomplete")
    return {
        "request": request.to_dict(),
        "manifest_sha256": manifest_sha256,
        "previous_review_id": previous_review_id,
        "event_record_sha256": event_record_sha256,
    }


@dataclass(frozen=True, slots=True)
class ArtifactReview:
    request: WorkspaceReviewRequest
    manifest_sha256: str
    previous_review_id: str | None
    event_record_sha256: str | None
    reviewed_at: str
    confirmation_fingerprint: str
    review_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "confirmed_by": "local_user",
            **asdict(self),
            "request": self.request.to_dict(),
        }

    @classmethod
    def create(
        cls,
        request: WorkspaceReviewRequest,
        manifest_sha256: str,
        previous_review_id: str | None,
        event_record_sha256: str | None,
        reviewed_at: str,
    ) -> ArtifactReview:
        _timestamp_instant(reviewed_at, "reviewed_at")
        basis = review_basis(request, manifest_sha256, previous_review_id, event_record_sha256)
        fingerprint = _hash_payload(basis)
        payload = {
            "schema_version": 1,
            "confirmed_by": "local_user",
            **basis,
            "reviewed_at": reviewed_at,
            "confirmation_fingerprint": fingerprint,
        }
        return cls(
            request,
            manifest_sha256,
            previous_review_id,
            event_record_sha256,
            reviewed_at,
            fingerprint,
            _hash_payload(payload),
        )

    @classmethod
    def from_dict(cls, payload: object) -> ArtifactReview:
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "confirmed_by",
            "request",
            "manifest_sha256",
            "previous_review_id",
            "event_record_sha256",
            "reviewed_at",
            "confirmation_fingerprint",
            "review_id",
        }:
            raise ValueError("Artifact review record fields are invalid")
        if (
            type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or payload["confirmed_by"] != "local_user"
        ):
            raise ValueError("Artifact review record authority is invalid")
        result = cls.create(
            WorkspaceReviewRequest.from_dict(payload["request"]),
            payload["manifest_sha256"],
            payload["previous_review_id"],
            payload["event_record_sha256"],
            payload["reviewed_at"],
        )
        if result.to_dict() != payload:
            raise ValueError("Artifact review record hash is invalid")
        return result


@dataclass(frozen=True, slots=True)
class WorkspaceSummary:
    workspace: WorkspaceRef
    relative_path: str
    prepared_at: str | None
    company: str
    role: str
    manifest_sha256: str | None
    intact: bool
    current: bool | None = None
    selected: bool = False
    round_type: str | None = None
    review: ArtifactReview | None = None

    @property
    def review_state(self) -> str:
        if self.review and self.review.manifest_sha256 == self.manifest_sha256:
            return self.review.request.disposition
        return "unreviewed"

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "review": self.review.to_dict() if self.review else None,
            "review_state": self.review_state,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceDiagnostic:
    code: str
    detail: str
    workspace: WorkspaceRef | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceInventory:
    versions: tuple[WorkspaceSummary, ...]
    diagnostics: tuple[WorkspaceDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "versions": [item.to_dict() for item in self.versions],
            "diagnostics": [asdict(item) for item in self.diagnostics],
            "partial": bool(self.diagnostics),
            "read_only": True,
            "external_actions": False,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceInspection:
    summary: WorkspaceSummary
    plan: dict[str, object]
    documents: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "summary": self.summary.to_dict(),
            "plan": deepcopy(self.plan),
            "documents": dict(self.documents),
            "read_only": True,
            "external_actions": False,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceReviewProposal:
    request: WorkspaceReviewRequest
    inspection: WorkspaceInspection
    previous_review_id: str | None
    event: OutcomeLearningEvent | None

    @property
    def confirmation_fingerprint(self) -> str:
        return _hash_payload(
            review_basis(
                self.request,
                self.inspection.summary.manifest_sha256,
                self.previous_review_id,
                self.event.record_sha256 if self.event else None,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "request": self.request.to_dict(),
            "inspection": self.inspection.to_dict(),
            "previous_review_id": self.previous_review_id,
            "interview_event": self.event.to_dict() if self.event else None,
            "confirmation_fingerprint": self.confirmation_fingerprint,
            "requires_user_confirmation": True,
            "external_event_recorded": False,
            "evidence_approval_changed": False,
            "application_ready": False,
        }


def build_review_proposal(
    request: WorkspaceReviewRequest,
    inspection: WorkspaceInspection,
    event: OutcomeLearningEvent | None,
) -> WorkspaceReviewProposal:
    summary = inspection.summary
    if summary.workspace != request.workspace or not summary.intact or summary.current is not True:
        raise ValueError("Review requires exact, intact, currently valid content")
    if request.interview_event_id is not None:
        expected_stage = "final_interview" if summary.round_type == "final" else "interview"
        if (
            event is None
            or event.event_id != request.interview_event_id
            or event.opportunity_id != request.workspace.opportunity_id
            or event.event_type != "outcome_recorded"
            or event.payload["stage"] != expected_stage
            or event.payload["outcome"] != "scheduled"
        ):
            raise ValueError(
                "The selected event does not confirm this opportunity's interview round"
            )
    elif event is not None:
        raise ValueError("No interview association was requested")
    return WorkspaceReviewProposal(
        request, inspection, summary.review.review_id if summary.review else None, event
    )


@dataclass(frozen=True, slots=True)
class WorkspaceReviewReceipt:
    review: ArtifactReview
    created: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "review": self.review.to_dict(),
            "created": self.created,
            "external_event_recorded": False,
            "application_ready": False,
        }


class WorkspaceRepository(Protocol):
    def has_consent(self) -> bool: ...
    def list(self) -> WorkspaceInventory: ...
    def inspect(self, workspace: WorkspaceRef) -> WorkspaceInspection: ...
    def review(
        self, request: WorkspaceReviewRequest, fingerprint: str | None
    ) -> WorkspaceReviewProposal | WorkspaceReviewReceipt: ...


@dataclass(frozen=True, slots=True)
class WorkspaceOperationError:
    code: str
    message: str


_T = TypeVar("_T")


def require_workspace_result(result: _T | WorkspaceOperationError) -> _T:
    if isinstance(result, WorkspaceOperationError):
        if result.code in {"consent_required", "human_required"}:
            raise PermissionError(result.message)
        if result.code == "io_error":
            raise OSError(result.message)
        raise ValueError(result.message)
    return result


class WorkspaceService:
    def __init__(self, repository: WorkspaceRepository) -> None:
        self._repository = repository

    def _execute(self, operation: Callable[[], _T]) -> _T | WorkspaceOperationError:
        try:
            if not self._repository.has_consent():
                return WorkspaceOperationError(
                    "consent_required", "Local-processing consent required"
                )
            return operation()
        except PermissionError as exc:
            if exc.errno is None:
                return WorkspaceOperationError(
                    "human_required", "Exact current local-user confirmation required"
                )
            return WorkspaceOperationError("io_error", "Local workspace storage is unavailable")
        except ValueError:
            return WorkspaceOperationError(
                "invalid_workspace", "Workspace input or current authority is invalid"
            )
        except OSError:
            return WorkspaceOperationError("io_error", "Local workspace storage is unavailable")

    def list(self) -> WorkspaceInventory | WorkspaceOperationError:
        return self._execute(self._repository.list)

    def inspect(self, workspace: WorkspaceRef) -> WorkspaceInspection | WorkspaceOperationError:
        return self._execute(
            lambda: self._repository.inspect(WorkspaceRef.from_dict(workspace.to_dict()))
        )

    def review(
        self,
        request: WorkspaceReviewRequest,
        *,
        confirmation_fingerprint: str | None = None,
        actor: str | None = None,
    ) -> WorkspaceReviewProposal | WorkspaceReviewReceipt | WorkspaceOperationError:
        def review():
            if confirmation_fingerprint is not None:
                if actor != "local_user":
                    raise PermissionError("Local-user review confirmation required")
                application_fingerprint(confirmation_fingerprint)
            return self._repository.review(
                WorkspaceReviewRequest.from_dict(request.to_dict()), confirmation_fingerprint
            )

        return self._execute(review)

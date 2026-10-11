"""Evidence use cases over the single injected evidence repository."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Collection, Mapping
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeVar, cast

from career_os.application.inventory import InventoryPage
from career_os.authority_ports import CandidateEvidenceRecord
from career_os.evidence_contract import (
    APPROXIMATION_VALUES,
    CONFIDENCE_VALUES,
    OWNERSHIP_VALUES,
    PUBLICATION_BOUNDARIES,
    Approximation,
    Confidence,
    EvidenceContractMetadata,
    EvidenceKind,
    EvidenceStatus,
    Ownership,
    PublicationBoundary,
    PublicationReadiness,
    PublicationRequest,
    PublicationUse,
    TimelineItem,
    validate_metric_details,
    validate_timeline,
)
from career_os.evidence_records import (
    PROPOSAL_ACTORS,
    DocumentLocation,
    EvidenceReadiness,
    EvidenceRevision,
    ExtractedCandidateDocument,
    ProposalActor,
    ReviewAction,
    SourceDocument,
)

MAX_PROPOSAL_BYTES = 64 * 1024
MAX_SOURCE_SPAN_BYTES = 64 * 1024
MAX_SOURCE_SPAN_LINES = 100
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_PROPOSAL_FIELDS = {
    "schema_version",
    "source_id",
    "source_hash",
    "extracted_hash",
    "kind",
    "statement",
    "details",
    "source_start_line",
    "source_end_line",
    "ownership",
    "confidence",
    "approximation",
    "publication_boundary",
    "actor",
    "reason",
    "metadata",
}
_INTERVIEW_ANSWER_FIELDS = {
    "schema_version",
    "question_id",
    "answers",
    "ownership",
    "confidence",
    "approximation",
    "publication_boundary",
    "role_family_relevance",
}


@dataclass(frozen=True, slots=True)
class InterviewField:
    field_id: str
    prompt: str
    choices: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"field_id": self.field_id, "prompt": self.prompt}
        if self.choices:
            payload["choices"] = list(self.choices)
        return payload


@dataclass(frozen=True, slots=True)
class InterviewQuestion:
    question_id: str
    prompt: str
    kind: EvidenceKind
    fields: tuple[InterviewField, ...]
    statement_template: str

    def to_dict(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "prompt": self.prompt,
            "evidence_kind": self.kind,
            "answer_fields": [field.to_dict() for field in self.fields],
        }


INTERVIEW_QUESTIONS = (
    InterviewQuestion(
        "role_timeline",
        "Record one role or engagement with its exact or best-known dates.",
        "role_timeline",
        (
            InterviewField("employer", "Employer or organization"),
            InterviewField("title", "Exact role or engagement title"),
            InterviewField("start_date", "Start date as YYYY, YYYY-MM, or YYYY-MM-DD"),
            InterviewField("end_date", "End date in the same format, or present"),
        ),
        "User-reported role: {title} at {employer}, {start_date} to {end_date}.",
    ),
    InterviewQuestion(
        "mandate_decision_rights",
        "Describe one mandate and the decision right you personally held.",
        "responsibility",
        (
            InterviewField("responsibility", "Mandate or responsibility"),
            InterviewField("decision_right", "Personal decision right or explicit limitation"),
        ),
        "User-reported mandate: {responsibility}. Decision right: {decision_right}.",
    ),
    InterviewQuestion(
        "team_management_layers",
        "Describe one team scope and whether you led directly, indirectly, or temporarily.",
        "leadership_team",
        (
            InterviewField("team_scope", "Team scope, size, and management layers if known"),
            InterviewField("leadership_type", "Direct, indirect, interim, project, or other"),
        ),
        "User-reported team scope: {team_scope}. Leadership type: {leadership_type}.",
    ),
    InterviewQuestion(
        "measured_outcome",
        "Record one measured outcome, including value, unit, period, and scope.",
        "achievement",
        (
            InterviewField("outcome", "Outcome without inflating personal ownership"),
            InterviewField("metric_value", "Finite numeric value"),
            InterviewField("metric_unit", "Unit, such as percent, EUR, customers, or days"),
            InterviewField("metric_period", "Measurement period"),
            InterviewField("metric_scope", "Personal, team, company, or shared scope"),
        ),
        (
            "User-reported outcome: {outcome}; metric {metric_value} {metric_unit} during "
            "{metric_period} for {metric_scope}."
        ),
    ),
    InterviewQuestion(
        "personal_action_team_outcome",
        "Record one interview story that separates your action from the resulting outcome.",
        "interview_story",
        (
            InterviewField("situation", "Situation and relevant context"),
            InterviewField("task", "Your mandate or task"),
            InterviewField("action", "Actions you personally took"),
            InterviewField("result", "Result and whose outcome it was"),
        ),
        "User-reported story: {situation} Task: {task} Action: {action} Result: {result}",
    ),
    InterviewQuestion(
        "stakeholder_seniority",
        "Record one stakeholder group, its confirmed seniority, and your interaction.",
        "stakeholder_seniority",
        (
            InterviewField("stakeholder", "Stakeholder role or group"),
            InterviewField("seniority", "Confirmed seniority; use unknown when not confirmed"),
            InterviewField("interaction", "Nature and frequency of interaction"),
        ),
        "User-reported stakeholder evidence: {stakeholder}; {seniority}; {interaction}.",
    ),
    InterviewQuestion(
        "skill_domain_environment",
        "Record one evidenced skill, domain, industry, and customer environment.",
        "skill_domain",
        (
            InterviewField("skill", "Specific skill"),
            InterviewField("domain", "Product or operating domain"),
            InterviewField("evidence_level", "Direct, transferable, limited, or unknown"),
            InterviewField("industry", "Industry or unknown"),
            InterviewField("customer_environment", "Customer or buyer environment, or unknown"),
        ),
        "User-reported skill evidence: {skill} in {domain}, evidence level {evidence_level}.",
    ),
    InterviewQuestion(
        "education_completion",
        "Record one education item and its exact completion status.",
        "education",
        (
            InterviewField("institution", "Institution"),
            InterviewField("program", "Program or qualification"),
            InterviewField(
                "completion_status",
                "Completion status",
                ("coursework", "partial", "certificate", "awarded_degree"),
            ),
        ),
        "User-reported education: {program} at {institution}; status {completion_status}.",
    ),
    InterviewQuestion(
        "genuine_gap",
        "Record one genuine gap or unsupported claim that should stay private.",
        "gap",
        (InterviewField("gap", "Specific gap or unsupported experience"),),
        "User-reported genuine gap: {gap}.",
    ),
    InterviewQuestion(
        "practical_constraint",
        "Record one compensation, location, work-model, or travel constraint.",
        "compensation_constraint",
        (
            InterviewField("constraint", "Constraint type"),
            InterviewField("value", "Value or limit"),
            InterviewField("currency", "Uppercase currency code, or N/A"),
            InterviewField("period", "Period, or N/A"),
            InterviewField("work_arrangement", "Work arrangement, or unknown"),
            InterviewField("geography", "Geography, or unknown"),
        ),
        "User-reported practical constraint: {constraint} = {value} {currency}; {period}.",
    ),
)
_INTERVIEW_QUESTION_BY_ID = {question.question_id: question for question in INTERVIEW_QUESTIONS}


def _clean_interview_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > 500 or "\x00" in cleaned:
        raise ValueError(f"{field} must contain 1-500 safe characters")
    return cleaned


@dataclass(frozen=True, slots=True)
class InterviewAnswerInput:
    question: InterviewQuestion
    answers: dict[str, str]
    ownership: Ownership
    confidence: Confidence
    approximation: Approximation
    publication_boundary: PublicationBoundary
    role_family_relevance: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> InterviewAnswerInput:
        if not isinstance(payload, dict) or set(payload) != _INTERVIEW_ANSWER_FIELDS:
            raise ValueError("Interview answer must contain exactly the documented fields")
        if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
            raise ValueError("Interview answer schema_version must be 1")
        question_id = payload.get("question_id")
        if not isinstance(question_id, str) or question_id not in _INTERVIEW_QUESTION_BY_ID:
            raise ValueError("Interview answer question_id is unsupported")
        question = _INTERVIEW_QUESTION_BY_ID[question_id]
        raw_answers = payload.get("answers")
        expected_fields = {field.field_id for field in question.fields}
        if not isinstance(raw_answers, dict) or set(raw_answers) != expected_fields:
            raise ValueError("Interview answers must match the question's documented fields")
        answers = {
            key: _clean_interview_text(value, f"answers.{key}")
            for key, value in raw_answers.items()
        }
        for field in question.fields:
            if field.choices and answers[field.field_id] not in field.choices:
                raise ValueError(f"answers.{field.field_id} must use a documented choice")
        ownership = cast(Ownership, payload.get("ownership"))
        confidence = cast(Confidence, payload.get("confidence"))
        approximation = cast(Approximation, payload.get("approximation"))
        publication_boundary = cast(PublicationBoundary, payload.get("publication_boundary"))
        if not isinstance(ownership, str) or ownership not in OWNERSHIP_VALUES:
            raise ValueError("Interview answer ownership is invalid")
        if not isinstance(confidence, str) or confidence not in CONFIDENCE_VALUES:
            raise ValueError("Interview answer confidence is invalid")
        if not isinstance(approximation, str) or approximation not in APPROXIMATION_VALUES:
            raise ValueError("Interview answer approximation is invalid")
        if (
            not isinstance(publication_boundary, str)
            or publication_boundary not in PUBLICATION_BOUNDARIES
        ):
            raise ValueError("Interview answer publication_boundary is invalid")
        if approximation != "exact" and confidence == "exact":
            raise ValueError("Approximate interview evidence cannot claim exact confidence")
        if question.kind == "gap" and publication_boundary != "private":
            raise ValueError("Interview gap answers must remain private")
        relevance = payload.get("role_family_relevance")
        if (
            not isinstance(relevance, list)
            or len(relevance) > 32
            or any(not isinstance(value, str) for value in relevance)
        ):
            raise ValueError("Interview role_family_relevance must be a bounded text list")
        role_family_relevance = tuple(
            _clean_interview_text(value, "role_family_relevance item") for value in relevance
        )
        if len(role_family_relevance) != len(set(role_family_relevance)):
            raise ValueError("Interview role_family_relevance contains duplicates")
        if question.kind == "achievement":
            validate_metric_details(answers)
        if question.kind == "role_timeline":
            issues = validate_timeline(
                (
                    TimelineItem(
                        evidence_id="guided-interview-role",
                        start_date=answers["start_date"],
                        end_date=answers["end_date"],
                    ),
                )
            )
            if issues:
                raise ValueError(issues[0].message)
        return cls(
            question=question,
            answers=answers,
            ownership=ownership,
            confidence=confidence,
            approximation=approximation,
            publication_boundary=publication_boundary,
            role_family_relevance=role_family_relevance,
        )


@dataclass(frozen=True, slots=True)
class EvidenceProposalInput:
    source_id: str
    source_hash: str
    extracted_hash: str
    kind: EvidenceKind
    statement: str
    details: dict[str, str]
    source_start_line: int
    source_end_line: int
    ownership: Ownership
    confidence: Confidence
    approximation: Approximation
    publication_boundary: PublicationBoundary
    actor: ProposalActor
    reason: str
    metadata: EvidenceContractMetadata

    @classmethod
    def from_dict(cls, payload: object) -> EvidenceProposalInput:
        if not isinstance(payload, dict) or set(payload) != _PROPOSAL_FIELDS:
            raise ValueError("Proposal must contain exactly the documented fields")
        if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
            raise ValueError("Proposal schema_version must be 1")
        for field in (
            "kind",
            "ownership",
            "confidence",
            "approximation",
            "publication_boundary",
            "actor",
        ):
            if not isinstance(payload.get(field), str):
                raise ValueError(f"Proposal {field} must be text")
        details = payload.get("details")
        if not isinstance(details, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in details.items()
        ):
            raise ValueError("Proposal details must map text keys to text values")
        ownership = cast(Ownership, payload.get("ownership"))
        actor = cast(ProposalActor, payload.get("actor"))
        if actor not in PROPOSAL_ACTORS - {"synthetic_fixture"}:
            raise ValueError("Proposal actor must identify the user, agent, rule, or model")
        source_hash = payload.get("source_hash")
        extracted_hash = payload.get("extracted_hash")
        if (
            not isinstance(source_hash, str)
            or not _HASH.fullmatch(source_hash)
            or not isinstance(extracted_hash, str)
            or not _HASH.fullmatch(extracted_hash)
        ):
            raise ValueError("Proposal source hashes must be lowercase SHA-256 values")
        start_line = payload.get("source_start_line")
        end_line = payload.get("source_end_line")
        if (
            isinstance(start_line, bool)
            or not isinstance(start_line, int)
            or isinstance(end_line, bool)
            or not isinstance(end_line, int)
        ):
            raise ValueError("Proposal source lines must be integers")
        statement = payload.get("statement")
        reason = payload.get("reason")
        source_id = payload.get("source_id")
        if not all(isinstance(value, str) for value in (statement, reason, source_id)):
            raise ValueError("Proposal identity, statement, and reason must be text")
        return cls(
            source_id=cast(str, source_id),
            source_hash=source_hash,
            extracted_hash=extracted_hash,
            kind=cast(EvidenceKind, payload.get("kind")),
            statement=cast(str, statement),
            details=cast(dict[str, str], details),
            source_start_line=start_line,
            source_end_line=end_line,
            ownership=ownership,
            confidence=cast(Confidence, payload.get("confidence")),
            approximation=cast(Approximation, payload.get("approximation")),
            publication_boundary=cast(PublicationBoundary, payload.get("publication_boundary")),
            actor=actor,
            reason=cast(str, reason),
            metadata=EvidenceContractMetadata.from_dict(
                payload.get("metadata"),
                fallback_ownership=ownership,
            ),
        )


@dataclass(frozen=True, slots=True)
class EvidenceCard:
    revision: int
    content_hash: str
    kind: str
    statement_preview: str
    status: str
    ownership: str
    confidence: str
    approximation: str
    publication_boundary: str


class EvidenceRepository(Protocol):
    """The existing vault remains the sole writer of sources, revisions and audit."""

    def transaction(self) -> AbstractContextManager[None]: ...

    def has_consent(self) -> bool: ...

    def list_current(self, *, limit: int, cursor: str | None) -> InventoryPage[EvidenceCard]: ...

    def record_consent(self, *, accepted: bool, actor: str = "local_user") -> str: ...

    def import_document(
        self,
        *,
        display_name: str,
        media_type: str,
        content: bytes,
    ) -> SourceDocument: ...

    def import_extracted_document(
        self,
        document: ExtractedCandidateDocument,
    ) -> SourceDocument: ...

    def source(self, source_id: str) -> SourceDocument: ...

    def sources(self) -> tuple[SourceDocument, ...]: ...

    def source_locations(
        self,
        source_id: str,
        start_line: int,
        end_line: int,
    ) -> tuple[DocumentLocation, ...]: ...

    def source_lines(self, source_id: str, start_line: int, end_line: int) -> tuple[str, ...]: ...

    def latest(self, evidence_id: str) -> EvidenceRevision: ...

    def enumerate(
        self,
        *,
        statuses: Collection[EvidenceStatus] | None = None,
        publication_use: PublicationUse | None = None,
    ) -> tuple[CandidateEvidenceRecord, ...]: ...

    def propose(
        self,
        *,
        source_id: str,
        kind: EvidenceKind,
        statement: str,
        details: Mapping[str, str],
        source_start_line: int,
        source_end_line: int,
        ownership: Ownership,
        confidence: Confidence,
        approximation: Approximation,
        publication_boundary: PublicationBoundary,
        metadata: EvidenceContractMetadata | None = None,
        evidence_id: str | None = None,
        actor: ProposalActor = "synthetic_fixture",
        reason: str | None = None,
    ) -> EvidenceRevision: ...

    def edit(
        self,
        evidence_id: str,
        *,
        expected_revision: int,
        statement: str | None = None,
        details: Mapping[str, str] | None = None,
        ownership: Ownership | None = None,
        confidence: Confidence | None = None,
        approximation: Approximation | None = None,
        publication_boundary: PublicationBoundary | None = None,
        metadata: EvidenceContractMetadata | None = None,
        actor: str = "local_user",
    ) -> EvidenceRevision: ...

    def review(
        self,
        evidence_id: str,
        revision: int,
        *,
        action: ReviewAction,
        expected_content_hash: str,
        actor: str = "local_user",
        reason: str | None = None,
    ) -> EvidenceRevision: ...

    def verify(
        self,
        evidence_id: str,
        revision: int,
        *,
        expected_content_hash: str,
        actor: str = "local_user",
    ) -> EvidenceRevision: ...

    def readiness(self) -> EvidenceReadiness: ...

    def validate_publication(self, request: PublicationRequest) -> PublicationReadiness: ...

    def review_queue(self) -> tuple[EvidenceRevision, ...]: ...


@dataclass(frozen=True, slots=True)
class EvidenceOperationError:
    code: Literal[
        "consent_required", "human_required", "stale_source", "invalid_evidence", "io_error"
    ]
    message: str


@dataclass(frozen=True, slots=True)
class SourceSpan:
    source_id: str
    source_hash: str
    extracted_hash: str
    start_line: int
    end_line: int
    lines: tuple[str, ...]
    locations: tuple[DocumentLocation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "source_id": self.source_id,
            "source_hash": self.source_hash,
            "extracted_hash": self.extracted_hash,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "lines": list(self.lines),
            "locations": [location.to_dict() for location in self.locations],
        }


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    source_id: str
    source_hash: str
    extracted_hash: str
    media_type: str
    input_format: str
    size_bytes: int
    line_count: int
    page_count: int | None
    location_count: int
    location_kinds: tuple[str, ...]
    managed_copy: bool
    imported_at: str

    @classmethod
    def from_document(cls, source: SourceDocument) -> SourceIdentity:
        return cls(
            source_id=source.source_id,
            source_hash=source.source_hash,
            extracted_hash=source.extracted_hash,
            media_type=source.media_type,
            input_format=source.input_format,
            size_bytes=source.size_bytes,
            line_count=source.line_count,
            page_count=source.page_count,
            location_count=len(source.locations),
            location_kinds=tuple(sorted({location.kind for location in source.locations})),
            managed_copy=source.managed_copy,
            imported_at=source.imported_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "location_kinds": list(self.location_kinds)}


_T = TypeVar("_T")


def require_evidence_result(result: _T | EvidenceOperationError) -> _T:
    """Preserve exception-based command/journey callers at their compatibility boundary."""
    if isinstance(result, EvidenceOperationError):
        if result.code in {"consent_required", "human_required"}:
            raise PermissionError(result.message)
        if result.code == "io_error":
            raise OSError(result.message)
        raise ValueError(result.message)
    return result


@dataclass(frozen=True, slots=True)
class InterviewSubmission:
    question_id: str
    created: bool
    revision: EvidenceRevision
    source: SourceDocument
    source_locations: tuple[DocumentLocation, ...]
    status: dict[str, object]


class EvidenceService:
    """No IO on construction; injected ports perform explicitly invoked local operations.

    Actor identity is supplied by a trusted human-confirmation boundary, never by
    document text or an agent proposal. The repository enforces exact revision/hash
    and actor checks. Operational errors are bounded records, not source payloads.
    """

    def __init__(
        self,
        repository: EvidenceRepository,
        extract_document: Callable[[str | Path], ExtractedCandidateDocument],
    ) -> None:
        self._repository = repository
        self._extract_document = extract_document

    def _execute(
        self, operation: Callable[[], _T], *, consent_required: bool = False
    ) -> _T | EvidenceOperationError:
        try:
            if consent_required and not self._repository.has_consent():
                return EvidenceOperationError(
                    "consent_required", "Operation requires explicit local consent"
                )
            return operation()
        except PermissionError as exc:
            # OS access failures carry errno; local authority refusals do not.
            if exc.errno is not None:
                return EvidenceOperationError("io_error", "Local evidence operation failed")
            return EvidenceOperationError(
                "human_required", "Operation requires local-user authority"
            )
        except ValueError:
            return EvidenceOperationError(
                "invalid_evidence", "Evidence input or current state is invalid"
            )
        except OSError:
            return EvidenceOperationError("io_error", "Local evidence operation failed")

    def import_document(self, path: str | Path) -> SourceDocument | EvidenceOperationError:
        return self._execute(
            lambda: self._repository.import_extracted_document(self._extract_document(path)),
            consent_required=True,
        )

    def propose(self, proposal: EvidenceProposalInput) -> EvidenceRevision | EvidenceOperationError:
        def operation() -> EvidenceRevision | EvidenceOperationError:
            source = self._repository.source(proposal.source_id)
            if (
                source.source_hash != proposal.source_hash
                or source.extracted_hash != proposal.extracted_hash
            ):
                return EvidenceOperationError("stale_source", "Proposal source identity is stale")
            if proposal.actor not in PROPOSAL_ACTORS - {"synthetic_fixture"}:
                raise ValueError("Public proposals must identify a non-fixture origin")
            return self._repository.propose(
                source_id=proposal.source_id,
                kind=proposal.kind,
                statement=proposal.statement,
                details=proposal.details,
                source_start_line=proposal.source_start_line,
                source_end_line=proposal.source_end_line,
                ownership=proposal.ownership,
                confidence=proposal.confidence,
                approximation=proposal.approximation,
                publication_boundary=proposal.publication_boundary,
                metadata=proposal.metadata,
                actor=proposal.actor,
                reason=proposal.reason,
            )

        return self._execute(operation, consent_required=True)

    def has_consent(self) -> bool | EvidenceOperationError:
        return self._execute(
            lambda: self._repository.has_consent(),
        )

    def record_consent(
        self, *, accepted: bool, actor: str = "local_user"
    ) -> str | EvidenceOperationError:
        return self._execute(
            lambda: self._repository.record_consent(accepted=accepted, actor=actor),
        )

    def source(self, source_id: str) -> SourceDocument | EvidenceOperationError:
        return self._execute(
            lambda: self._repository.source(source_id=source_id),
        )

    def list_sources(self) -> tuple[SourceIdentity, ...] | EvidenceOperationError:
        return self._execute(
            lambda: tuple(
                SourceIdentity.from_document(source) for source in self._repository.sources()
            ),
            consent_required=True,
        )

    def list_current(
        self, *, limit: int = 20, cursor: str | None = None
    ) -> InventoryPage[EvidenceCard] | EvidenceOperationError:
        """Current cards, including non-approved states; never a publication approval."""
        return self._execute(
            lambda: self._repository.list_current(limit=limit, cursor=cursor),
            consent_required=True,
        )

    def source_locations(
        self,
        source_id: str,
        start_line: int,
        end_line: int,
    ) -> tuple[DocumentLocation, ...] | EvidenceOperationError:
        return self._execute(
            lambda: self._repository.source_locations(
                source_id=source_id, start_line=start_line, end_line=end_line
            ),
        )

    def read_source_span(
        self,
        source_id: str,
        start_line: int,
        end_line: int,
    ) -> SourceSpan | EvidenceOperationError:
        def operation() -> SourceSpan:
            if (
                type(start_line) is not int
                or type(end_line) is not int
                or start_line < 1
                or end_line < start_line
                or end_line - start_line + 1 > MAX_SOURCE_SPAN_LINES
            ):
                raise ValueError("Source span must select 1-100 ordered lines")
            source = self._repository.source(source_id)
            lines = self._repository.source_lines(source_id, start_line, end_line)
            if len("\n".join(lines).encode("utf-8")) > MAX_SOURCE_SPAN_BYTES:
                raise ValueError("Source span exceeds the byte limit")
            locations = self._repository.source_locations(source_id, start_line, end_line)
            return SourceSpan(
                source_id=source.source_id,
                source_hash=source.source_hash,
                extracted_hash=source.extracted_hash,
                start_line=start_line,
                end_line=end_line,
                lines=lines,
                locations=locations,
            )

        return self._execute(operation, consent_required=True)

    def latest(self, evidence_id: str) -> EvidenceRevision | EvidenceOperationError:
        return self._execute(
            lambda: self._repository.latest(evidence_id=evidence_id),
        )

    def review_queue(self) -> tuple[EvidenceRevision, ...] | EvidenceOperationError:
        return self._execute(
            lambda: self._repository.review_queue(),
        )

    def edit(
        self,
        evidence_id: str,
        *,
        expected_revision: int,
        statement: str | None = None,
        details: Mapping[str, str] | None = None,
        ownership: Ownership | None = None,
        confidence: Confidence | None = None,
        approximation: Approximation | None = None,
        publication_boundary: PublicationBoundary | None = None,
        metadata: EvidenceContractMetadata | None = None,
        actor: str = "local_user",
    ) -> EvidenceRevision | EvidenceOperationError:
        return self._execute(
            lambda: self._repository.edit(
                evidence_id=evidence_id,
                expected_revision=expected_revision,
                statement=statement,
                details=details,
                ownership=ownership,
                confidence=confidence,
                approximation=approximation,
                publication_boundary=publication_boundary,
                metadata=metadata,
                actor=actor,
            ),
            consent_required=True,
        )

    def review(
        self,
        evidence_id: str,
        revision: int,
        *,
        action: ReviewAction,
        expected_content_hash: str,
        actor: str,
        reason: str | None = None,
    ) -> EvidenceRevision | EvidenceOperationError:
        return self._execute(
            lambda: self._repository.review(
                evidence_id=evidence_id,
                revision=revision,
                action=action,
                expected_content_hash=expected_content_hash,
                actor=actor,
                reason=reason,
            ),
            consent_required=True,
        )

    def readiness(self) -> EvidenceReadiness | EvidenceOperationError:
        return self._execute(
            lambda: self._repository.readiness(),
        )

    def validate_publication(
        self, request: PublicationRequest
    ) -> PublicationReadiness | EvidenceOperationError:
        return self._execute(
            lambda: self._repository.validate_publication(request=request),
        )

    def verify(
        self, evidence_id: str, revision: int, *, expected_content_hash: str, actor: str
    ) -> EvidenceRevision | EvidenceOperationError:
        return self._execute(
            lambda: self._repository.verify(
                evidence_id, revision, expected_content_hash=expected_content_hash, actor=actor
            ),
            consent_required=True,
        )

    def interview_status(self) -> dict[str, object] | EvidenceOperationError:
        return self._execute(lambda: _interview_status(self._repository), consent_required=True)

    def submit_interview_answer(
        self, *, display_name: str, content: bytes
    ) -> InterviewSubmission | EvidenceOperationError:
        def operation() -> InterviewSubmission | EvidenceOperationError:
            with self._repository.transaction():
                if not self._repository.has_consent():
                    return EvidenceOperationError(
                        "consent_required", "Operation requires explicit local consent"
                    )
                if not 1 <= len(content) <= MAX_PROPOSAL_BYTES:
                    raise ValueError("Interview answer must contain 1-65536 bytes")
                answer = InterviewAnswerInput.from_dict(json.loads(content.decode("utf-8")))
                source_hash = hashlib.sha256(content).hexdigest()
                evidence_id = f"interview-{answer.question.question_id}-{source_hash[:16]}"
                completed = _completed_interview_questions(self._repository)
                existing = completed.get(answer.question.question_id)
                created = existing is None
                if existing is not None:
                    if existing.evidence_id != evidence_id:
                        raise ValueError(
                            "Interview question already has an answer; review or edit that proposal first"
                        )
                    revision = existing
                    source = self._repository.source(revision.source_id)
                else:
                    source = self._repository.import_document(
                        display_name=display_name,
                        media_type="text/plain",
                        content=content,
                    )
                    metadata = EvidenceContractMetadata(
                        origin_ownership=answer.ownership,
                        role_family_relevance=answer.role_family_relevance,
                        intended_channels=_intended_channels(answer.publication_boundary),
                    )
                    revision = self._repository.propose(
                        evidence_id=evidence_id,
                        source_id=source.source_id,
                        kind=answer.question.kind,
                        statement=answer.question.statement_template.format_map(answer.answers),
                        details=answer.answers,
                        source_start_line=1,
                        source_end_line=source.line_count,
                        ownership=answer.ownership,
                        confidence=answer.confidence,
                        approximation=answer.approximation,
                        publication_boundary=answer.publication_boundary,
                        metadata=metadata,
                        actor="deterministic_rule",
                        reason=f"Guided evidence interview answer for {answer.question.question_id}",
                    )
                source_locations = self._repository.source_locations(
                    revision.source_id,
                    revision.source_start_line,
                    revision.source_end_line,
                )
                status = _interview_status(self._repository)
                return InterviewSubmission(
                    answer.question.question_id, created, revision, source, source_locations, status
                )

        return self._execute(operation, consent_required=True)


def _completed_interview_questions(vault: EvidenceRepository) -> dict[str, EvidenceRevision]:
    records = vault.enumerate()
    completed: dict[str, EvidenceRevision] = {}
    for question in INTERVIEW_QUESTIONS:
        prefix = f"interview-{question.question_id}-"
        matches = tuple(record for record in records if record.evidence_id.startswith(prefix))
        if len(matches) > 1:
            raise ValueError(f"Interview question {question.question_id} has conflicting answers")
        if matches:
            completed[question.question_id] = vault.latest(matches[0].evidence_id)
    return completed


def _interview_status(vault: EvidenceRepository) -> dict[str, object]:
    completed = _completed_interview_questions(vault)
    next_question = next(
        (question for question in INTERVIEW_QUESTIONS if question.question_id not in completed),
        None,
    )
    return {
        "interview_version": 1,
        "completed_questions": len(completed),
        "total_questions": len(INTERVIEW_QUESTIONS),
        "next_question": next_question.to_dict() if next_question is not None else None,
        "answer_contract": {
            "schema_version": 1,
            "required_top_level_fields": sorted(_INTERVIEW_ANSWER_FIELDS),
            "ownership_choices": sorted(OWNERSHIP_VALUES),
            "confidence_choices": sorted(CONFIDENCE_VALUES),
            "approximation_choices": sorted(APPROXIMATION_VALUES),
            "publication_boundary_choices": sorted(PUBLICATION_BOUNDARIES),
        },
        "answer_file_boundary": "inside_private_instance",
        "proposal_status": "unapproved",
        "proposal_origin": "deterministic_rule",
        "requires_user_review": True,
        "model_calls": False,
        "external_actions": False,
    }


def _intended_channels(boundary: PublicationBoundary) -> tuple[PublicationUse, ...]:
    return {
        "private": (),
        "application": ("application",),
        "application_and_interview": ("application", "interview"),
        "interview": ("interview",),
        "public": ("public",),
    }[boundary]

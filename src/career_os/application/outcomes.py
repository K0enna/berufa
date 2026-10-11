"""Confirmed career facts and descriptive reporting over one V2 event authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal, Protocol, TypeVar

from career_os.opportunity_ids import validate_opportunity_id
from career_os.outcome_records import (
    OutcomeLearningEvent,
    OutcomeLearningReport,
    _as_of_timestamp,
    _canonical_json,
    _semantic_payload,
    _timestamp_instant,
    calculate_report,
    create_interview_debrief_event,
    create_outcome_event,
    create_review_acknowledgement,
    create_submission_event,
)

MAX_EVENT_REQUEST_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class SubmissionInput:
    job_key: str
    company: str
    title: str
    discovery_source: str
    application_channel: str
    confirmation_method: str
    job_content_hash: str
    fit_score: float | None = None
    scorecard_version: str | None = None
    artifact_hashes: dict[str, str] | None = None
    provider: str | None = None
    external_id: str | None = None
    employer_job_id: str | None = None
    canonical_job_url: str | None = None
    application_url: str | None = None
    assessment_hash: str | None = None
    effective_scoring_fingerprint: str | None = None
    assessment_basis: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class OutcomeInput:
    job_key: str
    company: str
    title: str
    stage: str
    outcome: str
    reason_category: str
    evidence_quality: str
    employer_stated_reason: str = ""


@dataclass(frozen=True, slots=True)
class DebriefInput:
    job_key: str
    company: str
    title: str
    interview_stage: str
    quality_signal: str
    failure_point: str
    confidence: str
    summary: str = ""


_INPUT_TYPES = {
    "submission_confirmed": SubmissionInput,
    "outcome_recorded": OutcomeInput,
    "interview_debrief_recorded": DebriefInput,
}
_PUBLIC_OUTCOMES = {
    "application": {"rejected", "withdrawn", "no_response", "closed"},
    "interview": {"scheduled", "completed", "advanced", "rejected", "withdrawn", "closed"},
    "final_interview": {"scheduled", "completed", "advanced", "rejected", "withdrawn", "closed"},
    "offer": {"offer_received", "accepted", "declined", "withdrawn", "closed"},
}


def _request_bytes(payload: object) -> bytes:
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Event request must be finite, valid UTF-8 JSON") from exc
    if len(encoded) > MAX_EVENT_REQUEST_BYTES:
        raise ValueError("Event request exceeds the byte limit")
    return encoded


@dataclass(frozen=True, slots=True)
class EventRequest:
    event_type: str
    opportunity_id: str
    occurred_at: str
    details: SubmissionInput | OutcomeInput | DebriefInput

    @classmethod
    def from_dict(cls, payload: object) -> EventRequest:
        _request_bytes(payload)
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "event_type",
            "opportunity_id",
            "occurred_at",
            "details",
        }:
            raise ValueError("Event request must contain exactly the documented fields")
        if type(payload["schema_version"]) is not int or payload["schema_version"] not in {1, 2}:
            raise ValueError("Unsupported event request version")
        kind = payload["event_type"]
        if not isinstance(kind, str) or kind not in _INPUT_TYPES:
            raise ValueError("Unsupported public event type")
        if payload["schema_version"] == 2 and kind != "submission_confirmed":
            raise ValueError("Request version 2 is reserved for submission attribution")
        if not isinstance(payload["opportunity_id"], str):
            raise ValueError("Opportunity id must be a string")
        identity = validate_opportunity_id(payload["opportunity_id"])
        _timestamp_instant(payload["occurred_at"], "occurred_at")
        raw = payload["details"]
        if not isinstance(raw, dict):
            raise ValueError("Event details must be an object")
        if (
            kind == "submission_confirmed"
            and payload["schema_version"] == 1
            and (
                raw.get("fit_score") is None
                or raw.get("scorecard_version") is None
                or "assessment_basis" in raw
            )
        ):
            raise ValueError("V1 submission requests require supplied numeric scoring")
        if (
            kind == "submission_confirmed"
            and "effective_scoring_fingerprint" in raw
            and raw["effective_scoring_fingerprint"] is None
        ):
            raise ValueError("Effective scoring fingerprint must be omitted when unknown")
        try:
            details = _INPUT_TYPES[kind](**raw)
        except TypeError as exc:
            raise ValueError("Event details contain missing or unsupported fields") from exc
        return cls(kind, identity, payload["occurred_at"], details)

    def to_dict(self) -> dict[str, Any]:
        details = asdict(self.details)
        version = 1
        if isinstance(self.details, SubmissionInput):
            if self.details.fit_score is None or self.details.assessment_basis is not None:
                version = 2
            if self.details.assessment_basis is None:
                details.pop("assessment_basis")
        if (
            isinstance(self.details, SubmissionInput)
            and self.details.effective_scoring_fingerprint is None
        ):
            details.pop("effective_scoring_fingerprint")
        return {
            "schema_version": version,
            "event_type": self.event_type,
            "opportunity_id": self.opportunity_id,
            "occurred_at": self.occurred_at,
            "details": details,
        }


def _candidate(request: EventRequest) -> OutcomeLearningEvent:
    request = EventRequest.from_dict(request.to_dict())
    values = asdict(request.details)
    common = {"opportunity_id": request.opportunity_id, "confirmed": True}
    if isinstance(request.details, SubmissionInput):
        return create_submission_event(**values, **common, submitted_at=request.occurred_at)
    if isinstance(request.details, OutcomeInput):
        event = create_outcome_event(**values, **common, occurred_at=request.occurred_at)
        if event.payload["outcome"] not in _PUBLIC_OUTCOMES.get(event.payload["stage"], set()):
            raise ValueError("Outcome is not a supported hiring-stage fact")
        return event
    return create_interview_debrief_event(**values, **common, occurred_at=request.occurred_at)


def _fingerprint(event: OutcomeLearningEvent) -> str:
    semantic = _semantic_payload(
        event_type=event.event_type,
        opportunity_id=event.opportunity_id,
        occurred_at=event.occurred_at,
        payload=event.payload,
        schema_version=event.schema_version,
    )
    return hashlib.sha256(_canonical_json(semantic).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EventProposal:
    request: EventRequest
    event_id: str
    confirmation_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": "proposal",
            "external_event_confirmed": False,
            "ledger_changed": False,
            "request": self.request.to_dict(),
            "event_id": self.event_id,
            "confirmation_fingerprint": self.confirmation_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class EventReceipt:
    event: OutcomeLearningEvent
    appended: bool
    relative_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventRepository(Protocol):
    def list(self) -> tuple[OutcomeLearningEvent, ...]: ...

    def append(
        self, build: Callable[[tuple[OutcomeLearningEvent, ...]], OutcomeLearningEvent]
    ) -> EventReceipt: ...


@dataclass(frozen=True, slots=True)
class OutcomeOperationError:
    code: Literal["human_required", "invalid_event", "io_error"]
    message: str


_T = TypeVar("_T")


def require_outcome_result(result: _T | OutcomeOperationError) -> _T:
    if isinstance(result, OutcomeOperationError):
        if result.code == "human_required":
            raise PermissionError(result.message)
        if result.code == "io_error":
            raise OSError(result.message)
        raise ValueError(result.message)
    return result


def _execute(operation: Callable[[], _T]) -> _T | OutcomeOperationError:
    try:
        return operation()
    except PermissionError as exc:
        if exc.errno is None:
            return OutcomeOperationError(
                "human_required", "Exact local-user confirmation is required"
            )
        return OutcomeOperationError("io_error", "Local event storage is unavailable")
    except ValueError:
        return OutcomeOperationError(
            "invalid_event", "Event input, report basis or retained state is invalid"
        )
    except OSError:
        return OutcomeOperationError("io_error", "Local event storage is unavailable")


class CareerEventService:
    def __init__(
        self,
        repository: EventRepository | None = None,
        *,
        assessment_resolver: Callable[[str], dict[str, str]] | None = None,
    ) -> None:
        self._repository = repository
        self._assessment_resolver = assessment_resolver

    def _check_basis(self, event: OutcomeLearningEvent) -> None:
        basis = event.payload.get("assessment_basis")
        if basis is not None and (
            self._assessment_resolver is None
            or self._assessment_resolver(event.opportunity_id) != basis
        ):
            raise ValueError("Qualitative assessment basis is stale or unavailable")

    def _events(self) -> tuple[OutcomeLearningEvent, ...]:
        if self._repository is None:
            raise OSError("No local event repository was supplied")
        return self._repository.list()

    def propose(self, request: EventRequest) -> EventProposal | OutcomeOperationError:
        def preview() -> EventProposal:
            event = _candidate(request)
            if event.payload.get("assessment_basis") is not None and not any(
                retained.event_id == event.event_id for retained in self._events()
            ):
                self._check_basis(event)
            input_type = _INPUT_TYPES[event.event_type]
            normalized = input_type(
                **{
                    field.name: event.payload[field.name]
                    for field in fields(input_type)
                    if field.name in event.payload
                }
            )
            return EventProposal(
                EventRequest(event.event_type, event.opportunity_id, event.occurred_at, normalized),
                event.event_id,
                _fingerprint(event),
            )

        return _execute(preview)

    def _record(
        self,
        build: Callable[[tuple[OutcomeLearningEvent, ...]], OutcomeLearningEvent],
        *,
        actor: str,
    ) -> EventReceipt:
        if actor != "local_user":
            raise PermissionError("Only the local user may confirm a record")
        if self._repository is None:
            raise OSError("No local event repository was supplied")
        return self._repository.append(build)

    def confirm(
        self, request: EventRequest, *, confirmation_fingerprint: str, actor: str
    ) -> EventReceipt | OutcomeOperationError:
        def record() -> EventReceipt:
            if actor != "local_user":
                raise PermissionError("Only the local user may confirm an event")
            event = _candidate(request)
            if confirmation_fingerprint != _fingerprint(event):
                raise PermissionError("Event changed after preview")

            def build(existing: tuple[OutcomeLearningEvent, ...]) -> OutcomeLearningEvent:
                if not any(retained.event_id == event.event_id for retained in existing):
                    self._check_basis(event)
                return event

            return self._record(build, actor=actor)

        return _execute(record)

    def list(
        self, *, opportunity_id: str | None = None
    ) -> tuple[OutcomeLearningEvent, ...] | OutcomeOperationError:
        def read() -> tuple[OutcomeLearningEvent, ...]:
            if opportunity_id is not None:
                if not isinstance(opportunity_id, str):
                    raise ValueError("Opportunity id must be a string")
                identity = validate_opportunity_id(opportunity_id)
                return tuple(event for event in self._events() if event.opportunity_id == identity)
            return self._events()

        return _execute(read)


def _evidence_fingerprint(events: tuple[OutcomeLearningEvent, ...]) -> str:
    hashes = [event.record_sha256 for event in events if event.event_type != "review_acknowledged"]
    return hashlib.sha256(_canonical_json(hashes).encode("utf-8")).hexdigest()


def _report_inputs(
    events: tuple[OutcomeLearningEvent, ...],
    as_of: str | None,
    maturity_business_days: int,
    review_interval: int,
) -> tuple[tuple[OutcomeLearningEvent, ...], str]:
    for value in (maturity_business_days, review_interval):
        if type(value) is not int or not 1 <= value <= 20_000:
            raise ValueError("Report windows must be integers from 1 to 20,000")
    timestamp = _as_of_timestamp(as_of)
    cutoff = _timestamp_instant(timestamp, "as_of")
    return tuple(
        event for event in events if _timestamp_instant(event.recorded_at, "recorded_at") <= cutoff
    ), timestamp


@dataclass(frozen=True, slots=True)
class OutcomeReport:
    report: OutcomeLearningReport
    ledger_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": self.report.to_dict(),
            "ledger_fingerprint": self.ledger_fingerprint,
            "limitations": [
                "Descriptive counts of user-confirmed records, not causal evidence",
                "Small samples, missing submissions and immature cohorts limit interpretation",
                "Candidate debriefs and inferred reasons are not employer-confirmed explanations",
                "No automatic scoring, pipeline, or external-action changes",
            ],
        }


class OutcomeService:
    def __init__(self, events: CareerEventService) -> None:
        self._events = events

    def report(
        self,
        *,
        as_of: str | None = None,
        maturity_business_days: int = 10,
        review_interval: int = 10,
    ) -> OutcomeReport | OutcomeOperationError:
        def project() -> OutcomeReport:
            events = self._events._events()
            visible, timestamp = _report_inputs(
                events, as_of, maturity_business_days, review_interval
            )
            return OutcomeReport(
                calculate_report(
                    visible,
                    as_of=timestamp,
                    maturity_business_days=maturity_business_days,
                    review_interval=review_interval,
                ),
                _evidence_fingerprint(events),
            )

        return _execute(project)

    def acknowledge(
        self,
        *,
        report_basis_hash: str,
        ledger_fingerprint: str,
        as_of: str,
        maturity_business_days: int = 10,
        review_interval: int = 10,
        review_scope: str = "both",
        actor: str,
    ) -> EventReceipt | OutcomeOperationError:
        def build(events: tuple[OutcomeLearningEvent, ...]) -> OutcomeLearningEvent:
            if ledger_fingerprint != _evidence_fingerprint(events):
                raise ValueError("Evidence changed after report generation")
            if not isinstance(as_of, str):
                raise ValueError("The exact report as_of is required")
            visible, timestamp = _report_inputs(
                events, as_of, maturity_business_days, review_interval
            )
            return create_review_acknowledgement(
                visible,
                report_basis_hash=report_basis_hash,
                as_of=timestamp,
                maturity_business_days=maturity_business_days,
                review_interval=review_interval,
                review_scope=review_scope,
                confirmed=True,
            )

        return _execute(lambda: self._events._record(build, actor=actor))

"""Canonical Outcome Learning V2 values, validation and descriptive report policy."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from career_os.opportunity_ids import validate_opportunity_id as _validate_opportunity_id
from career_os.outcome_values import INTERVIEW_STAGES, OFFER_OUTCOMES
from career_os.outcome_values import create_outcome_event as create_legacy_outcome_event

SCHEMA_VERSION = 2


EVENT_TYPES = {
    "submission_confirmed",
    "outcome_recorded",
    "interview_debrief_recorded",
    "review_acknowledged",
}


DISCOVERY_SOURCES = {
    "direct_company",
    "job_board",
    "linkedin",
    "market_radar",
    "monitored_source",
    "other",
    "recruiter",
    "referral",
    "user_supplied",
    "web_search",
}


APPLICATION_CHANNELS = {
    "email",
    "employer_ats",
    "linkedin",
    "other",
    "recruiter",
    "referral",
}


CONFIRMATION_METHODS = {
    "candidate_confirmed_sent",
    "employer_confirmation_email",
    "employer_confirmation_page",
    "recruiter_confirmation",
}


ARTIFACT_KINDS = {
    "application_form",
    "case_study",
    "cover_letter",
    "cv",
    "other",
    "portfolio",
}


INTERVIEW_DEBRIEF_STAGES = {
    "case",
    "final",
    "hiring_manager",
    "other",
    "panel",
    "recruiter_screen",
}


QUALITY_SIGNALS = {
    "mixed",
    "negative",
    "positive",
    "strong_positive",
    "unknown",
}


FAILURE_POINTS = {
    "candidate_evidence",
    "company_problem",
    "employer_process",
    "interview_execution",
    "practical_constraints",
    "role_scope",
    "unknown",
}


CONFIDENCE_LEVELS = {"low", "medium", "high"}


REVIEW_SCOPES = {"both", "outcome_learning", "submission_hygiene"}


OUTCOME_ORIGINS = {"human_confirmed", "legacy_v1"}


MAX_LEDGER_BYTES = 10_000_000


MAX_LEDGER_EVENTS = 20_000


DEFAULT_MATURITY_BUSINESS_DAYS = 10


DEFAULT_REVIEW_INTERVAL = 10


PORTFOLIO_OPPORTUNITY_ID = "portfolio:outcome-learning"


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


_SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,79}")


_EVENT_ID_PATTERN = re.compile(r"olv[23]-[0-9a-f]{32}")


_ENVELOPE_FIELDS = {
    "schema_version",
    "event_id",
    "record_sha256",
    "event_type",
    "opportunity_id",
    "occurred_at",
    "recorded_at",
    "payload",
}


_PAYLOAD_FIELDS = {
    "submission_confirmed": {
        "job_key",
        "company",
        "title",
        "provider",
        "external_id",
        "employer_job_id",
        "canonical_job_url",
        "application_url",
        "discovery_source",
        "application_channel",
        "confirmation_method",
        "confirmed_by",
        "job_content_hash",
        "fit_score",
        "scorecard_version",
        "effective_scoring_fingerprint",
        "artifact_hashes",
        "assessment_hash",
    },
    "outcome_recorded": {
        "job_key",
        "company",
        "title",
        "stage",
        "outcome",
        "employer_stated_reason",
        "reason_category",
        "evidence_quality",
        "origin",
        "confirmed_by",
        "legacy_record_sha256",
    },
    "interview_debrief_recorded": {
        "job_key",
        "company",
        "title",
        "interview_stage",
        "quality_signal",
        "failure_point",
        "confidence",
        "summary",
        "confirmed_by",
    },
    "review_acknowledged": {
        "report_basis_hash",
        "report_as_of",
        "confirmed_submissions",
        "mature_submissions",
        "maturity_business_days",
        "review_interval",
        "review_scope",
        "confirmed_by",
    },
}


_REQUIRED_PAYLOAD_FIELDS = {
    "submission_confirmed": {
        "job_key",
        "company",
        "title",
        "provider",
        "external_id",
        "employer_job_id",
        "canonical_job_url",
        "application_url",
        "discovery_source",
        "application_channel",
        "confirmation_method",
        "confirmed_by",
        "job_content_hash",
        "fit_score",
        "scorecard_version",
        "artifact_hashes",
        "assessment_hash",
    },
    "outcome_recorded": {
        "job_key",
        "company",
        "title",
        "stage",
        "outcome",
        "employer_stated_reason",
        "reason_category",
        "evidence_quality",
        "origin",
    },
    "interview_debrief_recorded": _PAYLOAD_FIELDS["interview_debrief_recorded"],
    "review_acknowledged": _PAYLOAD_FIELDS["review_acknowledged"],
}


@dataclass(frozen=True, slots=True)
class OutcomeLearningEvent:
    schema_version: int
    event_id: str
    record_sha256: str
    event_type: str
    opportunity_id: str
    occurred_at: str
    recorded_at: str
    payload: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OutcomeLearningEvent:
        if not isinstance(raw, dict):
            raise ValueError("Outcome Learning event must be an object")
        unknown = set(raw) - _ENVELOPE_FIELDS
        missing = _ENVELOPE_FIELDS - set(raw)
        if unknown:
            raise ValueError(
                "Outcome Learning event has unsupported fields: " + ", ".join(sorted(unknown))
            )
        if missing:
            raise ValueError(
                "Outcome Learning event is missing fields: " + ", ".join(sorted(missing))
            )
        event = cls(
            schema_version=raw["schema_version"],
            event_id=raw["event_id"],
            record_sha256=raw["record_sha256"],
            event_type=raw["event_type"],
            opportunity_id=raw["opportunity_id"],
            occurred_at=raw["occurred_at"],
            recorded_at=raw["recorded_at"],
            payload=raw["payload"],
        )
        validate_event(event)
        return event

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OutcomeLearningReport:
    schema_version: int
    as_of: str
    maturity_business_days: int
    review_interval: int
    events_considered: int
    confirmed_submissions: int
    scored_submissions: int
    unscored_submissions: int
    mature_scored_submissions: int
    mature_unscored_submissions: int
    mature_submissions: int
    immature_submissions: int
    mature_ever_interviewed: int
    mature_offers: int
    mature_hires: int
    application_to_interview_rate: float | None
    application_to_offer_rate: float | None
    application_to_hire_rate: float | None
    interview_debriefs: int
    legacy_outcomes_excluded: int
    unmatched_outcomes_excluded: int
    segments: dict[str, dict[str, dict[str, float | int | None]]]
    last_reviewed_at: str | None
    last_submission_reviewed_at: str | None
    last_outcome_reviewed_at: str | None
    confirmed_since_review: int
    mature_since_review: int
    review_due: bool
    review_due_reasons: tuple[str, ...]
    data_quality_warnings: tuple[str, ...]
    basis_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(
    value: object,
    field: str,
    *,
    maximum: int,
    required: bool = True,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Outcome Learning {field} must be a string")
    normalized = " ".join(value.split())
    if required and not normalized:
        raise ValueError(f"Outcome Learning {field} is required")
    if len(normalized) > maximum:
        raise ValueError(f"Outcome Learning {field} must be at most {maximum} characters")
    return normalized


def _choice(value: object, field: str, choices: set[str]) -> str:
    normalized = _text(value, field, maximum=100).casefold()
    if normalized not in choices:
        raise ValueError(f"Outcome Learning {field} must be one of: {', '.join(sorted(choices))}")
    return normalized


def _timestamp(value: object | None, field: str, *, default_now: bool = False) -> str:
    if value is None and default_now:
        return datetime.now(UTC).replace(microsecond=0).isoformat()
    return _timestamp_instant(value, field).replace(microsecond=0).isoformat()


def _timestamp_instant(value: object | None, field: str) -> datetime:
    """Parse one authority timestamp without discarding its instant precision."""

    normalized = _text(value, field, maximum=100)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Outcome Learning {field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Outcome Learning {field} must include a timezone")
    return parsed.astimezone(UTC)


def _timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _as_of_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0).isoformat()
    normalized = _text(value, "as_of", maximum=100)
    try:
        parsed_date = date.fromisoformat(normalized)
    except ValueError:
        parsed_date = None
    if parsed_date is None:
        timestamp = _timestamp(normalized, "as_of")
        if _timestamp_value(timestamp) > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("Outcome Learning as_of cannot be in the future")
        return timestamp
    if parsed_date > datetime.now(UTC).date():
        raise ValueError("Outcome Learning as_of cannot be in the future")
    return (
        datetime.combine(parsed_date, datetime.max.time(), UTC).replace(microsecond=0).isoformat()
    )


def _sha256(value: object, field: str, *, required: bool = True) -> str:
    normalized = _text(value, field, maximum=64, required=required).casefold()
    if normalized and not _SHA256_PATTERN.fullmatch(normalized):
        raise ValueError(f"Outcome Learning {field} must be a lowercase SHA-256 value")
    return normalized


def _strict_sha256(value: object, field: str) -> str:
    normalized = _sha256(value, field)
    if value != normalized:
        raise ValueError(f"Outcome Learning {field} must be a lowercase SHA-256 value")
    return normalized


def _optional_text(value: object, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = _text(value, field, maximum=maximum, required=False)
    return normalized or None


def _url(value: object, field: str) -> str | None:
    normalized = _optional_text(value, field, maximum=2_000)
    if normalized is not None and not normalized.casefold().startswith("https://"):
        raise ValueError(f"Outcome Learning {field} must use HTTPS")
    return normalized


def _slug(value: object, field: str, *, required: bool = False) -> str | None:
    normalized = _optional_text(value, field, maximum=80)
    if normalized is None:
        if required:
            raise ValueError(f"Outcome Learning {field} is required")
        return None
    normalized = normalized.casefold()
    if not _SLUG_PATTERN.fullmatch(normalized):
        raise ValueError(f"Outcome Learning {field} must be a lowercase identifier")
    return normalized


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _semantic_payload(
    *,
    event_type: str,
    opportunity_id: str,
    occurred_at: str,
    payload: Mapping[str, Any],
    schema_version: int = SCHEMA_VERSION,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "event_type": event_type,
        "opportunity_id": opportunity_id,
        "occurred_at": occurred_at,
        "payload": dict(payload),
    }


def _event_id(
    *,
    event_type: str,
    opportunity_id: str,
    occurred_at: str,
    payload: Mapping[str, Any],
    schema_version: int = SCHEMA_VERSION,
) -> str:
    semantic = _semantic_payload(
        event_type=event_type,
        opportunity_id=opportunity_id,
        occurred_at=occurred_at,
        payload=payload,
        schema_version=schema_version,
    )
    digest = hashlib.sha256(_canonical_json(semantic).encode("utf-8")).hexdigest()
    return f"olv{schema_version}-{digest[:32]}"


def _record_sha256(event: OutcomeLearningEvent) -> str:
    payload = event.to_dict()
    payload.pop("record_sha256")
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _build_event(
    *,
    event_type: str,
    opportunity_id: str,
    occurred_at: str | None,
    recorded_at: str | None,
    payload: dict[str, Any],
    schema_version: int = SCHEMA_VERSION,
) -> OutcomeLearningEvent:
    normalized_type = _choice(event_type, "event_type", EVENT_TYPES)
    normalized_id = _validate_opportunity_id(opportunity_id)
    normalized_occurred = _timestamp(
        occurred_at,
        "occurred_at",
        default_now=occurred_at is None,
    )
    normalized_recorded = _timestamp(
        recorded_at,
        "recorded_at",
        default_now=recorded_at is None,
    )
    if _timestamp_value(normalized_recorded) < _timestamp_value(normalized_occurred):
        raise ValueError("Outcome Learning recorded_at cannot precede occurred_at")
    now = datetime.now(UTC) + timedelta(minutes=5)
    if _timestamp_value(normalized_occurred) > now or _timestamp_value(normalized_recorded) > now:
        raise ValueError("Outcome Learning event timestamps cannot be in the future")
    identifier = _event_id(
        event_type=normalized_type,
        opportunity_id=normalized_id,
        occurred_at=normalized_occurred,
        payload=payload,
        schema_version=schema_version,
    )
    partial = OutcomeLearningEvent(
        schema_version=schema_version,
        event_id=identifier,
        record_sha256="0" * 64,
        event_type=normalized_type,
        opportunity_id=normalized_id,
        occurred_at=normalized_occurred,
        recorded_at=normalized_recorded,
        payload=payload,
    )
    event = OutcomeLearningEvent(
        schema_version=partial.schema_version,
        event_id=partial.event_id,
        record_sha256=_record_sha256(partial),
        event_type=partial.event_type,
        opportunity_id=partial.opportunity_id,
        occurred_at=partial.occurred_at,
        recorded_at=partial.recorded_at,
        payload=partial.payload,
    )
    validate_event(event)
    return event


def _confirmed(confirmed: bool) -> None:
    if confirmed is not True:
        raise ValueError("Outcome Learning mutation requires explicit human confirmation")


def _artifact_hashes(values: Mapping[str, str] | None) -> dict[str, str]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise ValueError("Outcome Learning artifact_hashes must be an object")
    result: dict[str, str] = {}
    for raw_kind, raw_hash in values.items():
        kind = _choice(raw_kind, "artifact kind", ARTIFACT_KINDS)
        if kind in result:
            raise ValueError(f"Outcome Learning artifact kind is duplicated: {kind}")
        result[kind] = _sha256(raw_hash, f"artifact_hashes.{kind}")
    return dict(sorted(result.items()))


def parse_artifact_hashes(values: Iterable[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("Artifact hashes must use KIND=SHA256")
        kind, digest = value.split("=", 1)
        normalized_kind = _choice(kind, "artifact kind", ARTIFACT_KINDS)
        if normalized_kind in parsed:
            raise ValueError(f"Outcome Learning artifact kind is duplicated: {normalized_kind}")
        parsed[normalized_kind] = _sha256(digest, f"artifact_hashes.{normalized_kind}")
    return dict(sorted(parsed.items()))


def _submission_score(score: object, version: object) -> tuple[float | None, str | None]:
    if score is None and version is None:
        return None, None
    if isinstance(score, bool) or not isinstance(score, int | float):
        raise ValueError("Outcome Learning fit_score must be a number with a scorecard version")
    if not 0 <= score <= 100:
        raise ValueError("Outcome Learning fit_score must be between 0 and 100")
    return float(score), _text(version, "scorecard_version", maximum=100)


def _assessment_basis(value: object, source_hash: str) -> dict[str, str]:
    if (
        not isinstance(value, dict)
        or set(value) != {"kind", "source_hash", "evaluation_fingerprint"}
        or value["kind"] != "qualitative"
    ):
        raise ValueError("Assessment basis must identify one qualitative source and evaluation")
    if _strict_sha256(value["source_hash"], "source_hash") != source_hash:
        raise ValueError("Assessment source must match the submitted job content hash")
    _strict_sha256(value["evaluation_fingerprint"], "evaluation_fingerprint")
    return dict(value)


def create_submission_event(
    *,
    opportunity_id: str,
    job_key: str,
    company: str,
    title: str,
    discovery_source: str,
    application_channel: str,
    confirmation_method: str,
    job_content_hash: str,
    fit_score: float | None = None,
    scorecard_version: str | None = None,
    artifact_hashes: Mapping[str, str] | None = None,
    provider: str | None = None,
    external_id: str | None = None,
    employer_job_id: str | None = None,
    canonical_job_url: str | None = None,
    application_url: str | None = None,
    assessment_hash: str | None = None,
    effective_scoring_fingerprint: str | None = None,
    assessment_basis: dict[str, str] | None = None,
    submitted_at: str | None = None,
    recorded_at: str | None = None,
    confirmed: bool,
) -> OutcomeLearningEvent:
    _confirmed(confirmed)
    normalized_score, normalized_version = _submission_score(fit_score, scorecard_version)
    if normalized_score is None and effective_scoring_fingerprint is not None:
        raise ValueError("Unscored submission cannot claim an effective scoring fingerprint")
    normalized_assessment = None
    if assessment_hash is not None:
        normalized_assessment = _sha256(assessment_hash, "assessment_hash")
    payload: dict[str, Any] = {
        "job_key": _text(job_key, "job_key", maximum=500),
        "company": _text(company, "company", maximum=300),
        "title": _text(title, "title", maximum=500),
        "provider": _slug(provider, "provider"),
        "external_id": _optional_text(external_id, "external_id", maximum=300),
        "employer_job_id": _optional_text(
            employer_job_id,
            "employer_job_id",
            maximum=300,
        ),
        "canonical_job_url": _url(canonical_job_url, "canonical_job_url"),
        "application_url": _url(application_url, "application_url"),
        "discovery_source": _choice(
            discovery_source,
            "discovery_source",
            DISCOVERY_SOURCES,
        ),
        "application_channel": _choice(
            application_channel,
            "application_channel",
            APPLICATION_CHANNELS,
        ),
        "confirmation_method": _choice(
            confirmation_method,
            "confirmation_method",
            CONFIRMATION_METHODS,
        ),
        "confirmed_by": "local_user",
        "job_content_hash": _sha256(job_content_hash, "job_content_hash"),
        "fit_score": normalized_score,
        "scorecard_version": normalized_version,
        "artifact_hashes": _artifact_hashes(artifact_hashes),
        "assessment_hash": normalized_assessment,
    }
    if effective_scoring_fingerprint is not None:
        payload["effective_scoring_fingerprint"] = _strict_sha256(
            effective_scoring_fingerprint,
            "effective_scoring_fingerprint",
        )
    if assessment_basis is not None:
        payload["assessment_basis"] = _assessment_basis(
            assessment_basis, payload["job_content_hash"]
        )
    return _build_event(
        event_type="submission_confirmed",
        opportunity_id=opportunity_id,
        occurred_at=submitted_at,
        recorded_at=recorded_at,
        payload=payload,
        schema_version=3 if normalized_score is None or assessment_basis is not None else 2,
    )


def create_outcome_event(
    *,
    opportunity_id: str,
    job_key: str,
    company: str,
    title: str,
    stage: str,
    outcome: str,
    reason_category: str,
    evidence_quality: str,
    employer_stated_reason: str = "",
    occurred_at: str | None = None,
    recorded_at: str | None = None,
    confirmed: bool,
) -> OutcomeLearningEvent:
    _confirmed(confirmed)
    legacy = create_legacy_outcome_event(
        job_key=job_key,
        company=company,
        title=title,
        stage=stage,
        outcome=outcome,
        employer_stated_reason=employer_stated_reason,
        reason_category=reason_category,
        evidence_quality=evidence_quality,
        recorded_at=occurred_at,
    )
    payload = legacy.to_dict()
    payload.pop("recorded_at")
    payload.update({"origin": "human_confirmed", "confirmed_by": "local_user"})
    return _build_event(
        event_type="outcome_recorded",
        opportunity_id=opportunity_id,
        occurred_at=legacy.recorded_at,
        recorded_at=recorded_at,
        payload=payload,
    )


def _create_migrated_outcome_event(event: Any) -> OutcomeLearningEvent:
    source_payload = event.to_dict()
    source_hash = hashlib.sha256(_canonical_json(source_payload).encode("utf-8")).hexdigest()
    opportunity_id = event.opportunity_id if getattr(event, "opportunity_id", "") else ""
    if not opportunity_id:
        identity_hash = hashlib.sha256(event.job_key.encode("utf-8")).hexdigest()[:24]
        opportunity_id = f"legacy:{identity_hash}"
    payload = {
        "job_key": event.job_key,
        "company": event.company,
        "title": event.title,
        "stage": event.stage,
        "outcome": event.outcome,
        "employer_stated_reason": event.employer_stated_reason,
        "reason_category": event.reason_category,
        "evidence_quality": event.evidence_quality,
        "origin": "legacy_v1",
        "legacy_record_sha256": source_hash,
    }
    return _build_event(
        event_type="outcome_recorded",
        opportunity_id=opportunity_id,
        occurred_at=event.recorded_at,
        recorded_at=event.recorded_at,
        payload=payload,
    )


def create_interview_debrief_event(
    *,
    opportunity_id: str,
    job_key: str,
    company: str,
    title: str,
    interview_stage: str,
    quality_signal: str,
    failure_point: str,
    confidence: str,
    summary: str = "",
    occurred_at: str | None = None,
    recorded_at: str | None = None,
    confirmed: bool,
) -> OutcomeLearningEvent:
    _confirmed(confirmed)
    payload = {
        "job_key": _text(job_key, "job_key", maximum=500),
        "company": _text(company, "company", maximum=300),
        "title": _text(title, "title", maximum=500),
        "interview_stage": _choice(
            interview_stage,
            "interview_stage",
            INTERVIEW_DEBRIEF_STAGES,
        ),
        "quality_signal": _choice(quality_signal, "quality_signal", QUALITY_SIGNALS),
        "failure_point": _choice(failure_point, "failure_point", FAILURE_POINTS),
        "confidence": _choice(confidence, "confidence", CONFIDENCE_LEVELS),
        "summary": _text(summary, "summary", maximum=600, required=False),
        "confirmed_by": "local_user",
    }
    return _build_event(
        event_type="interview_debrief_recorded",
        opportunity_id=opportunity_id,
        occurred_at=occurred_at,
        recorded_at=recorded_at,
        payload=payload,
    )


def validate_event(event: OutcomeLearningEvent) -> None:
    if type(event.schema_version) is not int or event.schema_version not in {2, 3}:
        raise ValueError("Outcome Learning requires schema_version 2 or 3")
    if event.schema_version == 3 and event.event_type != "submission_confirmed":
        raise ValueError("Schema version 3 is reserved for submission attribution")
    if not isinstance(event.event_id, str) or not _EVENT_ID_PATTERN.fullmatch(event.event_id):
        raise ValueError("Outcome Learning event_id is malformed")
    _sha256(event.record_sha256, "record_sha256")
    event_type = _choice(event.event_type, "event_type", EVENT_TYPES)
    opportunity_id = _validate_opportunity_id(event.opportunity_id)
    occurred_at = _timestamp(event.occurred_at, "occurred_at")
    recorded_at = _timestamp(event.recorded_at, "recorded_at")
    if _timestamp_value(recorded_at) < _timestamp_value(occurred_at):
        raise ValueError("Outcome Learning recorded_at cannot precede occurred_at")
    now = datetime.now(UTC) + timedelta(minutes=5)
    if _timestamp_value(occurred_at) > now or _timestamp_value(recorded_at) > now:
        raise ValueError("Outcome Learning event timestamps cannot be in the future")
    if not isinstance(event.payload, dict):
        raise ValueError("Outcome Learning payload must be an object")
    allowed = _PAYLOAD_FIELDS[event_type] | (
        {"assessment_basis"} if event.schema_version == 3 else set()
    )
    required = _REQUIRED_PAYLOAD_FIELDS[event_type]
    unknown = set(event.payload) - allowed
    missing = required - set(event.payload)
    if unknown:
        raise ValueError(
            f"Outcome Learning {event_type} payload has unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    if missing:
        raise ValueError(
            f"Outcome Learning {event_type} payload is missing fields: "
            + ", ".join(sorted(missing))
        )
    _validate_payload(event_type, event.payload, schema_version=event.schema_version)
    expected_id = _event_id(
        event_type=event_type,
        opportunity_id=opportunity_id,
        occurred_at=occurred_at,
        payload=event.payload,
        schema_version=event.schema_version,
    )
    if event.event_id != expected_id:
        raise ValueError("Outcome Learning event_id does not match its semantic content")
    if event.record_sha256 != _record_sha256(event):
        raise ValueError("Outcome Learning record_sha256 does not match its record")


def _validate_payload(event_type: str, payload: dict[str, Any], *, schema_version: int = 2) -> None:
    if event_type == "submission_confirmed":
        _text(payload["job_key"], "job_key", maximum=500)
        _text(payload["company"], "company", maximum=300)
        _text(payload["title"], "title", maximum=500)
        if payload["provider"] is not None:
            _slug(payload["provider"], "provider")
        _optional_text(payload["external_id"], "external_id", maximum=300)
        _optional_text(payload["employer_job_id"], "employer_job_id", maximum=300)
        _url(payload["canonical_job_url"], "canonical_job_url")
        _url(payload["application_url"], "application_url")
        _choice(payload["discovery_source"], "discovery_source", DISCOVERY_SOURCES)
        _choice(payload["application_channel"], "application_channel", APPLICATION_CHANNELS)
        _choice(payload["confirmation_method"], "confirmation_method", CONFIRMATION_METHODS)
        if payload["confirmed_by"] != "local_user":
            raise ValueError("Outcome Learning submissions must be confirmed by the local user")
        _sha256(payload["job_content_hash"], "job_content_hash")
        score, _ = _submission_score(payload["fit_score"], payload["scorecard_version"])
        if score is None and schema_version == 2:
            raise ValueError("V2 submissions require numeric scoring")
        if score is not None and schema_version == 3 and "assessment_basis" not in payload:
            raise ValueError("Scored V3 submissions require a qualitative assessment basis")
        if score is None and "effective_scoring_fingerprint" in payload:
            raise ValueError("Unscored submission cannot claim an effective scoring fingerprint")
        if "assessment_basis" in payload:
            _assessment_basis(payload["assessment_basis"], payload["job_content_hash"])
        if _artifact_hashes(payload["artifact_hashes"]) != payload["artifact_hashes"]:
            raise ValueError("Outcome Learning artifact_hashes must be canonically sorted")
        if payload["assessment_hash"] is not None:
            _sha256(payload["assessment_hash"], "assessment_hash")
        if "effective_scoring_fingerprint" in payload:
            _strict_sha256(
                payload["effective_scoring_fingerprint"],
                "effective_scoring_fingerprint",
            )
        return
    if event_type == "outcome_recorded":
        legacy = create_legacy_outcome_event(
            job_key=payload["job_key"],
            company=payload["company"],
            title=payload["title"],
            stage=payload["stage"],
            outcome=payload["outcome"],
            employer_stated_reason=payload["employer_stated_reason"],
            reason_category=payload["reason_category"],
            evidence_quality=payload["evidence_quality"],
            recorded_at="2000-01-01T00:00:00+00:00",
        )
        for key, value in legacy.to_dict().items():
            if key != "recorded_at" and payload[key] != value:
                raise ValueError(f"Outcome Learning outcome payload field is not normalized: {key}")
        origin = _choice(payload["origin"], "origin", OUTCOME_ORIGINS)
        if origin == "human_confirmed":
            if payload.get("confirmed_by") != "local_user":
                raise ValueError("Outcome Learning outcomes must be confirmed by the local user")
            if payload.get("legacy_record_sha256") is not None:
                raise ValueError("Human-confirmed outcomes cannot have legacy provenance")
        else:
            if payload.get("confirmed_by") is not None:
                raise ValueError("Legacy outcomes cannot claim current human confirmation")
            _sha256(payload.get("legacy_record_sha256"), "legacy_record_sha256")
        return
    if event_type == "interview_debrief_recorded":
        _text(payload["job_key"], "job_key", maximum=500)
        _text(payload["company"], "company", maximum=300)
        _text(payload["title"], "title", maximum=500)
        _choice(payload["interview_stage"], "interview_stage", INTERVIEW_DEBRIEF_STAGES)
        _choice(payload["quality_signal"], "quality_signal", QUALITY_SIGNALS)
        _choice(payload["failure_point"], "failure_point", FAILURE_POINTS)
        _choice(payload["confidence"], "confidence", CONFIDENCE_LEVELS)
        _text(payload["summary"], "summary", maximum=600, required=False)
        if payload["confirmed_by"] != "local_user":
            raise ValueError("Outcome Learning debriefs must be confirmed by the local user")
        return
    _sha256(payload["report_basis_hash"], "report_basis_hash")
    _timestamp(payload["report_as_of"], "report_as_of")
    for field in ("confirmed_submissions", "mature_submissions"):
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Outcome Learning {field} must be a non-negative integer")
    for field in ("maturity_business_days", "review_interval"):
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"Outcome Learning {field} must be a positive integer")
    _choice(payload["review_scope"], "review_scope", REVIEW_SCOPES)
    if payload["confirmed_by"] != "local_user":
        raise ValueError("Outcome Learning reviews must be confirmed by the local user")


def business_days_elapsed(start: date, end: date) -> int:
    if end <= start:
        return 0
    days = 0
    current = start + timedelta(days=1)
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def _rate(numerator: int, denominator: int) -> float | None:
    return (100 * numerator / denominator) if denominator else None


def _score_band(value: float) -> str:
    if value < 60:
        return "0-59"
    if value < 70:
        return "60-69"
    if value < 80:
        return "70-79"
    if value < 90:
        return "80-89"
    return "90-100"


def _segment(
    submissions: Mapping[str, OutcomeLearningEvent],
    interviewed: set[str],
    offered: set[str],
    hired: set[str],
    *,
    field: str,
) -> dict[str, dict[str, float | int | None]]:
    grouped: dict[str, list[str]] = {}
    for opportunity_id, event in submissions.items():
        if field == "score_band":
            if event.payload["fit_score"] is None:
                continue
            value = _score_band(float(event.payload["fit_score"]))
        elif field == "effective_scoring_fingerprint":
            value = str(event.payload.get(field) or "unknown")
        else:
            value = str(event.payload[field])
        grouped.setdefault(value, []).append(opportunity_id)
    result: dict[str, dict[str, float | int | None]] = {}
    for value, opportunity_ids in sorted(grouped.items()):
        denominator = len(opportunity_ids)
        interview_count = sum(item in interviewed for item in opportunity_ids)
        offer_count = sum(item in offered for item in opportunity_ids)
        hire_count = sum(item in hired for item in opportunity_ids)
        result[value] = {
            "mature_submissions": denominator,
            "interviews": interview_count,
            "offers": offer_count,
            "hires": hire_count,
            "application_to_interview_rate": _rate(interview_count, denominator),
            "application_to_offer_rate": _rate(offer_count, denominator),
            "application_to_hire_rate": _rate(hire_count, denominator),
        }
    return result


def calculate_report(
    events: Iterable[OutcomeLearningEvent],
    *,
    as_of: str | None = None,
    maturity_business_days: int = DEFAULT_MATURITY_BUSINESS_DAYS,
    review_interval: int = DEFAULT_REVIEW_INTERVAL,
    identity_resolver: Callable[[object], str] | None = None,
) -> OutcomeLearningReport:
    if maturity_business_days < 1:
        raise ValueError("Outcome Learning maturity_business_days must be at least 1")
    if review_interval < 1:
        raise ValueError("Outcome Learning review_interval must be at least 1")
    normalized_as_of = _as_of_timestamp(as_of)
    as_of_value = _timestamp_value(normalized_as_of)
    event_list = list(events)
    for event in event_list:
        validate_event(event)
    considered = [
        event for event in event_list if _timestamp_value(event.occurred_at) <= as_of_value
    ]
    if identity_resolver is not None:
        projected: list[OutcomeLearningEvent] = []
        for event in considered:
            canonical_identity = str(identity_resolver(event.opportunity_id) or "").strip()
            if not canonical_identity:
                raise ValueError("Outcome Learning identity resolver returned an empty identity")
            projected.append(replace(event, opportunity_id=canonical_identity))
        considered = projected
    future_count = len(event_list) - len(considered)
    submissions: dict[str, OutcomeLearningEvent] = {}
    for event in considered:
        if event.event_type != "submission_confirmed":
            continue
        if event.opportunity_id in submissions:
            raise ValueError(
                "Outcome Learning report found multiple submissions for one opportunity_id"
            )
        submissions[event.opportunity_id] = event
    mature = {
        opportunity_id: event
        for opportunity_id, event in submissions.items()
        if business_days_elapsed(
            _timestamp_value(event.occurred_at).date(),
            as_of_value.date(),
        )
        >= maturity_business_days
    }
    outcomes = [event for event in considered if event.event_type == "outcome_recorded"]
    debriefs = [event for event in considered if event.event_type == "interview_debrief_recorded"]
    debrief_ids = {event.opportunity_id for event in debriefs}
    interviewed = {
        event.opportunity_id for event in outcomes if event.payload["stage"] in INTERVIEW_STAGES
    } | debrief_ids
    offered = {
        event.opportunity_id
        for event in outcomes
        if event.payload["stage"] == "offer" or event.payload["outcome"] in OFFER_OUTCOMES
    }
    hired = {event.opportunity_id for event in outcomes if event.payload["outcome"] == "accepted"}
    mature_ids = set(mature)
    mature_interviewed = interviewed & mature_ids
    mature_offered = offered & mature_ids
    mature_hired = hired & mature_ids
    legacy_outcomes = sum(
        event.payload["origin"] == "legacy_v1" and event.opportunity_id not in submissions
        for event in outcomes
    )
    unmatched_outcomes = sum(
        event.payload["origin"] == "human_confirmed" and event.opportunity_id not in submissions
        for event in outcomes
    )
    warnings: list[str] = []
    unscored = sum(event.payload["fit_score"] is None for event in submissions.values())
    mature_unscored = sum(event.payload["fit_score"] is None for event in mature.values())
    if unscored:
        warnings.append(
            f"{unscored} confirmed submission(s) have unknown numeric scoring; "
            f"score-band denominators include only {len(mature) - mature_unscored} "
            f"of {len(mature)} mature submissions."
        )
    if not submissions:
        warnings.append("No confirmed submissions are available for cohort reporting.")
    if legacy_outcomes:
        warnings.append(
            f"{legacy_outcomes} migrated legacy outcome(s) lack confirmed submission attribution "
            "and are excluded from conversion denominators."
        )
    if unmatched_outcomes:
        warnings.append(
            f"{unmatched_outcomes} confirmed outcome(s) have no matching submission event and are "
            "excluded from conversion denominators."
        )
    no_artifacts = sum(not event.payload["artifact_hashes"] for event in submissions.values())
    if no_artifacts:
        warnings.append(f"{no_artifacts} confirmed submission(s) have no frozen artifact hashes.")
    weak_requisition_identity = sum(
        not any(
            event.payload[field]
            for field in ("external_id", "employer_job_id", "canonical_job_url")
        )
        for event in submissions.values()
    )
    if weak_requisition_identity:
        warnings.append(
            f"{weak_requisition_identity} confirmed submission(s) lack provider/external, "
            "employer requisition, and canonical URL identity evidence."
        )
    if future_count:
        warnings.append(f"{future_count} event(s) occur after the report as-of timestamp.")
    acknowledgements = [event for event in considered if event.event_type == "review_acknowledged"]
    latest_ack = max(
        acknowledgements,
        key=lambda event: (_timestamp_value(event.occurred_at), event.event_id),
        default=None,
    )
    latest_submission_ack = max(
        (
            event
            for event in acknowledgements
            if event.payload["review_scope"] in {"both", "submission_hygiene"}
        ),
        key=lambda event: (_timestamp_value(event.occurred_at), event.event_id),
        default=None,
    )
    latest_outcome_ack = max(
        (
            event
            for event in acknowledgements
            if event.payload["review_scope"] in {"both", "outcome_learning"}
        ),
        key=lambda event: (_timestamp_value(event.occurred_at), event.event_id),
        default=None,
    )
    prior_confirmed = (
        int(latest_submission_ack.payload["confirmed_submissions"]) if latest_submission_ack else 0
    )
    prior_mature = (
        int(latest_outcome_ack.payload["mature_submissions"]) if latest_outcome_ack else 0
    )
    confirmed_since = max(0, len(submissions) - prior_confirmed)
    mature_since = max(0, len(mature) - prior_mature)
    due_reasons: list[str] = []
    if confirmed_since >= review_interval:
        due_reasons.append(f"{confirmed_since} confirmed submission(s) since the latest review")
    if mature_since >= review_interval:
        due_reasons.append(f"{mature_since} mature submission(s) since the latest review")
    segments = {
        "discovery_source": _segment(
            mature,
            mature_interviewed,
            mature_offered,
            mature_hired,
            field="discovery_source",
        ),
        "application_channel": _segment(
            mature,
            mature_interviewed,
            mature_offered,
            mature_hired,
            field="application_channel",
        ),
        "score_band": _segment(
            mature,
            mature_interviewed,
            mature_offered,
            mature_hired,
            field="score_band",
        ),
        "effective_scoring_fingerprint": _segment(
            mature,
            mature_interviewed,
            mature_offered,
            mature_hired,
            field="effective_scoring_fingerprint",
        ),
    }
    basis = {
        "schema_version": SCHEMA_VERSION,
        "as_of": normalized_as_of,
        "maturity_business_days": maturity_business_days,
        "review_interval": review_interval,
        "evidence_event_ids": sorted(
            event.event_id for event in considered if event.event_type != "review_acknowledged"
        ),
        "confirmed_submissions": len(submissions),
        "mature_submissions": len(mature),
        "mature_interviewed": len(mature_interviewed),
        "mature_offers": len(mature_offered),
        "mature_hires": len(mature_hired),
        "segments": segments,
    }
    basis_hash = hashlib.sha256(_canonical_json(basis).encode("utf-8")).hexdigest()
    return OutcomeLearningReport(
        schema_version=SCHEMA_VERSION,
        as_of=normalized_as_of,
        maturity_business_days=maturity_business_days,
        review_interval=review_interval,
        events_considered=len(considered),
        confirmed_submissions=len(submissions),
        scored_submissions=len(submissions) - unscored,
        unscored_submissions=unscored,
        mature_scored_submissions=len(mature) - mature_unscored,
        mature_unscored_submissions=mature_unscored,
        mature_submissions=len(mature),
        immature_submissions=len(submissions) - len(mature),
        mature_ever_interviewed=len(mature_interviewed),
        mature_offers=len(mature_offered),
        mature_hires=len(mature_hired),
        application_to_interview_rate=_rate(len(mature_interviewed), len(mature)),
        application_to_offer_rate=_rate(len(mature_offered), len(mature)),
        application_to_hire_rate=_rate(len(mature_hired), len(mature)),
        interview_debriefs=len(debriefs),
        legacy_outcomes_excluded=legacy_outcomes,
        unmatched_outcomes_excluded=unmatched_outcomes,
        segments=segments,
        last_reviewed_at=latest_ack.occurred_at if latest_ack else None,
        last_submission_reviewed_at=(
            latest_submission_ack.occurred_at if latest_submission_ack else None
        ),
        last_outcome_reviewed_at=(latest_outcome_ack.occurred_at if latest_outcome_ack else None),
        confirmed_since_review=confirmed_since,
        mature_since_review=mature_since,
        review_due=bool(due_reasons),
        review_due_reasons=tuple(due_reasons),
        data_quality_warnings=tuple(warnings),
        basis_hash=basis_hash,
    )


def create_review_acknowledgement(
    events: Iterable[OutcomeLearningEvent],
    *,
    report_basis_hash: str,
    as_of: str | None = None,
    maturity_business_days: int = DEFAULT_MATURITY_BUSINESS_DAYS,
    review_interval: int = DEFAULT_REVIEW_INTERVAL,
    review_scope: str = "both",
    acknowledged_at: str | None = None,
    identity_resolver: Callable[[object], str] | None = None,
    confirmed: bool,
) -> OutcomeLearningEvent:
    _confirmed(confirmed)
    report = calculate_report(
        events,
        as_of=as_of,
        maturity_business_days=maturity_business_days,
        review_interval=review_interval,
        identity_resolver=identity_resolver,
    )
    normalized_basis = _sha256(report_basis_hash, "report_basis_hash")
    if normalized_basis != report.basis_hash:
        raise ValueError(
            "Outcome Learning report basis is stale or was generated with different settings"
        )
    normalized_scope = _choice(review_scope, "review_scope", REVIEW_SCOPES)
    payload = {
        "report_basis_hash": report.basis_hash,
        "report_as_of": report.as_of,
        "confirmed_submissions": report.confirmed_submissions,
        "mature_submissions": report.mature_submissions,
        "maturity_business_days": maturity_business_days,
        "review_interval": review_interval,
        "review_scope": normalized_scope,
        "confirmed_by": "local_user",
    }
    return _build_event(
        event_type="review_acknowledged",
        opportunity_id=PORTFOLIO_OPPORTUNITY_ID,
        occurred_at=acknowledged_at,
        recorded_at=acknowledged_at,
        payload=payload,
    )

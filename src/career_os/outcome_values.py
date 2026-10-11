"""Shared outcome values; no ledger or pipeline access."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

STAGES = {
    "review",
    "outreach",
    "application",
    "interview",
    "final_interview",
    "offer",
}


OUTCOMES = {
    "identified",
    "reviewed",
    "contacted",
    "submitted",
    "scheduled",
    "completed",
    "advanced",
    "rejected",
    "withdrawn",
    "no_response",
    "offer_received",
    "accepted",
    "declined",
    "closed",
}


REASON_CATEGORIES = {
    "industry_customer_gap",
    "product_domain_gap",
    "functional_fit_gap",
    "experience_gap",
    "leadership_scope_gap",
    "seniority_gap",
    "geography_work_model_gap",
    "compensation_gap",
    "application_quality_gap",
    "timing_or_headcount",
    "role_closed",
    "candidate_choice",
    "competition",
    "no_reason",
    "other",
}


EVIDENCE_QUALITIES = {
    "direct_employer_statement",
    "recruiter_summary",
    "candidate_inference",
    "none",
}


SUPPRESSING_OUTCOMES = {"rejected", "withdrawn", "closed"}


TERMINAL_OUTCOMES = SUPPRESSING_OUTCOMES | {"declined", "accepted"}


APPLICATION_STAGES = {"application", "interview", "final_interview", "offer"}


INTERVIEW_STAGES = {"interview", "final_interview", "offer"}


OFFER_OUTCOMES = {"offer_received", "accepted", "declined"}


@dataclass(frozen=True, slots=True)
class OutcomeEvent:
    job_key: str
    company: str
    title: str
    stage: str
    outcome: str
    employer_stated_reason: str
    reason_category: str
    evidence_quality: str
    recorded_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_text(
    value: object,
    field: str,
    *,
    maximum: int,
    required: bool = True,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Outcome {field} must be a string")
    normalized = " ".join(value.split())
    if required and not normalized:
        raise ValueError(f"Outcome {field} is required")
    if len(normalized) > maximum:
        raise ValueError(f"Outcome {field} must be at most {maximum} characters")
    return normalized


def _normalize_choice(value: object, field: str, choices: set[str]) -> str:
    normalized = _normalize_text(value, field, maximum=100).casefold()
    if normalized not in choices:
        raise ValueError(f"Outcome {field} must be one of: {', '.join(sorted(choices))}")
    return normalized


def _normalize_timestamp(value: object | None) -> str:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0).isoformat()
    normalized = _normalize_text(value, "recorded_at", maximum=100)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Outcome recorded_at must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Outcome recorded_at must include a timezone")
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat()


def create_outcome_event(
    *,
    job_key: str,
    company: str,
    title: str,
    stage: str,
    outcome: str,
    employer_stated_reason: str = "",
    reason_category: str,
    evidence_quality: str,
    recorded_at: str | None = None,
) -> OutcomeEvent:
    event = OutcomeEvent(
        job_key=_normalize_text(job_key, "job_key", maximum=500),
        company=_normalize_text(company, "company", maximum=300),
        title=_normalize_text(title, "title", maximum=500),
        stage=_normalize_choice(stage, "stage", STAGES),
        outcome=_normalize_choice(outcome, "outcome", OUTCOMES),
        employer_stated_reason=_normalize_text(
            employer_stated_reason,
            "employer_stated_reason",
            maximum=1_000,
            required=False,
        ),
        reason_category=_normalize_choice(reason_category, "reason_category", REASON_CATEGORIES),
        evidence_quality=_normalize_choice(
            evidence_quality, "evidence_quality", EVIDENCE_QUALITIES
        ),
        recorded_at=_normalize_timestamp(recorded_at),
    )
    validate_outcome_event(event)
    return event


def validate_outcome_event(event: OutcomeEvent) -> None:
    normalized = {
        "job_key": _normalize_text(event.job_key, "job_key", maximum=500),
        "company": _normalize_text(event.company, "company", maximum=300),
        "title": _normalize_text(event.title, "title", maximum=500),
        "stage": _normalize_choice(event.stage, "stage", STAGES),
        "outcome": _normalize_choice(event.outcome, "outcome", OUTCOMES),
    }
    reason = _normalize_text(
        event.employer_stated_reason,
        "employer_stated_reason",
        maximum=1_000,
        required=False,
    )
    category = _normalize_choice(event.reason_category, "reason_category", REASON_CATEGORIES)
    quality = _normalize_choice(event.evidence_quality, "evidence_quality", EVIDENCE_QUALITIES)
    normalized["employer_stated_reason"] = reason
    normalized["reason_category"] = category
    normalized["evidence_quality"] = quality
    for field, canonical in normalized.items():
        if getattr(event, field) != canonical:
            raise ValueError(f"Outcome {field} must use its normalized value")
    if not isinstance(event.recorded_at, str):
        raise ValueError("Outcome recorded_at must be a timestamp string")
    if event.recorded_at != event.recorded_at.strip():
        raise ValueError("Outcome recorded_at must not have surrounding whitespace")
    _normalize_timestamp(event.recorded_at)
    if category == "no_reason":
        if reason:
            raise ValueError("Outcome no_reason events cannot include an employer-stated reason")
        if quality != "none":
            raise ValueError("Outcome no_reason events must use evidence quality none")
    elif quality == "none":
        raise ValueError("Outcome evidence quality none requires reason category no_reason")
    elif not reason:
        raise ValueError(
            "Outcome employer_stated_reason is required when a reason category is provided"
        )

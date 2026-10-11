"""Deterministic, read-only projection of the next human Rolevane decisions."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

ACTIVE_STAGES = {
    "review",
    "preparing",
    "applied",
    "interviewing",
    "final_interview",
    "offer",
    "hold",
}
INTERVIEW_STAGES = {"interviewing", "final_interview"}
REVIEW_RECOMMENDATIONS = {
    "priority_alert",
    "review_queue",
    "investigate_missing_data",
    "semantic_review_candidate",
}
CONFLICTING_IDENTITIES = {
    "stable_conflict",
    "stable_ambiguous",
    "legacy_conflict",
    "legacy_ambiguous",
}
SAFE_APPLICATION_IDENTITIES = {"stable_exact", "legacy_exact"}
TERMINAL_LIFECYCLE_OUTCOMES = {
    "rejected",
    "withdrawn",
    "role_closed",
    "closed",
    "accepted",
    "declined",
}
INTERVIEW_COMPLETION_OUTCOMES = {
    "advanced",
    "closed",
    "completed",
    "rejected",
    "withdrawn",
}
FOCUS_ITEM_STRING_LIMITS = {
    "kind": 80,
    "opportunity_id": 200,
    "company": 500,
    "role": 500,
    "stage": 100,
    "due_at": 100,
    "reason": 4_000,
    "source": 500,
    "action_route": 1_000,
    "identity_status": 100,
}


def _pipeline_value(entry: object, name: str) -> object:
    if isinstance(entry, Mapping):
        return entry.get(name)
    return getattr(entry, name, None)


def action_intent_timing(entry: object) -> date | None:
    """Return the latest explicit wait/snooze date, ignoring historical earlier dates."""

    intent = str(_pipeline_value(entry, "action_intent") or "").strip().casefold()
    if intent not in {"wait", "snoozed"}:
        return None
    dates: list[date] = []
    for name in ("due_date", "next_event_at"):
        raw = str(_pipeline_value(entry, name) or "").strip()
        if not raw:
            continue
        try:
            dates.append(date.fromisoformat(raw[:10]))
        except ValueError:
            continue
    return max(dates) if dates else None


def action_prompt_allowed(entry: object, *, as_of: date) -> bool:
    """Return whether ordinary action projections may surface this pipeline row."""

    intent = str(_pipeline_value(entry, "action_intent") or "").strip().casefold()
    if intent == "no_further_pursuit":
        return False
    if intent not in {"wait", "snoozed"}:
        return True
    timing = action_intent_timing(entry)
    return timing is not None and timing <= as_of


def _interview_round_for_legacy_kind(kind: str) -> str | None:
    if kind in {"interview_scheduled", "interview_completed"}:
        return "interview"
    if kind in {"final_interview_scheduled", "final_interview_completed"}:
        return "final"
    return None


def _interview_round_for_learning_event(event: Mapping[str, Any]) -> str | None:
    event_type = str(event.get("event_type") or "")
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    if event_type == "outcome_recorded":
        stage = str(payload.get("stage") or "").strip().casefold()
        if stage == "interview":
            return "interview"
        if stage == "final_interview":
            return "final"
        return None
    if event_type == "interview_debrief_recorded":
        interview_stage = str(payload.get("interview_stage") or "").strip().casefold()
        return "final" if interview_stage == "final" else ("interview" if interview_stage else None)
    return None


def _normalize_interview_round(value: object) -> str | None:
    normalized = _normalized(value)
    if not normalized:
        return None
    tokens = re.findall(r"[a-z0-9]+", normalized)
    for index, token in enumerate(tokens):
        if token != "final":
            continue
        previous = tokens[index - 1] if index else None
        if previous in {"non", "semi", "pre", "not"}:
            continue
        modifier_index = index - 1
        while modifier_index >= 0 and tokens[modifier_index] in {
            "yet",
            "quite",
            "actually",
            "really",
            "a",
            "an",
            "the",
        }:
            modifier_index -= 1
        if modifier_index >= 0 and tokens[modifier_index] == "not":
            continue
        return "final"
    # Every validated workspace lives beneath an interview-specific directory.
    # Unknown legacy/free-form round names are therefore non-final unless they
    # explicitly identify themselves as final.
    return "interview"


def _workspace_matches_interview_round(
    state: Mapping[str, Any],
    *,
    expected_round: str,
) -> bool:
    raw_round = state.get("round")
    if raw_round is None or not str(raw_round).strip():
        raw_round = state.get("interview_stage")
    if raw_round is None or not str(raw_round).strip():
        # Older workspaces did not persist round metadata. Keep those usable,
        # but never let unknown legacy state certify a final-round workspace.
        return expected_round == "interview"
    normalized_round = _normalize_interview_round(raw_round)
    return normalized_round == expected_round


def _workspace_matches_interview_cycle(
    state: Mapping[str, Any],
    *,
    expected_round: str,
    records: Iterable[Mapping[str, Any]],
) -> bool:
    """Fail closed when coarse lifecycle stages contain more than one interview cycle."""

    if not _workspace_matches_interview_round(state, expected_round=expected_round):
        return False
    records = tuple(records)
    if "confirmed_schedule_event_id" in state:
        scheduled = [
            record
            for record in records
            if record.get("status") == "scheduled" and record.get("round") == expected_round
        ]
        return bool(
            scheduled
            and state.get("confirmed_schedule_event_id")
            and scheduled[-1].get("event_id") == state["confirmed_schedule_event_id"]
        )
    scheduled_instants = {
        occurred_at
        for record in records
        if record.get("status") == "scheduled"
        and isinstance((occurred_at := record.get("occurred_at")), datetime)
    }
    if not scheduled_instants:
        return False
    current_cycle_started_at = max(scheduled_instants)
    has_prior_cycle_boundary = any(
        isinstance((occurred_at := record.get("occurred_at")), datetime)
        and occurred_at < current_cycle_started_at
        for record in records
    )
    if not has_prior_cycle_boundary:
        return True
    created_at = _timestamp(state.get("created_at"))
    return created_at is not None and created_at >= current_cycle_started_at


def _latest_terminal_lifecycle(
    career_events: Iterable[Mapping[str, Any]],
    outcome_learning_events: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    records: list[dict[str, Any]] = []
    legacy_outcomes = {
        "application_submitted": "submitted",
        "interview_scheduled": "scheduled",
        "interview_completed": "completed",
        "final_interview_scheduled": "scheduled",
        "final_interview_completed": "completed",
        "offer_received": "offer_received",
        "rejected": "rejected",
        "withdrawn": "withdrawn",
        "role_closed": "role_closed",
    }
    for event in career_events:
        outcome = legacy_outcomes.get(str(event.get("kind") or ""))
        if outcome is None:
            continue
        records.append(
            {
                "occurred_at": _timestamp(event.get("occurred_at")),
                "recorded_at": _timestamp(event.get("confirmed_at") or event.get("occurred_at")),
                "outcome": outcome,
                "source": "career_events",
                "sequence": len(records),
            }
        )
    for event in outcome_learning_events:
        event_type = str(event.get("event_type") or "")
        if event_type == "submission_confirmed":
            records.append(
                {
                    "occurred_at": _timestamp(event.get("occurred_at")),
                    "recorded_at": _timestamp(event.get("recorded_at") or event.get("occurred_at")),
                    "outcome": "submitted",
                    "source": "outcome_learning_v2",
                    "sequence": len(records),
                }
            )
            continue
        if event_type != "outcome_recorded":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        outcome = str(payload.get("outcome") or "").strip().casefold()
        if not outcome:
            continue
        records.append(
            {
                "occurred_at": _timestamp(event.get("occurred_at")),
                "recorded_at": _timestamp(event.get("recorded_at") or event.get("occurred_at")),
                "outcome": outcome,
                "source": "outcome_learning_v2",
                "sequence": len(records),
            }
        )
    if not records:
        return None
    latest = max(
        records,
        key=lambda record: (
            record["occurred_at"] or datetime.min.replace(tzinfo=UTC),
            record["recorded_at"] or datetime.min.replace(tzinfo=UTC),
            record["sequence"],
        ),
    )
    return latest if latest["outcome"] in TERMINAL_LIFECYCLE_OUTCOMES else None


def _interview_records(
    career_events: Iterable[Mapping[str, Any]],
    outcome_learning_events: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for event in career_events:
        kind = str(event.get("kind") or "")
        round_name = _interview_round_for_legacy_kind(kind)
        if round_name is None:
            continue
        records.append(
            {
                "occurred_at": _timestamp(event.get("occurred_at")),
                "round": round_name,
                "status": "completed" if kind.endswith("_completed") else "scheduled",
                "source": "career_events",
                "event_id": event.get("event_id"),
            }
        )
    for event in outcome_learning_events:
        round_name = _interview_round_for_learning_event(event)
        if round_name is None:
            continue
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload")
        outcome = (
            str(payload.get("outcome") or "").strip().casefold()
            if isinstance(payload, Mapping)
            else ""
        )
        if event_type == "outcome_recorded":
            if outcome == "scheduled":
                status = "scheduled"
            elif outcome in INTERVIEW_COMPLETION_OUTCOMES:
                status = "completed"
            else:
                continue
        else:
            status = "completed"
        records.append(
            {
                "occurred_at": _timestamp(event.get("occurred_at")),
                "round": round_name,
                "status": status,
                "outcome": outcome if event_type == "outcome_recorded" else None,
                "source": "outcome_learning_v2",
                "event_id": event.get("event_id"),
            }
        )
    return sorted(
        records,
        key=lambda record: record["occurred_at"] or datetime.min.replace(tzinfo=UTC),
    )


def _debrief_matches_current_round(
    event: Mapping[str, Any],
    *,
    current: Mapping[str, Any] | None,
) -> bool:
    if current is None:
        return False
    # Direct callers may provide the pre-schema event shape used by the original
    # focus projection tests. Loaded V2 records always carry both fields below.
    if "payload" not in event and "occurred_at" not in event:
        return current.get("round") == "interview"
    round_name = _interview_round_for_learning_event(event)
    if round_name != current.get("round"):
        return False
    occurred_at = _timestamp(event.get("occurred_at"))
    completed_at = current.get("occurred_at")
    return occurred_at is not None and completed_at is not None and occurred_at >= completed_at


def _preserve_advanced_debrief_completion(
    current: Mapping[str, Any] | None,
    learning_events: Iterable[Mapping[str, Any]],
    interview_records: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Keep an earlier debrief as completion evidence after advancement.

    ``outcome_recorded(..., advanced)`` confirms that the interview happened,
    but it is normally recorded after the candidate's debrief.  Treat the
    latest matching prior debrief as the effective completion point so the
    advancement event does not create a duplicate debrief prompt.
    """

    if current is None or current.get("outcome") != "advanced":
        return current
    completed_at = current.get("occurred_at")
    if not isinstance(completed_at, datetime):
        return current
    prior_boundaries = [
        record.get("occurred_at")
        for record in interview_records
        if record.get("round") == current.get("round")
        and isinstance(record.get("occurred_at"), datetime)
        and record["occurred_at"] < completed_at
    ]
    cycle_started_at = max(prior_boundaries) if prior_boundaries else None
    prior_debriefs = []
    for event in learning_events:
        if str(event.get("event_type") or "") != "interview_debrief_recorded":
            continue
        if _interview_round_for_learning_event(event) != current.get("round"):
            continue
        occurred_at = _timestamp(event.get("occurred_at"))
        if (
            occurred_at is not None
            and occurred_at <= completed_at
            and (cycle_started_at is None or occurred_at >= cycle_started_at)
        ):
            prior_debriefs.append(occurred_at)
    if not prior_debriefs:
        return current
    adjusted = dict(current)
    adjusted["occurred_at"] = max(prior_debriefs)
    return adjusted


@dataclass(frozen=True, slots=True)
class FocusItem:
    kind: str
    opportunity_id: str
    company: str
    role: str
    stage: str
    urgency: str
    due_at: str | None
    reason: str
    source: str
    action_route: str
    requires_user_confirmation: bool
    identity_status: str
    blocked: bool
    priority: int

    def __post_init__(self) -> None:
        """Keep every item within the persisted focus-snapshot contract."""

        for field_name, maximum in FOCUS_ITEM_STRING_LIMITS.items():
            value = getattr(self, field_name)
            if value is not None:
                if field_name in {"opportunity_id", "action_route"} and len(value) > maximum:
                    raise ValueError(f"Focus {field_name} exceeds the snapshot limit")
                object.__setattr__(self, field_name, value[:maximum])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FocusProjection:
    schema_version: int
    generated_at: str
    as_of: str
    read_only: bool
    external_actions: bool
    pipeline_changed: bool
    scoring_changed: bool
    partial: bool
    total_items: int
    returned_items: int
    omitted_items: int
    items: tuple[FocusItem, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["items"] = [item.to_dict() for item in self.items]
        return payload


def _normalized(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _opportunity_id(entry: Mapping[str, Any]) -> str:
    return str(entry.get("opportunity_id") or entry.get("pipeline_key") or "").strip()


def _stage(entry: Mapping[str, Any]) -> str:
    return str(entry.get("stage") or entry.get("section") or "review").strip().casefold()


def _timestamp(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(raw), datetime.min.time(), UTC)
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _lifecycle_event_visible_as_of(
    event: Mapping[str, Any],
    *,
    authority_timestamp_key: str,
    cutoff: datetime,
) -> bool:
    """Keep lifecycle evidence out of snapshots before it was authoritative.

    A lifecycle event has two relevant clocks: when it happened and when the
    local authority ledger learned/confirmed it.  Loaded records require both
    timestamps.  The fallback to ``occurred_at`` preserves compatibility with
    the pre-schema event shape accepted by direct callers and older tests.
    """

    occurred_raw = str(event.get("occurred_at") or "").strip()
    occurred_at = _timestamp(occurred_raw)
    if occurred_raw and occurred_at is None:
        return False
    authority_raw = str(event.get(authority_timestamp_key) or "").strip()
    authority_at = _timestamp(authority_raw)
    if authority_raw and authority_at is None:
        return False
    if authority_at is None and not authority_raw:
        authority_at = occurred_at
    return (occurred_at is None or occurred_at <= cutoff) and (
        authority_at is None or authority_at <= cutoff
    )


def _due_value(entry: Mapping[str, Any]) -> str | None:
    raw = str(entry.get("due_date") or entry.get("next_event_at") or "").strip()
    return raw or None


def _urgency(due_at: str | None, *, as_of: date, fallback: str = "normal") -> str:
    parsed = _timestamp(due_at)
    if parsed is None:
        return fallback
    due = parsed.date()
    if due < as_of:
        return "overdue"
    if due == as_of:
        return "today"
    if due <= as_of + timedelta(days=7):
        return "soon"
    return "upcoming"


def _route(opportunity_id: str, anchor: str = "") -> str:
    suffix = f"#{anchor}" if anchor else ""
    return f"/opportunity?id={quote(opportunity_id)}{suffix}"


def _latest_by_key(
    records: Iterable[Mapping[str, Any]],
    *,
    key: str,
    timestamp: str,
) -> dict[str, Mapping[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    for record in records:
        identity = str(record.get(key) or "").strip()
        if not identity:
            continue
        current = latest.get(identity)
        candidate_at = _timestamp(record.get(timestamp))
        current_at = _timestamp(current.get(timestamp)) if current is not None else None
        if current is None or (
            candidate_at is not None and (current_at is None or candidate_at >= current_at)
        ):
            latest[identity] = record
    return latest


def _feedback_recorded_at(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _feedback_snapshot_matches(
    feedback: Mapping[str, Any],
    job: Mapping[str, Any],
) -> bool:
    """Return whether feedback still describes the current ranked-job snapshot.

    Feedback V1 predates explicit posting hashes, so the projection uses the
    ranked row's existing content/score metadata when it is available and
    keeps key-only legacy records valid until a newer comparable snapshot is
    observed.  This lets changed postings return to review without requiring a
    feedback-ledger migration.
    """

    def first_text(record: Mapping[str, Any], *keys: str) -> str:
        for key in keys:
            value = str(record.get(key) or "").strip()
            if value:
                return value
        return ""

    feedback_hash = first_text(feedback, "job_content_hash", "content_hash")
    job_hash = first_text(job, "job_content_hash", "content_hash")
    if feedback_hash and job_hash and feedback_hash.casefold() != job_hash.casefold():
        return False

    feedback_score = feedback.get("score")
    job_score = job.get("total")
    try:
        if (
            feedback_score is not None
            and job_score is not None
            and float(feedback_score) != float(job_score)
        ):
            return False
    except (TypeError, ValueError):
        pass

    feedback_recommendation = first_text(feedback, "recommendation")
    job_recommendation = first_text(job, "recommendation")
    if (
        feedback_recommendation
        and job_recommendation
        and feedback_recommendation.casefold() != job_recommendation.casefold()
    ):
        return False

    for feedback_key, job_key in (("company", "company"), ("title", "title")):
        feedback_value = first_text(feedback, feedback_key)
        job_value = first_text(job, job_key)
        if feedback_value and job_value and _normalized(feedback_value) != _normalized(job_value):
            return False

    feedback_scorecard = first_text(feedback, "scorecard_version")
    job_scorecard = first_text(job, "scorecard_version")
    if feedback_scorecard and job_scorecard and feedback_scorecard != job_scorecard:
        return False

    feedback_scored_at = _timestamp(feedback.get("scored_at"))
    job_scored_at = _timestamp(job.get("scored_at"))
    if feedback_scored_at is not None and job_scored_at is not None:
        if feedback_scored_at != job_scored_at:
            return False
    elif job_scored_at is not None:
        feedback_at = _feedback_recorded_at(feedback.get("recorded_at"))
        if feedback_at is not None and job_scored_at > feedback_at:
            return False

    # A legacy feedback row with only job_key/recorded_at has no basis to
    # compare. Preserve its historical suppression behavior until the ranked
    # row exposes a newer comparable score/content snapshot.
    return True


def _job_matches(
    pipeline: Iterable[Mapping[str, Any]],
    jobs: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], set[str]]:
    job_rows = list(jobs)
    by_key: dict[str, list[Mapping[str, Any]]] = {}
    for job in job_rows:
        job_key = str(job.get("job_key") or "").strip()
        projected_key = str(job.get("canonical_opportunity_id") or job_key).strip()
        if job_key and projected_key:
            by_key.setdefault(projected_key, []).append(job)
    matches: dict[str, Mapping[str, Any]] = {}
    tracked: set[str] = set()
    for entry in pipeline:
        opportunity_id = _opportunity_id(entry)
        if not opportunity_id:
            continue
        exact_matches = by_key.get(opportunity_id, [])
        tracked.update(
            str(job.get("job_key") or "").strip()
            for job in exact_matches
            if str(job.get("job_key") or "").strip()
        )
        if len(exact_matches) == 1:
            exact = exact_matches[0]
            matches[opportunity_id] = exact
            job_key = str(exact.get("job_key") or "").strip()
            if job_key:
                tracked.add(job_key)
            continue
        job_url = str(entry.get("job_url") or "").strip()
        url_matches = [
            job for job in job_rows if job_url and str(job.get("url") or "").strip() == job_url
        ]
        if len(url_matches) == 1:
            matches[opportunity_id] = url_matches[0]
            job_key = str(url_matches[0].get("job_key") or "").strip()
            if job_key:
                tracked.add(job_key)
    return matches, tracked


def _sort_key(item: FocusItem) -> tuple[Any, ...]:
    # A date-only user task has no asserted instant. Its timezone-aware urgency
    # is already supplied by the task projection, unlike legacy pipeline dates.
    due = (
        None
        if item.kind == "planned_task" and item.due_at and len(item.due_at) == 10
        else _timestamp(item.due_at)
    )
    return (
        item.priority,
        due or datetime.max.replace(tzinfo=UTC),
        _normalized(item.company),
        _normalized(item.role),
        item.opportunity_id,
        item.kind,
        item.source,
    )


def _search_review_reason(
    job: Mapping[str, Any],
    feedback: Mapping[str, Any] | None,
    source_coverage: Mapping[str, Mapping[str, Any]],
) -> str:
    """Explain a review from existing evidence without granting lifecycle authority."""

    def brief(value: object, maximum: int = 180) -> str:
        return " ".join(str(value or "").split())[:maximum]

    source_id = str(job.get("source_id") or "").strip()
    route = source_coverage.get(source_id) if source_id else None
    source = brief(job.get("url"), 260) or brief(source_id) or "source not recorded"
    if route is None:
        coverage = "source route coverage unavailable"
    else:
        observation = route.get("latest_observation")
        observation = observation if isinstance(observation, Mapping) else {}
        status = str(observation.get("status") or "unverified")
        observed_at = brief(observation.get("observed_at"), 35) or "undated"
        coverage = (
            f"route {brief(source_id, 100)}: "
            f"{'enabled' if route.get('enabled') else 'held/disabled'}, "
            f"{brief(route.get('lifecycle'), 30) or 'unknown lifecycle'}, "
            f"latest {brief(status, 20)} observation ({observed_at})"
        )
        if status in {"empty", "observed"} and observation.get("jobs_seen") is not None:
            coverage += f", {brief(observation['jobs_seen'], 12)} jobs recorded in that observation"
        if status == "error" and route.get("last_success_at"):
            coverage += f", last success {brief(route['last_success_at'], 35)}"
        employer_status = brief(route.get("employer_coverage_status"), 40)
        if employer_status:
            coverage += f", target employer coverage {employer_status}"
        else:
            coverage += ", target employer coverage unresolved"

    progression = job.get("progression")
    progression = progression if isinstance(progression, Mapping) else {}
    value = (
        f"scored progression {brief(progression.get('verdict'), 45)}"
        if progression.get("verdict")
        else "scored progression unresolved"
    )
    evidence = progression.get("evidence")
    if isinstance(evidence, list) and evidence:
        value += f" ({brief(evidence[0], 190)})"
    # Dashboard supplies this transient marker only after the existing brief validator passes.
    # It changes the explanatory text, never scoring or lifecycle authority.
    value += (
        "; validated current-role comparison available in opportunity detail"
        if job.get("_validated_current_role_comparison") is True
        else "; W4 current-role comparison unavailable"
    )

    requirements = job.get("requirement_evidence")
    requirements = requirements if isinstance(requirements, list) else []
    supported_requirement = next(
        (
            row
            for row in requirements
            if isinstance(row, Mapping)
            and row.get("classification") in {"strong_verified_evidence", "transferable_evidence"}
        ),
        None,
    )
    candidate_evidence = (
        f"{brief(supported_requirement.get('classification'), 35)} for "
        f"{brief(supported_requirement.get('requirement_text'), 160)}"
        if supported_requirement is not None
        else "no verified candidate requirement match recorded in scored row"
    )
    mandatory_gaps = [
        row
        for row in requirements
        if isinstance(row, Mapping)
        and row.get("requirement_level") == "mandatory"
        and row.get("classification") == "genuine_gap"
    ]
    if mandatory_gaps:
        uncertainty = "unsupported mandatory requirement: " + brief(
            mandatory_gaps[0].get("requirement_text"), 200
        )
    else:
        unknowns = progression.get("unknowns")
        hard_gates = job.get("hard_gates")
        risks = job.get("risks")
        if isinstance(hard_gates, list) and hard_gates:
            uncertainty = "scored hard gate: " + brief(hard_gates[0], 200)
        elif isinstance(unknowns, list) and unknowns:
            uncertainty = "progression input: " + brief(unknowns[0], 100)
        elif isinstance(risks, list) and risks:
            uncertainty = "scored risk: " + brief(risks[0], 200)
        else:
            uncertainty = "mandate and economics need human verification"

    if feedback is None:
        prior = "no prior search decision for this job key"
        change = "no comparable prior decision snapshot; repost status unverified"
    else:
        prior = brief(feedback.get("decision"), 45) or "decision not recorded"
        if feedback.get("reason"):
            prior += f" ({brief(feedback['reason'], 180)})"
        feedback_hash = str(feedback.get("job_content_hash") or feedback.get("content_hash") or "")
        job_hash = str(job.get("job_content_hash") or job.get("content_hash") or "")
        if feedback_hash and job_hash and feedback_hash.casefold() != job_hash.casefold():
            change = "stored posting hash changed; material mandate change unverified"
        else:
            change = "scored snapshot changed; posting change unverified"

    return " | ".join(
        (
            f"Source: {source}; {coverage}",
            f"Value: {value}",
            f"Candidate evidence: {candidate_evidence}",
            f"Decisive uncertainty: {uncertainty}",
            f"Prior decision: {prior}",
            f"What changed: {change}",
            "Next human action: compare the current posting and evidence with the prior decision; "
            "record a search-quality decision before any pipeline or external action",
        )
    )


def build_focus_projection(
    *,
    pipeline: Iterable[Mapping[str, Any]],
    jobs: Iterable[Mapping[str, Any]] = (),
    source_coverage: Mapping[str, Mapping[str, Any]] | None = None,
    application_states: Iterable[Mapping[str, Any]] = (),
    interview_states: Iterable[Mapping[str, Any]] = (),
    feedback: Iterable[Mapping[str, Any]] = (),
    career_events: Iterable[Mapping[str, Any]] = (),
    outcome_learning_events: Iterable[Mapping[str, Any]] = (),
    communication: Mapping[str, Any] | None = None,
    diagnostics: Iterable[Mapping[str, Any]] = (),
    outcome_review: Mapping[str, Any] | None = None,
    review_items: Iterable[FocusItem] = (),
    application_states_available: bool = True,
    interview_states_available: bool = True,
    suppress_ready_interview_preparation: bool = False,
    pipeline_available: bool = True,
    jobs_available: bool = True,
    outcomes_available: bool = True,
    feedback_available: bool = True,
    career_events_available: bool = True,
    outcome_learning_available: bool = True,
    opportunity_identity_available: bool = True,
    as_of: date | None = None,
    generated_at: datetime | None = None,
    limit: int = 8,
) -> FocusProjection:
    """Compose one bounded queue without changing any underlying authority."""

    if not 1 <= limit <= 100:
        raise ValueError("Focus limit must be between 1 and 100")
    projection_date = as_of or datetime.now(UTC).date()
    generated = generated_at or datetime.now(UTC).replace(microsecond=0)
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("Focus generated_at must include a timezone")
    as_of_cutoff = datetime.combine(projection_date, datetime.max.time(), UTC)

    pipeline_rows = [dict(entry) for entry in pipeline]
    job_rows = []
    for job in jobs:
        row = dict(job)
        scored_raw = str(row.get("scored_at") or "").strip()
        scored_at = _timestamp(scored_raw)
        if scored_raw and (scored_at is None or scored_at > as_of_cutoff):
            continue
        job_rows.append(row)
    application_rows = [dict(state) for state in application_states]
    interview_rows = [dict(state) for state in interview_states]
    feedback_rows = []
    for item in feedback:
        recorded_at = _feedback_recorded_at(item.get("recorded_at"))
        if recorded_at is not None and recorded_at <= as_of_cutoff:
            feedback_rows.append(dict(item))
    career_rows = [
        dict(event)
        for event in career_events
        if _lifecycle_event_visible_as_of(
            event,
            authority_timestamp_key="confirmed_at",
            cutoff=as_of_cutoff,
        )
    ]
    learning_rows = [
        dict(event)
        for event in outcome_learning_events
        if _lifecycle_event_visible_as_of(
            event,
            authority_timestamp_key="recorded_at",
            cutoff=as_of_cutoff,
        )
    ]
    diagnostic_rows = [dict(item) for item in diagnostics]
    communication = dict(communication or {})
    source_coverage = source_coverage or {}

    items: list[FocusItem] = list(review_items)
    availability = {
        "pipeline": pipeline_available,
        "scored_jobs": jobs_available,
        "outcomes": outcomes_available,
        "feedback": feedback_available,
        "career_events": career_events_available,
        "outcome_learning_v2": outcome_learning_available,
        "opportunity_identity": opportunity_identity_available,
    }
    partial = (
        bool(diagnostic_rows)
        or not all(availability.values())
        or not application_states_available
        or not interview_states_available
        or bool(communication.get("errors"))
    )

    unavailable_reasons = {
        "pipeline": (
            "The authoritative pipeline is unavailable; tracked identity and search-review state "
            "cannot be resolved."
        ),
        "scored_jobs": "The scored-opportunity store is unavailable; search-review state is omitted.",
        "outcomes": (
            "The legacy outcome timeline is unavailable; suppressed/closed job visibility and "
            "search-review state cannot be resolved."
        ),
        "feedback": "The feedback ledger is unavailable; no missing-feedback conclusion was made.",
        "career_events": (
            "The confirmed career-event ledger is unavailable; lifecycle state cannot be resolved."
        ),
        "outcome_learning_v2": (
            "Outcome Learning V2 is unavailable; submission attribution, debrief, and review state "
            "cannot be resolved."
        ),
        "opportunity_identity": (
            "Exact opportunity identity bindings are unavailable; cross-authority lifecycle "
            "records were not joined."
        ),
    }
    for source, available in availability.items():
        if available:
            continue
        items.append(
            FocusItem(
                kind="system_blocker",
                opportunity_id=f"system:{source}",
                company="Rolevane",
                role="Input readiness",
                stage="system",
                urgency="blocked",
                due_at=None,
                reason=unavailable_reasons[source],
                source=source,
                action_route="/system",
                requires_user_confirmation=False,
                identity_status="not_applicable",
                blocked=True,
                priority=0,
            )
        )
    if communication.get("errors"):
        items.append(
            FocusItem(
                kind="system_blocker",
                opportunity_id="system:communication",
                company="Rolevane",
                role="Input readiness",
                stage="system",
                urgency="blocked",
                due_at=None,
                reason=(
                    "Communication records contain validation errors; due follow-up state is omitted."
                ),
                source="communication",
                action_route="/system",
                requires_user_confirmation=False,
                identity_status="not_applicable",
                blocked=True,
                priority=0,
            )
        )

    for index, diagnostic in enumerate(diagnostic_rows):
        if str(diagnostic.get("severity") or "warning").casefold() != "error":
            continue
        items.append(
            FocusItem(
                kind="system_blocker",
                opportunity_id=f"system:{index + 1}",
                company="Rolevane",
                role="System readiness",
                stage="system",
                urgency="blocked",
                due_at=None,
                reason=str(diagnostic.get("message") or "A required local input is unavailable."),
                source=str(diagnostic.get("source") or "diagnostics"),
                action_route="/system",
                requires_user_confirmation=False,
                identity_status="not_applicable",
                blocked=True,
                priority=0,
            )
        )

    for state in application_rows:
        identity_status = str(state.get("identity_status") or "unresolved")
        if identity_status not in {*CONFLICTING_IDENTITIES, "legacy_unique"}:
            continue
        opportunity_id = str(state.get("opportunity_id") or "identity:application")
        reason = (
            "Legacy application identity was resolved by company/role labels only; "
            "adopt or bind an exact opportunity ID before joining lifecycle records."
            if identity_status == "legacy_unique"
            else "Application workspace identity is ambiguous; no lifecycle records were joined."
        )
        items.append(
            FocusItem(
                kind="identity_conflict",
                opportunity_id=opportunity_id,
                company=str(state.get("company") or "Unknown company"),
                role=str(state.get("role") or "Unknown role"),
                stage=str(state.get("pipeline_stage") or "unknown"),
                urgency="blocked",
                due_at=None,
                reason=reason,
                source="application_registry",
                action_route="/applications",
                requires_user_confirmation=True,
                identity_status=identity_status,
                blocked=True,
                priority=1_000,
            )
        )

    application_groups: dict[str, list[dict[str, Any]]] = {}
    for state in application_rows:
        identity = str(state.get("opportunity_id") or "").strip()
        if identity and str(state.get("identity_status") or "") in SAFE_APPLICATION_IDENTITIES:
            application_groups.setdefault(identity, []).append(state)
    applications_by_id: dict[str, dict[str, Any]] = {}
    for identity, states in application_groups.items():
        if len(states) == 1:
            applications_by_id[identity] = states[0]
            continue
        items.append(
            FocusItem(
                kind="identity_conflict",
                opportunity_id=identity,
                company=str(states[0].get("company") or "Unknown company"),
                role=str(states[0].get("role") or "Unknown role"),
                stage=str(states[0].get("pipeline_stage") or "unknown"),
                urgency="blocked",
                due_at=None,
                reason=(
                    "Multiple application workspaces claim the same stable opportunity identity; "
                    "none was selected."
                ),
                source="application_registry",
                action_route="/applications",
                requires_user_confirmation=True,
                identity_status="stable_ambiguous",
                blocked=True,
                priority=1_000,
            )
        )
    interviews_by_id: dict[str, list[dict[str, Any]]] = {}
    for state in interview_rows:
        identity = str(state.get("opportunity_id") or "").strip()
        if identity:
            interviews_by_id.setdefault(identity, []).append(state)
    career_by_id: dict[str, list[dict[str, Any]]] = {}
    for event in career_rows:
        identity = str(event.get("opportunity_id") or "").strip()
        if identity:
            career_by_id.setdefault(identity, []).append(event)
    learning_by_id: dict[str, list[dict[str, Any]]] = {}
    for event in learning_rows:
        identity = str(event.get("opportunity_id") or "").strip()
        if identity:
            learning_by_id.setdefault(identity, []).append(event)

    latest_feedback = _latest_by_key(feedback_rows, key="job_key", timestamp="recorded_at")
    job_match_by_opportunity, tracked_job_keys = _job_matches(pipeline_rows, job_rows)
    terminal_lifecycle_by_id = {
        opportunity_id: terminal
        for opportunity_id in career_by_id.keys() | learning_by_id.keys()
        if (
            terminal := _latest_terminal_lifecycle(
                career_by_id.get(opportunity_id, ()),
                learning_by_id.get(opportunity_id, ()),
            )
        )
    }
    lifecycle_authority_available = career_events_available and outcome_learning_available

    for entry in pipeline_rows:
        opportunity_id = _opportunity_id(entry)
        if not opportunity_id:
            continue
        company = str(entry.get("company") or "Unknown company")
        role = str(entry.get("role") or "Unknown role")
        stage = _stage(entry)
        intent_timing = action_intent_timing(entry)
        due_at = intent_timing.isoformat() if intent_timing is not None else _due_value(entry)
        urgency = _urgency(due_at, as_of=projection_date)
        ordinary_action_allowed = action_prompt_allowed(entry, as_of=projection_date)
        events = sorted(
            career_by_id.get(opportunity_id, []),
            key=lambda event: str(event.get("occurred_at") or ""),
        )
        event_kinds = {str(event.get("kind") or "") for event in events}
        learning = learning_by_id.get(opportunity_id, [])
        learning_types = {str(event.get("event_type") or "") for event in learning}
        expected_interview_round = {
            "interviewing": "interview",
            "final_interview": "final",
        }.get(stage)
        interview_records = _interview_records(events, learning)
        explicit_interview_records = _interview_records(
            events,
            [
                event
                for event in learning
                if str(event.get("event_type") or "") != "interview_debrief_recorded"
            ],
        )
        if expected_interview_round is not None:
            interview_records = [
                record
                for record in interview_records
                if record.get("round") == expected_interview_round
            ]
            explicit_interview_records = [
                record
                for record in explicit_interview_records
                if record.get("round") == expected_interview_round
            ]
        current_interview = (
            explicit_interview_records[-1]
            if explicit_interview_records
            else (interview_records[-1] if interview_records else None)
        )
        current_interview = _preserve_advanced_debrief_completion(
            current_interview,
            learning,
            explicit_interview_records,
        )
        latest_terminal = terminal_lifecycle_by_id.get(opportunity_id)
        terminal_conflict = latest_terminal is not None and stage in ACTIVE_STAGES
        has_debrief = any(
            str(event.get("event_type") or "") == "interview_debrief_recorded"
            and _debrief_matches_current_round(event, current=current_interview)
            for event in learning
        )
        submission_confirmed = (
            "application_submitted" in event_kinds or "submission_confirmed" in learning_types
        )
        interview_scheduled = bool(
            current_interview and current_interview.get("status") == "scheduled" and not has_debrief
        )
        completed_interview = bool(
            current_interview and (current_interview.get("status") == "completed" or has_debrief)
        )
        interview_evidenced = current_interview is not None
        offer_confirmed = "offer_received" in event_kinds or any(
            str(event.get("event_type") or "") == "outcome_recorded"
            and isinstance(event.get("payload"), Mapping)
            and str(event["payload"].get("stage") or "").strip().casefold() == "offer"
            and str(event["payload"].get("outcome") or "").strip().casefold()
            in {"offer_received", "accepted", "declined"}
            for event in learning
        )
        lifecycle_confirmation_blocked = (
            lifecycle_authority_available
            and not terminal_conflict
            and (
                (stage in INTERVIEW_STAGES and not interview_evidenced)
                or (stage == "applied" and not submission_confirmed)
                or (stage == "offer" and not offer_confirmed)
            )
        )

        if terminal_conflict:
            items.append(
                FocusItem(
                    kind="lifecycle_inconsistency",
                    opportunity_id=opportunity_id,
                    company=company,
                    role=role,
                    stage=stage,
                    urgency="blocked",
                    due_at=due_at,
                    reason=(
                        f"Pipeline remains in {stage}, but the latest exact-ID confirmed "
                        f"lifecycle outcome is {latest_terminal['outcome']}. Resolve the "
                        "lifecycle conflict before taking further pipeline action."
                    ),
                    source=str(latest_terminal.get("source") or "lifecycle_authority"),
                    action_route=_route(opportunity_id, "lifecycle"),
                    requires_user_confirmation=True,
                    identity_status="exact_opportunity_id",
                    blocked=True,
                    priority=1_000,
                )
            )

        if completed_interview and outcome_learning_available and not has_debrief:
            items.append(
                FocusItem(
                    kind="interview_debrief",
                    opportunity_id=opportunity_id,
                    company=company,
                    role=role,
                    stage=stage,
                    urgency="high" if urgency == "normal" else urgency,
                    due_at=due_at,
                    reason=(
                        "A confirmed interview-completed event has no user-confirmed debrief. "
                        "Record quality, failure point, confidence, and summary without inference."
                    ),
                    source="career_events+outcome_learning_v2",
                    action_route=_route(opportunity_id, "interview"),
                    requires_user_confirmation=True,
                    identity_status="exact_opportunity_id",
                    blocked=False,
                    priority=20_000,
                )
            )

        if (
            career_events_available
            and outcome_learning_available
            and stage in INTERVIEW_STAGES
            and not terminal_conflict
            and not interview_evidenced
        ):
            items.append(
                FocusItem(
                    kind="lifecycle_confirmation",
                    opportunity_id=opportunity_id,
                    company=company,
                    role=role,
                    stage=stage,
                    urgency="blocked",
                    due_at=due_at,
                    reason=(
                        "Pipeline stage is interview-related, but the confirmed lifecycle "
                        "ledgers have no matching schedule/completion event. Confirm the event first."
                    ),
                    source="pipeline+career_events+outcome_learning_v2",
                    action_route=_route(opportunity_id, "lifecycle"),
                    requires_user_confirmation=True,
                    identity_status="exact_opportunity_id",
                    blocked=True,
                    priority=2_000,
                )
            )
        elif (
            stage in INTERVIEW_STAGES
            and not terminal_conflict
            and ordinary_action_allowed
            and interview_scheduled
            and not completed_interview
        ):
            states = interviews_by_id.get(opportunity_id, [])
            current_round = str(
                (current_interview or {}).get("round") or expected_interview_round or ""
            )
            ready = (
                interview_states_available
                and bool(current_round)
                and any(
                    state.get("valid") is True
                    and not state.get("warnings")
                    and _workspace_matches_interview_cycle(
                        state,
                        expected_round=current_round,
                        records=explicit_interview_records,
                    )
                    for state in states
                )
            )
            if not (suppress_ready_interview_preparation and ready):
                items.append(
                    FocusItem(
                        kind="interview_prep",
                        opportunity_id=opportunity_id,
                        company=company,
                        role=role,
                        stage=stage,
                        urgency="high" if urgency == "normal" else urgency,
                        due_at=due_at,
                        reason=(
                            "A confirmed interview needs preparation, but the installed interface cannot read real interview artifacts; readiness is unknown."
                            if not interview_states_available
                            else "Confirmed interview event found; the local preparation workspace is ready."
                            if ready
                            else "Confirmed interview event found; initialize or repair the preparation workspace."
                        ),
                        source=(
                            f"{str((current_interview or {}).get('source') or 'career_events')}"
                            "+interview_workspace"
                        ),
                        action_route=_route(opportunity_id, "interview"),
                        requires_user_confirmation=False,
                        identity_status="exact_opportunity_id",
                        blocked=not ready,
                        priority=10_000,
                    )
                )

        if stage == "preparing" and not terminal_conflict and ordinary_action_allowed:
            application = applications_by_id.get(opportunity_id)
            ready = application_states_available and bool(
                application and application.get("local_readiness") == "ready locally"
            )
            items.append(
                FocusItem(
                    kind="application_readiness",
                    opportunity_id=opportunity_id,
                    company=company,
                    role=role,
                    stage=stage,
                    urgency=urgency,
                    due_at=due_at,
                    reason=(
                        "Application preparation was requested, but the installed interface cannot read real application artifacts; readiness is unknown."
                        if not application_states_available
                        else "Application artifacts are locally validated; submission still requires an external human action."
                        if ready
                        else "Application preparation is not locally ready; review evidence, package, and validation state."
                    ),
                    source="pipeline+application_registry",
                    action_route=_route(opportunity_id, "application"),
                    requires_user_confirmation=ready,
                    identity_status=str(
                        (application or {}).get("identity_status") or "exact_opportunity_id"
                    ),
                    blocked=not ready,
                    priority=30_000,
                )
            )

        if stage == "applied" and not terminal_conflict:
            if career_events_available and outcome_learning_available and not submission_confirmed:
                items.append(
                    FocusItem(
                        kind="lifecycle_confirmation",
                        opportunity_id=opportunity_id,
                        company=company,
                        role=role,
                        stage=stage,
                        urgency="blocked",
                        due_at=due_at,
                        reason=(
                            "Pipeline says applied, but no confirmed application-submitted event exists. "
                            "Do not infer submission from files or stage alone."
                        ),
                        source="pipeline+career_events+outcome_learning_v2",
                        action_route=_route(opportunity_id, "lifecycle"),
                        requires_user_confirmation=True,
                        identity_status="exact_opportunity_id",
                        blocked=True,
                        priority=2_000,
                    )
                )
            elif (
                submission_confirmed
                and outcome_learning_available
                and "submission_confirmed" not in learning_types
            ):
                items.append(
                    FocusItem(
                        kind="submission_confirmation",
                        opportunity_id=opportunity_id,
                        company=company,
                        role=role,
                        stage=stage,
                        urgency=urgency,
                        due_at=due_at,
                        reason=(
                            "The submission event is confirmed, but Outcome Learning V2 lacks "
                            "immutable submission attribution and artifact hashes."
                        ),
                        source="career_events+outcome_learning_v2",
                        action_route=_route(opportunity_id, "lifecycle"),
                        requires_user_confirmation=True,
                        identity_status="exact_opportunity_id",
                        blocked=False,
                        priority=30_500,
                    )
                )

        if (
            stage == "offer"
            and not terminal_conflict
            and career_events_available
            and outcome_learning_available
            and not offer_confirmed
        ):
            items.append(
                FocusItem(
                    kind="lifecycle_confirmation",
                    opportunity_id=opportunity_id,
                    company=company,
                    role=role,
                    stage=stage,
                    urgency="blocked",
                    due_at=due_at,
                    reason=(
                        "Pipeline says offer, but no exact confirmed offer_received event exists. "
                        "Confirm the offer event before treating this as an offer."
                    ),
                    source="pipeline+career_events+outcome_learning_v2",
                    action_route=_route(opportunity_id, "lifecycle"),
                    requires_user_confirmation=True,
                    identity_status="exact_opportunity_id",
                    blocked=True,
                    priority=2_000,
                )
            )

        if (
            lifecycle_authority_available
            and not terminal_conflict
            and not lifecycle_confirmation_blocked
            and ordinary_action_allowed
            and stage in ACTIVE_STAGES
            and (entry.get("next_action") or due_at)
        ):
            is_overdue = urgency in {"overdue", "today"}
            items.append(
                FocusItem(
                    kind="pipeline_action",
                    opportunity_id=opportunity_id,
                    company=company,
                    role=role,
                    stage=stage,
                    urgency=urgency,
                    due_at=due_at,
                    reason=str(
                        entry.get("next_action") or "A due date exists without a next action."
                    ),
                    source="pipeline",
                    action_route=_route(opportunity_id),
                    requires_user_confirmation=False,
                    identity_status="exact_opportunity_id",
                    blocked=False,
                    priority=5_000 if is_overdue else 45_000,
                )
            )

        matched_job = job_match_by_opportunity.get(opportunity_id)
        matched_job_key = str((matched_job or {}).get("job_key") or "")
        if (
            pipeline_available
            and jobs_available
            and outcomes_available
            and feedback_available
            and lifecycle_authority_available
            and stage == "review"
            and not terminal_conflict
            and ordinary_action_allowed
            and matched_job_key
            and (
                matched_job_key not in latest_feedback
                or not _feedback_snapshot_matches(
                    latest_feedback[matched_job_key],
                    matched_job or {},
                )
            )
        ):
            items.append(
                FocusItem(
                    kind="search_review",
                    opportunity_id=opportunity_id,
                    company=company,
                    role=role,
                    stage=stage,
                    urgency="normal",
                    due_at=due_at,
                    reason=_search_review_reason(
                        matched_job or {}, latest_feedback.get(matched_job_key), source_coverage
                    ),
                    source="pipeline+scored_jobs+feedback",
                    action_route=_route(opportunity_id, "feedback"),
                    requires_user_confirmation=True,
                    identity_status="exact_job_identity",
                    blocked=False,
                    priority=50_000,
                )
            )

    for due in (
        (communication.get("due") or [])
        if lifecycle_authority_available and not communication.get("errors")
        else ()
    ):
        if not isinstance(due, Mapping):
            continue
        opportunity_id = str(due.get("opportunity_id") or "").strip()
        if not opportunity_id:
            continue
        entry = next((row for row in pipeline_rows if _opportunity_id(row) == opportunity_id), None)
        if entry is None:
            continue
        if not action_prompt_allowed(entry, as_of=projection_date):
            continue
        if terminal_lifecycle_by_id.get(opportunity_id):
            continue
        due_at = str(due.get("due_at") or "").strip() or None
        queue = str(due.get("queue") or "").strip().casefold()
        stop_reason = str(due.get("stop_reason") or "").strip()
        communication_blocked = queue == "blocked"
        blocked_detail = stop_reason or "Communication policy or budget currently blocks this plan."
        reason = (
            f"Communication follow-up is blocked: {blocked_detail}"
            if communication_blocked
            else "An approved local communication plan is due for human review; nothing is sent here."
        )
        items.append(
            FocusItem(
                kind="follow_up_review",
                opportunity_id=opportunity_id,
                company=str(entry.get("company") or "Unknown company"),
                role=str(entry.get("role") or "Unknown role"),
                stage=_stage(entry),
                urgency="blocked"
                if communication_blocked
                else _urgency(due_at, as_of=projection_date),
                due_at=due_at,
                reason=reason,
                source="communication_plans",
                action_route="/communication",
                requires_user_confirmation=True,
                identity_status="exact_opportunity_id",
                blocked=communication_blocked,
                priority=40_000,
            )
        )

    for job in (
        job_rows
        if (
            pipeline_available
            and jobs_available
            and outcomes_available
            and feedback_available
            and lifecycle_authority_available
        )
        else ()
    ):
        job_key = str(job.get("job_key") or "").strip()
        if (
            not job_key
            or job_key in tracked_job_keys
            or (
                job_key in latest_feedback
                and _feedback_snapshot_matches(latest_feedback[job_key], job)
            )
            or str(job.get("recommendation") or "") not in REVIEW_RECOMMENDATIONS
        ):
            continue
        try:
            score = max(0.0, min(100.0, float(job.get("total") or 0)))
        except (TypeError, ValueError):
            score = 0.0
        items.append(
            FocusItem(
                kind="search_review",
                opportunity_id=job_key,
                company=str(job.get("company") or "Unknown company"),
                role=str(job.get("title") or "Unknown role"),
                stage="untracked_review",
                urgency="normal",
                due_at=None,
                reason=(
                    f"Scored {score:.1f} with route "
                    f"{str(job.get('recommendation') or '').replace('_', ' ')}. "
                    + _search_review_reason(job, latest_feedback.get(job_key), source_coverage)
                ),
                source="scored_jobs+feedback",
                action_route=(f"/opportunities?job={quote(job_key, safe='')}#search-feedback"),
                requires_user_confirmation=True,
                identity_status="exact_job_key",
                blocked=False,
                priority=50_000 + int((100.0 - score) * 10),
            )
        )

    if outcome_learning_available and outcome_review and outcome_review.get("review_due"):
        reasons = outcome_review.get("review_due_reasons") or []
        reason = "; ".join(str(item) for item in reasons) or "Outcome-learning review is due."
        items.append(
            FocusItem(
                kind="outcome_review",
                opportunity_id="portfolio:outcome-learning",
                company="Rolevane",
                role="Outcome learning",
                stage="portfolio",
                urgency="normal",
                due_at=None,
                reason=reason,
                source="outcome_learning_v2",
                action_route="/system",
                requires_user_confirmation=True,
                identity_status="portfolio",
                blocked=False,
                priority=60_000,
            )
        )

    unique: dict[tuple[str, str, str], FocusItem] = {}
    for item in items:
        key = (item.kind, item.opportunity_id, item.source)
        existing = unique.get(key)
        if existing is None or _sort_key(item) < _sort_key(existing):
            unique[key] = item
    ordered = sorted(unique.values(), key=_sort_key)
    selected = tuple(ordered[:limit])
    return FocusProjection(
        schema_version=1,
        generated_at=generated.astimezone(UTC).replace(microsecond=0).isoformat(),
        as_of=projection_date.isoformat(),
        read_only=True,
        external_actions=False,
        pipeline_changed=False,
        scoring_changed=False,
        partial=partial,
        total_items=len(ordered),
        returned_items=len(selected),
        omitted_items=max(0, len(ordered) - len(selected)),
        items=selected,
    )


def render_focus_markdown(projection: FocusProjection) -> str:
    lines = [
        "# Rolevane Decision Inbox",
        "",
        f"Generated: {projection.generated_at}",
        f"As of: {projection.as_of}",
        "",
        (
            "Read-only projection. It performs no external action and changes neither pipeline "
            "nor scoring state."
        ),
        "",
    ]
    if projection.partial:
        lines.extend(
            [
                "> One or more optional inputs are degraded. Inspect diagnostics before relying on omitted state.",
                "",
            ]
        )
    if not projection.items:
        return "\n".join([*lines, "No material focus item is currently projected.", ""])
    lines.extend(
        [
            "| Urgency | Kind | Opportunity | Stage | Why now | Confirmation |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in projection.items:
        why = item.reason.replace("|", "\\|").replace("\n", " ")
        opportunity = f"{item.company} — {item.role}".replace("|", "\\|")
        lines.append(
            f"| {item.urgency} | {item.kind.replace('_', ' ')} | {opportunity} | "
            f"{item.stage} | {why} | {'Required' if item.requires_user_confirmation else 'No'} |"
        )
    if projection.omitted_items:
        lines.extend(
            ["", f"{projection.omitted_items} additional material item(s) omitted by limit."]
        )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "FocusItem",
    "FocusProjection",
    "build_focus_projection",
    "render_focus_markdown",
]

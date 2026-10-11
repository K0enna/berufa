"""Read-only public Focus over validated local authorities, never a task ledger."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from career_os.application.opportunities import SuppliedOpportunityState
from career_os.application.tasks import TaskRecord
from career_os.application.workspaces import WorkspaceInventory
from career_os.evidence_contract import ValidationIssue
from career_os.evidence_records import EvidenceReadiness, EvidenceRevision
from career_os.focus import (
    TERMINAL_LIFECYCLE_OUTCOMES,
    FocusItem,
    FocusProjection,
    _latest_terminal_lifecycle,
    build_focus_projection,
)
from career_os.outcome_records import OutcomeLearningEvent, _timestamp_instant, calculate_report


@dataclass(frozen=True, slots=True)
class FocusInputStatus:
    name: str
    state: Literal[
        "ready",
        "empty",
        "not_initialized",
        "consent_required",
        "invalid",
        "unavailable",
        "unsupported",
    ]
    detail: str


@dataclass(frozen=True, slots=True)
class FocusInputs:
    evidence_readiness: EvidenceReadiness | None = None
    evidence_reviews: tuple[EvidenceRevision, ...] = ()
    evidence_conflicts: tuple[str, ...] = ()
    timeline_issues: tuple[ValidationIssue, ...] = ()
    opportunities: tuple[SuppliedOpportunityState, ...] = ()
    events: tuple[OutcomeLearningEvent, ...] = ()
    statuses: tuple[FocusInputStatus, ...] = ()
    workspaces: WorkspaceInventory | None = None
    tasks: tuple[TaskRecord, ...] = ()


class FocusRepository(Protocol):
    def read(self) -> FocusInputs: ...


@dataclass(frozen=True, slots=True)
class FocusResult:
    projection: FocusProjection
    inputs: tuple[FocusInputStatus, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "projection": self.projection.to_dict(),
            "inputs": [asdict(status) for status in self.inputs],
            "manual_routes": {
                "evidence_review": "rolevane-product",
                "decision_review": "rolevane-role-evaluate-supplied",
                "decision_record": "rolevane-role-decide-supplied",
                "confirmed_events": "rolevane-event-list",
                "record_event": "rolevane-event-record",
                "outcome_review": "rolevane-outcome-report",
                "offer_comparison": "rolevane-offer-compare",
                "workspace_list": "rolevane-workspace list",
                "workspace_inspect": "rolevane-workspace inspect",
                "workspace_review": "rolevane-workspace review",
                "planned_tasks": "rolevane-task list",
            },
            "limitations": [
                "Read-only current-state projection; revalidate before any later action",
                "Uninitialized, unavailable and unsupported inputs are not empty history",
                "No inferred employer event, decision, due date or preparation readiness",
                "Action routes identify local views; this command does not start a web server",
            ],
            "model_calls": False,
        }


@dataclass(frozen=True, slots=True)
class FocusOperationError:
    code: Literal["invalid_request", "io_error"]
    message: str


def require_focus_result(result: FocusResult | FocusOperationError) -> FocusResult:
    if isinstance(result, FocusOperationError):
        if result.code == "io_error":
            raise OSError(result.message)
        raise ValueError(result.message)
    return result


def _review_item(
    kind: str,
    identity: str,
    reason: str,
    *,
    source: str,
    priority: int,
    company: str = "Rolevane",
    role: str = "Local review",
    blocked: bool = False,
    confirmation: bool = True,
    stage: str = "review",
    route: str = "/review",
) -> FocusItem:
    return FocusItem(
        kind,
        identity,
        company,
        role,
        stage,
        "blocked" if blocked else "normal",
        None,
        reason,
        source,
        route,
        confirmation,
        "exact_local_record",
        blocked,
        priority,
    )


def _lifecycle_rows(events: tuple[OutcomeLearningEvent, ...]) -> list[dict[str, object]]:
    grouped: dict[str, list[OutcomeLearningEvent]] = {}
    for event in events:
        if event.event_type != "review_acknowledged":
            grouped.setdefault(event.opportunity_id, []).append(event)
    rows: list[dict[str, object]] = []
    for identity, records in grouped.items():
        ordered = sorted(
            records,
            key=lambda item: (
                _timestamp_instant(item.occurred_at, "occurred_at"),
                _timestamp_instant(item.recorded_at, "recorded_at"),
            ),
        )
        lifecycle = [item for item in ordered if item.event_type != "interview_debrief_recorded"]
        latest = lifecycle[-1] if lifecycle else ordered[-1]
        if latest.event_type == "submission_confirmed":
            stage = "applied"
        elif latest.event_type == "interview_debrief_recorded":
            stage = (
                "final_interview"
                if latest.payload["interview_stage"] == "final"
                else "interviewing"
            )
        else:
            terminal = _latest_terminal_lifecycle((), [item.to_dict() for item in ordered])
            if terminal is not None:
                stage = str(terminal["outcome"])
            else:
                stage = {
                    "application": "applied",
                    "interview": "interviewing",
                    "final_interview": "final_interview",
                    "offer": "offer",
                }.get(str(latest.payload["stage"]), "review")
        rows.append(
            {
                "opportunity_id": identity,
                "company": latest.payload["company"],
                "role": latest.payload["title"],
                "stage": stage,
            }
        )
    return rows


class FocusService:
    def __init__(
        self, repository: FocusRepository, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def project(self, *, limit: int = 8) -> FocusResult | FocusOperationError:
        try:
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError("Focus limit must be an integer from 1 to 100")
            generated = self._clock()
            if generated.tzinfo is None or generated.utcoffset() is None:
                raise ValueError("Focus clock must include a timezone")
            inputs = self._repository.read()
            return self._project(inputs, limit, generated.astimezone(UTC))
        except ValueError:
            return FocusOperationError("invalid_request", "Focus input or clock is invalid")
        except OSError:
            return FocusOperationError("io_error", "Local Focus inputs are unavailable")

    def _project(self, inputs: FocusInputs, limit: int, generated: datetime) -> FocusResult:
        events = tuple(
            event
            for event in inputs.events
            if _timestamp_instant(event.occurred_at, "occurred_at") <= generated
            and _timestamp_instant(event.recorded_at, "recorded_at") <= generated
        )
        event_status = next(
            (item for item in inputs.statuses if item.name == "confirmed_events"), None
        )
        events_available = event_status is not None and event_status.state in {"ready", "empty"}
        report = None
        if events_available:
            try:
                report = calculate_report(events, as_of=generated.isoformat())
            except ValueError:
                events = ()
                events_available = False
                invalid = FocusInputStatus(
                    "confirmed_events",
                    "invalid",
                    "Confirmed event history violates the canonical report contract; history is unknown.",
                )
                inputs = replace(
                    inputs,
                    statuses=tuple(
                        invalid if item.name == "confirmed_events" else item
                        for item in inputs.statuses
                    ),
                )
        reviews: list[FocusItem] = []
        diagnostics: list[dict[str, str]] = []
        for status in inputs.statuses:
            if status.state in {"ready", "empty"}:
                continue
            diagnostics.append(
                {"severity": "warning", "source": status.name, "message": status.detail}
            )
            if status.state == "unsupported" or status.name == "confirmed_events":
                continue
            if status.name in {"prepared_artifacts", "planned_actions"} and any(
                item.name == "evidence" and item.state != "ready" for item in inputs.statuses
            ):
                continue
            reviews.append(
                _review_item(
                    "system_blocker"
                    if status.state in {"invalid", "unavailable"}
                    else "setup_review",
                    f"system:{status.name}",
                    status.detail,
                    source=status.name,
                    priority=0,
                    blocked=True,
                    confirmation=False,
                    stage="system",
                    route="/system",
                )
            )
        for identity in inputs.evidence_conflicts:
            reviews.append(
                _review_item(
                    "evidence_conflict",
                    f"evidence:{identity}",
                    "Approved evidence has an unresolved relationship conflict; review the exact revisions.",
                    source="evidence_vault",
                    priority=1_000,
                    blocked=True,
                    route="/evidence",
                )
            )
        for issue in inputs.timeline_issues:
            reviews.append(
                _review_item(
                    "evidence_conflict",
                    f"evidence:{issue.evidence_id or 'timeline'}:{sha256(issue.message.encode('utf-8')).hexdigest()}",
                    issue.message,
                    source=f"evidence_vault:{issue.code}",
                    priority=1_000,
                    blocked=True,
                    route="/evidence",
                )
            )
        for revision in inputs.evidence_reviews:
            reviews.append(
                _review_item(
                    "evidence_review",
                    f"evidence:{revision.evidence_id}",
                    f"Revision {revision.revision} is {revision.status}; review its exact statement, source and publication boundary.",
                    source="evidence_vault",
                    priority=6_000,
                    route="/evidence",
                )
            )
        if (
            inputs.evidence_readiness
            and not inputs.evidence_readiness.ready
            and not inputs.evidence_reviews
        ):
            reviews.append(
                _review_item(
                    "evidence_readiness",
                    "system:evidence-readiness",
                    "; ".join(inputs.evidence_readiness.blockers),
                    source="evidence_vault",
                    priority=2_000,
                    blocked=True,
                    route="/evidence",
                )
            )
        active_ids = {
            event.opportunity_id for event in events if event.event_type != "review_acknowledged"
        }
        prepared_ids: set[str] = set()
        interview_states: list[dict[str, object]] = []
        lifecycle_rows = _lifecycle_rows(events) if events_available else []
        terminal_ids = {
            row["opportunity_id"]
            for row in lifecycle_rows
            if row["stage"] in TERMINAL_LIFECYCLE_OUTCOMES
        }
        if inputs.workspaces is not None:
            for index, diagnostic in enumerate(inputs.workspaces.diagnostics):
                diagnostics.append(
                    {
                        "severity": "warning",
                        "source": "prepared_artifacts",
                        "message": diagnostic.detail,
                    }
                )
                reviews.append(
                    _review_item(
                        "system_blocker",
                        f"workspace-diagnostic:{index}:{diagnostic.code}",
                        diagnostic.detail,
                        source="prepared_artifacts",
                        priority=0,
                        blocked=True,
                        confirmation=False,
                        stage="system",
                        route="/system",
                    )
                )
            for version in inputs.workspaces.versions:
                if not version.selected:
                    continue
                ref = version.workspace
                prepared_ids.add(ref.opportunity_id)
                if ref.opportunity_id in terminal_ids:
                    continue
                review = version.review
                reviewed = (
                    review is not None
                    and _timestamp_instant(review.reviewed_at, "reviewed_at") <= generated
                )
                accepted = reviewed and version.review_state == "accepted"
                if version.current is not True or not accepted:
                    stale = version.current is not True
                    changes = reviewed and version.review_state == "changes_requested"
                    reviews.append(
                        _review_item(
                            "artifact_stale"
                            if stale
                            else "artifact_revision"
                            if changes
                            else f"{ref.kind}_review",
                            ref.opportunity_id,
                            "Prepared content has a changed authority basis; prepare and review a new version."
                            if stale
                            else "The latest local review requested changes; prepare a revised version."
                            if changes
                            else "Prepared content needs an exact-version local review; no external readiness is asserted.",
                            source=f"prepared_artifacts:{ref.key}",
                            priority=2_000 if stale else 15_000,
                            company=version.company,
                            role=version.role,
                            blocked=stale or changes,
                            route="/opportunity",
                        )
                    )
                if ref.kind == "interview":
                    event = next(
                        (
                            event
                            for event in events
                            if accepted
                            and review
                            and event.event_id == review.request.interview_event_id
                            and event.record_sha256 == review.event_record_sha256
                            and event.opportunity_id == ref.opportunity_id
                            and event.event_type == "outcome_recorded"
                            and event.payload["outcome"] == "scheduled"
                            and event.payload["stage"]
                            == ("final_interview" if version.round_type == "final" else "interview")
                        ),
                        None,
                    )
                    interview_states.append(
                        {
                            "opportunity_id": ref.opportunity_id,
                            "valid": bool(accepted and version.current is True and event),
                            "warnings": [],
                            "round": "final" if version.round_type == "final" else "interview",
                            "confirmed_schedule_event_id": event.event_id if event else None,
                        }
                    )
        if events_available:
            for state in inputs.opportunities:
                dossier = state.dossier.dossier
                identity = state.dossier.opportunity_id
                if identity in active_ids:
                    continue
                decision = state.latest_decision
                recommendation = str(dossier["role_evaluation"]["recommendation"])
                if (
                    state.decision_current
                    and decision is not None
                    and decision["decision"] in {"hold", "skip"}
                ):
                    continue
                if decision is None and recommendation == "skip":
                    continue
                if not state.decision_current:
                    kind = "decision_stale" if decision is not None else "decision_review"
                    reason = (
                        "The prior decision is not bound to the current evaluation; review the changed basis."
                        if decision
                        else "A confirmed supplied-role dossier needs a local decision; it is not an official verified posting."
                    )
                    blocked = False
                else:
                    kind = (
                        "preparation_choice"
                        if decision["decision"] == "pursue"
                        else "decision_conditions"
                    )
                    if kind == "preparation_choice" and identity in prepared_ids:
                        continue
                    reason = (
                        "The user chose pursue; choose the next preparation step explicitly. Supplied posting authority cannot establish official application readiness."
                        if kind == "preparation_choice"
                        else "The user chose conditional; review the recorded conditions before proceeding."
                    )
                    blocked = True
                reviews.append(
                    _review_item(
                        kind,
                        identity,
                        reason,
                        company=str(dossier["company"]),
                        role=str(dossier["title"]),
                        source="supplied_opportunities",
                        priority=40_000,
                        blocked=blocked,
                        route="/opportunity",
                        confirmation=True,
                    )
                )
        confirmed_offers = {
            event.opportunity_id
            for event in events
            if event.event_type == "outcome_recorded"
            and event.payload["stage"] == "offer"
            and event.payload["outcome"] == "offer_received"
        }
        for row in lifecycle_rows:
            if row["stage"] == "offer" and row["opportunity_id"] in confirmed_offers:
                reviews.append(
                    _review_item(
                        "offer_review",
                        str(row["opportunity_id"]),
                        "An offer event is confirmed; review supplied terms and compare economics before any decision. No offer facts or acceptance are inferred.",
                        company=str(row["company"]),
                        role=str(row["role"]),
                        source="outcome_learning_v2",
                        priority=30_000,
                        route="/opportunity",
                    )
                )
        states = {state.dossier.opportunity_id: state for state in inputs.opportunities}
        role_status = next(
            (status for status in inputs.statuses if status.name == "supplied_roles"), None
        )
        roles_available = role_status is not None and role_status.state in {"ready", "empty"}
        for task in inputs.tasks:
            if task.status != "open":
                continue
            state = states.get(task.opportunity_id)
            blocked = (
                not events_available
                or not roles_available
                or state is None
                or not state.decision_current
            )
            reason = task.description
            priority, urgency = 50_000, "normal"
            if task.due is not None:
                local_day = generated.astimezone(ZoneInfo(task.due.timezone)).date()
                if task.due.kind == "date":
                    due_day = date.fromisoformat(task.due.value)
                    overdue = due_day < local_day
                else:
                    due_instant = datetime.fromisoformat(task.due.value)
                    due_day = due_instant.astimezone(ZoneInfo(task.due.timezone)).date()
                    overdue = due_instant.astimezone(UTC) < generated
                priority = 5_000 if overdue else 10_000 if due_day == local_day else 45_000
                urgency = "overdue" if overdue else "today" if due_day == local_day else "normal"
                reason += f" | Due: {task.due.value} ({task.due.timezone})"
            if not events_available or not roles_available:
                reason += (
                    " | Career lifecycle or opportunity basis is unavailable; status is unknown."
                )
            elif state is None or not state.decision_current:
                reason += " | Opportunity is missing or stale; the saved task remains your plan."
            terminal = _latest_terminal_lifecycle(
                (),
                (
                    event.to_dict()
                    for event in events
                    if event.opportunity_id == task.opportunity_id
                ),
            )
            if terminal is not None:
                blocked = True
                reason += " | Opportunity has a terminal career event; review this retained plan."
            if blocked:
                priority, urgency = 2_000, "blocked"
            reviews.append(
                FocusItem(
                    kind="planned_task",
                    opportunity_id=task.opportunity_id,
                    company=str(state.dossier.dossier["company"]) if state else "Saved opportunity",
                    role=str(state.dossier.dossier["title"]) if state else "Local task",
                    stage="planned",
                    urgency=urgency,
                    due_at=task.due.value if task.due else None,
                    reason=reason,
                    source=f"planned_task:{task.task_id}",
                    action_route="/opportunity",
                    requires_user_confirmation=True,
                    identity_status="exact_local_task",
                    blocked=blocked,
                    priority=priority,
                )
            )
        projection = build_focus_projection(
            pipeline=lifecycle_rows,
            outcome_learning_events=[event.to_dict() for event in events]
            if events_available
            else (),
            outcome_review=report.to_dict() if report else None,
            outcome_learning_available=events_available,
            review_items=reviews,
            diagnostics=diagnostics,
            application_states_available=False,
            interview_states=interview_states,
            interview_states_available=inputs.workspaces is not None,
            suppress_ready_interview_preparation=True,
            as_of=generated.date(),
            generated_at=generated,
            limit=limit,
        )
        return FocusResult(projection, inputs.statuses)

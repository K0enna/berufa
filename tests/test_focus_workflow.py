from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from career_os.application.opportunities import SuppliedOpportunityState, SuppliedRoleResult
from career_os.application.tasks import TaskDue, TaskRecord
from career_os.evidence_contract import TimelineItem, validate_timeline
from career_os.evidence_vault import EvidenceVault
from career_os.focus import build_focus_projection
from career_os.focus_workflow import main
from career_os.infrastructure.focus import LocalFocusRepository, create_focus_service
from career_os.infrastructure.outcomes import append_event
from career_os.outcome_records import (
    calculate_report,
    create_outcome_event,
    create_review_acknowledgement,
    create_submission_event,
)
from career_os.public_api import (
    FocusInputs,
    FocusInputStatus,
    FocusOperationError,
    FocusService,
    require_focus_result,
)
from career_os.synthetic_evidence import seed_synthetic_evidence

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)


def test_planned_tasks_keep_distinct_identity_local_day_and_unknown_lifecycle():
    state = SuppliedOpportunityState(
        SuppliedRoleResult(
            "example_role",
            "a" * 64,
            "b" * 64,
            {
                "company": "Fictional",
                "title": "Example",
                "role_evaluation": {"recommendation": "conditional"},
            },
        ),
        {"decision": "hold"},
        True,
    )
    tasks = tuple(
        TaskRecord(
            "task_" + str(index) * 64,
            "example_role",
            1,
            "Review supplied scope",
            TaskDue("date", "2026-09-04", zone),
            "open",
            "2026-09-03T00:00:00+00:00",
            "2026-09-03T00:00:00+00:00",
            None,
            f"request-{index}",
            "c" * 64,
        )
        for index, zone in ((1, "Europe/Berlin"), (2, "Pacific/Kiritimati"))
    )
    inputs = replace(
        _inputs(opportunities=(state,)),
        tasks=tasks,
        statuses=(
            *_inputs().statuses,
            FocusInputStatus("supplied_roles", "ready", "Supplied roles"),
        ),
    )
    projection = _project(inputs).projection
    saved = [item for item in projection.items if item.kind == "planned_task"]
    assert len(saved) == 2 and len({item.source for item in saved}) == 2
    assert saved[0].urgency == "overdue" and "Pacific/Kiritimati" in saved[0].reason
    assert saved[1].urgency == "today" and "Europe/Berlin" in saved[1].reason
    assert _project(replace(inputs, tasks=tuple(reversed(tasks)))).projection == projection
    unavailable = replace(
        inputs,
        statuses=(
            FocusInputStatus("confirmed_events", "invalid", "Invalid history"),
            *inputs.statuses[1:],
        ),
    )
    assert all(
        item.blocked and "unknown" in item.reason
        for item in _project(unavailable).projection.items
        if item.kind == "planned_task"
    )
    stale = replace(inputs, opportunities=(replace(state, decision_current=False),))
    assert all(
        item.blocked and "stale" in item.reason
        for item in _project(stale).projection.items
        if item.kind == "planned_task"
    )
    completed = replace(tasks[0], status="completed", terminal_at=tasks[0].updated_at)
    assert not any(
        item.kind == "planned_task"
        for item in _project(replace(inputs, tasks=(completed,))).projection.items
    )


def _project(inputs: FocusInputs, **options):
    class Repository:
        def read(self):
            return inputs

    return require_focus_result(FocusService(Repository(), clock=lambda: NOW).project(**options))


def _event(
    identity="example-role",
    *,
    stage="interview",
    outcome="scheduled",
    occurred_at="2026-09-03T10:00:00Z",
):
    return create_outcome_event(
        opportunity_id=identity,
        job_key="example:role",
        company="Fictional Company",
        title="Example Lead",
        stage=stage,
        outcome=outcome,
        reason_category="no_reason",
        evidence_quality="none",
        occurred_at=occurred_at,
        recorded_at=occurred_at,
        confirmed=True,
    )


def _inputs(*events, opportunities=()):
    return FocusInputs(
        events=events,
        opportunities=opportunities,
        statuses=(
            FocusInputStatus(
                "confirmed_events", "ready" if events else "empty", "Canonical events"
            ),
        ),
    )


def test_absent_and_evidence_review_are_read_only(tmp_path: Path, capsys) -> None:
    root = tmp_path / "instance"
    service = create_focus_service(root)
    assert not root.exists()
    result = require_focus_result(service.project())
    assert result.projection.partial
    assert {item.name: item.state for item in result.inputs}["evidence"] == "not_initialized"
    assert not root.exists()
    vault = EvidenceVault(root)
    vault.record_consent(accepted=False)
    declined = require_focus_result(service.project())
    assert {item.name: item.state for item in declined.inputs}["evidence"] == "consent_required"
    assert not any(item.kind == "evidence_review" for item in declined.projection.items)
    vault.record_consent(accepted=True)
    seed_synthetic_evidence(vault)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    assert main(["--instance-root", str(root), "--limit", "3"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["projection"]["returned_items"] == 3
    assert output["projection"]["omitted_items"] == 3
    assert {item["kind"] for item in output["projection"]["items"]} == {"evidence_review"}
    assert not output["model_calls"] and output["projection"]["read_only"]
    assert {
        p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()
    } == before


def test_invalid_events_and_unknown_roles_are_visible_without_payloads(tmp_path: Path) -> None:
    vault = EvidenceVault(tmp_path)
    vault.record_consent(accepted=True)
    namespace = tmp_path / "data/runtime/opportunities/example_role"
    namespace.mkdir(parents=True)
    (namespace / "decision-dossier.json").write_text('{"private": "do not print"}')
    ledger = tmp_path / "data/runtime/outcome-learning-v2.jsonl"
    ledger.write_text("private broken content")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = require_focus_result(create_focus_service(tmp_path).project())
    statuses = {item.name: item.state for item in result.inputs}
    assert statuses["confirmed_events"] == "invalid"
    assert statuses["supplied_role:example_role"] == "invalid"
    assert result.projection.partial
    assert sum(item.source == "outcome_learning_v2" for item in result.projection.items) == 1
    assert "private broken" not in json.dumps(result.to_dict())
    assert "do not print" not in json.dumps(result.to_dict())
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_opportunity_bound_is_not_an_empty_queue(tmp_path: Path) -> None:
    EvidenceVault(tmp_path).record_consent(accepted=True)
    namespace = tmp_path / "data/runtime/opportunities"
    namespace.mkdir(parents=True)
    for index in range(101):
        (namespace / f"role_{index}").mkdir()
    result = LocalFocusRepository(tmp_path).read()
    assert not result.opportunities
    assert (
        next(item for item in result.statuses if item.name == "supplied_roles").state
        == "unavailable"
    )


def test_confirmed_interview_offer_and_terminal_events_use_exact_ids(tmp_path: Path) -> None:
    scheduled = _event()
    offered = _event("other-role", stage="offer", outcome="offer_received")
    result = _project(_inputs(scheduled, offered))
    assert {(item.kind, item.opportunity_id) for item in result.projection.items} == {
        ("interview_prep", "example-role"),
        ("offer_review", "other-role"),
    }
    prep = next(item for item in result.projection.items if item.kind == "interview_prep")
    assert prep.blocked and "readiness is unknown" in prep.reason
    unknown_offer = _project(_inputs(_event(stage="offer", outcome="no_response")))
    assert [item.kind for item in unknown_offer.projection.items] == ["lifecycle_confirmation"]
    completed = _event(outcome="completed", occurred_at="2026-09-04T09:00:00Z")
    result = _project(_inputs(scheduled, completed))
    assert [item.kind for item in result.projection.items] == ["interview_debrief"]
    terminal = _event(stage="offer", outcome="declined", occurred_at="2026-09-04T11:00:00Z")
    assert [
        item.kind for item in _project(_inputs(scheduled, completed, terminal)).projection.items
    ] == ["interview_debrief"]
    future = _event(occurred_at="2026-09-04T12:01:00Z")
    assert not _project(_inputs(future)).projection.items
    path = tmp_path / "data/runtime/outcome-learning-v2.jsonl"
    append_event(scheduled, path)
    before = path.read_bytes()
    local = require_focus_result(create_focus_service(tmp_path).project())
    assert any(item.kind == "interview_prep" for item in local.projection.items)
    assert path.read_bytes() == before


def test_focus_uses_canonical_outcome_review_acknowledgments(tmp_path: Path) -> None:
    events = tuple(
        create_submission_event(
            opportunity_id=f"example-{index}",
            job_key=f"example:{index}",
            company="Fictional",
            title="Example",
            discovery_source="user_supplied",
            application_channel="employer_ats",
            confirmation_method="candidate_confirmed_sent",
            job_content_hash="a" * 64,
            fit_score=70,
            scorecard_version="example-v1",
            submitted_at="2026-07-01T10:00:00Z",
            recorded_at="2026-07-01T10:00:00Z",
            confirmed=True,
        )
        for index in range(10)
    )
    assert [item.kind for item in _project(_inputs(*events)).projection.items] == ["outcome_review"]
    report = calculate_report(events, as_of=NOW.isoformat())
    ack = create_review_acknowledgement(
        events,
        report_basis_hash=report.basis_hash,
        as_of=NOW.isoformat(),
        acknowledged_at=NOW.isoformat(),
        confirmed=True,
    )
    assert not _project(_inputs(*events, ack)).projection.items
    duplicate = create_submission_event(
        opportunity_id="example-0",
        job_key="example:0",
        company="Fictional",
        title="Example",
        discovery_source="user_supplied",
        application_channel="employer_ats",
        confirmation_method="candidate_confirmed_sent",
        job_content_hash="a" * 64,
        fit_score=70,
        scorecard_version="example-v1",
        submitted_at="2026-07-02T10:00:00Z",
        recorded_at="2026-07-02T10:00:00Z",
        confirmed=True,
    )
    corrupt = (*events, duplicate)
    degraded = _project(_inputs(*corrupt))
    assert degraded.projection.partial
    assert (
        next(item for item in degraded.inputs if item.name == "confirmed_events").state == "invalid"
    )
    assert [item.kind for item in degraded.projection.items] == ["system_blocker"]
    ledger = tmp_path / "data/runtime/outcome-learning-v2.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("\n".join(json.dumps(item.to_dict()) for item in corrupt), encoding="utf-8")
    before = ledger.read_bytes()
    local = require_focus_result(create_focus_service(tmp_path).project())
    assert next(item for item in local.inputs if item.name == "confirmed_events").state == "invalid"
    assert ledger.read_bytes() == before


def test_distinct_timeline_pairs_survive_focus_deduplication() -> None:
    issues = validate_timeline(
        tuple(
            TimelineItem(f"record-{index}", "2025-01", "2025-12", "exclusive", True)
            for index in range(3)
        )
    )
    assert len(issues) == 3
    inputs = replace(_inputs(), timeline_issues=issues)
    full = _project(inputs)
    assert full.projection.total_items == 3
    assert {item.reason for item in full.projection.items} == {issue.message for issue in issues}
    assert len({item.opportunity_id for item in full.projection.items}) == 3
    limited = _project(inputs, limit=2)
    assert limited.projection.returned_items == 2 and limited.projection.omitted_items == 1
    assert (
        _project(replace(inputs, timeline_issues=tuple(reversed(issues)))).to_dict()
        == full.to_dict()
    )


@pytest.mark.parametrize(
    "decision,current,expected",
    [
        (None, False, "decision_review"),
        ("hold", True, None),
        ("skip", True, None),
        ("pursue", True, "preparation_choice"),
        ("conditional", True, "decision_conditions"),
        ("pursue", False, "decision_stale"),
    ],
)
def test_supplied_decisions_never_authorize_application_or_external_events(
    decision, current, expected
) -> None:
    dossier = SuppliedRoleResult(
        "example_role",
        "a" * 64,
        "b" * 64,
        {
            "company": "Fictional",
            "title": "Example",
            "role_evaluation": {"recommendation": "conditional"},
        },
    )
    state = SuppliedOpportunityState(dossier, {"decision": decision} if decision else None, current)
    result = _project(_inputs(opportunities=(state,)))
    assert [item.kind for item in result.projection.items] == ([expected] if expected else [])
    assert not result.projection.external_actions and not result.projection.pipeline_changed
    if expected:
        assert result.projection.items[0].requires_user_confirmation
    assert not _project(
        _inputs(_event("example_role", stage="offer", outcome="declined"), opportunities=(state,))
    ).projection.items
    skipped = replace(
        state,
        dossier=replace(
            dossier, dossier={**dossier.dossier, "role_evaluation": {"recommendation": "skip"}}
        ),
        latest_decision=None,
    )
    assert not _project(_inputs(opportunities=(skipped,))).projection.items


@pytest.mark.parametrize("limit", [True, 0, 101, "8"])
def test_invalid_limits_preserve_state(tmp_path: Path, limit) -> None:
    result = create_focus_service(tmp_path / "absent").project(limit=limit)
    assert isinstance(result, FocusOperationError)
    assert not (tmp_path / "absent").exists()


def test_links_are_not_followed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit directory symlinks")
    result = require_focus_result(create_focus_service(link).project())
    assert result.projection.partial
    assert next(item for item in result.inputs if item.name == "instance").state == "unavailable"
    assert not list(target.iterdir())


def test_search_inbox_preserves_decision_and_source_boundaries() -> None:
    base = {
        "company": "Example Systems",
        "title": "Director Customer Operations",
        "source_id": "example-route",
        "url": "https://jobs.example.test/requisition-1",
        "total": 85,
        "recommendation": "review_queue",
        "content_hash": "b" * 64,
        "scored_at": "2026-09-03T11:00:00+00:00",
        "progression": {
            "verdict": "conditional_progression",
            "evidence": ["Posting indicates broader commercial decision rights."],
            "unknowns": ["total_economics"],
        },
        "requirement_evidence": [
            {
                "requirement_level": "mandatory",
                "classification": "genuine_gap",
                "requirement_text": "A specialist qualification",
            },
            {
                "requirement_level": "preferred",
                "classification": "transferable_evidence",
                "requirement_text": "Enterprise customer operations",
            },
        ],
    }
    prior = {
        "job_key": "example:requisition-1",
        "decision": "reject",
        "reason": "Earlier scope was too narrow.",
        "content_hash": "a" * 64,
        "recorded_at": "2026-09-03T10:00:00+00:00",
    }
    route = {
        "example-route": {
            "enabled": False,
            "lifecycle": "quarantined",
            "latest_observation": {
                "status": "error",
                "observed_at": "2026-09-03T09:00:00+00:00",
                "jobs_seen": None,
            },
            "last_success_at": "2026-09-02T09:00:00+00:00",
            "employer_coverage_status": "unmonitored",
        }
    }
    jobs = [
        {**base, "job_key": "example:requisition-1"},
        {
            **base,
            "job_key": "example:requisition-2",
            "url": "https://jobs.example.test/requisition-2",
        },
    ]
    before = (repr(jobs), repr(route), repr(prior))

    projection = build_focus_projection(
        pipeline=[],
        jobs=jobs,
        source_coverage=route,
        feedback=[prior],
        as_of=NOW.date(),
        generated_at=NOW,
    )

    assert {item.opportunity_id for item in projection.items} == {
        "example:requisition-1",
        "example:requisition-2",
    }
    changed = next(item for item in projection.items if item.opportunity_id.endswith("-1"))
    assert "latest error" in changed.reason
    assert "last success" in changed.reason
    assert "target employer coverage unmonitored" in changed.reason
    assert "W4 current-role comparison unavailable" in changed.reason
    assert (
        "Candidate evidence: transferable_evidence for Enterprise customer operations"
        in changed.reason
    )
    assert "unsupported mandatory requirement: A specialist qualification" in changed.reason
    assert "Prior decision: reject (Earlier scope was too narrow.)" in changed.reason
    assert "material mandate change unverified" in changed.reason
    assert "Next human action:" in changed.reason
    assert projection.read_only and not projection.external_actions
    assert not projection.pipeline_changed and not projection.scoring_changed
    assert (repr(jobs), repr(route), repr(prior)) == before

    unchanged = build_focus_projection(
        pipeline=[],
        jobs=[
            {
                **base,
                "job_key": "example:requisition-1",
                "content_hash": "a" * 64,
                "scored_at": "2026-09-03T09:00:00+00:00",
            }
        ],
        feedback=[{**prior, "score": 85, "recommendation": "review_queue"}],
        as_of=NOW.date(),
        generated_at=NOW,
    )
    assert all(item.kind != "search_review" for item in unchanged.items)

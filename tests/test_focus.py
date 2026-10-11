from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import pytest

from career_os.focus import (
    FOCUS_ITEM_STRING_LIMITS,
    FocusItem,
    build_focus_projection,
    render_focus_markdown,
)


def _pipeline(**overrides):
    value = {
        "opportunity_id": "opp-example",
        "company": "Example Systems",
        "role": "Director Customer Operations",
        "stage": "review",
        "status": "Review",
        "next_action": "Review the evidence and decide.",
        "due_date": "2026-08-18",
        "job_url": "https://jobs.example.test/roles/1",
    }
    value.update(overrides)
    return value


def _generated() -> datetime:
    return datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def test_focus_schema_matches_runtime_contract() -> None:
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "focus-snapshot.schema.json").read_text(
            encoding="utf-8"
        )
    )
    projection = build_focus_projection(
        pipeline=[_pipeline()],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    ).to_dict()
    assert schema["additionalProperties"] is False
    assert set(projection) == set(schema["required"])
    assert set(projection["items"][0]) == set(schema["$defs"]["item"]["required"])
    item_schema = schema["$defs"]["item"]["properties"]
    assert {
        "kind": item_schema["kind"]["maxLength"],
        "opportunity_id": item_schema["opportunity_id"]["maxLength"],
        "company": item_schema["company"]["maxLength"],
        "role": item_schema["role"]["maxLength"],
        "stage": item_schema["stage"]["maxLength"],
        "due_at": item_schema["due_at"]["oneOf"][0]["maxLength"],
        "reason": item_schema["reason"]["maxLength"],
        "source": item_schema["source"]["maxLength"],
        "action_route": item_schema["action_route"]["maxLength"],
        "identity_status": item_schema["identity_status"]["maxLength"],
    } == FOCUS_ITEM_STRING_LIMITS


def test_focus_is_deterministic_and_never_infers_submission_from_stage_or_files() -> None:
    pipeline = [_pipeline(stage="applied", status="Applied")]
    application = {
        "opportunity_id": "opp-example",
        "company": "Example Systems",
        "role": "Director Customer Operations",
        "identity_status": "stable_exact",
        "local_readiness": "ready locally",
        "pipeline_stage": "applied",
    }

    first = build_focus_projection(
        pipeline=pipeline,
        application_states=[application],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    second = build_focus_projection(
        pipeline=pipeline,
        application_states=[application],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    assert first.to_dict() == second.to_dict()
    assert first.items[0].kind == "lifecycle_confirmation"
    assert first.items[0].blocked is True
    assert "no confirmed application-submitted event" in first.items[0].reason
    assert all(item.kind != "submission_confirmed" for item in first.items)
    assert first.read_only is True
    assert first.external_actions is False
    assert first.pipeline_changed is False
    assert first.scoring_changed is False


def test_focus_shares_explicit_action_intent_without_changing_lifecycle_stage() -> None:
    quiet = build_focus_projection(
        pipeline=[
            _pipeline(
                stage="preparing",
                action_intent="no_further_pursuit",
                due_date="2026-08-18",
            )
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    waiting = build_focus_projection(
        pipeline=[
            _pipeline(
                action_intent="wait",
                due_date="2026-08-17",
                next_event_at="2026-08-20T08:00:00+00:00",
            )
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    review_due = build_focus_projection(
        pipeline=[
            _pipeline(
                action_intent="wait",
                due_date="2026-08-20",
                next_event_at="2026-08-17T08:00:00+00:00",
            )
        ],
        as_of=date(2026, 8, 20),
        generated_at=_generated(),
    )

    assert quiet.items == ()
    assert waiting.items == ()
    assert [item.kind for item in review_due.items] == ["pipeline_action"]
    assert review_due.items[0].stage == "review"


def test_confirmed_interview_projects_prep_then_debrief_without_inference() -> None:
    pipeline = [_pipeline(stage="interviewing", next_action="Prepare for the interview.")]
    scheduled = {
        "opportunity_id": "opp-example",
        "kind": "interview_scheduled",
        "occurred_at": "2026-08-18T08:00:00+00:00",
    }
    workspace = {
        "opportunity_id": "opp-example",
        "valid": True,
        "round": "Hiring manager",
    }

    prep = build_focus_projection(
        pipeline=pipeline,
        career_events=[scheduled],
        interview_states=[workspace],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    prep_item = next(item for item in prep.items if item.kind == "interview_prep")
    assert prep_item.blocked is False

    completed = {**scheduled, "kind": "interview_completed"}
    debrief = build_focus_projection(
        pipeline=pipeline,
        career_events=[completed],
        as_of=date(2026, 8, 19),
        generated_at=_generated(),
    )
    debrief_item = next(item for item in debrief.items if item.kind == "interview_debrief")
    assert "user-confirmed debrief" in debrief_item.reason

    recorded = build_focus_projection(
        pipeline=pipeline,
        career_events=[completed],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "interview_debrief_recorded",
            }
        ],
        as_of=date(2026, 8, 19),
        generated_at=_generated(),
    )
    assert all(item.kind != "interview_debrief" for item in recorded.items)


def test_interview_focus_uses_the_latest_round_and_matching_debrief_stage() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline(stage="final_interview")],
        career_events=[
            {
                "opportunity_id": "opp-example",
                "kind": "interview_completed",
                "occurred_at": "2026-08-16T10:00:00+00:00",
            },
            {
                "opportunity_id": "opp-example",
                "kind": "final_interview_scheduled",
                "occurred_at": "2026-08-18T10:00:00+00:00",
            },
        ],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "interview_debrief_recorded",
                "occurred_at": "2026-08-17T12:00:00+00:00",
                "payload": {"interview_stage": "hiring_manager"},
            }
        ],
        interview_states=[{"opportunity_id": "opp-example", "valid": True}],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    assert any(item.kind == "interview_prep" for item in projection.items)
    assert all(item.kind != "interview_debrief" for item in projection.items)
    assert all(item.kind != "lifecycle_confirmation" for item in projection.items)

    mismatch = build_focus_projection(
        pipeline=[_pipeline(stage="final_interview")],
        career_events=[
            {
                "opportunity_id": "opp-example",
                "kind": "final_interview_completed",
                "occurred_at": "2026-08-18T10:00:00+00:00",
            }
        ],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "interview_debrief_recorded",
                "occurred_at": "2026-08-18T11:00:00+00:00",
                "payload": {"interview_stage": "hiring_manager"},
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert any(item.kind == "interview_debrief" for item in mismatch.items)

    matched = build_focus_projection(
        pipeline=[_pipeline(stage="final_interview")],
        career_events=[
            {
                "opportunity_id": "opp-example",
                "kind": "final_interview_completed",
                "occurred_at": "2026-08-18T10:00:00+00:00",
            }
        ],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "interview_debrief_recorded",
                "occurred_at": "2026-08-18T11:00:00+00:00",
                "payload": {"interview_stage": "final"},
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert all(item.kind != "interview_debrief" for item in matched.items)


def test_final_interview_requires_final_round_evidence() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline(stage="final_interview")],
        career_events=[
            {
                "opportunity_id": "opp-example",
                "kind": "interview_completed",
                "occurred_at": "2026-08-17T10:00:00+00:00",
            }
        ],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "interview_debrief_recorded",
                "occurred_at": "2026-08-17T11:00:00+00:00",
                "payload": {"interview_stage": "hiring_manager"},
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    assert any(item.kind == "lifecycle_confirmation" for item in projection.items)
    assert all(item.kind != "interview_prep" for item in projection.items)


def test_interview_prep_requires_matching_workspace_round_with_legacy_fallback() -> None:
    kwargs = {
        "pipeline": [_pipeline(stage="final_interview")],
        "career_events": [
            {
                "opportunity_id": "opp-example",
                "kind": "final_interview_scheduled",
                "occurred_at": "2026-08-18T10:00:00+00:00",
            }
        ],
        "as_of": date(2026, 8, 18),
        "generated_at": _generated(),
    }

    mismatched = build_focus_projection(
        **kwargs,
        interview_states=[
            {"opportunity_id": "opp-example", "valid": True, "round": "Hiring manager"}
        ],
    )
    mismatched_item = next(item for item in mismatched.items if item.kind == "interview_prep")
    assert mismatched_item.blocked is True

    matching = build_focus_projection(
        **kwargs,
        interview_states=[
            {"opportunity_id": "opp-example", "valid": True, "round": "Final interview"}
        ],
    )
    matching_item = next(item for item in matching.items if item.kind == "interview_prep")
    assert matching_item.blocked is False

    presales_final = build_focus_projection(
        **kwargs,
        interview_states=[
            {"opportunity_id": "opp-example", "valid": True, "round": "Pre-sales final interview"}
        ],
    )
    presales_final_item = next(
        item for item in presales_final.items if item.kind == "interview_prep"
    )
    assert presales_final_item.blocked is False

    for round_name in (
        "Non-final interview",
        "Semi-final interview",
        "Pre-final interview",
        "Not final interview",
        "Not yet final interview",
        "Not quite final interview",
        "Not the final interview",
        "Not yet the final interview",
    ):
        explicitly_nonfinal = build_focus_projection(
            **kwargs,
            interview_states=[
                {"opportunity_id": "opp-example", "valid": True, "round": round_name}
            ],
        )
        prep_item = next(
            item for item in explicitly_nonfinal.items if item.kind == "interview_prep"
        )
        assert prep_item.blocked is True

    legacy = build_focus_projection(
        **kwargs,
        interview_states=[
            {
                "opportunity_id": "opp-example",
                "valid": True,
                "round": "legacy-round-1",
                "workspace": "C:/applications/example/interviews/legacy-round-1",
            }
        ],
    )
    legacy_item = next(item for item in legacy.items if item.kind == "interview_prep")
    assert legacy_item.blocked is True

    legacy_nonfinal = build_focus_projection(
        pipeline=[_pipeline(stage="interviewing")],
        career_events=[
            {
                "opportunity_id": "opp-example",
                "kind": "interview_scheduled",
                "occurred_at": "2026-08-18T10:00:00+00:00",
            }
        ],
        interview_states=[
            {
                "opportunity_id": "opp-example",
                "valid": True,
                "round": "legacy-round-1",
                "workspace": "C:/applications/example/interviews/legacy-round-1",
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    legacy_nonfinal_item = next(
        item for item in legacy_nonfinal.items if item.kind == "interview_prep"
    )
    assert legacy_nonfinal_item.blocked is False


def test_interview_prep_requires_workspace_bound_to_latest_nonfinal_cycle() -> None:
    kwargs = {
        "pipeline": [_pipeline(stage="interviewing")],
        "career_events": [
            {
                "opportunity_id": "opp-example",
                "kind": "interview_completed",
                "occurred_at": "2026-08-17T10:00:00+00:00",
            },
            {
                "opportunity_id": "opp-example",
                "kind": "interview_scheduled",
                "occurred_at": "2026-08-18T10:00:00+00:00",
            },
        ],
        "as_of": date(2026, 8, 18),
        "generated_at": _generated(),
    }
    old_workspace = {
        "opportunity_id": "opp-example",
        "valid": True,
        "round": "Hiring manager",
        "created_at": "2026-08-16T11:00:00+00:00",
    }

    stale = build_focus_projection(**kwargs, interview_states=[old_workspace])
    stale_item = next(item for item in stale.items if item.kind == "interview_prep")
    assert stale_item.blocked is True

    current = build_focus_projection(
        **kwargs,
        interview_states=[
            old_workspace,
            {
                "opportunity_id": "opp-example",
                "valid": True,
                "round": "Panel",
                "created_at": "2026-08-18T10:00:00+00:00",
            },
        ],
    )
    current_item = next(item for item in current.items if item.kind == "interview_prep")
    assert current_item.blocked is False


def test_interview_prep_treats_workspace_warnings_as_not_ready() -> None:
    kwargs = {
        "pipeline": [_pipeline(stage="interviewing")],
        "career_events": [
            {
                "opportunity_id": "opp-example",
                "kind": "interview_scheduled",
                "occurred_at": "2026-08-18T10:00:00+00:00",
            }
        ],
        "as_of": date(2026, 8, 18),
        "generated_at": _generated(),
    }
    warned_workspace = {
        "opportunity_id": "opp-example",
        "valid": True,
        "round": "Hiring manager",
        "warnings": ["No evidence IDs are selected"],
    }

    warned = build_focus_projection(**kwargs, interview_states=[warned_workspace])
    warned_item = next(item for item in warned.items if item.kind == "interview_prep")
    assert warned_item.blocked is True

    ready = build_focus_projection(
        **kwargs,
        interview_states=[{**warned_workspace, "warnings": []}],
    )
    ready_item = next(item for item in ready.items if item.kind == "interview_prep")
    assert ready_item.blocked is False


def test_v2_advanced_interview_outcome_is_completed_round_evidence() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline(stage="interviewing")],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "outcome_recorded",
                "occurred_at": "2026-08-17T10:00:00+00:00",
                "payload": {"stage": "interview", "outcome": "advanced"},
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    assert any(item.kind == "interview_debrief" for item in projection.items)
    assert all(item.kind != "lifecycle_confirmation" for item in projection.items)


def test_advanced_outcome_does_not_reprompt_matching_prior_debrief() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline(stage="interviewing")],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "interview_debrief_recorded",
                "occurred_at": "2026-08-18T09:00:00+00:00",
                "payload": {"interview_stage": "hiring_manager"},
            },
            {
                "opportunity_id": "opp-example",
                "event_type": "outcome_recorded",
                "occurred_at": "2026-08-18T10:00:00+00:00",
                "payload": {"stage": "interview", "outcome": "advanced"},
            },
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    assert all(item.kind != "interview_debrief" for item in projection.items)
    assert all(item.kind != "lifecycle_confirmation" for item in projection.items)


def test_advanced_outcome_does_not_reuse_debrief_from_an_earlier_interview_cycle() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline(stage="interviewing")],
        career_events=[
            {
                "opportunity_id": "opp-example",
                "kind": "interview_scheduled",
                "occurred_at": "2026-08-18T10:00:00+00:00",
            }
        ],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "interview_debrief_recorded",
                "occurred_at": "2026-08-18T09:00:00+00:00",
                "payload": {"interview_stage": "hiring_manager"},
            },
            {
                "opportunity_id": "opp-example",
                "event_type": "outcome_recorded",
                "occurred_at": "2026-08-18T11:00:00+00:00",
                "payload": {"stage": "interview", "outcome": "advanced"},
            },
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    assert any(item.kind == "interview_debrief" for item in projection.items)


def test_invalid_feedback_timestamp_does_not_suppress_search_review() -> None:
    job = {
        "job_key": "job-invalid-feedback",
        "company": "Example Systems",
        "title": "Director Customer Operations",
        "total": 82,
        "recommendation": "review_queue",
    }
    for feedback in (
        {"job_key": job["job_key"], "recorded_at": "not-a-timestamp"},
        {"job_key": job["job_key"]},
    ):
        projection = build_focus_projection(
            pipeline=[],
            jobs=[job],
            feedback=[feedback],
            as_of=date(2026, 8, 18),
            generated_at=_generated(),
        )
        assert any(
            item.kind == "search_review" and item.opportunity_id == job["job_key"]
            for item in projection.items
        )


def test_terminal_lifecycle_conflict_blocks_stale_actions_but_keeps_debrief() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline(stage="interviewing")],
        career_events=[
            {
                "opportunity_id": "opp-example",
                "kind": "interview_completed",
                "occurred_at": "2026-08-18T10:00:00+00:00",
            },
            {
                "opportunity_id": "opp-example",
                "kind": "rejected",
                "occurred_at": "2026-08-19T10:00:00+00:00",
            },
        ],
        as_of=date(2026, 8, 19),
        generated_at=_generated(),
    )

    assert any(item.kind == "lifecycle_inconsistency" and item.blocked for item in projection.items)
    assert any(item.kind == "interview_debrief" for item in projection.items)
    assert all(item.kind not in {"pipeline_action", "interview_prep"} for item in projection.items)

    review_projection = build_focus_projection(
        pipeline=[_pipeline(stage="review", next_action="Review this role.")],
        jobs=[
            {
                "job_key": "opp-example",
                "company": "Example Systems",
                "title": "Director Customer Operations",
                "total": 82,
                "recommendation": "review_queue",
            }
        ],
        career_events=[
            {
                "opportunity_id": "opp-example",
                "kind": "role_closed",
                "occurred_at": "2026-08-19T10:00:00+00:00",
            }
        ],
        as_of=date(2026, 8, 19),
        generated_at=_generated(),
    )
    assert all(
        item.kind not in {"pipeline_action", "search_review"} for item in review_projection.items
    )

    v2_terminal = build_focus_projection(
        pipeline=[_pipeline(stage="offer", next_action="Review offer terms.")],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "outcome_recorded",
                "occurred_at": "2026-08-19T10:00:00+00:00",
                "payload": {"stage": "offer", "outcome": "declined"},
            }
        ],
        as_of=date(2026, 8, 19),
        generated_at=_generated(),
    )
    assert any(item.kind == "lifecycle_inconsistency" for item in v2_terminal.items)
    assert all(item.kind != "pipeline_action" for item in v2_terminal.items)

    later_active_event = build_focus_projection(
        pipeline=[_pipeline(stage="interviewing")],
        career_events=[
            {
                "opportunity_id": "opp-example",
                "kind": "rejected",
                "occurred_at": "2026-08-18T09:00:00+00:00",
            },
            {
                "opportunity_id": "opp-example",
                "kind": "interview_scheduled",
                "occurred_at": "2026-08-18T09:00:00+00:00",
            },
        ],
        as_of=date(2026, 8, 19),
        generated_at=_generated(),
    )
    assert all(item.kind != "lifecycle_inconsistency" for item in later_active_event.items)


def test_pipeline_unavailable_blocks_search_review_and_tracked_state() -> None:
    projection = build_focus_projection(
        pipeline=[],
        pipeline_available=False,
        jobs=[
            {
                "job_key": "job-untracked",
                "company": "Example Systems",
                "title": "Director Customer Operations",
                "total": 82,
                "recommendation": "review_queue",
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    blocker = next(item for item in projection.items if item.opportunity_id == "system:pipeline")
    assert blocker.kind == "system_blocker"
    assert projection.partial is True
    assert all(item.kind != "search_review" for item in projection.items)


def test_offer_stage_requires_exact_offer_confirmation() -> None:
    missing = build_focus_projection(
        pipeline=[_pipeline(stage="offer")],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert any(item.kind == "lifecycle_confirmation" for item in missing.items)

    invalid_v2 = build_focus_projection(
        pipeline=[_pipeline(stage="offer")],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "outcome_recorded",
                "occurred_at": "2026-08-18T10:00:00+00:00",
                "payload": {"stage": "offer", "outcome": "completed"},
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert any(item.kind == "lifecycle_confirmation" for item in invalid_v2.items)

    legacy = build_focus_projection(
        pipeline=[_pipeline(stage="offer")],
        career_events=[
            {
                "opportunity_id": "opp-example",
                "kind": "offer_received",
                "occurred_at": "2026-08-18T10:00:00+00:00",
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert all(item.kind != "lifecycle_confirmation" for item in legacy.items)

    v2 = build_focus_projection(
        pipeline=[_pipeline(stage="offer")],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "outcome_recorded",
                "occurred_at": "2026-08-18T10:00:00+00:00",
                "payload": {"stage": "offer", "outcome": "offer_received"},
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert all(item.kind != "lifecycle_confirmation" for item in v2.items)


def test_outcome_learning_v2_is_lifecycle_confirmation_authority() -> None:
    submission = build_focus_projection(
        pipeline=[_pipeline(stage="applied", status="Applied")],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "submission_confirmed",
                "occurred_at": "2026-08-18T08:00:00+00:00",
                "payload": {},
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert all(item.kind != "lifecycle_confirmation" for item in submission.items)
    assert all(item.kind != "submission_confirmation" for item in submission.items)

    debrief = build_focus_projection(
        pipeline=[_pipeline(stage="interviewing")],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "interview_debrief_recorded",
                "occurred_at": "2026-08-18T09:00:00+00:00",
                "payload": {"interview_stage": "hiring_manager"},
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert all(
        item.kind not in {"lifecycle_confirmation", "interview_debrief"} for item in debrief.items
    )

    interview_outcome = build_focus_projection(
        pipeline=[_pipeline(stage="interviewing")],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "outcome_recorded",
                "occurred_at": "2026-08-18T10:00:00+00:00",
                "payload": {"stage": "interview", "outcome": "completed"},
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert any(item.kind == "interview_debrief" for item in interview_outcome.items)
    assert all(item.kind != "lifecycle_confirmation" for item in interview_outcome.items)


def test_outcome_learning_authority_respects_as_of_cutoff() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline(stage="applied", status="Applied")],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "submission_confirmed",
                "occurred_at": "2026-08-19T08:00:00+00:00",
                "payload": {},
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert any(item.kind == "lifecycle_confirmation" for item in projection.items)


def test_unavailable_authority_is_explicit_and_never_interpreted_as_absence() -> None:
    for unavailable in ("application_states_available", "interview_states_available"):
        partial = build_focus_projection(
            pipeline=(), **{unavailable: False}, as_of=date(2026, 8, 18), generated_at=_generated()
        )
        assert partial.partial is True
        assert not partial.items
    projection = build_focus_projection(
        pipeline=[_pipeline(stage="applied", status="Applied")],
        career_events_available=False,
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    blocker = next(
        item for item in projection.items if item.opportunity_id == "system:career_events"
    )
    assert blocker.kind == "system_blocker"
    assert projection.partial is True
    assert all(item.kind != "lifecycle_confirmation" for item in projection.items)


def test_unavailable_lifecycle_authority_suppresses_active_pipeline_actions() -> None:
    for unavailable, blocker_source in (
        ("career_events", "career_events"),
        ("outcome_learning", "outcome_learning_v2"),
    ):
        projection = build_focus_projection(
            pipeline=[_pipeline(stage="applied", status="Applied")],
            career_events_available=unavailable != "career_events",
            outcome_learning_available=unavailable != "outcome_learning",
            as_of=date(2026, 8, 18),
            generated_at=_generated(),
        )

        assert any(
            item.kind == "system_blocker" and item.source == blocker_source
            for item in projection.items
        )
        assert all(item.kind != "pipeline_action" for item in projection.items)


def test_unavailable_lifecycle_authority_suppresses_all_action_like_queues() -> None:
    jobs = [
        {
            "job_key": "job-tracked",
            "company": "Example Systems",
            "title": "Director Customer Operations",
            "url": "https://jobs.example.test/roles/1",
            "total": 82,
            "recommendation": "review_queue",
        },
        {
            "job_key": "job-untracked",
            "company": "Second Systems",
            "title": "VP Customer Operations",
            "url": "https://jobs.example.test/roles/2",
            "total": 88,
            "recommendation": "priority_alert",
        },
    ]
    communication = {
        "due": [
            {
                "opportunity_id": "opp-example",
                "due_at": "2026-08-18T10:00:00+00:00",
                "queue": "due",
            }
        ]
    }

    for unavailable, blocker_source in (
        ("career_events", "career_events"),
        ("outcome_learning", "outcome_learning_v2"),
    ):
        projection = build_focus_projection(
            pipeline=[_pipeline()],
            jobs=jobs,
            communication=communication,
            career_events_available=unavailable != "career_events",
            outcome_learning_available=unavailable != "outcome_learning",
            as_of=date(2026, 8, 18),
            generated_at=_generated(),
        )

        assert any(
            item.kind == "system_blocker" and item.source == blocker_source
            for item in projection.items
        )
        assert all(
            item.kind not in {"pipeline_action", "search_review", "follow_up_review"}
            for item in projection.items
        )


def test_communication_audit_errors_suppress_due_follow_up_actions() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline()],
        communication={
            "errors": ["Approved plan recipient is missing."],
            "due": [
                {
                    "opportunity_id": "opp-example",
                    "due_at": "2026-08-18T10:00:00+00:00",
                    "queue": "due",
                }
            ],
        },
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    assert any(item.opportunity_id == "system:communication" for item in projection.items)
    assert all(item.kind != "follow_up_review" for item in projection.items)


def test_unavailable_legacy_outcomes_block_search_review_conclusions() -> None:
    projection = build_focus_projection(
        pipeline=[],
        jobs=[
            {
                "job_key": "job-review",
                "company": "Example Systems",
                "title": "Director Customer Operations",
                "total": 82,
                "recommendation": "review_queue",
            }
        ],
        outcomes_available=False,
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    blocker = next(item for item in projection.items if item.opportunity_id == "system:outcomes")
    assert blocker.kind == "system_blocker"
    assert "suppressed/closed job visibility" in blocker.reason
    assert projection.partial is True
    assert all(item.kind != "search_review" for item in projection.items)


def test_interview_pipeline_stage_without_confirmed_event_is_a_blocking_confirmation() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline(stage="final_interview")],
        interview_states=[{"opportunity_id": "opp-example", "valid": True}],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert projection.items[0].kind == "lifecycle_confirmation"
    assert projection.items[0].blocked is True
    assert all(item.kind != "interview_prep" for item in projection.items)


def test_focus_as_of_ignores_later_confirmed_events() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline(stage="interviewing")],
        career_events=[
            {
                "opportunity_id": "opp-example",
                "kind": "interview_scheduled",
                "occurred_at": "2026-08-19T08:00:00+00:00",
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert any(item.kind == "lifecycle_confirmation" for item in projection.items)
    assert all(item.kind != "interview_prep" for item in projection.items)


def test_focus_as_of_includes_final_fractional_second() -> None:
    projection = build_focus_projection(
        pipeline=[],
        jobs=[
            {
                "job_key": "job-final-fraction",
                "company": "Example Systems",
                "title": "Director Customer Operations",
                "total": 82,
                "recommendation": "review_queue",
                "scored_at": "2026-08-18T23:59:59.500000+00:00",
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    assert any(
        item.kind == "search_review" and item.opportunity_id == "job-final-fraction"
        for item in projection.items
    )


def test_focus_as_of_requires_lifecycle_authority_timestamp() -> None:
    career_projection = build_focus_projection(
        pipeline=[_pipeline(stage="interviewing")],
        career_events=[
            {
                "opportunity_id": "opp-example",
                "kind": "interview_scheduled",
                "occurred_at": "2026-08-17T08:00:00+00:00",
                "confirmed_at": "2026-08-19T08:00:00+00:00",
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    learning_projection = build_focus_projection(
        pipeline=[_pipeline(stage="interviewing")],
        outcome_learning_events=[
            {
                "opportunity_id": "opp-example",
                "event_type": "outcome_recorded",
                "occurred_at": "2026-08-17T08:00:00+00:00",
                "recorded_at": "2026-08-19T08:00:00+00:00",
                "payload": {"stage": "interview", "outcome": "scheduled"},
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    assert any(item.kind == "lifecycle_confirmation" for item in career_projection.items)
    assert all(item.kind != "interview_prep" for item in career_projection.items)
    assert any(item.kind == "lifecycle_confirmation" for item in learning_projection.items)
    assert all(item.kind != "interview_prep" for item in learning_projection.items)


def test_focus_as_of_excludes_malformed_nonempty_lifecycle_timestamps() -> None:
    for event_source, event in (
        (
            "career_events",
            {
                "opportunity_id": "opp-example",
                "kind": "interview_scheduled",
                "occurred_at": "not-a-timestamp",
                "confirmed_at": "2026-08-17T08:00:00+00:00",
            },
        ),
        (
            "outcome_learning_events",
            {
                "opportunity_id": "opp-example",
                "event_type": "outcome_recorded",
                "occurred_at": "2026-08-17T08:00:00+00:00",
                "recorded_at": "not-a-timestamp",
                "payload": {"stage": "interview", "outcome": "scheduled"},
            },
        ),
    ):
        projection = build_focus_projection(
            pipeline=[_pipeline(stage="interviewing")],
            **{event_source: [event]},
            as_of=date(2026, 8, 18),
            generated_at=_generated(),
        )

        assert any(item.kind == "lifecycle_confirmation" for item in projection.items)
        assert all(item.kind != "interview_prep" for item in projection.items)


def test_lifecycle_confirmation_blocks_ordinary_pipeline_actions() -> None:
    for stage in ("applied", "interviewing", "offer"):
        projection = build_focus_projection(
            pipeline=[_pipeline(stage=stage)],
            as_of=date(2026, 8, 18),
            generated_at=_generated(),
        )

        blocker = next(item for item in projection.items if item.kind == "lifecycle_confirmation")
        assert blocker.blocked is True
        assert all(item.kind != "pipeline_action" for item in projection.items)


def test_blocked_communication_due_item_propagates_stop_reason() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline()],
        communication={
            "due": [
                {
                    "opportunity_id": "opp-example",
                    "due_at": "2026-08-18T10:00:00+00:00",
                    "queue": "blocked",
                    "stop_reason": "Organization-level communication budget is exhausted",
                }
            ]
        },
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    follow_up = next(item for item in projection.items if item.kind == "follow_up_review")
    assert follow_up.blocked is True
    assert follow_up.urgency == "blocked"
    assert "Organization-level communication budget is exhausted" in follow_up.reason


def test_blocked_communication_due_item_fails_closed_without_stop_reason() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline()],
        communication={
            "due": [
                {
                    "opportunity_id": "opp-example",
                    "due_at": "2026-08-18T10:00:00+00:00",
                    "queue": "blocked",
                    "stop_reason": "",
                }
            ]
        },
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    follow_up = next(item for item in projection.items if item.kind == "follow_up_review")
    assert follow_up.blocked is True
    assert follow_up.urgency == "blocked"
    assert "policy or budget" in follow_up.reason


def test_identity_conflict_is_visible_and_communication_requires_exact_identity() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline()],
        application_states=[
            {
                "opportunity_id": "",
                "company": "Example Systems",
                "role": "Director Customer Operations",
                "identity_status": "legacy_ambiguous",
                "pipeline_stage": "review",
            }
        ],
        communication={
            "due": [
                {
                    "opportunity_id": "Example Systems",
                    "purpose": "Unsafe company-name fallback",
                    "due_at": "2026-08-18",
                },
                {
                    "opportunity_id": "opp-example",
                    "purpose": "Exact follow-up review",
                    "due_at": "2026-08-18",
                },
            ]
        },
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    kinds = [item.kind for item in projection.items]
    assert kinds[0] == "identity_conflict"
    assert kinds.count("follow_up_review") == 1


def test_duplicate_stable_application_identity_never_selects_a_workspace() -> None:
    states = [
        {
            "opportunity_id": "opp-example",
            "company": "Example Systems",
            "role": "Director Customer Operations",
            "identity_status": "stable_exact",
            "pipeline_stage": "preparing",
            "local_readiness": readiness,
        }
        for readiness in ("ready locally", "not ready locally")
    ]
    projection = build_focus_projection(
        pipeline=[_pipeline(stage="preparing")],
        application_states=states,
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    conflict = next(item for item in projection.items if item.kind == "identity_conflict")
    readiness = next(item for item in projection.items if item.kind == "application_readiness")
    assert conflict.identity_status == "stable_ambiguous"
    assert readiness.blocked is True
    assert "not locally ready" in readiness.reason


def test_label_only_application_identity_is_blocked_and_not_joined() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline(stage="preparing")],
        application_states=[
            {
                "opportunity_id": "opp-example",
                "company": "Example Systems",
                "role": "Director Customer Operations",
                "identity_status": "legacy_unique",
                "pipeline_stage": "preparing",
                "local_readiness": "ready locally",
            }
        ],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    conflict = next(item for item in projection.items if item.kind == "identity_conflict")
    readiness = next(item for item in projection.items if item.kind == "application_readiness")
    assert "company/role labels only" in conflict.reason
    assert readiness.blocked is True
    assert "not locally ready" in readiness.reason


def test_scored_job_url_match_is_case_sensitive_and_exact() -> None:
    job = {
        "job_key": "job-exact",
        "company": "Example Systems",
        "title": "Director Customer Operations",
        "url": "https://jobs.example.test/roles/exact",
        "total": 80,
        "recommendation": "review_queue",
    }
    mismatched = build_focus_projection(
        pipeline=[_pipeline(job_url="https://jobs.example.test/Roles/Exact")],
        jobs=[job],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    mismatched_reviews = [item for item in mismatched.items if item.kind == "search_review"]
    assert [item.opportunity_id for item in mismatched_reviews] == ["job-exact"]

    matched = build_focus_projection(
        pipeline=[_pipeline(job_url=job["url"])],
        jobs=[job],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    matched_reviews = [item for item in matched.items if item.kind == "search_review"]
    assert [item.opportunity_id for item in matched_reviews] == ["opp-example"]


def test_feedback_suppresses_review_prompt_and_limit_reports_omissions() -> None:
    jobs = [
        {
            "job_key": f"job-{index}",
            "company": f"Company {index}",
            "title": "Director Customer Operations",
            "total": 90 - index,
            "recommendation": "review_queue",
        }
        for index in range(4)
    ]
    projection = build_focus_projection(
        pipeline=[],
        jobs=jobs,
        feedback=[{"job_key": "job-0", "recorded_at": "2026-08-18T10:00:00+00:00"}],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
        limit=2,
    )
    assert projection.total_items == 3
    assert projection.returned_items == 2
    assert projection.omitted_items == 1
    assert [item.opportunity_id for item in projection.items] == ["job-1", "job-2"]
    assert projection.items[0].action_route == "/opportunities?job=job-1#search-feedback"
    markdown = render_focus_markdown(projection)
    assert "Read-only projection" in markdown
    assert "1 additional material item(s) omitted" in markdown


def test_feedback_reopens_review_when_ranked_snapshot_changes() -> None:
    feedback = {
        "job_key": "job-changing",
        "company": "Example Systems",
        "title": "Director Customer Operations",
        "decision": "reject",
        "reason": "The initial scope was too narrow.",
        "score": 82,
        "recommendation": "review_queue",
        "recorded_at": "2026-08-18T10:00:00+00:00",
    }
    unchanged = {
        "job_key": "job-changing",
        "company": "Example Systems",
        "title": "Director Customer Operations",
        "total": 82,
        "recommendation": "review_queue",
        "content_hash": "a" * 64,
        "scored_at": "2026-08-18T09:00:00+00:00",
    }
    changed = {
        **unchanged,
        "content_hash": "b" * 64,
        "total": 85,
        "scored_at": "2026-08-18T11:00:00+00:00",
    }

    before_change = build_focus_projection(
        pipeline=[],
        jobs=[unchanged],
        feedback=[feedback],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    after_change = build_focus_projection(
        pipeline=[],
        jobs=[changed],
        feedback=[feedback],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    assert all(item.kind != "search_review" for item in before_change.items)
    assert any(
        item.kind == "search_review" and item.opportunity_id == "job-changing"
        for item in after_change.items
    )


def test_historical_focus_excludes_scores_created_after_the_cutoff() -> None:
    job = {
        "job_key": "job-future-score",
        "company": "Example Systems",
        "title": "Director Customer Operations",
        "total": 86,
        "recommendation": "review_queue",
        "scored_at": "2026-08-19T09:00:00+00:00",
    }

    historical = build_focus_projection(
        pipeline=[],
        jobs=[job],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    visible_after_score = build_focus_projection(
        pipeline=[],
        jobs=[job],
        as_of=date(2026, 8, 19),
        generated_at=_generated(),
    )

    assert all(item.kind != "search_review" for item in historical.items)
    assert any(item.opportunity_id == job["job_key"] for item in visible_after_score.items)


def test_all_focus_item_reasons_respect_focus_snapshot_limit() -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline(next_action="x" * 4_500)],
        diagnostics=[{"severity": "error", "message": "y" * 4_500, "source": "oversized"}],
        outcome_review={"review_due": True, "review_due_reasons": ["z" * 4_500]},
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )

    bounded = {
        item.kind: item
        for item in projection.items
        if item.kind in {"pipeline_action", "system_blocker", "outcome_review"}
    }
    assert set(bounded) == {"pipeline_action", "system_blocker", "outcome_review"}
    assert all(len(item.reason) == 4_000 for item in bounded.values())


def test_focus_item_enforces_every_schema_bounded_string() -> None:
    values = {
        field_name: "x" + "x" * maximum
        for field_name, maximum in FOCUS_ITEM_STRING_LIMITS.items()
        if field_name not in {"opportunity_id", "action_route"}
    }
    values["opportunity_id"] = "x" * FOCUS_ITEM_STRING_LIMITS["opportunity_id"]
    values["action_route"] = "/" + "x" * (FOCUS_ITEM_STRING_LIMITS["action_route"] - 1)
    item = FocusItem(
        **values,
        urgency="normal",
        requires_user_confirmation=False,
        blocked=False,
        priority=1,
    )

    assert {
        field_name: len(getattr(item, field_name)) for field_name in FOCUS_ITEM_STRING_LIMITS
    } == FOCUS_ITEM_STRING_LIMITS
    assert FocusItem(**{**item.to_dict(), "action_route": "/system"}).action_route == "/system"
    with pytest.raises(ValueError, match="action_route"):
        FocusItem(**{**item.to_dict(), "action_route": "/" + "x" * 1_000})


def test_focus_rejects_unrepresentable_opaque_identity_and_action_route() -> None:
    long_id = "é" * FOCUS_ITEM_STRING_LIMITS["opportunity_id"]
    with pytest.raises(ValueError, match="action_route"):
        build_focus_projection(
            pipeline=[_pipeline(opportunity_id=long_id)],
            as_of=date(2026, 8, 18),
            generated_at=_generated(),
        )
    with pytest.raises(ValueError, match="opportunity_id"):
        build_focus_projection(
            pipeline=[_pipeline(opportunity_id="x" * 201)],
            as_of=date(2026, 8, 18),
            generated_at=_generated(),
        )


@pytest.mark.parametrize("opportunity_id", ["x" * 200, "é" * 160])
def test_focus_route_keeps_exact_representable_identity_and_projection_state(
    opportunity_id: str,
) -> None:
    projection = build_focus_projection(
        pipeline=[_pipeline(opportunity_id=opportunity_id)],
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    item = next(item for item in projection.items if item.kind == "pipeline_action")

    assert item.opportunity_id == opportunity_id
    assert item.action_route == f"/opportunity?id={quote(opportunity_id)}"
    assert parse_qs(urlparse(item.action_route).query)["id"] == [opportunity_id]
    assert projection.generated_at == _generated().isoformat()
    assert projection.as_of == "2026-08-18"
    assert projection.read_only is True
    assert projection.pipeline_changed is False


def test_outcome_review_and_partial_state_are_explicit() -> None:
    projection = build_focus_projection(
        pipeline=[],
        diagnostics=[
            {"severity": "warning", "message": "Optional ledger malformed", "source": "ledger"}
        ],
        outcome_review={"review_due": True, "review_due_reasons": ["10 mature submissions"]},
        as_of=date(2026, 8, 18),
        generated_at=_generated(),
    )
    assert projection.partial is True
    assert projection.items[0].kind == "outcome_review"
    assert projection.items[0].requires_user_confirmation is True

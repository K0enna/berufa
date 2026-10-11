from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from career_os.authority_ports import ModelAccess
from career_os.product_runtime import ProductRuntime
from career_os.store import CareerStore, DatabaseRoleError
from career_os.synthetic_runtime import build_synthetic_runtime, run_smoke


def _source_id(runtime: ProductRuntime) -> str:
    return runtime.opportunities.list()[0].posting.source_id  # type: ignore[union-attr]


def test_runtime_construction_is_side_effect_free_and_instance_bound(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    first = build_synthetic_runtime(first_root, instance_tag="first")
    second = build_synthetic_runtime(second_root, instance_tag="second")

    assert not first_root.exists()
    assert not second_root.exists()
    assert first.paths.production_database != second.paths.production_database
    assert (
        first.opportunities.list()[0].opportunity_id
        != second.opportunities.list()[0].opportunity_id
    )

    with pytest.raises(ValueError, match="belongs to another instance"):
        ProductRuntime.compose(
            second_root,
            candidate_evidence=first.candidate_evidence,
            career_events=second.career_events,
            opportunities=second.opportunities,
            workspaces=second.workspaces,
            providers=second.providers,
            provider_policy=second.provider_policy,
            model_policy=second.model_policy,
            evaluator=second.evaluator,
            projections=second.projections,
            clock=second.clock,
        )


def test_runtime_opens_role_bound_stores_only_after_explicit_calls(tmp_path: Path) -> None:
    root = tmp_path / "participant"
    runtime = build_synthetic_runtime(root, instance_tag="participant")
    assert not root.exists()

    with runtime.open_production_store() as production:
        assert production.path == runtime.paths.production_database
    with runtime.open_shadow_store() as shadow:
        assert shadow.path == runtime.paths.shadow_database
    with pytest.raises(DatabaseRoleError):
        CareerStore(
            runtime.paths.production_database,
            read_only=True,
            expected_role="shadow",
            default_production_path=runtime.paths.production_database,
        )


def test_runtime_evaluates_exact_official_posting_and_projects_read_only_item(
    tmp_path: Path,
) -> None:
    runtime = build_synthetic_runtime(tmp_path / "participant", instance_tag="participant")

    evaluation = runtime.evaluate(_source_id(runtime))
    projection = runtime.project_decision(evaluation)

    assert evaluation.opportunity_id == evaluation.posting.job_key
    assert projection.read_only is True
    assert projection.external_actions is False
    assert projection.pipeline_changed is False
    assert projection.scoring_changed is False
    assert projection.items[0].requires_user_confirmation is True
    assert projection.items[0].action_route.startswith("/")
    with pytest.raises(ValueError, match="Unknown exact opportunity identity"):
        runtime.opportunities.resolve("Fictional Company / Similar Title")


def test_runtime_rejects_unverified_lead_as_official_authority(tmp_path: Path) -> None:
    runtime = build_synthetic_runtime(tmp_path / "participant", instance_tag="participant")
    source_id = _source_id(runtime)
    registry = runtime.providers
    source = registry.source(source_id)
    registry.sources[source_id] = replace(source, authority="lead")  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="unverified lead"):
        runtime.evaluate(source_id, purpose="official")


def test_runtime_rejects_posting_from_a_different_exact_source(tmp_path: Path) -> None:
    runtime = build_synthetic_runtime(tmp_path / "participant", instance_tag="participant")
    source_id = _source_id(runtime)
    registry = runtime.providers
    source = registry.source(source_id)
    provider = registry.provider(source.provider)
    posting = runtime.opportunities.list()[0].posting
    assert posting is not None
    provider.postings[source_id] = replace(posting, source_id="different-source")  # type: ignore[index]

    with pytest.raises(ValueError, match="different exact source"):
        runtime.evaluate(source_id)


def test_career_event_proposal_never_confirms_from_file_presence(tmp_path: Path) -> None:
    runtime = build_synthetic_runtime(tmp_path / "participant", instance_tag="participant")
    opportunity_id = runtime.opportunities.list()[0].opportunity_id
    proposal = runtime.propose_career_event(
        opportunity_id,
        kind="interview_completed",
        occurred_at="2030-01-02T08:00:00+00:00",
        summary="Synthetic interview completed.",
    )
    artifact = tmp_path / "participant" / "tmp" / "generated-note.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("generated", encoding="utf-8")

    assert proposal.external_event_confirmed is False
    assert runtime.career_events.list() == ()
    with pytest.raises(PermissionError, match="explicit human confirmation"):
        runtime.confirm_career_event(
            proposal,
            confirmed=False,
            confirmed_by="local_user",
        )
    assert runtime.career_events.list() == ()
    event = runtime.confirm_career_event(
        proposal,
        confirmed=True,
        confirmed_by="local_user",
    )
    assert event.opportunity_id == opportunity_id
    assert len(runtime.career_events.list()) == 1


def test_synthetic_career_event_confirmation_is_actor_bound_and_idempotent(
    tmp_path: Path,
) -> None:
    runtime = build_synthetic_runtime(tmp_path / "participant", instance_tag="participant")
    opportunity_id = runtime.opportunities.list()[0].opportunity_id
    proposal = runtime.propose_career_event(
        opportunity_id,
        kind="application_submitted",
        occurred_at="2030-01-02T08:00:00+00:00",
        summary="Synthetic application submitted.",
    )

    first = runtime.confirm_career_event(
        proposal,
        confirmed=True,
        confirmed_by="local_user",
    )
    repeated = runtime.confirm_career_event(
        proposal,
        confirmed=True,
        confirmed_by="local_user",
    )

    assert repeated is first
    assert runtime.career_events.list() == (first,)
    with pytest.raises(ValueError, match="different actor"):
        runtime.confirm_career_event(
            proposal,
            confirmed=True,
            confirmed_by="another_user",
        )
    assert runtime.career_events.list() == (first,)


def test_synthetic_proposal_identity_includes_both_follow_up_fields(tmp_path: Path) -> None:
    runtime = build_synthetic_runtime(tmp_path / "participant", instance_tag="participant")
    opportunity_id = runtime.opportunities.list()[0].opportunity_id
    base = dict(
        opportunity_id=opportunity_id,
        kind="application_submitted",
        occurred_at="2030-01-02T08:00:00+00:00",
        summary="Synthetic application submitted.",
    )
    first = runtime.propose_career_event(**base, next_action=None, due_date=None)
    changed_action = runtime.propose_career_event(
        **base, next_action="Follow up with fictional recruiter.", due_date=None
    )
    changed_date = runtime.propose_career_event(**base, next_action=None, due_date="2030-01-09")

    assert len({first.proposal_id, changed_action.proposal_id, changed_date.proposal_id}) == 3
    assert runtime.propose_career_event(**base, next_action=None, due_date=None) == first
    first_event = runtime.confirm_career_event(first, confirmed=True, confirmed_by="local_user")
    action_event = runtime.confirm_career_event(
        changed_action, confirmed=True, confirmed_by="local_user"
    )
    date_event = runtime.confirm_career_event(
        changed_date, confirmed=True, confirmed_by="local_user"
    )
    assert len({first_event.event_id, action_event.event_id, date_event.event_id}) == 3
    proposed_after_confirmation = runtime.propose_career_event(
        **base, next_action="Send a fictional follow-up.", due_date="2030-01-10"
    )
    later_event = runtime.confirm_career_event(
        proposed_after_confirmation, confirmed=True, confirmed_by="local_user"
    )
    assert later_event.event_id not in {
        first_event.event_id,
        action_event.event_id,
        date_event.event_id,
    }
    assert (
        runtime.confirm_career_event(first, confirmed=True, confirmed_by="local_user")
        is first_event
    )


@pytest.mark.parametrize(
    "route",
    [
        "//example.invalid/path",
        r"/\example.invalid/path",
        r"/local\path",
        "/local\npath",
        "/local\x7fpath",
    ],
)
def test_runtime_rejects_nonlocal_decision_routes(tmp_path: Path, route: str) -> None:
    runtime = build_synthetic_runtime(tmp_path / "participant", instance_tag="participant")
    evaluation = runtime.evaluate(_source_id(runtime))
    valid = runtime.projections.project(evaluation)

    class RouteProjection:
        instance_root = runtime.paths.root

        def project(self, _evaluation):  # noqa: ANN001, ANN201
            return replace(valid, items=(replace(valid.items[0], action_route=route),))

    with pytest.raises(ValueError, match="human-confirmation contract"):
        replace(runtime, projections=RouteProjection()).project_decision(evaluation)


def test_runtime_preserves_local_decision_route_with_query(tmp_path: Path) -> None:
    runtime = build_synthetic_runtime(tmp_path / "participant", instance_tag="participant")
    evaluation = runtime.evaluate(_source_id(runtime))
    valid = runtime.projections.project(evaluation)
    route = "/opportunities/synthetic-role?tab=review&source=local"

    class RouteProjection:
        instance_root = runtime.paths.root

        def project(self, _evaluation):  # noqa: ANN001, ANN201
            return replace(valid, items=(replace(valid.items[0], action_route=route),))

    assert (
        replace(runtime, projections=RouteProjection())
        .project_decision(evaluation)
        .items[0]
        .action_route
        == route
    )


def test_generated_outputs_remain_inside_instance_exports(tmp_path: Path) -> None:
    runtime = build_synthetic_runtime(tmp_path / "participant", instance_tag="participant")
    output = runtime.output_path("reports/decision.json")

    assert runtime.paths.exports.resolve(strict=False) in output.parents
    assert not output.exists()
    with pytest.raises(ValueError, match="safe export-relative"):
        runtime.output_path("../outside.json")


def test_workspace_paths_and_plans_are_revalidated_at_use_time(tmp_path: Path) -> None:
    runtime = build_synthetic_runtime(tmp_path / "participant", instance_tag="participant")
    opportunity_id = runtime.opportunities.list()[0].opportunity_id

    workspace = runtime.resolve_workspace(opportunity_id, kind="application")
    plan = runtime.plan_workspace(opportunity_id, kind="interview", round_name="first")

    assert workspace is not None
    assert runtime.paths.applications.resolve(strict=False) in workspace.path.parents
    assert plan.filesystem_changed is False
    assert not plan.target.exists()
    runtime.workspaces.records = (  # type: ignore[attr-defined]
        replace(workspace, path=tmp_path / "another-instance" / "workspace"),
    )
    with pytest.raises(ValueError, match="crossed the participant instance boundary"):
        runtime.resolve_workspace(opportunity_id, kind="application")


@pytest.mark.parametrize(
    "unsafe_identity", ["../outside", "/outside", r"C:\outside", r"..\outside"]
)
def test_synthetic_workspace_plans_reject_cross_platform_path_identities(
    tmp_path: Path,
    unsafe_identity: str,
) -> None:
    runtime = build_synthetic_runtime(tmp_path / "participant", instance_tag="participant")
    opportunity_id = runtime.opportunities.list()[0].opportunity_id

    with pytest.raises(ValueError, match="one safe path component"):
        runtime.workspaces.plan_application(unsafe_identity)
    with pytest.raises(ValueError, match="one safe path component"):
        runtime.workspaces.plan_interview(opportunity_id, round_name=unsafe_identity)


def test_model_policy_keeps_credentials_reference_only(tmp_path: Path) -> None:
    runtime = build_synthetic_runtime(tmp_path / "participant", instance_tag="participant")
    assert runtime.authorize_model(purpose="ranking", data_categories=("posting",)).allowed is False

    class UnsafeModelPolicy:
        instance_root = runtime.paths.root

        def authorize(self, *, purpose: str, data_categories: tuple[str, ...]) -> ModelAccess:
            del purpose, data_categories
            return ModelAccess(
                allowed=True,
                provider="synthetic",
                model="synthetic-model",
                credential_ref="raw-secret-value",
            )

    unsafe = replace(runtime, model_policy=UnsafeModelPolicy())
    with pytest.raises(ValueError, match="credentials must be supplied by reference"):
        unsafe.authorize_model(purpose="ranking", data_categories=("posting",))


def test_installed_synthetic_smoke_contract() -> None:
    result = run_smoke()

    assert result["instances"] == 2
    assert result["offline"] is True
    assert result["read_only_projection"] is True
    assert result["external_actions"] is False
    assert len(set(result["opportunity_ids"])) == 2

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

import career_os.participant_flow as participant_flow
from career_os.participant_flow import RESET_PHRASE, SYNTHETIC_POSTING
from career_os.product_journey import ProductJourney, run_full_smoke


def _reviewed_journey(root: Path, *, reject: set[str] | None = None) -> ProductJourney:
    journey = ProductJourney(root)
    journey.record_boundary_consent(accepted=True)
    journey.import_synthetic_document()
    rejected = reject or set()
    while card := journey.next_review():
        journey.review(
            card.evidence_id,
            card.revision,
            action="reject" if card.evidence_id in rejected else "approve",
            expected_content_hash=card.content_hash,
        )
    assert journey.readiness().ready
    return journey


def _evaluated_journey(root: Path) -> ProductJourney:
    journey = _reviewed_journey(root)
    journey.select_role(
        "customer_post_sales_leadership",
        location="Germany",
        work_model="flexible",
        maximum_travel_percent=25,
    )
    journey.evaluate_posting()
    return journey


def test_role_selection_preferences_posting_and_evidence_remain_distinct(tmp_path: Path) -> None:
    journey = _evaluated_journey(tmp_path / "participant")
    state = journey.flow.snapshot()
    dossier = journey.dossier()

    assert dossier is not None
    assert dossier.opportunity_id == SYNTHETIC_POSTING.job_key
    assert dossier.posting_hash == SYNTHETIC_POSTING.content_hash
    assert dossier.official_source_verified is True
    assert dossier.role_pack_id == "customer_post_sales_leadership"
    assert dossier.recommendation == "conditional"
    assert state["role_selection"]["candidate_preferences"] == {
        "location": "Germany",
        "work_model": "flexible",
        "maximum_travel_percent": 25,
        "currency": "EUR",
    }
    outcomes = {item.requirement_id: item.outcome for item in dossier.assessments}
    assert outcomes["post_sales_team_leadership"] == "proven"
    assert outcomes["new_logo_ae_leadership"] == "disproven"
    assert outcomes["budget_ownership"] == "unknown"
    assert dossier.role_pack_version == "1.1.0"
    assert dossier.role_pack_fingerprint
    assert dossier.selection_status == "selected"
    assert dossier.adjacent_archetype_ids == ("customer_operations_leadership",)
    assert dossier.evaluation_fingerprint


def test_customer_walking_skeleton_rejects_unrelated_gate_d_pack(tmp_path: Path) -> None:
    journey = _reviewed_journey(tmp_path / "participant")

    with pytest.raises(ValueError, match="not supported by this synthetic participant fixture"):
        journey.select_role(
            "product_leadership",
            location="Germany",
            work_model="flexible",
            maximum_travel_percent=25,
        )


@pytest.mark.parametrize("change", ["additional_evidence", "revised_evidence"])
def test_changed_approved_evidence_invalidates_derived_state_without_deleting_history(
    tmp_path: Path, change: str
) -> None:
    journey = _evaluated_journey(tmp_path / "participant")
    dossier = journey.dossier()
    assert dossier is not None
    journey.record_decision(dossier.recommendation)
    artifacts = journey.prepare_bundle()
    journey.confirm_synthetic_lifecycle(confirmed=True)
    flow_bytes = journey.flow.path.read_bytes()
    retained = {
        journey.instance_root / item["relative_path"]: (
            journey.instance_root / item["relative_path"]
        ).read_bytes()
        for item in artifacts
    }
    vault = journey.evidence
    sources = vault.sources()
    fingerprints = dict(vault.context_fingerprints())
    original = vault.latest("synthetic-responsibility")
    if change == "additional_evidence":
        revised = vault.propose(
            evidence_id="synthetic-additional-responsibility",
            source_id=original.source_id,
            kind=original.kind,
            statement=original.statement,
            details=original.details,
            source_start_line=original.source_start_line,
            source_end_line=original.source_end_line,
            ownership=original.ownership,
            confidence=original.confidence,
            approximation=original.approximation,
            publication_boundary=original.publication_boundary,
            metadata=original.metadata,
        )
    else:
        revised = vault.edit(
            original.evidence_id,
            expected_revision=original.revision,
            statement=original.statement + " This wording was reviewed again.",
        )
    vault.review(
        revised.evidence_id,
        revised.revision,
        action="approve",
        expected_content_hash=revised.content_hash,
    )

    assert vault.sources() == sources
    assert dict(vault.context_fingerprints()) != fingerprints
    state = journey.state()
    assert state.next_route == "/readiness"
    assert not any(
        (
            state.role_pack_selected,
            state.dossier_ready,
            state.decision_recorded,
            state.bundle_ready,
            state.lifecycle_confirmed,
        )
    )
    assert journey.dossier() is None
    for action in (
        lambda: journey.record_decision("pursue"),
        journey.prepare_bundle,
        lambda: journey.confirm_synthetic_lifecycle(confirmed=True),
    ):
        with pytest.raises(ValueError):
            action()
    assert journey.flow.path.read_bytes() == flow_bytes
    assert all(path.read_bytes() == content for path, content in retained.items())


def test_not_found_is_not_disproven_and_fallback_blocks_application_readiness(
    tmp_path: Path,
) -> None:
    journey = _reviewed_journey(
        tmp_path / "participant",
        reject={"synthetic-achievement", "synthetic-gap", "synthetic-forbidden-degree"},
    )
    journey.select_role(
        "generic_fallback",
        location="Germany",
        work_model="remote",
        maximum_travel_percent=10,
    )
    dossier = journey.evaluate_posting()
    outcomes = {item.requirement_id: item.outcome for item in dossier.assessments}

    assert outcomes["onboarding_improvement"] == "not_found"
    assert outcomes["new_logo_ae_leadership"] == "not_found"
    assert dossier.recommendation == "hold"
    assert dossier.confidence <= 0.45
    assert dossier.application_ready is False


def test_legacy_dossier_is_forced_non_ready_until_current_pack_rescore(tmp_path: Path) -> None:
    journey = _evaluated_journey(tmp_path / "participant")
    state = journey.flow.snapshot()
    evaluation = state["evaluation"]
    assert isinstance(evaluation, dict)
    evaluation["application_ready"] = True
    evaluation.pop("role_pack_fingerprint")
    evaluation.pop("selection_status")
    evaluation.pop("evaluation_fingerprint")
    journey.flow.path.write_text(json.dumps(state), encoding="utf-8")

    dossier = journey.dossier()

    assert dossier is not None
    assert dossier.selection_status == "manual_review"
    assert dossier.application_ready is False


@pytest.mark.parametrize("posting_change", ["identity", "content"])
def test_saved_evaluation_is_invalidated_by_current_posting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, posting_change: str
) -> None:
    journey = _evaluated_journey(tmp_path / "participant")
    dossier = journey.dossier()
    assert dossier is not None
    journey.record_decision(dossier.recommendation)
    before = journey.flow.snapshot()
    source_ids = tuple(source.source_id for source in journey.evidence.sources())
    approved = tuple(
        (item.evidence_id, item.revision, item.status, item.content_hash)
        for item in journey.evidence.enumerate(statuses=("approved",))
    )
    stored_bytes = journey.flow.path.read_bytes()
    if posting_change == "identity":
        posting = replace(SYNTHETIC_POSTING, external_id="new-role-2030")
    else:
        posting = replace(
            SYNTHETIC_POSTING, description=SYNTHETIC_POSTING.description + " Updated."
        )
    monkeypatch.setattr(participant_flow, "SYNTHETIC_POSTING", posting)
    monkeypatch.setattr(
        participant_flow,
        "SYNTHETIC_POSTING_PROFILE",
        replace(
            participant_flow.SYNTHETIC_POSTING_PROFILE,
            opportunity_id=posting.job_key,
            posting_hash=posting.content_hash,
        ),
    )

    state = journey.flow.snapshot()
    assert state["role_selection"] == before["role_selection"]
    assert state["evaluation"] is None
    assert state["decision"] is None
    assert state["created_at"] == before["created_at"]
    assert state["updated_at"] == before["updated_at"]
    assert journey.flow.path.read_bytes() == stored_bytes
    assert tuple(source.source_id for source in journey.evidence.sources()) == source_ids
    assert (
        tuple(
            (item.evidence_id, item.revision, item.status, item.content_hash)
            for item in journey.evidence.enumerate(statuses=("approved",))
        )
        == approved
    )
    assert journey.dossier() is None
    assert journey.state().next_route == "/posting"
    with pytest.raises(ValueError, match="Evaluate the exact posting"):
        journey.record_decision("conditional")

    refreshed = journey.evaluate_posting()
    assert refreshed.opportunity_id == posting.job_key
    assert refreshed.posting_hash == posting.content_hash
    assert journey.dossier() == refreshed
    assert journey.state().next_route == "/dossier"


def test_decision_and_role_reselection_are_one_serialized_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journey = _evaluated_journey(tmp_path / "participant")
    original_dossier = journey.flow.dossier
    dossier_entered = Event()
    release_dossier = Event()
    selection_started = Event()

    def paused_dossier():
        result = original_dossier()
        dossier_entered.set()
        assert release_dossier.wait(5)
        return result

    monkeypatch.setattr(journey.flow, "dossier", paused_dossier)

    def reselect():
        selection_started.set()
        journey.select_role(
            "customer_post_sales_leadership",
            location="France",
            work_model="remote",
            maximum_travel_percent=5,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        decision = executor.submit(journey.record_decision, "conditional")
        assert dossier_entered.wait(5)
        selection = executor.submit(reselect)
        assert selection_started.wait(5)
        try:
            selection.result(timeout=0.2)
        except FutureTimeout:
            pass
        finally:
            release_dossier.set()
        decision.result(timeout=5)
        selection.result(timeout=5)

    state = journey.flow.snapshot()
    assert state["role_selection"]["candidate_preferences"]["location"] == "France"
    assert state["evaluation"] is None
    assert state["decision"] is None


def test_decision_is_not_lifecycle_and_override_requires_structured_reason(
    tmp_path: Path,
) -> None:
    journey = _evaluated_journey(tmp_path / "participant")

    with pytest.raises(ValueError, match="structured reason"):
        journey.record_decision("pursue")
    state = journey.record_decision("pursue", override_reason="practical_constraint")

    assert state.decision_recorded is True
    assert state.bundle_ready is False
    assert state.lifecycle_confirmed is False
    assert journey.flow.snapshot()["lifecycle_events"] == []
    assert not (journey.instance_root / "applications").exists()


def test_bundle_uses_only_approved_application_evidence_and_never_implies_submission(
    tmp_path: Path,
) -> None:
    journey = _evaluated_journey(tmp_path / "participant")
    dossier = journey.dossier()
    assert dossier is not None
    journey.record_decision(dossier.recommendation)

    artifacts = journey.prepare_bundle()
    application = next(
        journey.instance_root / item["relative_path"]
        for item in artifacts
        if item["relative_path"].endswith("application-brief.md")
    ).read_text(encoding="utf-8")
    requirement_map = next(
        journey.instance_root / item["relative_path"]
        for item in artifacts
        if item["relative_path"].endswith("requirement-evidence-map.json")
    )
    mapping = json.loads(requirement_map.read_text(encoding="utf-8"))

    assert "not a finished CV and not a submitted application" in application
    assert "[evidence:synthetic-responsibility#r1]" in application
    assert "completed a bachelor's degree" not in application.casefold()
    assert mapping["external_actions"] is False
    assert {item["outcome"] for item in mapping["requirements"]} >= {
        "proven",
        "disproven",
        "unknown",
    }


def test_lifecycle_requires_confirmation_export_is_local_and_reset_is_verified(
    tmp_path: Path,
) -> None:
    root = tmp_path / "participant"
    journey = _evaluated_journey(root)
    dossier = journey.dossier()
    assert dossier is not None
    journey.record_decision(dossier.recommendation)
    journey.prepare_bundle()

    with pytest.raises(PermissionError, match="explicit confirmation"):
        journey.confirm_synthetic_lifecycle(confirmed=False)
    state = journey.confirm_synthetic_lifecycle(confirmed=True)
    payload = journey.export_user_data()

    assert state.lifecycle_confirmed is True
    event = journey.flow.snapshot()["lifecycle_events"][0]
    assert event["external_event"] is False
    assert event["application_submitted"] is False
    assert payload["export_manifest"]["credentials_included"] is False
    assert payload["export_manifest"]["remote_telemetry"] is False
    assert payload["participant_flow"]["metrics"]["decision"] == dossier.recommendation
    assert all("Northstar" not in key for key in payload["participant_flow"]["metrics"])

    with pytest.raises(PermissionError, match="exact phrase"):
        journey.reset("delete")
    inventory = journey.reset_preview()
    report = journey.reset(RESET_PHRASE)

    assert "exports/career-os-user-export.json" in inventory
    assert report.removed_count == len(inventory)
    assert report.instance_removed is True
    assert not root.exists()


def test_full_smoke_completes_and_deletes_two_isolated_instances() -> None:
    result = run_full_smoke()

    assert result["instances"] == 2
    assert result["journeys_completed"] == 2
    assert result["instances_removed"] is True
    assert result["model_calls"] is False
    assert result["external_actions"] is False
    for item in result["proofs"]:
        assert item["doctor"]["workspace"] == "ok"
        assert item["doctor"]["layout"] == "ok"
        assert item["doctor"]["evidence_storage"] == "ok"
        assert item["doctor_ready"] is True
        assert item["exported"] is True
        assert item["export_stage"] == "populated_workspace"
        assert item["exported_sources"] == 1
        assert item["journey_exported"] is True
        assert item["journey_export_stage"] == "completed_journey"
        assert len(item["journey_export_sha256"]) == 64
        assert item["journey_reset"] is True
        assert item["journey_removed"] == item["journey_inventory_before_reset"]
        assert item["deleted"] is True
        assert item["removed"] == item["inventory_before_delete"]
        assert item["inventory_before_reset"] == item["inventory_before_delete"]


def test_reset_refuses_a_reparse_boundary_without_touching_external_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "participant"
    journey = _reviewed_journey(root)
    boundary = root / "data" / "escape"
    boundary.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    original = participant_flow._is_reparse_or_symlink
    monkeypatch.setattr(
        participant_flow,
        "_is_reparse_or_symlink",
        lambda path: path == boundary or original(path),
    )

    with pytest.raises(ValueError, match="reparse"):
        journey.reset(RESET_PHRASE)

    assert root.exists()
    assert outside.read_text(encoding="utf-8") == "keep"

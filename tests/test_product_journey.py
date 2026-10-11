from __future__ import annotations

import json
from pathlib import Path

import pytest

import career_os.product_journey as product_journey
from career_os.product_journey import ProductJourney


def test_product_journey_is_resumable_and_model_disabled(tmp_path: Path) -> None:
    root = tmp_path / "participant"
    journey = ProductJourney(root)

    assert journey.state().stage == "boundary"
    assert not root.exists()
    state = journey.record_boundary_consent(accepted=True)
    assert state.stage == "import"
    state = journey.import_synthetic_document()
    assert state.stage == "review"
    assert state.review_items == 6
    assert state.model_calls is False
    assert state.external_actions is False

    resumed = ProductJourney(root)
    assert resumed.state().stage == "review"
    assert resumed.next_review() is not None


def test_atomic_review_actions_do_not_create_external_or_application_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "participant"
    journey = ProductJourney(root)
    journey.record_boundary_consent(accepted=True)
    journey.import_synthetic_document()
    first = journey.next_review()
    assert first is not None

    journey.review(
        first.evidence_id,
        first.revision,
        action="defer",
        expected_content_hash=first.content_hash,
    )

    assert journey.state().review_items == 6
    assert not (root / "applications").exists()
    assert not (root / "pipeline").exists()
    assert not (root / "data" / "runtime" / "career-events.jsonl").exists()


def test_local_document_import_requires_consent_and_creates_no_proposal(
    tmp_path: Path,
) -> None:
    document = tmp_path / "career.txt"
    document.write_text("Synthetic local role evidence.\n", encoding="utf-8")
    journey = ProductJourney(tmp_path / "participant")

    with pytest.raises(PermissionError, match="explicit local consent"):
        journey.import_local_document(document)
    journey.record_boundary_consent(accepted=True)
    source = journey.import_local_document(document)

    assert source.display_name == "career.txt"
    assert journey.state().source_count == 1
    assert journey.state().review_items == 0
    assert journey.readiness().ready is False
    assert journey.evidence.enumerate() == ()


def test_complete_synthetic_review_reaches_readiness(tmp_path: Path) -> None:
    journey = ProductJourney(tmp_path / "participant")
    journey.record_boundary_consent(accepted=True)
    journey.import_synthetic_document()

    initial_inventory = journey.evidence.review_queue()
    assert len(initial_inventory) == journey.state().review_items == 6
    approved_ids: set[str] = set()

    for expected_card in initial_inventory:
        card = journey.next_review()
        assert card is not None, "review queue ended before the initial inventory was approved"
        assert (card.evidence_id, card.revision, card.content_hash) == (
            expected_card.evidence_id,
            expected_card.revision,
            expected_card.content_hash,
        )
        assert card.evidence_id not in approved_ids, "review queue did not advance"
        journey.review(
            card.evidence_id,
            card.revision,
            action="approve",
            expected_content_hash=card.content_hash,
        )
        approved_ids.add(card.evidence_id)

    assert len(approved_ids) == len(initial_inventory)
    assert journey.next_review() is None

    readiness = journey.readiness()
    snapshot = journey.export_snapshot()
    assert readiness.ready is True
    assert readiness.approved == 6
    assert journey.state().stage == "readiness"
    assert snapshot["external_actions"] is False
    assert snapshot["model_calls"] is False
    assert snapshot["journey"]["approved_items"] == 6


@pytest.mark.parametrize(("instances_removed", "expected_status"), [(True, 0), (False, 1)])
def test_full_main_preserves_json_and_reports_removal_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    instances_removed: bool,
    expected_status: int,
) -> None:
    report = {"instances_removed": instances_removed, "proofs": []}
    monkeypatch.setattr(product_journey, "run_full_smoke", lambda: report)

    assert product_journey.full_main() == expected_status
    assert json.loads(capsys.readouterr().out) == report

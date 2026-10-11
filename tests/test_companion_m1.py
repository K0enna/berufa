"""Authority and lifecycle acceptance through actual fictional public services."""

from __future__ import annotations

import copy
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from career_os.application.companion import (
    CompanionError,
    CompanionService,
    companion_json,
)
from career_os.application.interviews import InterviewRequest, require_interview_result
from career_os.application.offers import OfferService, require_offer_result
from career_os.application.opportunities import SuppliedDecisionInput, require_opportunity_result
from career_os.evidence_vault import EvidenceVault
from career_os.infrastructure import companion as companion_storage
from career_os.infrastructure.companion import LocalCompanionRepository, LocalCompanionReview
from career_os.infrastructure.instance_data import create_instance_data_service
from career_os.infrastructure.instances import create_instance_service
from career_os.infrastructure.interviews import create_interview_service
from career_os.infrastructure.opportunities import create_opportunity_service
from career_os.infrastructure.recovery import create_recovery_service
from career_os.synthetic_evidence import seed_synthetic_evidence


@pytest.fixture
def connected(tmp_path):
    root = tmp_path / "fictional"
    create_instance_service(root).initialize(actor="local_user")
    vault = EvidenceVault(root)
    vault.record_consent(accepted=True)
    _, records = seed_synthetic_evidence(vault)
    for record in records:
        vault.review(
            record.evidence_id,
            record.revision,
            action="reject" if record.kind == "forbidden_claim" else "approve",
            expected_content_hash=record.content_hash,
        )
    repository = LocalCompanionRepository(root)
    human = LocalCompanionReview(repository)
    human.setup()
    return root, vault, repository, human, CompanionService(repository)


def role_proposal():
    return {
        "schema_version": 1,
        "authority": "user_supplied",
        "mapping_status": "unconfirmed",
        "opportunity_id": "fictional_role",
        "company": "Fictional Systems GmbH",
        "title": "Customer Operations Lead",
        "location": "Germany",
        "work_model": "hybrid",
        "source_url": None,
        "posting_text": "Lead a wholly fictional post-sales service team.",
        "role_signals": ["post_sales_scope"],
        "requirements": [
            {
                "requirement_id": "service_leadership",
                "level": "mandatory",
                "statement": "Lead the post-sales service team.",
            }
        ],
        "evidence_routes": {
            "direct": {"service_leadership": ["synthetic-responsibility"]},
            "transferable": {},
            "gaps": {},
        },
        "constraints": {
            "preferred_location": "Germany",
            "preferred_work_model": "hybrid",
            "maximum_travel_percent": 20,
            "currency": "EUR",
        },
    }


def grant_evidence(human, purpose="assess_role"):
    ref, _ = human.inspect("evidence", "synthetic-responsibility")
    return ref, human.grant(ref, ("statement", "status"), purpose)


def staged_assessment(connected):
    _, _, _, human, service = connected
    grant_evidence(human)
    result = service.call("assessment", {"request": role_proposal(), "idempotency_key": "role_one"})
    assert result["state"] == "prepared"
    return result["pending_id"]


def files(root):
    return {
        p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_v1_retained_capacity_includes_every_state_and_keeps_completed_replay(
    connected, monkeypatch
):
    from career_os.application.companion import MAX_COMPANION_STATE_BYTES, validate_companion_state

    root, vault, repository, human, service = connected
    completed = staged_assessment(connected)
    receipt = human.confirm(completed, human.issue_confirmation(completed))
    pending = service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "pending_fixture"}
    )["pending_id"]
    with repository.vault.read_transaction():
        state = repository._state()
    template = state["pending"][pending]
    # Accepted pre-change v1 fixture: completed history plus prepared, cancelled,
    # expired and ambiguous executing records all occupy retained capacity.
    for index in range(254):
        item = copy.deepcopy(template)
        item["key"] = f"retained_fixture_{index}"
        item["state"] = ("prepared", "cancelled", "expired", "executing")[index % 4]
        state["pending"][f"pending-{index:032x}"] = item
    assert len(state["pending"]) == 256
    with repository.vault.existing_transaction():
        repository._save(state)
    before = files(root)
    assert service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "overflow"}
    ) == {"error": "input_too_large"}
    assert service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "role_one"}
    ) == {"pending_id": completed, "state": "completed"}
    with repository.vault.read_transaction():
        assert repository._state()["pending"][completed]["receipt"] == receipt
        raw = repository.vault.companion_metadata()
    assert files(root) == before

    human.lifecycle_confirm(human.lifecycle_preview("upgrade"))
    assert service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "still_full"}
    ) == {"error": "input_too_large"}
    assert service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "role_one"}
    ) == {"pending_id": completed, "state": "completed"}
    now = int(time.time())
    monkeypatch.setattr(companion_storage.time, "time", lambda: now + 30 * 86400)
    eligible = "pending-" + f"{2:032x}"
    human.lifecycle_confirm(human.lifecycle_preview("retire", (eligible,)))
    grant_evidence(human)
    newly_staged = service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "new_after_retirement"}
    )
    assert newly_staged["state"] == "prepared"
    assert service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "retained_fixture_2"}
    ) == {"error": "conflict"}
    assert service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "full_again"}
    ) == {"error": "input_too_large"}
    before = files(root)
    exact = raw + " " * (MAX_COMPANION_STATE_BYTES - len(raw.encode()))
    assert validate_companion_state(exact, repository.instance_hash) == state
    with pytest.raises(CompanionError, match="input_too_large"):
        validate_companion_state(exact + " ", repository.instance_hash)
    too_many = copy.deepcopy(state)
    too_many["pending"]["pending-" + "f" * 32] = copy.deepcopy(template)
    with pytest.raises(CompanionError, match="integrity_error"):
        validate_companion_state(companion_json(too_many).decode(), repository.instance_hash)
    assert files(root) == before


def test_exact_grants_default_deny_partial_fields_revocation_and_revision_change(connected):
    root, vault, _, human, service = connected
    ref, _ = human.inspect("evidence", "synthetic-responsibility")
    read = {
        "ref": ref.to_dict(),
        "fields": ["statement"],
        "purpose": "resume_work",
        "historical": False,
    }
    before = files(root)
    assert service.call("read", read) == {"error": "not_found_or_denied"}
    assert (
        service.call(
            "list", {"kind": "evidence", "purpose": "resume_work", "offset": 0, "limit": 20}
        )["items"]
        == []
    )
    assert files(root) == before
    grant = human.grant(ref, ("statement",), "resume_work")
    assert service.call("read", read)["data"] == {
        "statement": vault.latest(ref.object_id).statement
    }
    assert service.call("read", {**read, "fields": ["details"]}) == {"error": "not_found_or_denied"}
    human.revoke(grant)
    assert service.call("read", read) == {"error": "not_found_or_denied"}
    human.grant(ref, ("statement",), "resume_work")
    card = vault.latest(ref.object_id)
    vault.edit(
        card.evidence_id, expected_revision=card.revision, statement=card.statement + " Revised."
    )
    assert service.call("read", read) == {"error": "stale_basis"}
    current_ref, _ = human.inspect("evidence", card.evidence_id)
    assert service.call("read", {**read, "ref": current_ref.to_dict()}) == {
        "error": "not_found_or_denied"
    }


def test_explicit_v2_upgrade_cancel_stale_preview_and_committed_retry(connected, monkeypatch):
    root, vault, repository, human, service = connected
    pending = staged_assessment(connected)
    token = human.issue_confirmation(pending)
    before = files(root)
    with pytest.raises(CompanionError, match="upgrade_required"):
        human.lifecycle_preview("cancel", (pending,))
    upgrade = human.lifecycle_preview("upgrade")
    with repository.vault.read_transaction():
        original = repository._state()
    assert files(root) == before and original["version"] == 1
    receipt = human.lifecycle_confirm(upgrade)
    with repository.vault.read_transaction():
        upgraded = repository._state()
    assert upgraded["pending"] == original["pending"] and upgraded["grants"] == original["grants"]
    assert upgraded["archive"] == {} and upgraded["version"] == 2
    assert human.lifecycle_confirm(upgrade) == receipt
    for operation in ("upgrade", "cancel", "retire"):
        assert service.call(operation, {}) == {"error": "unavailable_operation"}
    preview = human.lifecycle_preview("cancel", (pending,))
    human.revoke(next(iter(upgraded["grants"])))
    with pytest.raises(CompanionError, match="stale_basis"):
        human.lifecycle_confirm(preview)
    preview = human.lifecycle_preview("cancel", (pending,))
    business_before = files(root / "data/runtime/opportunities")
    receipt = human.lifecycle_confirm(preview)
    with pytest.raises(CompanionError, match="human_required"):
        human.confirm(pending, token)
    with repository.vault.read_transaction():
        cancelled = repository._state()["pending"][pending]
    assert cancelled["state"] == "cancelled" and cancelled["confirmation"] is None
    assert cancelled["created_at"] == original["pending"][pending]["created_at"]
    assert files(root / "data/runtime/opportunities") == business_before
    card = vault.latest("synthetic-responsibility")
    vault.edit(
        card.evidence_id, expected_revision=card.revision, statement=card.statement + " Revised."
    )
    assert human.lifecycle_confirm(preview) == receipt
    changed = copy.deepcopy(preview)
    changed["records"][pending]["request"]["title"] = "Changed after review"
    with pytest.raises(CompanionError, match="invalid_input"):
        human.lifecycle_confirm(changed)
    now = int(time.time())
    monkeypatch.setattr(companion_storage.time, "time", lambda: now + 30 * 86400)
    human.lifecycle_confirm(human.lifecycle_preview("retire", (pending,)))
    assert human.lifecycle_confirm(preview) == receipt


def test_retirement_boundary_canonical_receipt_replay_and_explicit_historical_grant(
    connected, monkeypatch
):
    root, vault, repository, human, service = connected
    pending = staged_assessment(connected)
    receipt = human.confirm(pending, human.issue_confirmation(pending))
    human.lifecycle_confirm(human.lifecycle_preview("upgrade"))
    expired = service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "expired_one"}
    )["pending_id"]
    unknown = service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "unknown_one"}
    )["pending_id"]
    with repository.vault.existing_transaction():
        state = repository._state()
        state["pending"][unknown]["state"] = "executing"
        repository._save(state)
    original = copy.deepcopy(state["pending"][pending])
    boundary = max(record["created_at"] for record in state["pending"].values()) + 30 * 86400
    monkeypatch.setattr(
        companion_storage.time, "time", lambda: original["created_at"] + 30 * 86400 - 1
    )
    with pytest.raises(CompanionError, match="not_retirable"):
        human.lifecycle_preview("retire", (pending,))
    monkeypatch.setattr(companion_storage.time, "time", lambda: boundary)
    assert service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "role_one"}
    ) == {"error": "conflict"}
    with pytest.raises(CompanionError, match="not_retirable"):
        human.lifecycle_preview("retire", (unknown,))
    with repository.vault.existing_transaction():
        damaged = repository._state()
        damaged["pending"][pending]["receipt"]["source_hash"] = "f" * 64
        repository._save(damaged)
    before = files(root)
    with pytest.raises(CompanionError, match="execution_unknown"):
        human.lifecycle_preview("retire", (pending,))
    assert files(root) == before
    with repository.vault.existing_transaction():
        repaired = repository._state()
        repaired["pending"][pending] = original
        repository._save(repaired)
    preview = human.lifecycle_preview("retire", (pending, expired))
    business_before = files(root / "data/runtime/opportunities")
    retired = human.lifecycle_confirm(preview)
    assert human.lifecycle_confirm(preview) == retired
    assert human.recover(pending) == receipt
    assert files(root / "data/runtime/opportunities") == business_before
    with repository.vault.read_transaction():
        state = repository._state()
    assert pending not in state["pending"] and expired not in state["pending"]
    archived = state["archive"][pending]
    assert {key: archived[key] for key in original} == original
    assert archived["retirement"]["at"] == boundary
    with pytest.raises(CompanionError, match="not_found_or_denied"):
        human.inspect("pending", pending)
    ref, data = human.inspect("pending", pending, historical=True)
    read = {
        "ref": ref.to_dict(),
        "fields": ["receipt", "retired_at"],
        "purpose": "receipt_lookup",
        "historical": True,
    }
    assert service.call("read", read) == {"error": "not_found_or_denied"}
    grant = human.grant(ref, ("receipt", "retired_at"), "receipt_lookup", historical=True)
    assert service.call("read", read)["data"] == {
        "receipt": receipt,
        "retired_at": data["retired_at"],
    }
    human.revoke(grant)
    assert service.call("read", read) == {"error": "not_found_or_denied"}
    changed = role_proposal()
    changed["title"] = "Different key bytes"
    assert service.call("assessment", {"request": changed, "idempotency_key": "role_one"}) == {
        "error": "conflict"
    }


@pytest.mark.parametrize("operation", ["upgrade", "cancel", "retire"])
def test_lifecycle_interrupted_transaction_and_timeout_after_commit(
    connected, monkeypatch, operation
):
    _, vault, repository, human, _ = connected
    pending = staged_assessment(connected)
    if operation != "upgrade":
        human.lifecycle_confirm(human.lifecycle_preview("upgrade"))
    if operation == "retire":
        now = int(time.time())
        monkeypatch.setattr(companion_storage.time, "time", lambda: now + 30 * 86400)
    preview = human.lifecycle_preview(operation, () if operation == "upgrade" else (pending,))
    with repository.vault.read_transaction():
        original = repository.vault.companion_metadata()
    save = repository._save

    def interrupted(state):
        save(state)
        raise OSError("Fictional interruption before SQLite transaction commit")

    monkeypatch.setattr(repository, "_save", interrupted)
    with pytest.raises(OSError):
        human.lifecycle_confirm(preview)
    with repository.vault.read_transaction():
        assert repository.vault.companion_metadata() == original
    monkeypatch.setattr(repository, "_save", save)
    confirm = human.lifecycle_confirm
    captured = {}

    def lost_response(selected):
        captured["receipt"] = confirm(selected)
        raise TimeoutError("Fictional response lost after commit")

    monkeypatch.setattr(human, "lifecycle_confirm", lost_response)
    with pytest.raises(TimeoutError):
        human.lifecycle_confirm(preview)
    monkeypatch.setattr(human, "lifecycle_confirm", confirm)
    assert human.lifecycle_confirm(preview) == captured["receipt"]


def test_v2_archive_count_and_exact_byte_bounds_refuse_without_freeing_active(
    connected, monkeypatch
):
    from career_os.application.companion import (
        MAX_COMPANION_ARCHIVE_BYTES,
        validate_companion_state,
    )

    root, vault, repository, human, _ = connected
    pending = staged_assessment(connected)
    human.lifecycle_confirm(human.lifecycle_preview("upgrade"))
    now = int(time.time())
    monkeypatch.setattr(companion_storage.time, "time", lambda: now + 30 * 86400)
    human.lifecycle_confirm(human.lifecycle_preview("retire", (pending,)))
    with repository.vault.read_transaction():
        state = repository._state()
    template = state["archive"][pending]
    # Valid bounded history fixtures retain the original full record structure.
    full = copy.deepcopy(state)
    for index in range(4095):
        item = copy.deepcopy(template)
        item["key"] = f"archived_{index}"
        full["archive"][f"pending-{index:032x}"] = item
    active = copy.deepcopy(template)
    active.pop("retirement")
    active["key"] = "active_after_archive"
    full["pending"][pending] = active
    full["archive"].pop(pending)
    # Restore the archive to exactly 4096 entries without colliding with an active ID.
    full["archive"]["pending-" + "e" * 32] = {**copy.deepcopy(template), "key": "archive_last"}
    with repository.vault.existing_transaction():
        repository._save(full)
    before = files(root)
    with pytest.raises(CompanionError, match="input_too_large"):
        human.lifecycle_preview("retire", (pending,))
    assert files(root) == before
    sized = copy.deepcopy(state)
    sized["archive"] = {}
    item = copy.deepcopy(template)
    item["request"]["posting_text"] = "x" * 16000
    count = MAX_COMPANION_ARCHIVE_BYTES // (len(companion_json(item)) + 100) - 1
    for index in range(count):
        entry = copy.deepcopy(item)
        entry["key"] = f"byte_fixture_{index}"
        entry["digest"] = companion_storage.companion_digest(entry["request"])
        sized["archive"][f"pending-{index:032x}"] = entry
    last = next(reversed(sized["archive"].values()))
    padding = MAX_COMPANION_ARCHIVE_BYTES - len(companion_json(sized["archive"]))
    assert 0 < padding < 200000
    last["request"]["posting_text"] += "x" * padding
    last["digest"] = companion_storage.companion_digest(last["request"])
    assert len(companion_json(sized["archive"])) == MAX_COMPANION_ARCHIVE_BYTES
    assert (
        validate_companion_state(companion_json(sized).decode(), repository.instance_hash)[
            "version"
        ]
        == 2
    )
    last["request"]["posting_text"] += "x"
    last["digest"] = companion_storage.companion_digest(last["request"])
    with pytest.raises(CompanionError, match="input_too_large"):
        validate_companion_state(companion_json(sized).decode(), repository.instance_hash)
    assert files(root) == before


def test_pending_assessment_confirmation_session_replay_and_same_key_conflict(connected):
    root, _, _, human, service = connected
    pending = staged_assessment(connected)
    assert not (root / "data/runtime/opportunities").exists()
    reviewed = human.pending_review(pending)
    assert reviewed["preview"]["dossier"]["mapping_confirmed_by"] is None
    same = service.call("assessment", {"request": role_proposal(), "idempotency_key": "role_one"})
    assert same["pending_id"] == pending
    changed = role_proposal()
    changed["company"] = "Another fictional company"
    assert service.call("assessment", {"request": changed, "idempotency_key": "role_one"}) == {
        "error": "conflict"
    }
    token = human.issue_confirmation(pending)
    other = LocalCompanionReview(LocalCompanionRepository(root))
    with pytest.raises(CompanionError, match="human_required"):
        other.confirm(pending, token)
    receipt = human.confirm(pending, token)
    assert receipt["kind"] == "opportunity"
    assert (
        json.loads(
            (root / "data/runtime/opportunities/fictional_role/decision-dossier.json").read_text()
        )["mapping_confirmed_by"]
        == "local_user"
    )
    with pytest.raises(CompanionError, match="human_required"):
        human.confirm(pending, token)
    assert (
        service.call("assessment", {"request": role_proposal(), "idempotency_key": "role_one"})[
            "state"
        ]
        == "completed"
    )


def test_timeout_after_real_assessment_commit_recovers_without_duplicate_write(
    connected, monkeypatch
):
    root, _, _, human, service = connected
    pending = staged_assessment(connected)
    token = human.issue_confirmation(pending)
    complete = human._complete

    def lose_receipt(*_args):
        raise TimeoutError("FICTIONAL transport interruption after actual public writer committed")

    monkeypatch.setattr(human, "_complete", lose_receipt)
    with pytest.raises(TimeoutError):
        human.confirm(pending, token)
    assert (
        service.call("assessment", {"request": role_proposal(), "idempotency_key": "role_one"})[
            "state"
        ]
        == "executing"
    )
    before = files(root / "data/runtime/opportunities")
    monkeypatch.setattr(human, "_complete", complete)
    assert human.recover(pending)["kind"] == "opportunity"
    assert files(root / "data/runtime/opportunities") == before
    assert human.recover(pending)["kind"] == "opportunity"


def test_assessment_rejects_evidence_changed_after_capability_admission(connected, monkeypatch):
    root, vault, _, human, _ = connected
    pending = staged_assessment(connected)
    token = human.issue_confirmation(pending)
    execute = human._execute

    def change_reviewed_evidence(record):
        card = vault.latest("synthetic-responsibility")
        changed = vault.edit(
            card.evidence_id,
            expected_revision=card.revision,
            statement=card.statement + " Revised fictional scope.",
        )
        vault.review(
            changed.evidence_id,
            changed.revision,
            action="approve",
            expected_content_hash=changed.content_hash,
        )
        return execute(record)

    monkeypatch.setattr(human, "_execute", change_reviewed_evidence)
    with pytest.raises(CompanionError, match="execution_unknown"):
        human.confirm(pending, token)
    assert not (root / "data/runtime/opportunities").exists()
    with pytest.raises(CompanionError, match="execution_unknown"):
        human.recover(pending)


def test_concurrent_same_key_returns_one_pending_and_reads_do_not_modify_state(connected):
    root, _, _, _, service = connected
    staged_assessment(connected)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: service.call(
                    "assessment", {"request": role_proposal(), "idempotency_key": "role_one"}
                ),
                range(2),
            )
        )
    assert results[0] == results[1]
    before = files(root)
    assert (
        service.call(
            "list", {"kind": "pending", "purpose": "receipt_lookup", "offset": 0, "limit": 20}
        )["items"]
        == []
    )
    assert files(root) == before


@pytest.mark.parametrize("namespace_version", [1, 2])
def test_filtered_export_complete_backup_restore_revocation_and_selected_delete(
    connected, tmp_path, monkeypatch, namespace_version
):
    root, vault, _, human, service = connected
    pending = staged_assessment(connected)
    if namespace_version == 2:
        upgrade = human.lifecycle_preview("upgrade")
        human.lifecycle_confirm(upgrade)
    human.issue_confirmation(pending)
    completed = service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "completed_role"}
    )["pending_id"]
    completed_receipt = human.confirm(completed, human.issue_confirmation(completed))
    interrupted = service.call(
        "assessment", {"request": role_proposal(), "idempotency_key": "interrupted_role"}
    )["pending_id"]
    complete = human._complete

    def lose_receipt(*_args):
        raise TimeoutError("Fictional receipt loss before offline backup")

    monkeypatch.setattr(human, "_complete", lose_receipt)
    with pytest.raises(TimeoutError):
        human.confirm(interrupted, human.issue_confirmation(interrupted))
    monkeypatch.setattr(human, "_complete", complete)
    if namespace_version == 2:
        now = int(time.time())
        monkeypatch.setattr(companion_storage.time, "time", lambda: now + 30 * 86400)
        human.lifecycle_confirm(human.lifecycle_preview("retire", (completed,)))
    original_history = vault.audit_events()
    original_records = vault.enumerate()
    exported = tmp_path / "filtered"
    create_instance_data_service(root).export_user_data(
        str(exported), offline=True, actor="local_user"
    )
    payload = json.loads((exported / "career-os-user-data.json").read_text())
    assert len(payload["data"]["companion_pending"]) == (3 if namespace_version == 1 else 2)
    if namespace_version == 2:
        archived = payload["data"]["companion_archive"]
        assert len(archived) == 1 and archived[0]["receipt"] == completed_receipt
        assert "key" not in archived[0] and "retirement" not in archived[0]
        assert archived[0]["retired_at"] == now + 30 * 86400
    serialized = companion_json(payload)
    assert (
        b"token_hash" not in serialized
        and b"session_hash" not in serialized
        and b'"grants"' not in serialized
    )
    backup = tmp_path / "backup"
    create_recovery_service(root).backup(str(backup), offline=True, actor="local_user")
    target = tmp_path / "restored"
    recovery = create_recovery_service(target)
    preview = recovery.restore(str(backup))
    recovery.restore(
        str(backup),
        confirmation_fingerprint=preview.confirmation_fingerprint,
        offline=True,
        actor="local_user",
    )
    restored_vault = EvidenceVault(target)
    assert restored_vault.audit_events() == original_history
    assert restored_vault.enumerate() == original_records
    restored_repo = LocalCompanionRepository(target)
    with restored_vault.read_transaction():
        state = json.loads(restored_vault.companion_metadata())
    assert not state["enabled"] and state["grants"] == {}
    assert state["pending"][pending]["confirmation"] is None
    assert state["pending"][pending]["state"] == "cancelled"
    all_records = {**state["pending"], **state.get("archive", {})}
    assert all_records[completed]["receipt"] == completed_receipt
    assert state["pending"][interrupted]["state"] == "executing"
    assert all(record["restored"] for record in all_records.values())
    restored_human = LocalCompanionReview(restored_repo)
    if namespace_version == 2:
        with pytest.raises(CompanionError, match="not_found_or_denied"):
            restored_human.lifecycle_confirm(upgrade)
    restored_business_files = files(target / "data/runtime/opportunities")
    assert restored_human.recover(completed) == completed_receipt
    assert restored_human.recover(interrupted) == completed_receipt
    assert files(target / "data/runtime/opportunities") == restored_business_files
    assert CompanionService(restored_repo).call(
        "list", {"kind": "evidence", "purpose": "assess_role", "offset": 0, "limit": 20}
    ) == {"error": "not_found_or_denied"}
    restored_human.reconnect()
    assert (
        CompanionService(restored_repo).call(
            "list", {"kind": "evidence", "purpose": "assess_role", "offset": 0, "limit": 20}
        )["items"]
        == []
    )
    with pytest.raises(CompanionError, match="human_required"):
        restored_human.issue_confirmation(pending)
    before = files(root)
    deletion = create_instance_data_service(target)
    delete_preview = deletion.delete()
    deletion.delete(
        confirmation_fingerprint=delete_preview.confirmation_fingerprint,
        offline=True,
        actor="local_user",
    )
    assert not target.exists() and backup.exists() and exported.exists()
    assert files(root) == before
    human.disconnect()
    assert service.call(
        "list", {"kind": "evidence", "purpose": "assess_role", "offset": 0, "limit": 20}
    ) == {"error": "not_found_or_denied"}
    assert vault.audit_events() == original_history
    human.reconnect()
    assert (
        service.call(
            "list", {"kind": "evidence", "purpose": "assess_role", "offset": 0, "limit": 20}
        )["items"]
        == []
    )
    replay = service.call("assessment", {"request": role_proposal(), "idempotency_key": "role_one"})
    assert replay == (
        {"pending_id": pending, "state": "prepared"}
        if namespace_version == 1
        else {"error": "conflict"}
    )
    # The selected restored workspace above was deleted. Reconnection must not
    # recreate its vault or recover authority from the retained backup.
    with pytest.raises(ValueError):
        restored_human.reconnect()
    assert not target.exists()


def test_unknown_offer_has_no_numeric_verdict_and_complete_input_matches_public_service(
    connected, monkeypatch
):
    _, _, _, human, service = connected
    root = Path(__file__).resolve().parents[1]
    example = root / "public_repo/examples/offer-comparison.json"
    if not example.exists():
        example = root / "examples/offer-comparison.json"
    payload = json.loads(example.read_text())
    complete = require_offer_result(OfferService().preflight(payload)).to_dict()
    assert "expected" in complete and "confirmation_fingerprint" in complete
    known = service.call("offer", {"request": payload, "idempotency_key": "complete_offer"})[
        "pending_id"
    ]
    saved = human.confirm(known, human.issue_confirmation(known))
    payload["offer"]["base"] = None
    pending = service.call("offer", {"request": payload, "idempotency_key": "unknown_offer"})[
        "pending_id"
    ]
    preview = human.pending_review(pending)["preview"]
    assert preview["request"]["offer"]["base"] is None
    assert preview["expected"] is None and preview["downside"] is None
    assert preview["unknown_fields"] == ["offer.base"]
    receipt = human.confirm(pending, human.issue_confirmation(pending))
    assert receipt["comparison_saved"] is False
    human.lifecycle_confirm(human.lifecycle_preview("upgrade"))
    now = int(time.time())
    monkeypatch.setattr(companion_storage.time, "time", lambda: now + 30 * 86400)
    human.lifecycle_confirm(human.lifecycle_preview("retire", (pending, known)))
    assert human.recover(pending) == receipt and human.recover(known) == saved


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("approve", {}),
        ("read", {"actor": "local_user"}),
        ("list", {"kind": "evidence", "purpose": "resume_work", "offset": 0, "limit": 21}),
        (
            "assessment",
            {"request": {"mapping_confirmed_by": "local_user"}, "idempotency_key": "bad"},
        ),
    ],
)
def test_forbidden_operations_and_fields_are_content_free_and_do_not_write(
    connected, operation, payload
):
    root, _, _, _, service = connected
    before = files(root)
    assert "error" in service.call(operation, payload)
    assert files(root) == before


@pytest.mark.parametrize("failure", ["forged", "expired", "stale"])
def test_confirmation_refuses_forgery_expiry_and_changed_authority(connected, monkeypatch, failure):
    root, vault, _, human, _ = connected
    pending = staged_assessment(connected)
    token = human.issue_confirmation(pending)
    if failure == "forged":
        token = "0" * 64
    elif failure == "expired":
        now = int(time.time())
        monkeypatch.setattr(companion_storage, "time", SimpleNamespace(time=lambda: now + 601))
    else:
        card = vault.latest("synthetic-responsibility")
        vault.edit(
            card.evidence_id,
            expected_revision=card.revision,
            statement=card.statement + " Revised.",
        )
    before = files(root)
    with pytest.raises((CompanionError, ValueError)):
        human.confirm(pending, token)
    assert files(root) == before
    assert not (root / "data/runtime/opportunities").exists()


def test_historical_revision_requires_separate_mode_grant_and_stays_labelled(connected):
    _, vault, _, human, service = connected
    old_ref, _ = human.inspect("evidence", "synthetic-responsibility")
    human.grant(old_ref, ("statement",), "resume_work")
    card = vault.latest(old_ref.object_id)
    vault.edit(
        card.evidence_id, expected_revision=card.revision, statement=card.statement + " Revised."
    )
    old_ref, old = human.inspect(
        "evidence", old_ref.object_id, historical=True, revision=old_ref.revision
    )
    request = {
        "ref": old_ref.to_dict(),
        "fields": ["statement"],
        "purpose": "resume_work",
        "historical": True,
    }
    assert service.call("read", request) == {"error": "not_found_or_denied"}
    human.grant(old_ref, ("statement",), "resume_work", historical=True)
    assert service.call("read", request) == {
        "ref": old_ref.to_dict(),
        "historical": True,
        "data": {"statement": old["statement"]},
    }


def test_other_instance_and_nonfictional_source_are_rejected_without_import_or_write(
    connected, tmp_path
):
    root, _, _, human, _ = connected
    ref, _ = human.inspect("evidence", "synthetic-responsibility")
    second = tmp_path / "second"
    create_instance_service(second).initialize(actor="local_user")
    vault = EvidenceVault(second)
    vault.record_consent(accepted=True)
    vault.import_document(
        display_name="different-allowed-local-document.txt",
        media_type="text/plain",
        content=b"An arbitrary source outside the sealed fictional persona.\n",
    )
    second_repo = LocalCompanionRepository(second)
    before = files(second)
    with pytest.raises(CompanionError, match="unsupported_profile"):
        LocalCompanionReview(second_repo).setup()
    result = CompanionService(second_repo).call(
        "read",
        {
            "ref": ref.to_dict(),
            "fields": ["statement"],
            "purpose": "resume_work",
            "historical": False,
        },
    )
    assert result == {"error": "unsupported_profile"}
    assert files(second) == before
    assert root.exists()


def test_corrupt_bridge_namespace_fails_lifecycle_without_creating_export_or_restore(
    connected, tmp_path
):
    root, _, _, _, _ = connected
    with sqlite3.connect(root / "data/evidence-vault.db") as connection:
        connection.execute(
            "UPDATE vault_meta SET value=? WHERE key='companion_m1_state'", ('{"version":999}',)
        )
    before = files(root)
    target = tmp_path / "rejected"
    with pytest.raises(ValueError):
        create_instance_data_service(root).export_user_data(
            str(target), offline=True, actor="local_user"
        )
    assert not target.exists() and files(root) == before


def test_new_human_terminal_route_refuses_noninteractive_input_before_instance_access(
    tmp_path, capsys
):
    from career_os.companion_workflow import main

    root = tmp_path / "absent"
    assert main([str(root)]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "independent_human_terminal_required"}
    assert not root.exists()


def test_bridge_missing_instance_is_inert_and_grants_expire_without_mutation(
    connected, tmp_path, monkeypatch
):
    absent = tmp_path / "absent-instance"
    args = {"kind": "evidence", "purpose": "resume_work", "offset": 0, "limit": 20}
    assert "error" in CompanionService(LocalCompanionRepository(absent)).call("list", args)
    assert not absent.exists()
    root, _, _, human, service = connected
    ref, _ = human.inspect("evidence", "synthetic-responsibility")
    now = int(time.time())
    monkeypatch.setattr(companion_storage, "time", SimpleNamespace(time=lambda: now))
    human.grant(ref, ("statement",), "resume_work", lifetime_seconds=1)
    read = {
        "ref": ref.to_dict(),
        "fields": ["statement"],
        "purpose": "resume_work",
        "historical": False,
    }
    before = files(root)
    assert "data" in service.call("read", read)
    monkeypatch.setattr(companion_storage, "time", SimpleNamespace(time=lambda: now + 1))
    assert service.call("read", read) == {"error": "not_found_or_denied"}
    assert service.call("list", args)["items"] == []
    assert files(root) == before


def test_source_span_read_and_confirmed_proposal_use_existing_evidence_service(
    connected, monkeypatch
):
    _, vault, _, human, service = connected
    card = vault.latest("synthetic-responsibility")
    identity = f"{card.source_id}:{card.source_start_line}:{card.source_end_line}"
    ref, source = human.inspect("source_span", identity)
    human.grant(ref, ("lines", "source_hash", "extracted_hash"), "evidence_review")
    read = service.call(
        "read",
        {
            "ref": ref.to_dict(),
            "fields": ["lines"],
            "purpose": "evidence_review",
            "historical": False,
        },
    )
    assert read["data"]["lines"] == list(
        vault.source_lines(card.source_id, card.source_start_line, card.source_end_line)
    )
    request = evidence_request(card)
    pending = service.call("evidence", {"request": request, "idempotency_key": "propose_one"})
    before = vault.enumerate()
    assert pending["state"] == "prepared" and vault.enumerate() == before
    receipt = human.confirm(pending["pending_id"], human.issue_confirmation(pending["pending_id"]))
    proposed = vault.latest(receipt["object_id"])
    assert proposed.status == "unapproved"
    assert proposed.statement == request["statement"]
    assert proposed.source_hash == source["source_hash"]
    assert vault.latest(card.evidence_id) == card
    human.lifecycle_confirm(human.lifecycle_preview("upgrade"))
    now = int(time.time())
    monkeypatch.setattr(companion_storage.time, "time", lambda: now + 30 * 86400)
    human.lifecycle_confirm(human.lifecycle_preview("retire", (pending["pending_id"],)))
    assert human.recover(pending["pending_id"]) == receipt


def evidence_request(card):
    return {
        "schema_version": 1,
        "source_id": card.source_id,
        "source_hash": card.source_hash,
        "extracted_hash": card.extracted_hash,
        "kind": card.kind,
        "statement": card.statement,
        "details": card.details,
        "source_start_line": card.source_start_line,
        "source_end_line": card.source_end_line,
        "ownership": card.ownership,
        "confidence": card.confidence,
        "approximation": card.approximation,
        "publication_boundary": card.publication_boundary,
        "reason": "Fictional proposed correction; preserve existing reviewed evidence.",
        "metadata": asdict(card.metadata),
    }


def test_evidence_byte_limit_rejects_before_pending_write(connected):
    root, vault, _, human, service = connected
    card = vault.latest("synthetic-responsibility")
    ref, _ = human.inspect(
        "source_span", f"{card.source_id}:{card.source_start_line}:{card.source_end_line}"
    )
    human.grant(ref, ("lines",), "evidence_review")
    request = evidence_request(card)
    request["reason"] = "x" * (64 * 1024)
    before = files(root)
    assert service.call("evidence", {"request": request, "idempotency_key": "oversized"}) == {
        "error": "input_too_large"
    }
    assert files(root) == before


def test_decision_rejects_history_changed_after_capability_admission(connected, monkeypatch):
    root, _, _, human, service = connected
    assessment = staged_assessment(connected)
    human.confirm(assessment, human.issue_confirmation(assessment))
    ref, dossier = human.inspect("opportunity", "fictional_role")
    human.grant(ref, ("source_hash", "evaluation_fingerprint", "latest_decision"), "resume_work")
    initial = require_opportunity_result(
        create_opportunity_service(root).resolve_supplied("fictional_role")
    )
    request = {
        "opportunity_id": "fictional_role",
        "source_hash": dossier["source_hash"],
        "evaluation_fingerprint": initial.dossier.evaluation_fingerprint,
        "decision": "pursue",
        "reason_code": "mandate_fit",
        "note": "Fictional reviewed decision.",
    }
    pending = service.call("decision", {"request": request, "idempotency_key": "decision_one"})[
        "pending_id"
    ]
    token = human.issue_confirmation(pending)
    execute = human._execute

    def competing_decision(record):
        competing = SuppliedDecisionInput.from_choice(
            {**request, "decision": "skip", "note": "Separate current human choice."}
        )
        require_opportunity_result(
            create_opportunity_service(root).record_decision(competing, actor="local_user")
        )
        return execute(record)

    monkeypatch.setattr(human, "_execute", competing_decision)
    with pytest.raises(CompanionError, match="execution_unknown"):
        human.confirm(pending, token)
    current = require_opportunity_result(
        create_opportunity_service(root).resolve_supplied("fictional_role")
    )
    assert current.latest_decision["decision"] == "skip"
    assert current.latest_decision["sequence"] == 1


def test_confirmed_decision_and_interview_prepare_match_public_services_and_current_reads(
    connected,
    monkeypatch,
):
    root, _, _, human, service = connected
    assessment = staged_assessment(connected)
    human.confirm(assessment, human.issue_confirmation(assessment))
    ref, dossier = human.inspect("opportunity", "fictional_role")
    human.grant(ref, ("source_hash", "evaluation_fingerprint"), "resume_work")
    decision = {
        "opportunity_id": "fictional_role",
        "source_hash": dossier["source_hash"],
        "evaluation_fingerprint": dossier["evaluation_fingerprint"],
        "decision": "pursue",
        "reason_code": "mandate_fit",
        "note": "Fictional decision for preparation only.",
    }
    pending = service.call(
        "decision", {"request": decision, "idempotency_key": "interview_choice"}
    )["pending_id"]
    choice_receipt = human.confirm(pending, human.issue_confirmation(pending))
    decision_pending = pending
    assert (
        choice_receipt["decision"] == "pursue"
        and choice_receipt["external_event_confirmed"] is False
    )
    ref, _ = human.inspect("opportunity", "fictional_role")
    human.grant(ref, ("latest_decision", "decision_current"), "prepare_interview")
    grant_evidence(human, "prepare_interview")
    request = InterviewRequest(
        "fictional_role",
        "round_one",
        "Fictional round",
        "hiring_manager",
        ("synthetic-responsibility",),
    )
    direct = require_interview_result(create_interview_service(root).plan(request)).to_dict()
    pending = service.call(
        "interview", {"request": request.to_dict(), "idempotency_key": "interview_one"}
    )["pending_id"]
    assert human.pending_review(pending)["preview"] == direct
    assert not (root / "applications/fictional_role").exists()
    receipt = human.confirm(pending, human.issue_confirmation(pending))
    ref, content = human.inspect("interview", receipt["object_id"])
    human.grant(ref, ("plan", "summary"), "resume_work")
    actual = service.call(
        "read",
        {
            "ref": ref.to_dict(),
            "fields": ["plan", "summary"],
            "purpose": "resume_work",
            "historical": False,
        },
    )
    assert actual["data"]["plan"] == direct
    assert actual["data"]["summary"]["current"] is True
    assert "relative_path" not in actual["data"]["summary"]
    human.lifecycle_confirm(human.lifecycle_preview("upgrade"))
    now = int(time.time())
    monkeypatch.setattr(companion_storage.time, "time", lambda: now + 30 * 86400)
    human.lifecycle_confirm(human.lifecycle_preview("retire", (decision_pending, pending)))
    assert human.recover(decision_pending) == choice_receipt and human.recover(pending) == receipt

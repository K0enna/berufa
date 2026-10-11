from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from career_os.application.instance_data import InstanceDataService, UserDataExportReceipt
from career_os.application.opportunities import require_opportunity_result
from career_os.application.recovery import recovery_json
from career_os.evidence_vault import EvidenceVault
from career_os.infrastructure import evidence_storage, instance_data, instances, recovery
from career_os.infrastructure.documents import create_document_service, inspect_document_archive
from career_os.infrastructure.instance_data import create_instance_data_service
from career_os.infrastructure.instances import create_instance_service
from career_os.infrastructure.opportunities import create_opportunity_service
from career_os.infrastructure.recovery import INCOMPLETE, create_recovery_service
from career_os.infrastructure.tasks import create_task_service
from career_os.instance_workflow import main
from career_os.public_api import (
    DocumentBasis,
    DocumentImportRequest,
    DocumentReviewRequest,
    EventRequest,
    SubmissionInput,
    TaskDue,
    require_outcome_result,
)
from career_os.synthetic_evidence import seed_synthetic_evidence
from career_os.synthetic_role_pack_fixtures import SYNTHETIC_POSTING_PROFILE


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "original"
    create_instance_service(root).initialize(actor="local_user")
    vault = EvidenceVault(root)
    vault.record_consent(accepted=True)
    _, proposed = seed_synthetic_evidence(vault)
    first = proposed[0]
    vault.review(
        first.evidence_id,
        first.revision,
        action="approve",
        expected_content_hash=first.content_hash,
    )
    revised = vault.edit(
        first.evidence_id, expected_revision=first.revision, statement=first.statement
    )
    vault.review(
        revised.evidence_id,
        revised.revision,
        action="defer",
        expected_content_hash=revised.content_hash,
    )
    (root / "inbox").mkdir(exist_ok=True)
    (root / "inbox" / "supplied.txt").write_bytes(b"Synthetic retained local source\n")
    (root / "exports" / "empty").mkdir(parents=True, exist_ok=True)
    return root


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _history(database: Path) -> list[str]:
    with sqlite3.connect(database) as connection:
        return [line for line in connection.iterdump() if "instance_root_hash" not in line]


def _backup(workspace: Path, tmp_path: Path) -> Path:
    backup = tmp_path / "backup"
    create_recovery_service(workspace).backup(str(backup), offline=True, actor="local_user")
    return backup


def _persist_supplied_opportunity(workspace: Path) -> tuple[bytes, Path, Path]:
    vault = EvidenceVault(workspace)
    while queue := vault.review_queue():
        card = queue[0]
        vault.review(
            card.evidence_id,
            card.revision,
            action="reject" if card.kind == "forbidden_claim" else "approve",
            expected_content_hash=card.content_hash,
        )
    request = {
        "schema_version": 1,
        "authority": "user_supplied",
        "opportunity_id": "synthetic_archived_role",
        "company": "Fictional Archive GmbH",
        "title": "Archived Operations Lead",
        "location": "Germany",
        "work_model": "hybrid",
        "source_url": "https://example.invalid/jobs/archived-role",
        "posting_text": "A wholly fictional retained posting for archive verification.",
        "role_signals": list(SYNTHETIC_POSTING_PROFILE.signals),
        "requirements": [
            {
                "requirement_id": item.requirement_id,
                "level": item.level,
                "statement": item.statement,
            }
            for item in SYNTHETIC_POSTING_PROFILE.requirements
        ],
        "evidence_routes": {
            "direct": {
                "post_sales_team_leadership": ["synthetic-responsibility"],
                "service_operating_reviews": ["synthetic-responsibility"],
                "onboarding_improvement": ["synthetic-achievement"],
            },
            "transferable": {"completed_degree": ["synthetic-education"]},
            "gaps": {"new_logo_ae_leadership": ["synthetic-gap"]},
        },
        "constraints": {
            "preferred_location": "Germany",
            "preferred_work_model": "hybrid",
            "maximum_travel_percent": 25,
            "currency": "EUR",
        },
        "mapping_confirmed_by": "local_user",
    }
    source = json.dumps(request, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    opportunities = create_opportunity_service(workspace)
    preview = require_opportunity_result(opportunities.evaluate_supplied(source))
    require_opportunity_result(
        opportunities.evaluate_supplied(
            source,
            confirm_fingerprint=preview.evaluation_fingerprint,
            actor="local_user",
        )
    )
    directory = workspace / "data/runtime/opportunities/synthetic_archived_role"
    return source, directory / "supplied-request.json", directory / "decision-dossier.json"


def test_task_history_restores_security_binding_only_and_exports_all_revisions(workspace, tmp_path):
    _persist_supplied_opportunity(workspace)
    tasks = create_task_service(workspace)
    first = tasks.create(
        opportunity_id="synthetic_archived_role",
        description="Review fictional scope",
        due=TaskDue("date", "2026-10-06", "Europe/Berlin"),
        request_id="recovery-create",
        actor="local_user",
    )
    completed = tasks.set_status(
        first.record.task_id,
        status="completed",
        expected_revision=1,
        request_id="recovery-complete",
        actor="local_user",
    )
    retained = tasks.list()
    ledger = workspace / recovery.TASK_LEDGER
    original = json.loads(ledger.read_bytes())
    backup = _backup(workspace, tmp_path)
    target = tmp_path / "tasks-restored"
    service = create_recovery_service(target)
    preview = service.restore(str(backup))
    assert preview.to_dict()["rebinds_planned_task_instance"] is True
    assert preview.to_dict()["rebinds_evidence_instance_only"] is False
    service.restore(
        str(backup),
        confirmation_fingerprint=preview.confirmation_fingerprint,
        offline=True,
        actor="local_user",
    )
    assert create_task_service(target).list() == retained
    rebound = json.loads((target / recovery.TASK_LEDGER).read_bytes())
    assert rebound["instance_hash"] != original["instance_hash"]
    assert {k: v for k, v in rebound.items() if k != "instance_hash"} == {
        k: v for k, v in original.items() if k != "instance_hash"
    }
    retry = create_task_service(target).set_status(
        first.record.task_id,
        status="completed",
        expected_revision=1,
        request_id="recovery-complete",
        actor="local_user",
    )
    assert retry.record == completed.record and not retry.changed
    for mode in ("strict", "historical"):
        destination = tmp_path / f"tasks-export-{mode}.json"
        receipt = create_instance_data_service(target).export_user_data(
            str(destination),
            historical_archive=mode == "historical",
            offline=True,
            actor="local_user",
        )
        exported_file = destination / instance_data.EXPORT_FILE
        payload = json.loads(exported_file.read_bytes())
        assert dict(receipt.record_counts)["planned_task_revisions"] == 2
        assert payload["data"]["planned_task_history"] == [r.to_dict() for r in retained.history]
        assert "instance_hash" not in exported_file.read_text()
    assert not (target / "data/runtime/outcome-learning-v2.jsonl").exists()


def test_corrupt_task_history_refuses_backup_and_doctor_preserves_bytes(workspace, tmp_path):
    runtime = workspace / "data/runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    ledger = runtime / "planned-tasks.json"
    ledger.write_bytes(b'{"private-description":"do not print"}')
    with pytest.raises(ValueError):
        create_recovery_service(workspace).backup(
            str(tmp_path / "refused"), offline=True, actor="local_user"
        )
    report = create_instance_service(workspace).doctor()
    assert not report.ready
    assert "do not print" not in json.dumps(report.to_dict())
    assert ledger.read_bytes() == b'{"private-description":"do not print"}'


def test_document_restore_preserves_all_versions_reviews_and_filtered_export(
    workspace, tmp_path, monkeypatch
):
    from test_document_intake import _write_text_pdf

    _persist_supplied_opportunity(workspace)
    vault = EvidenceVault(workspace)
    revision = next(r for r in vault.enumerate() if r.status == "approved")
    basis = (DocumentBasis(revision.evidence_id, revision.revision, revision.content_hash),)
    pdf = tmp_path / "fictional-draft.pdf"
    _write_text_pdf(pdf, "Complete fictional draft with retained qualification")
    documents = create_document_service(workspace)
    first = documents.import_version(
        DocumentImportRequest(
            "restore-document",
            "Fictional CV",
            "cv",
            "Private local provenance",
            {"md": b"Fictional complete wording", "pdf": pdf.read_bytes()},
            basis=basis,
        ),
        actor="local_user",
    )
    request = DocumentReviewRequest(
        first.ref,
        "accepted",
        "Internal review note",
        (hashlib.sha256(pdf.read_bytes()).hexdigest(),),
    )
    preview = documents.review(request)
    documents.review(
        request, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
    )
    second = documents.import_version(
        DocumentImportRequest(
            "restore-revision",
            "Fictional CV revision",
            "cv",
            "Retained correction",
            {"md": b"Revised fictional wording"},
            basis=basis,
            document_id=first.ref.document_id,
        ),
        actor="local_user",
    )
    request = DocumentReviewRequest(
        second.ref, "changes_requested", "Retain this rejected wording in my own archive"
    )
    preview = documents.review(request)
    documents.review(
        request, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
    )
    retained = inspect_document_archive(workspace)
    from career_os.infrastructure.outcomes import create_event_service

    dossier = require_opportunity_result(
        create_opportunity_service(workspace).resolve_supplied("synthetic_archived_role")
    ).dossier
    events = create_event_service(workspace)
    submission = EventRequest(
        "submission_confirmed",
        dossier.opportunity_id,
        "2026-07-01T10:00:00+00:00",
        SubmissionInput(
            dossier.opportunity_id,
            "Fictional Archive GmbH",
            "Archived Operations Lead",
            "user_supplied",
            "employer_ats",
            "candidate_confirmed_sent",
            dossier.source_hash,
            assessment_basis={
                "kind": "qualitative",
                "source_hash": dossier.source_hash,
                "evaluation_fingerprint": dossier.evaluation_fingerprint,
            },
        ),
    )
    proposal = require_outcome_result(events.propose(submission))
    event = require_outcome_result(
        events.confirm(
            submission,
            confirmation_fingerprint=proposal.confirmation_fingerprint,
            actor="local_user",
        )
    ).event
    event_bytes = (workspace / "data/runtime/outcome-learning-v2.jsonl").read_bytes()
    backup = _backup(workspace, tmp_path)
    target = tmp_path / "documents-restored"
    service = create_recovery_service(target)
    preview = service.restore(str(backup))
    service.restore(
        str(backup),
        confirmation_fingerprint=preview.confirmation_fingerprint,
        offline=True,
        actor="local_user",
    )
    assert inspect_document_archive(target) == retained
    assert (target / "data/runtime/outcome-learning-v2.jsonl").read_bytes() == event_bytes
    assert require_outcome_result(create_event_service(target).list()) == (event,)
    assert create_document_service(target).inspect(first.ref).basis_current is True
    assert create_document_service(target).inspect(second.ref).review_state == "changes_requested"
    for historical in (False, True):
        destination = tmp_path / f"document-export-{historical}"
        receipt = create_instance_data_service(target).export_user_data(
            str(destination), offline=True, actor="local_user", historical_archive=historical
        )
        payload = json.loads((destination / instance_data.EXPORT_FILE).read_bytes())
        assert dict(receipt.record_counts)["content_document_versions"] == 2
        assert dict(receipt.record_counts)["confirmed_outcome_events"] == 1
        assert payload["data"]["confirmed_outcomes"] == [event.to_dict()]
        assert payload["data"]["content_document_reviews"] == list(retained.reviews)
        exported = next(
            v
            for v in payload["data"]["content_document_versions"]
            if v["ref"] == first.ref.to_dict()
        )
        assert base64.b64decode(exported["artifacts"]["pdf"]["content_base64"]) == pdf.read_bytes()
        assert any(
            v["ref"] == second.ref.to_dict() for v in payload["data"]["content_document_versions"]
        )
    source_before, backup_before = _files(workspace), _files(backup)
    monkeypatch.setattr(
        recovery.shutil, "rmtree", lambda *_a, **_kw: pytest.fail("No recursive removal")
    )
    deletion = create_instance_data_service(target)
    preview = deletion.delete()
    deletion.delete(
        confirmation_fingerprint=preview.confirmation_fingerprint, offline=True, actor="local_user"
    )
    assert (
        not target.exists()
        and _files(workspace) == source_before
        and _files(backup) == backup_before
    )


def test_complete_backup_restore_rebinds_only_instance_and_preserves_all_history(
    workspace: Path, tmp_path: Path
) -> None:
    before = _files(workspace)
    service = create_recovery_service(workspace)
    inventory = service.inventory()
    backup = _backup(workspace, tmp_path)
    assert service.verify(str(backup)) == inventory
    assert _files(backup / "workspace") == before
    restored_root = tmp_path / "restored"
    restored = create_recovery_service(restored_root)
    preview = restored.restore(str(backup))
    assert preview.to_dict()["rebinds_evidence_instance_only"] is True
    assert preview.to_dict()["rebinds_planned_task_instance"] is False
    assert not restored_root.exists()
    result = restored.restore(
        str(backup),
        confirmation_fingerprint=preview.confirmation_fingerprint,
        offline=True,
        actor="local_user",
    )
    assert result.evidence_instance_rebound
    assert result.backup_fingerprint == inventory.fingerprint
    after = _files(restored_root)
    assert {k: v for k, v in after.items() if k != recovery.DATABASE} == {
        k: v for k, v in before.items() if k != recovery.DATABASE
    }
    assert _history(restored_root / recovery.DATABASE) == _history(workspace / recovery.DATABASE)
    original_vault, restored_vault = EvidenceVault(workspace), EvidenceVault(restored_root)
    assert restored_vault.has_consent()
    assert restored_vault.sources() == original_vault.sources()
    assert restored_vault.audit_events() == original_vault.audit_events()
    for item in original_vault.enumerate():
        assert restored_vault.revisions(item.evidence_id) == original_vault.revisions(
            item.evidence_id
        )
    assert restored_vault.context_fingerprints() == original_vault.context_fingerprints()
    assert (restored_root / "exports" / "empty").is_dir()
    assert (restored_root / instances.LAYOUT_MARKER).read_bytes() == instances.LAYOUT_MARKER_CONTENT
    with sqlite3.connect(restored_vault.path) as connection:
        assert (
            dict(connection.execute("SELECT key, value FROM vault_meta"))[
                "instance_layout_identity"
            ]
            == evidence_storage.INSTANCE_LAYOUT_IDENTITY
        )
    restored_vault.record_consent(accepted=False)
    assert original_vault.has_consent()
    assert _files(workspace) == before
    assert service.verify(str(backup)) == inventory


def test_recovery_supports_state_before_evidence_onboarding(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "offer-request.json").write_bytes(b"{}\n")
    backup = _backup(root, tmp_path)
    target = create_recovery_service(tmp_path / "restored")
    preview = target.restore(str(backup))
    result = target.restore(
        str(backup),
        confirmation_fingerprint=preview.confirmation_fingerprint,
        offline=True,
        actor="local_user",
    )
    assert not result.evidence_instance_rebound
    assert _files(tmp_path / "restored") == _files(root)


@pytest.mark.parametrize(
    "actor,offline", [("model", True), ("local_user", False), ("local_user", 1)]
)
def test_backup_requires_explicit_offline_local_user(
    workspace: Path, tmp_path: Path, actor: str, offline: bool
) -> None:
    with pytest.raises(PermissionError):
        create_recovery_service(workspace).backup(
            str(tmp_path / "backup"), offline=offline, actor=actor
        )
    assert not (tmp_path / "backup").exists()


def test_restore_confirmation_binds_backup_and_destination(workspace: Path, tmp_path: Path) -> None:
    backup = _backup(workspace, tmp_path)
    first = create_recovery_service(tmp_path / "first")
    preview = first.restore(str(backup))
    second = create_recovery_service(tmp_path / "second")
    with pytest.raises(PermissionError):
        second.restore(
            str(backup),
            confirmation_fingerprint=preview.confirmation_fingerprint,
            offline=True,
            actor="local_user",
        )
    manifest_path = backup / recovery.MANIFEST
    value = json.loads(manifest_path.read_bytes())
    data = backup / "workspace/inbox/supplied.txt"
    data.write_bytes(b"Changed synthetic input\n")
    for row in value["entries"]:
        if row["path"] == "inbox/supplied.txt":
            row.update(asdict(recovery._file_entry(data, row["path"])))
    manifest_path.write_bytes(recovery_json(value))
    with pytest.raises(PermissionError):
        first.restore(
            str(backup),
            confirmation_fingerprint=preview.confirmation_fingerprint,
            offline=True,
            actor="local_user",
        )
    assert not (tmp_path / "first").exists()
    assert not (tmp_path / "second").exists()


@pytest.mark.parametrize(
    "damage", ["content", "missing", "extra", "manifest", "traversal", "duplicate", "marker"]
)
def test_invalid_backup_never_creates_target(workspace: Path, tmp_path: Path, damage: str) -> None:
    backup = _backup(workspace, tmp_path)
    manifest_path = backup / recovery.MANIFEST
    if damage == "content":
        (backup / "workspace/inbox/supplied.txt").write_bytes(b"tampered")
    elif damage == "missing":
        (backup / "workspace/inbox/supplied.txt").unlink()
    elif damage == "extra":
        (backup / "workspace/extra.txt").write_bytes(b"extra")
    elif damage == "marker":
        (backup / INCOMPLETE).write_bytes(b"incomplete")
    else:
        value = json.loads(manifest_path.read_bytes())
        if damage == "manifest":
            value["schema_version"] = 99
        elif damage == "traversal":
            value["entries"][0]["path"] = "../outside"
        else:
            value["entries"].append(value["entries"][0])
        manifest_path.write_bytes(recovery_json(value))
    target = tmp_path / "restored"
    with pytest.raises((ValueError, OSError)):
        create_recovery_service(target).restore(str(backup))
    assert not target.exists()


@pytest.mark.parametrize(
    "state", ["locked", "foreign", "newer", "corrupt", "sidecar", "other_database", "incomplete"]
)
def test_unsafe_workspace_is_preserved_without_backup(
    workspace: Path, tmp_path: Path, state: str
) -> None:
    database = workspace / recovery.DATABASE
    connection = None
    if state == "locked":
        connection = sqlite3.connect(database)
        connection.execute("BEGIN IMMEDIATE")
    elif state in {"foreign", "newer"}:
        with sqlite3.connect(database) as editing:
            key, value = (
                ("instance_root_hash", "foreign")
                if state == "foreign"
                else ("schema_version", "99")
            )
            editing.execute("UPDATE vault_meta SET value = ? WHERE key = ?", (value, key))
    elif state == "corrupt":
        database.write_bytes(b"not SQLite")
    elif state == "sidecar":
        database.with_name(database.name + "-journal").write_bytes(b"pending")
    elif state == "other_database":
        (workspace / "unknown.db").write_bytes(database.read_bytes())
    else:
        (workspace / INCOMPLETE).write_bytes(b"incomplete")
    before = _files(workspace)
    try:
        with pytest.raises((ValueError, OSError)):
            _backup(workspace, tmp_path)
    finally:
        if connection is not None:
            connection.close()
    assert _files(workspace) == before
    assert not (tmp_path / "backup").exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "ancestor"])
def test_recovery_refuses_linked_state(workspace: Path, tmp_path: Path, kind: str) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"retained outside")
    root = workspace
    try:
        if kind == "hardlink":
            os.link(outside, workspace / "linked.txt")
        elif kind == "symlink":
            (workspace / "linked.txt").symlink_to(outside)
        else:
            root = tmp_path / "linked-root"
            root.symlink_to(workspace, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit this link type")
    with pytest.raises((ValueError, OSError)):
        _backup(root, tmp_path)
    assert outside.read_bytes() == b"retained outside"


def test_existing_and_nested_targets_are_never_overwritten(workspace: Path, tmp_path: Path) -> None:
    service = create_recovery_service(workspace)
    for target in (workspace, workspace / "nested", tmp_path):
        with pytest.raises(ValueError):
            service.backup(str(target), offline=True, actor="local_user")
    backup = _backup(workspace, tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError):
        create_recovery_service(existing).restore(str(backup))
    assert not list(existing.iterdir())
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    with pytest.raises(ValueError):
        create_recovery_service(repository / "private")


def test_low_space_and_failed_copy_preserve_source(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _files(workspace)
    with monkeypatch.context() as limited:
        limited.setattr(recovery.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
        with pytest.raises(OSError, match="space"):
            _backup(workspace, tmp_path)
    assert not (tmp_path / "backup").exists()
    original_copy = recovery._copy

    def fail_copy(source, target, entries):
        original_copy(source, target, entries[:1])
        raise OSError("simulated interrupted write")

    monkeypatch.setattr(recovery, "_copy", fail_copy)
    with pytest.raises(OSError):
        _backup(workspace, tmp_path)
    assert (tmp_path / "backup" / INCOMPLETE).is_file()
    assert _files(workspace) == before
    with pytest.raises(ValueError, match="incomplete"):
        create_recovery_service(workspace).verify(str(tmp_path / "backup"))


def test_restore_rebind_failure_retains_marked_target_and_unchanged_backup(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = _backup(workspace, tmp_path)
    before = _files(backup)
    target = tmp_path / "restored"
    service = create_recovery_service(target)
    preview = service.restore(str(backup))

    def fail_rebind(connection, *_):
        connection.execute("BEGIN EXCLUSIVE")
        connection.execute("DELETE FROM evidence_events")
        raise ValueError("simulated transaction failure")

    monkeypatch.setattr(recovery, "rebind_restored_storage", fail_rebind)
    with pytest.raises(ValueError):
        service.restore(
            str(backup),
            confirmation_fingerprint=preview.confirmation_fingerprint,
            offline=True,
            actor="local_user",
        )
    assert (target / INCOMPLETE).is_file()
    assert _history(target / recovery.DATABASE) == _history(workspace / recovery.DATABASE)
    assert _files(backup) == before


def test_cli_json_and_content_minimized_failure(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--instance-root", str(workspace), "inventory"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["backup_fingerprint"]) == 64
    assert (
        main(
            ["--instance-root", str(workspace), "backup", "--destination", str(tmp_path / "backup")]
        )
        == 1
    )
    failure = capsys.readouterr()
    assert not failure.out
    assert "instance operation failed" in failure.err
    assert str(workspace) not in failure.err


def test_changed_source_and_limits_never_produce_a_complete_backup(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = create_recovery_service(workspace)
    for constant in ("MAX_ENTRIES", "MAX_FILE_BYTES", "MAX_TOTAL_BYTES", "MAX_MANIFEST_BYTES"):
        with monkeypatch.context() as limited:
            limited.setattr(recovery, constant, 1)
            with pytest.raises(ValueError):
                _backup(workspace, tmp_path)
        assert not (tmp_path / "backup").exists()
    original_copy = recovery._copy

    def changed_source(source, target, entries):
        original_copy(source, target, entries)
        (source / "new-file.txt").write_bytes(b"Another writer is still active")

    monkeypatch.setattr(recovery, "_copy", changed_source)
    with pytest.raises(ValueError, match="changed"):
        _backup(workspace, tmp_path)
    assert (tmp_path / "backup" / INCOMPLETE).is_file()
    with pytest.raises(ValueError, match="incomplete"):
        service.verify(str(tmp_path / "backup"))


def test_restore_does_not_execute_schema_trigger(workspace: Path, tmp_path: Path) -> None:
    with sqlite3.connect(workspace / recovery.DATABASE) as connection:
        connection.execute(
            "CREATE TRIGGER erase_history AFTER UPDATE ON vault_meta "
            "BEGIN DELETE FROM evidence_events; END"
        )
    history = _history(workspace / recovery.DATABASE)

    with pytest.raises(ValueError, match="schema"):
        _backup(workspace, tmp_path)

    assert not (tmp_path / "backup").exists()
    assert _history(workspace / recovery.DATABASE) == history


def test_restore_checks_copied_bytes_before_rebinding(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = _backup(workspace, tmp_path)
    target = tmp_path / "restored"
    service = create_recovery_service(target)
    preview = service.restore(str(backup))
    original_copy = recovery._copy

    def damaged_copy(source, destination, entries):
        original_copy(source, destination, entries)
        (destination / "inbox/supplied.txt").write_bytes(b"simulated damaged copy")

    monkeypatch.setattr(recovery, "_copy", damaged_copy)
    with pytest.raises(ValueError, match="Restored files"):
        service.restore(
            str(backup),
            confirmation_fingerprint=preview.confirmation_fingerprint,
            offline=True,
            actor="local_user",
        )
    assert (target / INCOMPLETE).is_file()
    assert (target / recovery.DATABASE).read_bytes() == (workspace / recovery.DATABASE).read_bytes()


@pytest.mark.parametrize("name", ["CONIN$", "CONOUT$", "COM\u00b9", "LPT\u00b3.txt", "CON .txt"])
def test_portable_inventory_refuses_all_windows_device_names(name: str) -> None:
    with pytest.raises(ValueError, match="nonportable"):
        recovery._portable_path("inbox/" + name)


def test_verify_and_restore_read_only_backup(workspace: Path, tmp_path: Path) -> None:
    backup = _backup(workspace, tmp_path)
    database = backup / "workspace" / recovery.DATABASE
    before = _files(backup)
    database.chmod(0o400)
    try:
        service = create_recovery_service(tmp_path / "restored")
        inventory = service.verify(str(backup))
        preview = service.restore(str(backup))
        result = service.restore(
            str(backup),
            confirmation_fingerprint=preview.confirmation_fingerprint,
            offline=True,
            actor="local_user",
        )
        assert result.backup_fingerprint == inventory.fingerprint
        assert _files(backup) == before
        assert (
            EvidenceVault(tmp_path / "restored").audit_events()
            == EvidenceVault(workspace).audit_events()
        )
    finally:
        database.chmod(0o600)


@pytest.mark.parametrize("operation", ["backup", "restore"])
@pytest.mark.parametrize("retained", [None, "other-file.txt", INCOMPLETE])
def test_marker_failure_removes_only_empty_new_destination(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    retained: str | None,
) -> None:
    backup = _backup(workspace, tmp_path)
    target = tmp_path / "target"
    service = create_recovery_service(target)
    preview = service.restore(str(backup))
    original = recovery._write_new

    def fail_marker(path, content):
        if path == target / INCOMPLETE:
            if retained is not None:
                (target / retained).write_bytes(b"retained despite marker failure")
            raise OSError("simulated marker quota failure")
        original(path, content)

    monkeypatch.setattr(recovery, "_write_new", fail_marker)
    with pytest.raises(OSError, match="marker"):
        if operation == "backup":
            create_recovery_service(workspace).backup(str(target), offline=True, actor="local_user")
        else:
            service.restore(
                str(backup),
                confirmation_fingerprint=preview.confirmation_fingerprint,
                offline=True,
                actor="local_user",
            )
    if retained is None:
        assert not target.exists()
    else:
        assert (target / retained).read_bytes() == b"retained despite marker failure"
    assert _files(backup / "workspace") == _files(workspace)


def test_initialize_is_empty_private_isolated_and_never_overwrites(tmp_path: Path) -> None:
    assert instances.PRIVATE_IGNORE == (
        b"# Private Career OS workspace: do not commit candidate data.\n*\n!.gitignore\n"
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (first, second):
        service = create_instance_service(root)
        with pytest.raises(PermissionError):
            service.initialize(actor="agent")
        assert not root.exists()
        receipt = service.initialize(actor="local_user")
        assert receipt.changed and receipt.evidence_storage_version == 4
        assert all((root / name).is_dir() for name in instances.LAYOUT)
        assert (root / ".gitignore").read_bytes() == instances.PRIVATE_IGNORE
        assert (root / instances.LAYOUT_MARKER).read_bytes() == instances.LAYOUT_MARKER_CONTENT
        assert not (root / INCOMPLETE).exists()
        vault = EvidenceVault(root)
        assert not vault.has_consent()
        assert vault.sources() == vault.enumerate() == vault.audit_events() == ()
        before = _files(root)
        with pytest.raises(ValueError, match="new workspace"):
            service.initialize(actor="local_user")
        assert _files(root) == before
    EvidenceVault(first).record_consent(accepted=True)
    assert not EvidenceVault(second).has_consent()


@pytest.mark.parametrize("failure", ["runtime", "space", "write"])
def test_init_failure_preserves_existing_data_and_marks_partial_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    root = tmp_path / "workspace"
    retained = tmp_path / "existing.txt"
    retained.write_bytes(b"Synthetic retained input")
    if failure == "runtime":
        monkeypatch.setattr(instances, "_runtime_supported", lambda: False)
    elif failure == "space":
        monkeypatch.setattr(recovery.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    else:
        original = recovery._write_new

        def fail_write(path, content):
            if path.name == ".gitignore":
                raise OSError("Synthetic private exception: never print this")
            original(path, content)

        monkeypatch.setattr(recovery, "_write_new", fail_write)
    with pytest.raises((ValueError, OSError)):
        create_instance_service(root).initialize(actor="local_user")
    assert retained.read_bytes() == b"Synthetic retained input"
    assert (root / INCOMPLETE).exists() if failure == "write" else not root.exists()


@pytest.mark.parametrize(
    "state",
    [
        "ready",
        "missing",
        "partial",
        "layout",
        "ignore",
        "marker",
        "metadata",
        "table",
        "column",
        "constraint",
        "constraint_comment",
        "foreign_key_action",
        "events",
        "corrupt",
        "locked",
        "unsafe",
    ],
)
def test_doctor_reports_bounded_status_without_private_content_or_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    from career_os.application.instances import DoctorCheck

    monkeypatch.setattr(
        instances,
        "_environment_checks",
        lambda: (
            DoctorCheck("runtime", "ok"),
            DoctorCheck("package", "ok"),
            DoctorCheck("dependencies", "ok"),
        ),
    )
    root = tmp_path / "private-sensitive-name"
    service = create_instance_service(root)
    connection = None
    if state != "missing":
        service.initialize(actor="local_user")
        (root / "inbox" / "private-document.txt").write_bytes(b"Sensitive synthetic career text")
    if state == "partial":
        (root / INCOMPLETE).write_bytes(b"Synthetic interruption")
    elif state == "layout":
        (root / "exports").rmdir()
    elif state == "ignore":
        (root / ".gitignore").unlink()
    elif state == "marker":
        (root / instances.LAYOUT_MARKER).unlink()
    elif state == "metadata":
        with sqlite3.connect(root / recovery.DATABASE) as mutation:
            mutation.execute(
                "UPDATE vault_meta SET value = 'unsupported-layout' "
                "WHERE key = 'instance_layout_identity'"
            )
    elif state == "table":
        with sqlite3.connect(root / recovery.DATABASE) as mutation:
            mutation.execute("DROP TABLE source_documents")
    elif state == "column":
        with sqlite3.connect(root / recovery.DATABASE) as mutation:
            mutation.execute("ALTER TABLE source_documents ADD COLUMN unsupported TEXT")
    elif state == "constraint":
        with sqlite3.connect(root / recovery.DATABASE) as mutation:
            mutation.execute("DROP TABLE consent_receipts")
            mutation.execute(
                "CREATE TABLE consent_receipts ("
                "receipt_id TEXT PRIMARY KEY, boundary_version TEXT NOT NULL, "
                "accepted INTEGER NOT NULL, actor TEXT NOT NULL, occurred_at TEXT NOT NULL)"
            )
    elif state == "constraint_comment":
        with sqlite3.connect(root / recovery.DATABASE) as mutation:
            mutation.execute("DROP TABLE consent_receipts")
            mutation.execute(
                "CREATE TABLE consent_receipts ("
                "receipt_id TEXT PRIMARY KEY, boundary_version TEXT NOT NULL, "
                "accepted INTEGER NOT NULL, actor TEXT NOT NULL, occurred_at TEXT NOT NULL) "
                "/* CHECK (accepted IN (0, 1)) */"
            )
            mutation.execute(
                "INSERT INTO consent_receipts VALUES (?, ?, ?, ?, ?)",
                ("invalid", "local-private-v1", 2, "local_user", "2026-01-01T00:00:00Z"),
            )
    elif state == "foreign_key_action":
        with sqlite3.connect(root / recovery.DATABASE) as mutation:
            mutation.execute("DROP TABLE evidence_events")
            mutation.execute(
                "CREATE TABLE evidence_events ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, "
                "evidence_id TEXT NOT NULL, revision INTEGER NOT NULL, action TEXT NOT NULL, "
                "actor TEXT NOT NULL, occurred_at TEXT NOT NULL, content_hash TEXT NOT NULL, "
                "reason TEXT, FOREIGN KEY (evidence_id, revision) "
                "REFERENCES evidence_revisions(evidence_id, revision) ON DELETE CASCADE)"
            )
    elif state == "corrupt":
        (root / recovery.DATABASE).write_bytes(b"Sensitive corrupt bytes")
    elif state == "events":
        (root / "data/runtime/outcome-learning-v2.jsonl").write_bytes(b'{"schema_version":99}\n')
    elif state == "locked":
        connection = sqlite3.connect(root / recovery.DATABASE)
        connection.execute("BEGIN EXCLUSIVE")
    elif state == "unsafe":
        (root / ".git").mkdir()
    before = _files(root)
    try:
        report = service.doctor()
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()
    assert report.ready is (state == "ready")
    rendered = json.dumps(report.to_dict()).lower()
    for private in (str(root).lower(), "sensitive", "private-document", "corrupt bytes"):
        assert private not in rendered
    assert _files(root) == before
    assert not root.exists() if state == "missing" else root.exists()
    with pytest.raises(ValueError, match="Listener port"):
        service.doctor(listener_port=0)


def test_doctor_environment_reports_missing_dependencies_and_cli_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed = []

    def dependency_check(args, **kwargs):
        observed.append((args, kwargs))
        return SimpleNamespace(
            returncode=1,
            stdout="rolevane 0.1.0 requires pypdf, which is not installed.\n",
        )

    monkeypatch.setattr(instances.metadata, "version", lambda name: "0.1.0")
    monkeypatch.setattr(instances.subprocess, "run", dependency_check)
    root = tmp_path / "private-workspace"
    assert main(["--instance-root", str(root), "init"]) == 0
    capsys.readouterr()
    assert main(["--instance-root", str(root), "doctor"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert {item["name"]: item["status"] for item in result["checks"]}[
        "dependencies"
    ] == "missing_or_incompatible"
    assert observed[0][0][-1] == "check"
    assert observed[0][1]["stdout"] == instances.subprocess.PIPE
    assert observed[0][1]["stderr"] == instances.subprocess.DEVNULL
    monkeypatch.setattr(
        instances.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="unrelated-package 1.0 requires missing-package, which is not installed.\n",
        ),
    )
    assert main(["--instance-root", str(root), "doctor"]) == 0
    unrelated = json.loads(capsys.readouterr().out)
    assert {item["name"]: item["status"] for item in unrelated["checks"]}["dependencies"] == "ok"
    assert main(["--instance-root", str(root), "init"]) == 1
    failure = capsys.readouterr()
    assert failure.out == "" and str(root) not in failure.err


def test_user_data_export_is_complete_filtered_checksummed_and_read_only(
    workspace: Path, tmp_path: Path
) -> None:
    secret = "synthetic-provider-token-must-not-export"
    config = workspace / "config"
    config.mkdir()
    (config / "credentials.json").write_text(json.dumps({"token": secret}), encoding="utf-8")
    before = _files(workspace)
    destination = tmp_path / "user-export"
    service = create_instance_data_service(workspace)
    with pytest.raises(PermissionError):
        service.export_user_data(str(destination), offline=False, actor="local_user")
    with pytest.raises(PermissionError):
        service.export_user_data(str(destination), offline=True, actor="agent")
    assert not destination.exists()

    receipt = service.export_user_data(str(destination), offline=True, actor="local_user")
    content = (destination / instance_data.EXPORT_FILE).read_bytes()
    payload = json.loads(content)
    vault = EvidenceVault(workspace)
    exported = payload["data"]["evidence_vault"]
    expected_revisions = sorted(
        (
            asdict(revision)
            for item in vault.enumerate()
            for revision in vault.revisions(item.evidence_id)
        ),
        key=lambda item: (item["evidence_id"], item["revision"]),
    )

    assert receipt.sha256 == hashlib.sha256(content).hexdigest()
    assert receipt.size_bytes == len(content)
    assert exported["evidence_revisions"] == json.loads(json.dumps(expected_revisions))
    assert exported["audit_events"] == [asdict(item) for item in vault.audit_events()]
    assert len(exported["consent_receipts"]) == 1
    assert len(exported["sources"]) == len(vault.sources())
    for source, item in zip(vault.sources(), exported["sources"], strict=True):
        assert item["metadata"] == json.loads(json.dumps(asdict(source)))
        original = base64.b64decode(item["original_content_base64"], validate=True)
        assert hashlib.sha256(original).hexdigest() == source.source_hash
        assert hashlib.sha256(item["extracted_text"].encode()).hexdigest() == source.extracted_hash
    assert payload["export_manifest"]["record_counts"] == dict(receipt.record_counts)
    assert payload["export_manifest"]["credentials_included"] is False
    assert payload["export_manifest"]["internal_security_metadata_included"] is False
    assert payload["export_manifest"]["restorable"] is False
    assert "historical_opportunity_archive" not in payload["data"]
    assert "export_mode" not in receipt.to_dict()
    assert (
        payload["export_manifest"]["excluded_workspace_categories"]["configuration_or_credentials"][
            "entries"
        ]
        == 2
    )
    assert secret.encode() not in content
    assert b"credentials.json" not in content
    assert b"instance_root_hash" not in content
    assert str(workspace).encode() not in content
    assert _files(workspace) == before


def test_historical_archive_preserves_stale_supplied_bytes_without_current_promotion(
    workspace: Path, tmp_path: Path
) -> None:
    secret = b"synthetic-archive-credential-must-not-export"
    config = workspace / "config"
    config.mkdir(exist_ok=True)
    (config / "credentials.json").write_bytes(secret)
    source, request_path, dossier_path = _persist_supplied_opportunity(workspace)
    request_bytes = request_path.read_bytes()
    dossier_bytes = dossier_path.read_bytes()
    assert request_bytes == source

    vault = EvidenceVault(workspace)
    previous = vault.latest("synthetic-role-timeline")
    revised = vault.edit(
        previous.evidence_id,
        expected_revision=previous.revision,
        statement=previous.statement + " Retained unrelated revision.",
    )
    vault.review(
        revised.evidence_id,
        revised.revision,
        action="approve",
        expected_content_hash=revised.content_hash,
    )
    before = _files(workspace)
    service = create_instance_data_service(workspace)
    with pytest.raises(PermissionError):
        service.export_user_data(
            str(tmp_path / "denied-archive"),
            offline=False,
            actor="local_user",
            historical_archive=True,
        )
    with pytest.raises(PermissionError):
        service.export_user_data(
            str(tmp_path / "denied-archive"),
            offline=True,
            actor="agent",
            historical_archive=True,
        )
    assert not (tmp_path / "denied-archive").exists()
    with pytest.raises(ValueError, match="stale or has been modified"):
        service.export_user_data(str(tmp_path / "strict-export"), offline=True, actor="local_user")

    destination = tmp_path / "historical-export"
    receipt = service.export_user_data(
        str(destination),
        offline=True,
        actor="local_user",
        historical_archive=True,
    )
    content = (destination / instance_data.EXPORT_FILE).read_bytes()
    payload = json.loads(content)
    archive = payload["data"]["historical_opportunity_archive"]
    entry = archive["entries"][0]
    captured = {item["kind"]: item for item in entry["captured_files"]}

    assert payload["data"]["opportunities"] == []
    assert payload["data"]["role_preferences"] == []
    assert archive == {
        "schema_version": 1,
        "historical_integrity": "unverified",
        "current_readiness": "not_evaluated",
        "current_decision_eligible": False,
        "provenance_limitation": "original_evaluation_inputs_not_retained",
        "entries": archive["entries"],
    }
    assert entry["opportunity_id"] == "synthetic_archived_role"
    assert entry["decisions"] == []
    for kind, original in (
        ("supplied_request", request_bytes),
        ("decision_dossier", dossier_bytes),
    ):
        item = captured[kind]
        assert item["transfer_encoding"] == "base64"
        assert item["original_encoding"] == "utf-8"
        assert item["size_bytes"] == len(original)
        assert item["sha256"] == hashlib.sha256(original).hexdigest()
        assert base64.b64decode(item["content_base64"], validate=True) == original
    counts = payload["export_manifest"]["record_counts"]
    assert counts["opportunities"] == counts["role_preferences"] == 0
    assert counts["historical_opportunity_archives"] == 1
    assert counts["historical_opportunity_archive_files"] == 2
    assert counts["historical_opportunity_decisions"] == 0
    assert receipt.record_counts == tuple(sorted(counts.items()))
    assert receipt.sha256 == hashlib.sha256(content).hexdigest()
    assert receipt.to_dict()["export_mode"] == "historical_opportunity_archive"
    assert receipt.to_dict()["historical_archive_scope"] == "supplied_request_and_dossier_bytes"
    assert receipt.to_dict()["non_opportunity_record_validation"] == "unchanged_strict"
    assert payload["export_manifest"]["export_mode"] == "historical_opportunity_archive"
    assert payload["export_manifest"]["restorable"] is False
    assert secret not in content
    assert b"credentials.json" not in content
    assert _files(workspace) == before


def test_strict_export_keeps_the_existing_repository_call_contract() -> None:
    receipt = UserDataExportReceipt("destination", instance_data.EXPORT_FILE, "0" * 64, 1, ())

    class ExistingRepository:
        def export_user_data(self, destination: str) -> UserDataExportReceipt:
            assert destination == "destination"
            return receipt

    service = InstanceDataService(ExistingRepository())  # type: ignore[arg-type]
    assert service.export_user_data("destination", offline=True, actor="local_user") is receipt


@pytest.mark.parametrize("changed_field", ["authority", "recommendation", "source_hash"])
def test_historical_archive_labels_modified_dossiers_unverified(
    workspace: Path, tmp_path: Path, changed_field: str
) -> None:
    _, _, dossier_path = _persist_supplied_opportunity(workspace)
    dossier = json.loads(dossier_path.read_bytes())
    if changed_field == "recommendation":
        dossier["role_evaluation"]["recommendation"] = "skip"
    else:
        dossier[changed_field] = "modified"
    changed = json.dumps(dossier, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    dossier_path.write_bytes(changed)
    service = create_instance_data_service(workspace)
    with pytest.raises(ValueError, match="stale or has been modified"):
        service.export_user_data(str(tmp_path / "strict-export"), offline=True, actor="local_user")

    destination = tmp_path / "historical-export"
    service.export_user_data(
        str(destination), offline=True, actor="local_user", historical_archive=True
    )
    payload = json.loads((destination / instance_data.EXPORT_FILE).read_bytes())
    archive = payload["data"]["historical_opportunity_archive"]
    captured = archive["entries"][0]["captured_files"][1]
    assert archive["historical_integrity"] == "unverified"
    assert archive["current_decision_eligible"] is False
    assert base64.b64decode(captured["content_base64"], validate=True) == changed


@pytest.mark.parametrize(
    "damage",
    [
        "request_identity",
        "request_json",
        "dossier_json",
        "decision_chain",
        "request_oversize",
        "dossier_oversize",
        "unsafe_link",
        "corrupt_vault",
    ],
)
def test_historical_archive_keeps_opportunity_validation_fail_closed(
    workspace: Path, tmp_path: Path, damage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, request_path, dossier_path = _persist_supplied_opportunity(workspace)
    if damage == "request_identity":
        request = json.loads(request_path.read_bytes())
        request["opportunity_id"] = "different_identity"
        request_path.write_text(json.dumps(request), encoding="utf-8")
    elif damage == "request_json":
        request_path.write_bytes(b"{not-json}\n")
    elif damage == "dossier_json":
        dossier_path.write_bytes(b"{not-json}\n")
    elif damage == "decision_chain":
        decisions = dossier_path.with_name("decisions.json")
        decisions.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "opportunity_id": "synthetic_archived_role",
                    "events": [{}],
                }
            ),
            encoding="utf-8",
        )
    elif damage == "request_oversize":
        monkeypatch.setattr(instance_data, "MAX_ROLE_REQUEST_BYTES", 8)
    elif damage == "dossier_oversize":
        monkeypatch.setattr(instance_data, "MAX_OPPORTUNITY_STATE_BYTES", 8)
    elif damage == "unsafe_link":
        outside = tmp_path / "outside-dossier.json"
        outside.write_bytes(dossier_path.read_bytes())
        dossier_path.unlink()
        try:
            os.link(outside, dossier_path)
        except OSError:
            pytest.skip("Hard-link creation is unavailable on this platform")
    else:
        vault = EvidenceVault(workspace)
        source = vault.sources()[0]
        with sqlite3.connect(vault.path) as connection:
            connection.execute(
                "UPDATE source_documents SET original_content = ? WHERE source_id = ?",
                (b"tampered", source.source_id),
            )
    destination = tmp_path / "historical-export"
    with pytest.raises((PermissionError, ValueError)):
        create_instance_data_service(workspace).export_user_data(
            str(destination), offline=True, actor="local_user", historical_archive=True
        )
    assert not destination.exists()


def test_historical_archive_cli_and_concurrent_change_leave_truthful_incomplete_output(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, _, dossier_path = _persist_supplied_opportunity(workspace)
    cli_destination = tmp_path / "cli-historical-export"
    assert (
        main(
            [
                "--instance-root",
                str(workspace),
                "export",
                "--destination",
                str(cli_destination),
                "--offline",
                "--historical-archive",
            ]
        )
        == 0
    )
    cli_receipt = json.loads(capsys.readouterr().out)
    assert cli_receipt["export_mode"] == "historical_opportunity_archive"

    service = create_instance_data_service(workspace)
    repository = service._repository
    original_inventory = repository._inventory
    calls = 0

    def mutate_before_final_inventory():
        nonlocal calls
        calls += 1
        if calls == 3:
            dossier_path.write_bytes(dossier_path.read_bytes() + b" ")
        return original_inventory()

    monkeypatch.setattr(repository, "_inventory", mutate_before_final_inventory)
    changed_destination = tmp_path / "changed-historical-export"
    with pytest.raises(ValueError, match="incomplete output was retained"):
        service.export_user_data(
            str(changed_destination),
            offline=True,
            actor="local_user",
            historical_archive=True,
        )
    assert (changed_destination / instance_data.EXPORT_FILE).is_file()
    assert (changed_destination / INCOMPLETE).is_file()


def test_delete_requires_current_preview_and_removes_only_selected_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    create_instance_service(first).initialize(actor="local_user")
    create_instance_service(second).initialize(actor="local_user")
    (first / "inbox" / "source.txt").write_bytes(b"Synthetic selected source")
    (second / "inbox" / "retained.txt").write_bytes(b"Synthetic sibling source")
    outside_source = tmp_path / "outside-source.txt"
    outside_source.write_bytes(b"Synthetic outside source")
    backup = tmp_path / "retained-backup"
    create_recovery_service(first).backup(str(backup), offline=True, actor="local_user")
    sibling_before = _files(second)
    backup_before = _files(backup)
    service = create_instance_data_service(first)
    stale = service.delete()
    (first / "inbox" / "changed.txt").write_bytes(b"Changed after preview")
    with pytest.raises(PermissionError, match="preview changed"):
        service.delete(
            confirmation_fingerprint=stale.confirmation_fingerprint,
            offline=True,
            actor="local_user",
        )
    assert first.exists()
    preview = service.delete()
    with pytest.raises(PermissionError):
        service.delete(
            confirmation_fingerprint=preview.confirmation_fingerprint,
            offline=False,
            actor="local_user",
        )
    with pytest.raises(PermissionError):
        service.delete(
            confirmation_fingerprint=preview.confirmation_fingerprint,
            offline=True,
            actor="agent",
        )
    monkeypatch.setattr(
        recovery.shutil,
        "rmtree",
        lambda *_args, **_kwargs: pytest.fail("recursive deletion is forbidden"),
    )
    receipt = service.delete(
        confirmation_fingerprint=preview.confirmation_fingerprint,
        offline=True,
        actor="local_user",
    )
    assert receipt.instance_removed and receipt.removed_entries == len(preview.inventory.entries)
    assert not first.exists()
    assert _files(second) == sibling_before
    assert _files(backup) == backup_before
    assert outside_source.read_bytes() == b"Synthetic outside source"
    assert any(
        "Installed Rolevane" in line for line in receipt.to_dict()["retained_outside_workspace"]
    )
    with pytest.raises(ValueError, match="Workspace is missing"):
        service.delete()


def test_export_refuses_unknown_managed_state_and_delete_refuses_busy_database(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    create_instance_service(root).initialize(actor="local_user")
    unknown = root / "data" / "runtime" / "unsupported.json"
    unknown.write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="unsupported managed state"):
        create_instance_data_service(root).export_user_data(
            str(tmp_path / "export"), offline=True, actor="local_user"
        )
    assert not (tmp_path / "export").exists()
    unknown.unlink()
    metrics = root / "data" / "participant-flow.json"
    metrics.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metrics": {"schema_version": 1, "token": "must-not-export"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="metrics are invalid"):
        create_instance_data_service(root).export_user_data(
            str(tmp_path / "export"), offline=True, actor="local_user"
        )
    assert not (tmp_path / "export").exists()
    metrics.unlink()
    before = _files(root)
    connection = sqlite3.connect(root / recovery.DATABASE)
    connection.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(OSError, match="busy"):
            create_instance_data_service(root).delete()
    finally:
        connection.rollback()
        connection.close()
    assert _files(root) == before


def test_delete_partial_failure_stays_inside_selected_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected, sibling = tmp_path / "selected", tmp_path / "sibling"
    create_instance_service(selected).initialize(actor="local_user")
    create_instance_service(sibling).initialize(actor="local_user")
    (selected / "inbox" / "one.txt").write_bytes(b"one")
    (selected / "inbox" / "two.txt").write_bytes(b"two")
    sibling_before = _files(sibling)
    service = create_instance_data_service(selected)
    preview = service.delete()
    original = instance_data._unlink_verified
    calls = 0

    def fail_second(root: Path, entry) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Synthetic interrupted deletion")
        original(root, entry)

    monkeypatch.setattr(instance_data, "_unlink_verified", fail_second)
    with pytest.raises(OSError, match="interrupted"):
        service.delete(
            confirmation_fingerprint=preview.confirmation_fingerprint,
            offline=True,
            actor="local_user",
        )
    assert selected.exists()
    assert _files(sibling) == sibling_before

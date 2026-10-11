from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from career_os.application.tasks import (
    MAX_TASK_LEDGER_BYTES,
    MAX_TASK_REVISIONS,
    MAX_TASKS,
    TaskDue,
    TaskMutation,
    TaskRecord,
    TaskService,
    parse_task_ledger,
    serialize_task_ledger,
    task_json,
)
from career_os.evidence_vault import EvidenceVault
from career_os.file_transactions import FileConflictError
from career_os.infrastructure import tasks as task_storage
from career_os.infrastructure.instances import create_instance_service
from career_os.infrastructure.tasks import (
    LocalTaskRepository,
    create_task_service,
)
from tests.test_application_workflow import prepared_context


def _ready_task_instance(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "participant"
    create_instance_service(root).initialize(actor="local_user")
    _, _, decision = prepared_context.__wrapped__(root)
    return root, decision.opportunity_id


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_task_ledger_read_is_empty_without_migrating_an_initialized_instance(
    tmp_path: Path,
) -> None:
    root, _opportunity_id = _ready_task_instance(tmp_path)
    service = create_task_service(root)
    before = _file_snapshot(root)

    inventory = service.list()

    assert inventory.current == ()
    assert inventory.history == ()
    assert _file_snapshot(root) == before
    assert not (root / "data/runtime/planned-tasks.json").exists()
    assert not (root / "data/runtime/.planned-tasks.json.lock").exists()


def test_task_create_edit_complete_replay_and_restart_preserve_history(
    tmp_path: Path,
) -> None:
    root, opportunity_id = _ready_task_instance(tmp_path)
    service = create_task_service(root)
    outcome_path = root / "data/runtime/outcome-learning-v2.jsonl"
    outcome_path.write_bytes(b"unchanged outcome ledger fixture\n")
    outcome_before = outcome_path.read_bytes()
    due = TaskDue("date", "2026-10-06", "Europe/Berlin")

    created = service.create(
        opportunity_id=opportunity_id,
        description="Review the role with the team",
        due=due,
        request_id="create-001",
        actor="local_user",
    )
    assert created.changed is True
    assert created.record.revision == 1
    assert created.record.status == "open"

    updated = service.update(
        created.record.task_id,
        description="Review the saved role with the team",
        due=None,
        expected_revision=1,
        request_id="update-001",
        actor="local_user",
    )
    completed = service.set_status(
        created.record.task_id,
        status="completed",
        expected_revision=2,
        request_id="complete-001",
        actor="local_user",
    )
    restarted = create_task_service(root)

    assert updated.record.revision == 2
    assert updated.record.due is None
    assert completed.record.revision == 3
    assert completed.record.terminal_at == completed.record.updated_at
    assert restarted.list(opportunity_id).current == (completed.record,)
    assert tuple(record.revision for record in restarted.list().history) == (1, 2, 3)
    assert outcome_path.read_bytes() == outcome_before

    replayed_create = restarted.create(
        opportunity_id=opportunity_id,
        description="Review the role with the team",
        due=due,
        request_id="create-001",
        actor="local_user",
    )
    replayed_update = restarted.update(
        created.record.task_id,
        description="Review the saved role with the team",
        due=None,
        expected_revision=1,
        request_id="update-001",
        actor="local_user",
    )
    replayed_completion = restarted.set_status(
        created.record.task_id,
        status="completed",
        expected_revision=2,
        request_id="complete-001",
        actor="local_user",
    )
    assert replayed_create == type(created)(created.record, changed=False)
    assert replayed_update == type(updated)(updated.record, changed=False)
    assert replayed_completion == type(completed)(completed.record, changed=False)


def test_task_can_be_cancelled_and_cannot_be_reopened(tmp_path: Path) -> None:
    root, opportunity_id = _ready_task_instance(tmp_path)
    service = create_task_service(root)
    created = service.create(
        opportunity_id=opportunity_id,
        description="Decide whether to continue",
        due=None,
        request_id="cancel-create",
        actor="local_user",
    )
    cancelled = service.set_status(
        created.record.task_id,
        status="cancelled",
        expected_revision=1,
        request_id="cancel-task",
        actor="local_user",
    )
    replayed = create_task_service(root).set_status(
        created.record.task_id,
        status="cancelled",
        expected_revision=1,
        request_id="cancel-task",
        actor="local_user",
    )

    assert cancelled.record.status == "cancelled"
    assert cancelled.record.revision == 2
    assert cancelled.record.terminal_at == cancelled.record.updated_at
    assert replayed.record == cancelled.record and not replayed.changed
    with pytest.raises(ValueError, match="cannot be changed"):
        service.set_status(
            created.record.task_id,
            status="completed",
            expected_revision=2,
            request_id="reopen-cancelled",
            actor="local_user",
        )


def test_task_replay_precedes_stale_revision_terminal_and_role_checks(tmp_path: Path) -> None:
    root, opportunity_id = _ready_task_instance(tmp_path)
    service = create_task_service(root)
    created = service.create(
        opportunity_id=opportunity_id,
        description="Prepare a discussion",
        due=None,
        request_id="create-replay",
        actor="local_user",
    )
    updated = service.update(
        created.record.task_id,
        description="Prepare a focused discussion",
        due=None,
        expected_revision=1,
        request_id="update-replay",
        actor="local_user",
    )
    completed = service.set_status(
        created.record.task_id,
        status="completed",
        expected_revision=2,
        request_id="status-replay",
        actor="local_user",
    )

    evidence = EvidenceVault(root).latest("synthetic-responsibility")
    EvidenceVault(root).edit(
        evidence.evidence_id,
        expected_revision=evidence.revision,
        statement=evidence.statement + " Updated synthetic source statement.",
    )
    with pytest.raises(ValueError):
        service.create(
            opportunity_id=opportunity_id,
            description="A new task requires a current role assessment",
            due=None,
            request_id="new-create-stale-role",
            actor="local_user",
        )

    replayed_create = service.create(
        opportunity_id=opportunity_id,
        description="Prepare a discussion",
        due=None,
        request_id="create-replay",
        actor="local_user",
    )
    replayed_update = service.update(
        created.record.task_id,
        description="Prepare a focused discussion",
        due=None,
        expected_revision=1,
        request_id="update-replay",
        actor="local_user",
    )
    replayed_status = service.set_status(
        created.record.task_id,
        status="completed",
        expected_revision=2,
        request_id="status-replay",
        actor="local_user",
    )
    assert replayed_create.record == created.record and not replayed_create.changed
    assert replayed_update.record == updated.record and not replayed_update.changed
    assert replayed_status.record == completed.record and not replayed_status.changed


def test_task_request_collisions_stale_revisions_and_terminal_edits_are_rejected(
    tmp_path: Path,
) -> None:
    root, opportunity_id = _ready_task_instance(tmp_path)
    service = create_task_service(root)
    created = service.create(
        opportunity_id=opportunity_id,
        description="Book a review",
        due=None,
        request_id="task-request-a",
        actor="local_user",
    )
    path = root / "data/runtime/planned-tasks.json"
    before = path.read_bytes()

    with pytest.raises(ValueError, match="different operation"):
        service.create(
            opportunity_id=opportunity_id,
            description="Different content",
            due=None,
            request_id="task-request-a",
            actor="local_user",
        )
    service.update(
        created.record.task_id,
        description="Book a focused review",
        due=None,
        expected_revision=1,
        request_id="task-request-b",
        actor="local_user",
    )
    with pytest.raises(FileConflictError, match="supplied revision"):
        service.update(
            created.record.task_id,
            description="Stale write",
            due=None,
            expected_revision=1,
            request_id="task-request-c",
            actor="local_user",
        )
    service.set_status(
        created.record.task_id,
        status="completed",
        expected_revision=2,
        request_id="task-request-d",
        actor="local_user",
    )
    terminal_bytes = path.read_bytes()
    with pytest.raises(ValueError, match="cannot be changed"):
        service.update(
            created.record.task_id,
            description="Edit after completion",
            due=None,
            expected_revision=3,
            request_id="task-request-e",
            actor="local_user",
        )
    assert path.read_bytes() == terminal_bytes
    assert terminal_bytes != before


@pytest.mark.parametrize(
    "value",
    [
        "2026-03-29T02:30:00+01:00",
        "2026-10-25T02:30:00+00:00",
    ],
)
def test_timed_task_due_rejects_dst_gaps_and_incompatible_offsets(value: str) -> None:
    with pytest.raises(ValueError, match="DST gap or has an incompatible"):
        TaskDue("timed", value, "Europe/Berlin")


def test_timed_task_due_accepts_both_explicit_berlin_dst_folds() -> None:
    earlier = TaskDue("timed", "2026-10-25T02:30:00+02:00", "Europe/Berlin")
    later = TaskDue("timed", "2026-10-25T02:30:00+01:00", "Europe/Berlin")

    assert earlier.to_dict()["value"].endswith("+02:00")
    assert later.to_dict()["value"].endswith("+01:00")


def test_task_clock_cannot_move_backwards_and_failed_write_preserves_ledger(
    tmp_path: Path,
) -> None:
    root, opportunity_id = _ready_task_instance(tmp_path)
    times = iter(
        (
            datetime(2026, 10, 5, 8, 0, tzinfo=UTC),
            datetime(2026, 10, 5, 7, 59, tzinfo=UTC),
        )
    )
    service = TaskService(LocalTaskRepository(root, clock=lambda: next(times)))
    created = service.create(
        opportunity_id=opportunity_id,
        description="Check the agenda",
        due=None,
        request_id="clock-create",
        actor="local_user",
    )
    path = root / "data/runtime/planned-tasks.json"
    before = path.read_bytes()

    with pytest.raises(ValueError, match="clock cannot move backwards"):
        service.set_status(
            created.record.task_id,
            status="completed",
            expected_revision=1,
            request_id="clock-complete",
            actor="local_user",
        )

    assert path.read_bytes() == before
    assert create_task_service(root).list().current == (created.record,)


def test_task_service_requires_local_actor_consent_initialized_identity_and_complete_root(
    tmp_path: Path,
) -> None:
    root, opportunity_id = _ready_task_instance(tmp_path)
    service = create_task_service(root)
    with pytest.raises(TypeError):
        service.create(
            opportunity_id=opportunity_id,
            description="Actor is required",
            due=None,
            request_id="actor-required",
        )
    with pytest.raises(PermissionError, match="local-user"):
        service.create(
            opportunity_id=opportunity_id,
            description="Actor must be the local user",
            due=None,
            request_id="actor-denied",
            actor="agent",
        )

    vault = EvidenceVault(root)
    vault.record_consent(accepted=False)
    with pytest.raises(PermissionError, match="consent"):
        service.list()

    incomplete_case = tmp_path / "incomplete-case"
    incomplete_case.mkdir()
    consented_root, _ = _ready_task_instance(incomplete_case)
    marker = consented_root / ".career-os-recovery-incomplete"
    marker.write_text("incomplete", encoding="utf-8")
    with pytest.raises(ValueError, match="safe and ready"):
        create_task_service(consented_root).list()

    manual_root = tmp_path / "manual-vault"
    (manual_root / "data/runtime").mkdir(parents=True)
    EvidenceVault(manual_root).record_consent(accepted=True)
    with pytest.raises(PermissionError, match="initialized private instance"):
        create_task_service(manual_root).list()


@pytest.mark.parametrize("foreign", [False, True])
def test_malformed_or_foreign_task_ledgers_are_rejected_without_repair(
    tmp_path: Path,
    foreign: bool,
) -> None:
    root, _opportunity_id = _ready_task_instance(tmp_path)
    path = root / "data/runtime/planned-tasks.json"
    if foreign:
        path.write_bytes(serialize_task_ledger((), "f" * 64))
    else:
        path.write_bytes(b'{"instance_hash":"x","instance_hash":"y"}')
    before = _file_snapshot(root)

    with pytest.raises(ValueError):
        create_task_service(root).list()

    assert _file_snapshot(root) == before


@pytest.mark.parametrize(
    ("field_path", "invalid"),
    [
        (("revisions", 0, "operation", "operation"), []),
        (("revisions", 0, "operation", "operation"), {}),
        (("revisions", 1, "operation", "status"), []),
        (("revisions", 1, "operation", "status"), {}),
        (("revisions", 1, "record", "status"), []),
        (("revisions", 1, "record", "status"), {}),
        (("revisions", 0, "operation", "due", "kind"), []),
        (("revisions", 0, "operation", "due", "kind"), {}),
        (("revisions", 0, "record", "due", "kind"), []),
        (("revisions", 0, "record", "due", "kind"), {}),
    ],
)
def test_nonstring_task_enums_in_persisted_ledger_fail_without_repair(
    tmp_path: Path,
    field_path: tuple[str | int, ...],
    invalid: object,
) -> None:
    root, opportunity_id = _ready_task_instance(tmp_path)
    service = create_task_service(root)
    created = service.create(
        opportunity_id=opportunity_id,
        description="Review the fictional role",
        due=TaskDue("date", "2026-10-06", "Europe/Berlin"),
        request_id="corruption-create",
        actor="local_user",
    )
    service.set_status(
        created.record.task_id,
        status="completed",
        expected_revision=1,
        request_id="corruption-complete",
        actor="local_user",
    )

    ledger_path = root / "data/runtime/planned-tasks.json"
    payload = json.loads(ledger_path.read_bytes())
    target = payload
    for field in field_path[:-1]:
        target = target[field]
    target[field_path[-1]] = invalid
    corrupted = task_json(payload)
    ledger_path.write_bytes(corrupted)

    with pytest.raises(ValueError):
        create_task_service(root).list()

    assert ledger_path.read_bytes() == corrupted


@pytest.mark.parametrize("invalid", [[], {}])
def test_task_dtos_reject_nonstring_operation_status_and_due_kind(invalid: object) -> None:
    task_id = "task_" + "a" * 64
    valid_mutation = TaskMutation(
        "status",
        "dto-status",
        task_id=task_id,
        expected_revision=1,
        status="completed",
    )
    valid_record = TaskRecord(
        task_id,
        "example_role",
        1,
        "Review the fictional role",
        None,
        "open",
        "2026-10-05T08:00:00+00:00",
        "2026-10-05T08:00:00+00:00",
        None,
        "dto-create",
        "b" * 64,
    )

    with pytest.raises(ValueError):
        TaskDue(invalid, "2026-10-06", "Europe/Berlin")
    with pytest.raises(ValueError):
        TaskDue.from_dict({"kind": invalid, "value": "2026-10-06", "timezone": "Europe/Berlin"})
    with pytest.raises(ValueError):
        TaskMutation(invalid, "dto-operation")
    with pytest.raises(ValueError):
        TaskMutation.from_dict({**valid_mutation.to_dict(), "operation": invalid})
    with pytest.raises(ValueError):
        TaskMutation(
            "status",
            "dto-invalid-status",
            task_id=task_id,
            expected_revision=1,
            status=invalid,
        )
    with pytest.raises(ValueError):
        TaskMutation.from_dict({**valid_mutation.to_dict(), "status": invalid})
    with pytest.raises(ValueError):
        TaskRecord(
            task_id,
            "example_role",
            1,
            "Review the fictional role",
            None,
            invalid,
            "2026-10-05T08:00:00+00:00",
            "2026-10-05T08:00:00+00:00",
            None,
            "dto-create",
            "b" * 64,
        )
    with pytest.raises(ValueError):
        TaskRecord.from_dict({**valid_record.to_dict(), "status": invalid})


def _large_valid_history() -> tuple[TaskRecord, ...]:
    timestamp = "2026-10-05T08:00:00+00:00"
    description = "x" * 1_000
    create = TaskMutation(
        "create",
        "size-create",
        opportunity_id="synthetic_role",
        description=description,
    )
    task_id = "task_" + hashlib.sha256(task_json(create.to_dict())).hexdigest()
    records = [
        TaskRecord(
            task_id,
            "synthetic_role",
            1,
            description,
            None,
            "open",
            timestamp,
            timestamp,
            None,
            create.request_id,
            hashlib.sha256(task_json(create.to_dict())).hexdigest(),
        )
    ]
    for revision in range(2, 602):
        mutation = TaskMutation(
            "update",
            f"size-update-{revision}",
            task_id=task_id,
            description=description,
            due=None,
            expected_revision=revision - 1,
        )
        records.append(
            TaskRecord(
                task_id,
                "synthetic_role",
                revision,
                description,
                None,
                "open",
                timestamp,
                timestamp,
                None,
                mutation.request_id,
                hashlib.sha256(task_json(mutation.to_dict())).hexdigest(),
            )
        )
    return tuple(records)


def test_task_parser_and_serializer_enforce_ledger_and_replacement_byte_limits() -> None:
    with pytest.raises(ValueError, match="must contain 1-"):
        parse_task_ledger(b" " * (MAX_TASK_LEDGER_BYTES + 1), "a" * 64)

    with pytest.raises(ValueError, match="replacement exceeds"):
        serialize_task_ledger(_large_valid_history(), "a" * 64)

    history = _large_valid_history()
    oversized_revision_history = (history * (MAX_TASK_REVISIONS // len(history) + 1))[
        : MAX_TASK_REVISIONS + 1
    ]
    with pytest.raises(ValueError, match="revision limit"):
        serialize_task_ledger(oversized_revision_history, "a" * 64)

    too_many_tasks: list[TaskRecord] = []
    timestamp = "2026-10-05T08:00:00+00:00"
    for index in range(MAX_TASKS + 1):
        mutation = TaskMutation(
            "create",
            f"limit-task-{index}",
            opportunity_id="synthetic_role",
            description="Task at the capacity boundary",
        )
        digest = hashlib.sha256(task_json(mutation.to_dict())).hexdigest()
        too_many_tasks.append(
            TaskRecord(
                "task_" + digest,
                "synthetic_role",
                1,
                "Task at the capacity boundary",
                None,
                "open",
                timestamp,
                timestamp,
                None,
                mutation.request_id,
                digest,
            )
        )
    with pytest.raises(ValueError, match="task limit"):
        serialize_task_ledger(tuple(too_many_tasks), "a" * 64)


def test_task_service_capacity_rejects_new_writes_but_keeps_exact_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, opportunity_id = _ready_task_instance(tmp_path)
    service = create_task_service(root)
    created = service.create(
        opportunity_id=opportunity_id,
        description="Keep the reviewed task",
        due=None,
        request_id="capacity-create",
        actor="local_user",
    )
    path = root / "data/runtime/planned-tasks.json"
    before = path.read_bytes()
    monkeypatch.setattr(task_storage, "MAX_TASK_REVISIONS", 1)

    with pytest.raises(ValueError, match="revision limit"):
        service.set_status(
            created.record.task_id,
            status="completed",
            expected_revision=1,
            request_id="capacity-complete",
            actor="local_user",
        )
    replay = service.create(
        opportunity_id=opportunity_id,
        description="Keep the reviewed task",
        due=None,
        request_id="capacity-create",
        actor="local_user",
    )

    assert replay.record == created.record and not replay.changed
    assert path.read_bytes() == before


def test_task_history_is_instance_bound_and_operation_digest_is_validated(
    tmp_path: Path,
) -> None:
    root, opportunity_id = _ready_task_instance(tmp_path)
    service = create_task_service(root)
    created = service.create(
        opportunity_id=opportunity_id,
        description="Validate task history",
        due=None,
        request_id="history-create",
        actor="local_user",
    )
    path = root / "data/runtime/planned-tasks.json"
    content = path.read_bytes()
    instance_hash = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()
    history = parse_task_ledger(content, instance_hash)

    assert history == (created.record,)
    assert serialize_task_ledger(history, instance_hash) == content
    with pytest.raises(ValueError, match="different private instance"):
        parse_task_ledger(content, "b" * 64)

    payload = json.loads(content)
    payload["revisions"][0]["record"]["description"] = "altered"
    altered = task_json(payload)
    with pytest.raises(ValueError):
        parse_task_ledger(altered, instance_hash)

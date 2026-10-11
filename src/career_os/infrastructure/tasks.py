"""Instance-bound durable storage for local planned tasks."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from career_os.application.tasks import (
    MAX_TASK_LEDGER_BYTES,
    MAX_TASK_REVISIONS,
    MAX_TASKS,
    TaskInventory,
    TaskMutation,
    TaskReceipt,
    TaskRecord,
    TaskService,
    _mutation_digest,
    _validated_operation_history,
    parse_task_ledger,
    serialize_task_ledger,
)
from career_os.evidence_vault import EvidenceVault
from career_os.file_transactions import (
    FileConflictError,
    _safe_regular_file_path,
    locked_compare_and_swap,
    sha256_hex,
)
from career_os.infrastructure import evidence_storage
from career_os.infrastructure.evidence import read_private_json
from career_os.infrastructure.opportunities import LocalOpportunityRepository

_INCOMPLETE_MARKER = ".career-os-recovery-incomplete"
_TASK_RELATIVE_PATH = Path("data/runtime/planned-tasks.json")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _root_hash(root: Path) -> str:
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()


class LocalTaskRepository:
    """One initialized private instance's append-only task revision ledger."""

    def __init__(
        self,
        instance_root: str | Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._vault = EvidenceVault(instance_root)
        self.instance_root = self._vault.instance_root
        self._clock = clock
        self._opportunities = LocalOpportunityRepository(
            self.instance_root,
            vault=self._vault,
            read_only_resolution=True,
        )

    @property
    def path(self) -> Path:
        return self.instance_root / _TASK_RELATIVE_PATH

    def _safe_selected_root(self) -> Path:
        root = self.instance_root
        try:
            if not root.is_dir() or root.resolve(strict=True) != root:
                raise ValueError("Selected task instance is unavailable")
            marker = _safe_regular_file_path(root / _INCOMPLETE_MARKER)
            _safe_regular_file_path(root / _TASK_RELATIVE_PATH)
            if marker.exists():
                raise ValueError("Selected task instance is incomplete")
            if not (root / "data" / "runtime").is_dir():
                raise ValueError("Selected task runtime directory is unavailable")
        except (OSError, ValueError) as exc:
            raise ValueError("Selected task instance is not safe and ready") from exc
        return root

    def _require_ready(self) -> tuple[Path, str]:
        root = self._safe_selected_root()
        try:
            with self._vault._connect(create=False) as connection:
                identity = evidence_storage.validate_current_storage(connection, root)
                if identity != evidence_storage.INSTANCE_LAYOUT_IDENTITY:
                    raise PermissionError("Task storage requires an initialized private instance")
                consent = connection.execute(
                    "SELECT accepted FROM consent_receipts ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                if consent is None or not bool(consent["accepted"]):
                    raise PermissionError("Task storage requires local-processing consent")
        except PermissionError:
            raise
        except (OSError, ValueError) as exc:
            raise ValueError("Selected task instance is not initialized and ready") from exc
        return root, _root_hash(root)

    def _ledger_exists(self) -> bool:
        path = _safe_regular_file_path(self.path)
        if not path.parent.is_dir():
            raise ValueError("Task runtime directory is unavailable")
        return path.exists()

    def _load_previous(self, previous: bytes, *, exists: bool, instance_hash: str):
        if not exists:
            if previous:
                raise ValueError("Task ledger existence changed during the transaction")
            return ()
        return parse_task_ledger(previous, instance_hash)

    def _read_history(self, instance_hash: str) -> tuple[TaskRecord, ...]:
        path = _safe_regular_file_path(self.path)
        if not path.parent.is_dir():
            raise ValueError("Task runtime directory is unavailable")
        if not path.exists():
            return ()
        _payload, content = read_private_json(
            self.instance_root,
            path,
            label="Planned task ledger",
            maximum_bytes=MAX_TASK_LEDGER_BYTES,
        )
        return parse_task_ledger(content, instance_hash)

    @staticmethod
    def _replay(history: Sequence[TaskRecord], mutation: TaskMutation) -> TaskReceipt | None:
        digest = _mutation_digest(mutation)
        for original, record in _validated_operation_history(history):
            if record.request != mutation.request_id:
                continue
            if original != mutation or record.request_digest != digest:
                raise ValueError("Task request_id is already bound to a different operation")
            return TaskReceipt(record, changed=False)
        return None

    @staticmethod
    def _check_capacity(history: Sequence[TaskRecord], *, creating: bool) -> None:
        if len(history) >= MAX_TASK_REVISIONS:
            raise ValueError("Task ledger revision limit has been reached")
        if creating and len({record.task_id for record in history}) >= MAX_TASKS:
            raise ValueError("Task ledger task limit has been reached")

    def _timestamp_for(self, history: Sequence[TaskRecord]) -> str:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Task clock must return a timezone-aware datetime")
        now = now.astimezone(UTC).replace(microsecond=0)
        if history:
            previous = datetime.fromisoformat(history[-1].updated_at)
            if now < previous:
                raise ValueError("Task clock cannot move backwards")
        return now.isoformat(timespec="seconds")

    @staticmethod
    def _current_records(history: Sequence[TaskRecord]) -> dict[str, TaskRecord]:
        return {record.task_id: record for record in history}

    def _append(self, history: tuple[TaskRecord, ...], mutation: TaskMutation) -> TaskRecord:
        creating = mutation.operation == "create"
        self._check_capacity(history, creating=creating)
        states = self._current_records(history)
        previous = None if creating else states.get(mutation.task_id or "")
        if creating:
            task_id = "task_" + _mutation_digest(mutation)
            if task_id in states:
                raise ValueError("Task identity already exists under a different request")
            opportunity_id = mutation.opportunity_id
            description = mutation.description
            due = mutation.due
            status = "open"
            terminal_at = None
            created_at = None
        else:
            if previous is None:
                raise ValueError("Task does not exist")
            if previous.revision != mutation.expected_revision:
                raise FileConflictError("Task changed after the supplied revision was reviewed")
            if previous.status != "open":
                raise ValueError("Completed or cancelled tasks cannot be changed")
            task_id = previous.task_id
            opportunity_id = previous.opportunity_id
            created_at = previous.created_at
            if mutation.operation == "update":
                description = mutation.description
                due = mutation.due
                status = "open"
                terminal_at = None
            else:
                description = previous.description
                due = previous.due
                status = mutation.status
                terminal_at = "pending"

        timestamp = self._timestamp_for(history)
        if terminal_at == "pending":
            terminal_at = timestamp
        record = TaskRecord(
            task_id=task_id,
            opportunity_id=opportunity_id or "",
            revision=1 if previous is None else previous.revision + 1,
            description=description or "",
            due=due,
            status=status,  # type: ignore[arg-type]
            created_at=timestamp if created_at is None else created_at,
            updated_at=timestamp,
            terminal_at=terminal_at,
            request=mutation.request_id,
            request_digest=_mutation_digest(mutation),
        )
        return record

    def _apply_update_locked(
        self,
        mutation: TaskMutation,
        *,
        instance_hash: str,
        exists: bool,
    ) -> TaskReceipt:
        outcome: TaskReceipt | None = None

        def update(previous: bytes) -> bytes:
            nonlocal outcome
            history = self._load_previous(previous, exists=exists, instance_hash=instance_hash)
            replay = self._replay(history, mutation)
            if replay is not None:
                outcome = replay
                return previous
            record = self._append(history, mutation)
            replacement = serialize_task_ledger((*history, record), instance_hash)
            outcome = TaskReceipt(record, changed=True)
            return replacement

        locked_compare_and_swap(
            self.path,
            update,
            expected_exists=exists,
            maximum_bytes=MAX_TASK_LEDGER_BYTES,
        )
        if outcome is None:
            raise AssertionError("Task ledger transaction returned no receipt")
        return outcome

    def _apply_create(self, mutation: TaskMutation, *, instance_hash: str) -> TaskReceipt:
        exists = self._ledger_exists()
        checked = locked_compare_and_swap(
            self.path,
            lambda previous: previous,
            expected_exists=exists,
            maximum_bytes=MAX_TASK_LEDGER_BYTES,
        )
        history = self._load_previous(checked.current, exists=exists, instance_hash=instance_hash)
        replay = self._replay(history, mutation)
        if replay is not None:
            return replay
        self._check_capacity(history, creating=True)

        # Only a genuinely new create reads the current role. Existing retries above
        # remain valid even when that assessment has since become stale.
        self._opportunities.resolve_supplied(mutation.opportunity_id or "")

        outcome: TaskReceipt | None = None

        def append(previous: bytes) -> bytes:
            nonlocal outcome
            current_history = self._load_previous(
                previous, exists=exists, instance_hash=instance_hash
            )
            replayed = self._replay(current_history, mutation)
            if replayed is not None:
                outcome = replayed
                return previous
            record = self._append(current_history, mutation)
            replacement = serialize_task_ledger((*current_history, record), instance_hash)
            outcome = TaskReceipt(record, changed=True)
            return replacement

        locked_compare_and_swap(
            self.path,
            append,
            expected_sha256=sha256_hex(checked.current),
            expected_exists=exists,
            maximum_bytes=MAX_TASK_LEDGER_BYTES,
        )
        if outcome is None:
            raise AssertionError("Task ledger transaction returned no receipt")
        return outcome

    def apply(self, mutation: TaskMutation) -> TaskReceipt:
        if not isinstance(mutation, TaskMutation):
            raise ValueError("Task repository requires a normalized TaskMutation")
        self._safe_selected_root()
        with self._vault.existing_transaction():
            _root, instance_hash = self._require_ready()
            if mutation.operation == "create":
                return self._apply_create(mutation, instance_hash=instance_hash)
            exists = self._ledger_exists()
            return self._apply_update_locked(
                mutation,
                instance_hash=instance_hash,
                exists=exists,
            )

    def list(self, opportunity_id: str | None = None) -> TaskInventory:
        self._safe_selected_root()
        with self._vault.read_transaction():
            _root, instance_hash = self._require_ready()
            history = self._read_history(instance_hash)
        if opportunity_id is not None:
            history = tuple(record for record in history if record.opportunity_id == opportunity_id)
        current: dict[str, TaskRecord] = {}
        for record in history:
            current[record.task_id] = record
        return TaskInventory(
            current=tuple(current[key] for key in sorted(current)),
            history=history,
        )


def create_task_service(instance_root: str | Path) -> TaskService:
    return TaskService(LocalTaskRepository(instance_root))


__all__ = [
    "LocalTaskRepository",
    "create_task_service",
    "parse_task_ledger",
    "serialize_task_ledger",
]

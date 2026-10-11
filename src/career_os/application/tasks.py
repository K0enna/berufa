"""Human-entered local tasks bound to confirmed supplied opportunities."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, Protocol, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from career_os.application.opportunities import _identifier as _opportunity_identifier

TaskStatus = Literal["open", "completed", "cancelled"]
TaskOperation = Literal["create", "update", "status"]
TaskDueKind = Literal["date", "timed"]

MAX_TASK_LEDGER_BYTES = 1024 * 1024
MAX_TASK_REVISIONS = 2_000
MAX_TASKS = 500
MAX_TASK_DESCRIPTION_CHARS = 1_000
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}\Z")
_TASK_ID = re.compile(r"task_[0-9a-f]{64}\Z")
_DATE_VALUE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_TIMED_VALUE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z"
)
_TASK_STATUSES = frozenset({"open", "completed", "cancelled"})
_MUTATION_FIELDS = {
    "operation",
    "request_id",
    "opportunity_id",
    "task_id",
    "description",
    "due",
    "expected_revision",
    "status",
}
_RECORD_FIELDS = {
    "task_id",
    "opportunity_id",
    "revision",
    "description",
    "due",
    "status",
    "created_at",
    "updated_at",
    "terminal_at",
    "request",
    "request_digest",
}


def _request_identifier(value: object) -> str:
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise ValueError("request_id must be a bounded safe identifier")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("request_id must contain valid Unicode text") from exc
    return value


def _description(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Task description must be text")
    cleaned = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned or len(cleaned) > MAX_TASK_DESCRIPTION_CHARS:
        raise ValueError(f"Task description must contain 1-{MAX_TASK_DESCRIPTION_CHARS} characters")
    try:
        cleaned.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Task description must contain valid Unicode text") from exc
    if any(unicodedata.category(char) == "Cc" and char != "\n" for char in cleaned):
        raise ValueError("Task description cannot contain control characters")
    if re.search(r"<[A-Za-z/!?][^>]*>", cleaned):
        raise ValueError("Task description must remain plain text")
    return cleaned


def _timezone(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError("Task due timezone must be an IANA timezone name")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Task due timezone must be an available IANA timezone") from exc
    return value


def _parse_due(kind: object, value: object, timezone: object) -> None:
    if not isinstance(kind, str) or kind not in {"date", "timed"}:
        raise ValueError("Task due kind must be date or timed")
    if not isinstance(value, str):
        raise ValueError("Task due value must be text")
    selected_timezone = ZoneInfo(_timezone(timezone))
    if kind == "date":
        if not _DATE_VALUE.fullmatch(value):
            raise ValueError("Date task due values must use YYYY-MM-DD")
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError as exc:
            raise ValueError("Date task due value is not a valid calendar date") from exc
        return

    if not _TIMED_VALUE.fullmatch(value):
        raise ValueError("Timed task due values need ISO seconds and an explicit UTC offset")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Timed task due value is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timed task due values need an explicit UTC offset")

    local_wall_time = parsed.replace(tzinfo=None)
    matches_zone = False
    for fold in (0, 1):
        local = local_wall_time.replace(tzinfo=selected_timezone, fold=fold)
        round_trip = local.astimezone(UTC).astimezone(selected_timezone)
        if (
            round_trip.replace(tzinfo=None) == local_wall_time
            and round_trip.utcoffset() == parsed.utcoffset()
        ):
            matches_zone = True
            break
    if not matches_zone:
        raise ValueError("Timed task due value is a DST gap or has an incompatible timezone offset")


@dataclass(frozen=True, slots=True)
class TaskDue:
    kind: TaskDueKind
    value: str
    timezone: str

    def __post_init__(self) -> None:
        _parse_due(self.kind, self.value, self.timezone)

    @classmethod
    def from_dict(cls, payload: object) -> TaskDue:
        if not isinstance(payload, dict) or set(payload) != {"kind", "value", "timezone"}:
            raise ValueError("Task due must contain exactly kind, value, and timezone")
        return cls(
            cast(TaskDueKind, payload["kind"]),
            cast(str, payload["value"]),
            cast(str, payload["timezone"]),
        )

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value, "timezone": self.timezone}


def _optional_due(value: object) -> TaskDue | None:
    if value is None:
        return None
    if not isinstance(value, TaskDue):
        raise ValueError("Task due must be a TaskDue value or None")
    return value


def _revision(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("expected_revision must be a positive integer")
    return value


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError(f"Task {field} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Task {field} must be a canonical UTC timestamp") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != UTC.utcoffset(parsed)
        or parsed.isoformat(timespec="seconds") != value
    ):
        raise ValueError(f"Task {field} must be a canonical UTC timestamp")
    return parsed.astimezone(UTC)


def _mutation_digest(mutation: TaskMutation) -> str:
    return hashlib.sha256(task_json(mutation.to_dict())).hexdigest()


def _valid_instance_hash(instance_hash: object) -> str:
    if not isinstance(instance_hash, str) or not _HASH.fullmatch(instance_hash):
        raise ValueError("Task ledger instance hash must be lowercase SHA-256")
    return instance_hash


@dataclass(frozen=True, slots=True)
class TaskMutation:
    operation: TaskOperation
    request_id: str
    opportunity_id: str | None = None
    task_id: str | None = None
    description: str | None = None
    due: TaskDue | None = None
    expected_revision: int | None = None
    status: Literal["completed", "cancelled"] | None = None

    def __post_init__(self) -> None:
        _request_identifier(self.request_id)
        if not isinstance(self.operation, str) or self.operation not in {
            "create",
            "update",
            "status",
        }:
            raise ValueError("Task mutation operation is invalid")
        if self.operation == "create":
            if (
                self.opportunity_id is None
                or self.task_id is not None
                or self.description is None
                or self.expected_revision is not None
                or self.status is not None
            ):
                raise ValueError("Create task mutation fields are invalid")
            if _opportunity_identifier(self.opportunity_id, "opportunity_id") != (
                self.opportunity_id
            ):
                raise ValueError("Create task opportunity identity is not canonical")
            if _description(self.description) != self.description:
                raise ValueError("Create task description is not normalized")
            _optional_due(self.due)
            return
        if self.operation == "update":
            if (
                self.opportunity_id is not None
                or self.task_id is None
                or self.description is None
                or self.expected_revision is None
                or self.status is not None
            ):
                raise ValueError("Update task mutation fields are invalid")
            _task_identifier(self.task_id)
            if _description(self.description) != self.description:
                raise ValueError("Updated task description is not normalized")
            _optional_due(self.due)
            _revision(self.expected_revision)
            return
        if self.operation == "status":
            if (
                self.opportunity_id is not None
                or self.task_id is None
                or self.description is not None
                or self.due is not None
                or self.expected_revision is None
                or not isinstance(self.status, str)
                or self.status not in {"completed", "cancelled"}
            ):
                raise ValueError("Task status mutation fields are invalid")
            _task_identifier(self.task_id)
            _revision(self.expected_revision)
            return
        raise ValueError("Task mutation operation is invalid")

    @classmethod
    def from_dict(cls, payload: object) -> TaskMutation:
        if not isinstance(payload, dict) or set(payload) != _MUTATION_FIELDS:
            raise ValueError("Task mutation must contain exactly the documented fields")
        raw_due = payload["due"]
        due = None if raw_due is None else TaskDue.from_dict(raw_due)
        operation = payload["operation"]
        if not isinstance(operation, str) or operation not in {"create", "update", "status"}:
            raise ValueError("Task mutation operation is invalid")
        return cls(
            operation=cast(TaskOperation, operation),
            request_id=cast(str, payload["request_id"]),
            opportunity_id=cast(str | None, payload["opportunity_id"]),
            task_id=cast(str | None, payload["task_id"]),
            description=cast(str | None, payload["description"]),
            due=due,
            expected_revision=cast(int | None, payload["expected_revision"]),
            status=cast(Literal["completed", "cancelled"] | None, payload["status"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "request_id": self.request_id,
            "opportunity_id": self.opportunity_id,
            "task_id": self.task_id,
            "description": self.description,
            "due": self.due.to_dict() if self.due is not None else None,
            "expected_revision": self.expected_revision,
            "status": self.status,
        }


def _task_identifier(value: object) -> str:
    if not isinstance(value, str) or not _TASK_ID.fullmatch(value):
        raise ValueError("task_id must be a stable task identifier")
    return value


def _task_id_for(mutation: TaskMutation) -> str:
    if mutation.operation != "create":
        raise ValueError("Only create mutations have a deterministic task identity")
    return "task_" + _mutation_digest(mutation)


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: str
    opportunity_id: str
    revision: int
    description: str
    due: TaskDue | None
    status: TaskStatus
    created_at: str
    updated_at: str
    terminal_at: str | None
    request: str
    request_digest: str

    def __post_init__(self) -> None:
        _task_identifier(self.task_id)
        if _opportunity_identifier(self.opportunity_id, "opportunity_id") != self.opportunity_id:
            raise ValueError("Task opportunity identity is not canonical")
        _revision(self.revision)
        if _description(self.description) != self.description:
            raise ValueError("Task description is not normalized")
        _optional_due(self.due)
        if not isinstance(self.status, str) or self.status not in _TASK_STATUSES:
            raise ValueError("Task status is invalid")
        _timestamp(self.created_at, "created_at")
        updated = _timestamp(self.updated_at, "updated_at")
        created = _timestamp(self.created_at, "created_at")
        if updated < created:
            raise ValueError("Task timestamps cannot move backwards")
        if self.terminal_at is None:
            if self.status != "open":
                raise ValueError("Terminal task status requires terminal_at")
        else:
            terminal = _timestamp(self.terminal_at, "terminal_at")
            if self.status == "open" or terminal != updated:
                raise ValueError("Task terminal timestamp must match its terminal update")
        _request_identifier(self.request)
        if not isinstance(self.request_digest, str) or not _HASH.fullmatch(self.request_digest):
            raise ValueError("Task request_digest must be lowercase SHA-256")

    @classmethod
    def from_dict(cls, payload: object) -> TaskRecord:
        if not isinstance(payload, dict) or set(payload) != _RECORD_FIELDS:
            raise ValueError("Task record must contain exactly the documented fields")
        raw_due = payload["due"]
        due = None if raw_due is None else TaskDue.from_dict(raw_due)
        status = payload["status"]
        if not isinstance(status, str) or status not in _TASK_STATUSES:
            raise ValueError("Task status is invalid")
        return cls(
            task_id=cast(str, payload["task_id"]),
            opportunity_id=cast(str, payload["opportunity_id"]),
            revision=cast(int, payload["revision"]),
            description=cast(str, payload["description"]),
            due=due,
            status=cast(TaskStatus, status),
            created_at=cast(str, payload["created_at"]),
            updated_at=cast(str, payload["updated_at"]),
            terminal_at=cast(str | None, payload["terminal_at"]),
            request=cast(str, payload["request"]),
            request_digest=cast(str, payload["request_digest"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "opportunity_id": self.opportunity_id,
            "revision": self.revision,
            "description": self.description,
            "due": self.due.to_dict() if self.due is not None else None,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "terminal_at": self.terminal_at,
            "request": self.request,
            "request_digest": self.request_digest,
        }


@dataclass(frozen=True, slots=True)
class TaskReceipt:
    record: TaskRecord
    changed: bool


@dataclass(frozen=True, slots=True)
class TaskInventory:
    current: tuple[TaskRecord, ...]
    history: tuple[TaskRecord, ...]


def task_json(value: object) -> bytes:
    """Serialize task data as strict canonical UTF-8 JSON bytes."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("Task data cannot be serialized as canonical JSON") from exc


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Task ledger contains duplicate JSON keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"Task ledger contains invalid JSON constant {value}")


def _parse_canonical_json(content: bytes) -> object:
    if not isinstance(content, bytes) or not 1 <= len(content) <= MAX_TASK_LEDGER_BYTES:
        raise ValueError(f"Task ledger must contain 1-{MAX_TASK_LEDGER_BYTES} bytes")
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Task ledger must be valid bounded UTF-8 JSON") from exc
    if task_json(payload) != content:
        raise ValueError("Task ledger JSON must use canonical encoding")
    return payload


def _mutation_for_record(record: TaskRecord, previous: TaskRecord | None) -> TaskMutation:
    if record.revision == 1:
        return TaskMutation(
            "create",
            record.request,
            opportunity_id=record.opportunity_id,
            description=record.description,
            due=record.due,
        )
    if previous is None:
        raise ValueError("Task history is missing the prior revision")
    if record.terminal_at is not None:
        return TaskMutation(
            "status",
            record.request,
            task_id=record.task_id,
            expected_revision=previous.revision,
            status=cast(Literal["completed", "cancelled"], record.status),
        )
    return TaskMutation(
        "update",
        record.request,
        task_id=record.task_id,
        description=record.description,
        due=record.due,
        expected_revision=previous.revision,
    )


def _validated_operation_history(
    records: Sequence[TaskRecord],
) -> tuple[tuple[TaskMutation, TaskRecord], ...]:
    if len(records) > MAX_TASK_REVISIONS:
        raise ValueError("Task ledger revision limit has been exceeded")
    states: dict[str, TaskRecord] = {}
    requests: set[str] = set()
    distinct_tasks: set[str] = set()
    pairs: list[tuple[TaskMutation, TaskRecord]] = []
    last_timestamp: datetime | None = None

    for record in records:
        if not isinstance(record, TaskRecord):
            raise ValueError("Task history entries must be TaskRecord values")
        previous = states.get(record.task_id)
        mutation = _mutation_for_record(record, previous)
        if record.request in requests:
            raise ValueError("Task ledger request identifiers must be unique")
        requests.add(record.request)
        if record.request_digest != _mutation_digest(mutation):
            raise ValueError("Task record request digest does not match its operation")
        if (
            last_timestamp is not None
            and _timestamp(record.updated_at, "updated_at") < last_timestamp
        ):
            raise ValueError("Task ledger timestamps cannot move backwards")
        last_timestamp = _timestamp(record.updated_at, "updated_at")

        if previous is None:
            if (
                record.revision != 1
                or record.task_id != _task_id_for(mutation)
                or record.status != "open"
                or record.terminal_at is not None
                or record.created_at != record.updated_at
            ):
                raise ValueError("Task creation snapshot is invalid")
            distinct_tasks.add(record.task_id)
            if len(distinct_tasks) > MAX_TASKS:
                raise ValueError("Task ledger task limit has been exceeded")
        else:
            if (
                record.revision != previous.revision + 1
                or record.opportunity_id != previous.opportunity_id
                or record.created_at != previous.created_at
                or previous.status != "open"
                or record.updated_at < previous.updated_at
            ):
                raise ValueError("Task revision history is not a legal consecutive transition")
            if mutation.operation == "update":
                if record.status != "open" or record.terminal_at is not None:
                    raise ValueError("Task update cannot make a task terminal")
            elif mutation.operation == "status":
                if (
                    record.status != mutation.status
                    or record.terminal_at != record.updated_at
                    or record.description != previous.description
                    or record.due != previous.due
                ):
                    raise ValueError("Task terminal transition snapshot is invalid")
            else:
                raise ValueError("A task can only be created once")
        states[record.task_id] = record
        pairs.append((mutation, record))
    return tuple(pairs)


def parse_task_ledger(content: bytes, instance_hash: str) -> tuple[TaskRecord, ...]:
    """Parse and fully validate one instance-bound immutable task ledger."""
    expected_hash = _valid_instance_hash(instance_hash)
    payload = _parse_canonical_json(content)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "instance_hash", "revisions"}
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
    ):
        raise ValueError("Task ledger header or schema is invalid")
    if payload.get("instance_hash") != expected_hash:
        raise ValueError("Task ledger belongs to a different private instance")
    raw_revisions = payload.get("revisions")
    if not isinstance(raw_revisions, list) or len(raw_revisions) > MAX_TASK_REVISIONS:
        raise ValueError("Task ledger revisions must be a bounded list")
    records: list[TaskRecord] = []
    previous: dict[str, TaskRecord] = {}
    for raw in raw_revisions:
        if not isinstance(raw, dict) or set(raw) != {"operation", "record"}:
            raise ValueError("Task ledger revision must contain operation and record")
        mutation = TaskMutation.from_dict(raw["operation"])
        record = TaskRecord.from_dict(raw["record"])
        if mutation.request_id != record.request:
            raise ValueError("Task ledger request identity does not match its record")
        if record.request_digest != _mutation_digest(mutation):
            raise ValueError("Task ledger request digest does not match its operation")
        expected_mutation = _mutation_for_record(record, previous.get(record.task_id))
        if mutation != expected_mutation:
            raise ValueError("Task ledger operation does not match its immutable revision")
        records.append(record)
        previous[record.task_id] = record
    _validated_operation_history(records)
    return tuple(records)


def serialize_task_ledger(history: Sequence[TaskRecord], instance_hash: str) -> bytes:
    """Serialize validated immutable history under the selected root identity."""
    root_hash = _valid_instance_hash(instance_hash)
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        raise ValueError("Task history must be a sequence of TaskRecord values")
    pairs = _validated_operation_history(history)
    payload = {
        "schema_version": 1,
        "instance_hash": root_hash,
        "revisions": [
            {"operation": operation.to_dict(), "record": record.to_dict()}
            for operation, record in pairs
        ],
    }
    content = task_json(payload)
    if len(content) > MAX_TASK_LEDGER_BYTES:
        raise ValueError("Task ledger replacement exceeds its 1 MiB limit")
    return content


class TaskRepository(Protocol):
    def apply(self, mutation: TaskMutation) -> TaskReceipt: ...

    def list(self, opportunity_id: str | None = None) -> TaskInventory: ...


class TaskService:
    """Normalize task input and require local-user authority for each mutation."""

    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository

    @staticmethod
    def _require_actor(actor: str) -> None:
        if actor != "local_user":
            raise PermissionError("Task changes require local-user authority")

    def create(
        self,
        *,
        opportunity_id: str,
        description: str,
        due: TaskDue | None,
        request_id: str,
        actor: str,
    ) -> TaskReceipt:
        self._require_actor(actor)
        mutation = TaskMutation(
            "create",
            _request_identifier(request_id),
            opportunity_id=_opportunity_identifier(opportunity_id, "opportunity_id"),
            description=_description(description),
            due=_optional_due(due),
        )
        return self._repository.apply(mutation)

    def update(
        self,
        task_id: str,
        *,
        description: str,
        due: TaskDue | None,
        expected_revision: int,
        request_id: str,
        actor: str,
    ) -> TaskReceipt:
        self._require_actor(actor)
        mutation = TaskMutation(
            "update",
            _request_identifier(request_id),
            task_id=_task_identifier(task_id),
            description=_description(description),
            due=_optional_due(due),
            expected_revision=_revision(expected_revision),
        )
        return self._repository.apply(mutation)

    def set_status(
        self,
        task_id: str,
        *,
        status: Literal["completed", "cancelled"],
        expected_revision: int,
        request_id: str,
        actor: str,
    ) -> TaskReceipt:
        self._require_actor(actor)
        mutation = TaskMutation(
            "status",
            _request_identifier(request_id),
            task_id=_task_identifier(task_id),
            expected_revision=_revision(expected_revision),
            status=status,
        )
        return self._repository.apply(mutation)

    def list(self, opportunity_id: str | None = None) -> TaskInventory:
        identity = (
            None
            if opportunity_id is None
            else _opportunity_identifier(opportunity_id, "opportunity_id")
        )
        return self._repository.list(identity)


__all__ = [
    "MAX_TASK_DESCRIPTION_CHARS",
    "MAX_TASK_LEDGER_BYTES",
    "MAX_TASK_REVISIONS",
    "MAX_TASKS",
    "TaskDue",
    "TaskInventory",
    "TaskMutation",
    "TaskOperation",
    "TaskRecord",
    "TaskReceipt",
    "TaskService",
    "TaskStatus",
    "task_json",
]

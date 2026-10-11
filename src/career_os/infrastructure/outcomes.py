"""Single bounded, atomic append authority for public and private V2 callers."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from career_os.application.outcomes import CareerEventService, EventReceipt, OutcomeService
from career_os.file_transactions import (
    FileConflictError,
    _safe_regular_file_path,
    locked_compare_and_swap,
)
from career_os.infrastructure.evidence import read_private_json as read_private_json
from career_os.instance_paths import InstancePaths
from career_os.outcome_records import (
    MAX_LEDGER_BYTES,
    MAX_LEDGER_EVENTS,
    OutcomeLearningEvent,
    _timestamp_instant,
    validate_event,
)


@dataclass(frozen=True, slots=True)
class AppendResult:
    path: Path
    event_id: str
    appended: bool


def _parse_events(content: bytes, *, as_of: datetime | None = None) -> list[OutcomeLearningEvent]:
    cutoff: datetime | None = None
    if as_of is not None:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("Outcome Learning as_of must include a timezone")
        cutoff = as_of.astimezone(UTC)
    if len(content) > MAX_LEDGER_BYTES:
        raise ValueError("Outcome Learning ledger exceeds the 10 MB safety limit")
    events: list[OutcomeLearningEvent] = []
    seen: set[str] = set()
    record_count = 0
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid Outcome Learning ledger encoding") from exc
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        record_count += 1
        if record_count > MAX_LEDGER_EVENTS:
            raise ValueError("Outcome Learning ledger exceeds the 20,000-event safety limit")
        try:
            raw = json.loads(line)
            if cutoff is not None:
                if not isinstance(raw, dict):
                    raise ValueError("Outcome Learning event must be an object")
                if _timestamp_instant(raw.get("recorded_at"), "recorded_at") > cutoff:
                    continue
            event = OutcomeLearningEvent.from_dict(raw)
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise ValueError(f"Invalid Outcome Learning event at line {number}: {exc}") from exc
        if event.event_id in seen:
            raise ValueError(f"Invalid Outcome Learning event at line {number}: duplicate event_id")
        seen.add(event.event_id)
        events.append(event)
    return events


def load_events(path: str | Path, *, as_of: datetime | None = None) -> list[OutcomeLearningEvent]:
    try:
        source = _safe_regular_file_path(path)
    except ValueError as exc:
        raise ValueError("Outcome Learning ledger must be a regular file") from exc
    try:
        with source.open("rb") as stream:
            content = stream.read(MAX_LEDGER_BYTES + 1)
    except FileNotFoundError:
        content = b""
    return _parse_events(content, as_of=as_of)


def _append(
    path: str | Path,
    build: Callable[[tuple[OutcomeLearningEvent, ...]], OutcomeLearningEvent],
) -> tuple[OutcomeLearningEvent, bool]:
    receipt: tuple[OutcomeLearningEvent, bool] | None = None

    def append(current: bytes) -> bytes:
        nonlocal receipt
        existing = tuple(_parse_events(current))
        event = build(existing)
        validate_event(event)
        for retained in existing:
            if retained.event_id == event.event_id:
                receipt = (retained, False)
                return current
        if event.event_type == "submission_confirmed" and any(
            item.event_type == "submission_confirmed"
            and item.opportunity_id == event.opportunity_id
            for item in existing
        ):
            raise ValueError(
                "Outcome Learning already has an immutable submission for this opportunity_id"
            )
        if len(existing) >= MAX_LEDGER_EVENTS:
            raise ValueError("Outcome Learning ledger exceeds the 20,000-event safety limit")
        encoded = (json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        separator = b"\n" if current and not current.endswith(b"\n") else b""
        if len(current) + len(separator) + len(encoded) > MAX_LEDGER_BYTES:
            raise ValueError("Outcome Learning ledger exceeds the 10 MB safety limit")
        receipt = (event, True)
        return current + separator + encoded

    try:
        locked_compare_and_swap(path, append, maximum_bytes=MAX_LEDGER_BYTES)
    except FileConflictError as exc:
        raise OSError(
            "Another writer changed the event ledger; retry the confirmed request"
        ) from exc
    assert receipt is not None
    return receipt


def append_event(event: OutcomeLearningEvent, path: str | Path) -> AppendResult:
    validate_event(event)
    retained, appended = _append(path, lambda _events: event)
    return AppendResult(Path(path), retained.event_id, appended)


class LocalEventRepository:
    def __init__(self, instance_root: str | Path) -> None:
        self.paths = InstancePaths.from_root(instance_root)
        self.path = self.paths.runtime / "outcome-learning-v2.jsonl"

    def list(self) -> tuple[OutcomeLearningEvent, ...]:
        return tuple(load_events(self.path))

    def append(
        self, build: Callable[[tuple[OutcomeLearningEvent, ...]], OutcomeLearningEvent]
    ) -> EventReceipt:
        event, appended = _append(self.path, build)
        return EventReceipt(event, appended, self.path.relative_to(self.paths.root).as_posix())


def create_event_service(instance_root: str | Path) -> CareerEventService:
    from career_os.application.opportunities import OpportunityService, require_opportunity_result
    from career_os.infrastructure.opportunities import LocalOpportunityRepository

    def resolve(identity: str) -> dict[str, str]:
        state = require_opportunity_result(
            OpportunityService(
                LocalOpportunityRepository(instance_root, read_only_resolution=True)
            ).resolve_supplied(identity)
        )
        return {
            "kind": "qualitative",
            "source_hash": state.dossier.source_hash,
            "evaluation_fingerprint": state.dossier.evaluation_fingerprint,
        }

    return CareerEventService(LocalEventRepository(instance_root), assessment_resolver=resolve)


def create_outcome_service(instance_root: str | Path) -> OutcomeService:
    return OutcomeService(create_event_service(instance_root))

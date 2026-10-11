"""Public contracts for explicit, offline whole-workspace recovery."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Protocol


def recovery_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class RecoveryEntry:
    path: str
    kind: str
    size_bytes: int
    sha256: str | None


@dataclass(frozen=True, slots=True)
class RecoveryInventory:
    source_instance_hash: str
    evidence_storage_version: int | None
    entries: tuple[RecoveryEntry, ...]

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 1, **asdict(self)}

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(recovery_json(self.to_dict())).hexdigest()

    @property
    def size_bytes(self) -> int:
        return sum(entry.size_bytes for entry in self.entries)


@dataclass(frozen=True, slots=True)
class RestorePreview:
    destination: str
    inventory: RecoveryInventory
    rebinds_planned_task_instance: bool = False

    @property
    def confirmation_fingerprint(self) -> str:
        return hashlib.sha256(
            recovery_json(
                {"destination": self.destination, "backup_fingerprint": self.inventory.fingerprint}
            )
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "destination": self.destination,
            "inventory": self.inventory.to_dict(),
            "backup_fingerprint": self.inventory.fingerprint,
            "confirmation_fingerprint": self.confirmation_fingerprint,
            "requires_offline_workspace": True,
            "rebinds_evidence_instance_only": not self.rebinds_planned_task_instance,
            "rebinds_planned_task_instance": self.rebinds_planned_task_instance,
            "migrates_schema": False,
            "overwrites_existing_target": False,
        }


@dataclass(frozen=True, slots=True)
class RecoveryReceipt:
    operation: str
    destination: str
    backup_fingerprint: str
    entries: int
    size_bytes: int
    evidence_instance_rebound: bool = False

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 1, **asdict(self), "encrypted": False}


class RecoveryRepository(Protocol):
    def inventory(self) -> RecoveryInventory: ...
    def backup(self, destination: str) -> RecoveryReceipt: ...
    def verify(self, backup: str) -> RecoveryInventory: ...
    def preview_restore(self, backup: str) -> RestorePreview: ...
    def restore(self, backup: str, confirmation_fingerprint: str) -> RecoveryReceipt: ...


class InstanceRecoveryService:
    def __init__(self, repository: RecoveryRepository) -> None:
        self._repository = repository

    def inventory(self) -> RecoveryInventory:
        return self._repository.inventory()

    def verify(self, backup: str) -> RecoveryInventory:
        return self._repository.verify(backup)

    def backup(self, destination: str, *, offline: bool, actor: str) -> RecoveryReceipt:
        self._require_offline(offline, actor)
        return self._repository.backup(destination)

    def restore(
        self,
        backup: str,
        *,
        confirmation_fingerprint: str | None = None,
        offline: bool = False,
        actor: str | None = None,
    ) -> RestorePreview | RecoveryReceipt:
        if confirmation_fingerprint is None:
            return self._repository.preview_restore(backup)
        self._require_offline(offline, actor)
        return self._repository.restore(backup, confirmation_fingerprint)

    @staticmethod
    def _require_offline(offline: bool, actor: str | None) -> None:
        if offline is not True or actor != "local_user":
            raise PermissionError(
                "Local-user confirmation that all workspace writers stopped is required"
            )

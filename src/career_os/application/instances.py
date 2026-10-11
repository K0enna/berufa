"""Explicit private-workspace setup, diagnostics and backed-up upgrade contracts."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Protocol

from career_os.application.recovery import InstanceRecoveryService, RecoveryInventory, recovery_json


@dataclass(frozen=True, slots=True)
class InstanceReceipt:
    operation: str
    evidence_storage_version: int
    changed: bool
    backup_fingerprint: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 1, **asdict(self)}


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def ready(self) -> bool:
        return all(
            check.status in {"ok", "not_requested", "legacy_compatible"} for check in self.checks
        )

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 1, "ready": self.ready, **asdict(self)}


@dataclass(frozen=True, slots=True)
class UpgradePreview:
    backup_destination: str
    inventory: RecoveryInventory
    target_version: int
    migrations: tuple[str, ...]

    @property
    def confirmation_fingerprint(self) -> str:
        return hashlib.sha256(
            recovery_json(
                {
                    "operation": "upgrade",
                    "backup_destination": self.backup_destination,
                    "inventory_fingerprint": self.inventory.fingerprint,
                    "target_version": self.target_version,
                    "migrations": self.migrations,
                }
            )
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "backup_destination": self.backup_destination,
            "inventory_fingerprint": self.inventory.fingerprint,
            "from_version": self.inventory.evidence_storage_version,
            "target_version": self.target_version,
            "migrations": self.migrations,
            "already_current": self.inventory.evidence_storage_version == self.target_version,
            "confirmation_fingerprint": self.confirmation_fingerprint,
            "requires_offline_workspace": True,
        }


class InstanceRepository(Protocol):
    def initialize(self) -> InstanceReceipt: ...
    def doctor(self, listener_port: int | None) -> DoctorReport: ...
    def preview_upgrade(self, backup_destination: str) -> UpgradePreview: ...
    def upgrade(
        self, backup_destination: str, confirmation_fingerprint: str
    ) -> InstanceReceipt: ...


class InstanceService:
    def __init__(self, repository: InstanceRepository) -> None:
        self._repository = repository

    def initialize(self, *, actor: str) -> InstanceReceipt:
        if actor != "local_user":
            raise PermissionError("Only the local user can initialize a private workspace")
        return self._repository.initialize()

    def doctor(self, *, listener_port: int | None = None) -> DoctorReport:
        if listener_port is not None and (
            type(listener_port) is not int or not 1 <= listener_port <= 65535
        ):
            raise ValueError("Listener port must be an integer between 1 and 65535")
        return self._repository.doctor(listener_port)

    def upgrade(
        self,
        backup_destination: str,
        *,
        confirmation_fingerprint: str | None = None,
        offline: bool = False,
        actor: str | None = None,
    ) -> UpgradePreview | InstanceReceipt:
        if confirmation_fingerprint is None:
            return self._repository.preview_upgrade(backup_destination)
        InstanceRecoveryService._require_offline(offline, actor)
        return self._repository.upgrade(backup_destination, confirmation_fingerprint)

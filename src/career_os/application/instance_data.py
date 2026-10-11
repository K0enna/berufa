"""Explicit export and deletion contracts for one initialized private workspace."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Protocol

from career_os.application.recovery import (
    InstanceRecoveryService,
    RecoveryInventory,
    recovery_json,
)


def user_data_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


RETAINED_OUTSIDE_WORKSPACE = (
    "Installed Rolevane code outside the selected workspace is retained.",
    "Source documents stored outside the selected workspace are not followed or removed.",
    "Separately stored backups and exports outside the selected workspace are retained.",
)


@dataclass(frozen=True, slots=True)
class UserDataExportReceipt:
    destination: str
    relative_file: str
    sha256: str
    size_bytes: int
    record_counts: tuple[tuple[str, int], ...]
    historical_opportunity_archive: bool = False

    def to_dict(self) -> dict[str, object]:
        payload = {
            "schema_version": 1,
            "operation": "export",
            "destination": self.destination,
            "relative_file": self.relative_file,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "record_counts": dict(self.record_counts),
            "portable": True,
            "inspectable": True,
            "restorable": False,
            "credentials_included": False,
            "internal_security_metadata_included": False,
        }
        if self.historical_opportunity_archive:
            payload.update(
                {
                    "export_mode": "historical_opportunity_archive",
                    "historical_archive_scope": "supplied_request_and_dossier_bytes",
                    "non_opportunity_record_validation": "unchanged_strict",
                }
            )
        return payload


@dataclass(frozen=True, slots=True)
class InstanceDeletePreview:
    inventory: RecoveryInventory

    @property
    def confirmation_fingerprint(self) -> str:
        return hashlib.sha256(
            recovery_json(
                {
                    "operation": "delete",
                    "instance_hash": self.inventory.source_instance_hash,
                    "inventory_fingerprint": self.inventory.fingerprint,
                }
            )
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "operation": "delete_preview",
            "inventory": self.inventory.to_dict(),
            "inventory_fingerprint": self.inventory.fingerprint,
            "confirmation_fingerprint": self.confirmation_fingerprint,
            "requires_offline_workspace": True,
            "deletes_selected_workspace_only": True,
            "logical_deletion_only": True,
            "retained_outside_workspace": RETAINED_OUTSIDE_WORKSPACE,
        }


@dataclass(frozen=True, slots=True)
class InstanceDeleteReceipt:
    removed_entries: int
    removed_bytes: int
    instance_removed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "operation": "delete",
            **asdict(self),
            "logical_deletion_only": True,
            "retained_outside_workspace": RETAINED_OUTSIDE_WORKSPACE,
        }


class InstanceDataRepository(Protocol):
    def export_user_data(
        self, destination: str, *, historical_archive: bool = False
    ) -> UserDataExportReceipt: ...
    def preview_delete(self) -> InstanceDeletePreview: ...
    def delete(self, confirmation_fingerprint: str) -> InstanceDeleteReceipt: ...


class InstanceDataService:
    def __init__(self, repository: InstanceDataRepository) -> None:
        self._repository = repository

    def export_user_data(
        self,
        destination: str,
        *,
        offline: bool,
        actor: str,
        historical_archive: bool = False,
    ) -> UserDataExportReceipt:
        InstanceRecoveryService._require_offline(offline, actor)
        if type(historical_archive) is not bool:
            raise ValueError("Historical archive mode must be a boolean")
        if historical_archive:
            return self._repository.export_user_data(destination, historical_archive=True)
        return self._repository.export_user_data(destination)

    def delete(
        self,
        *,
        confirmation_fingerprint: str | None = None,
        offline: bool = False,
        actor: str | None = None,
    ) -> InstanceDeletePreview | InstanceDeleteReceipt:
        if confirmation_fingerprint is None:
            return self._repository.preview_delete()
        InstanceRecoveryService._require_offline(offline, actor)
        return self._repository.delete(confirmation_fingerprint)

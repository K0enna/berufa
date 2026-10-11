"""Local instance lifecycle; diagnostics never include workspace content or raw errors."""

from __future__ import annotations

import hashlib
import http.client
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from importlib import metadata
from pathlib import Path

from career_os import __version__
from career_os.application.instances import (
    DoctorCheck,
    DoctorReport,
    InstanceReceipt,
    InstanceService,
    UpgradePreview,
)
from career_os.evidence_vault import EvidenceVault
from career_os.file_transactions import _fsync_directory, _safe_regular_file_path
from career_os.infrastructure import evidence_storage, recovery
from career_os.infrastructure.documents import inspect_document_archive
from career_os.infrastructure.outcomes import LocalEventRepository

LAYOUT = ("inbox", "applications", "exports", "tmp", "data", "data/runtime", "data/logs")
# These persisted bytes are part of the initialized-workspace integrity check.
PRIVATE_IGNORE = b"# Private Career OS workspace: do not commit candidate data.\n*\n!.gitignore\n"
LAYOUT_MARKER = ".career-os-instance.json"
LAYOUT_MARKER_CONTENT = (
    json.dumps(
        {"identity": evidence_storage.INSTANCE_LAYOUT_IDENTITY, "schema_version": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
    + "\n"
).encode("ascii")


def _runtime_supported() -> bool:
    return sys.version_info >= (3, 11) and sys.platform in {"win32", "linux"}


def _environment_checks() -> tuple[DoctorCheck, ...]:
    distribution_name = "rolevane"
    try:
        installed = metadata.version(distribution_name)
        package = "ok" if installed == __version__ else "version_mismatch"
    except metadata.PackageNotFoundError:
        distribution_name = "career-os"
        try:
            installed = metadata.version(distribution_name)
            package = "ok" if installed == __version__ else "version_mismatch"
        except metadata.PackageNotFoundError:
            package = "not_installed"
    try:
        # pip check is local-only and uses the environment's dependency resolver. Its
        # output is inspected only to distinguish this distribution from unrelated failures.
        result = subprocess.run(
            [sys.executable, "-I", "-m", "pip", "--disable-pip-version-check", "check"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        own_failure = any(
            line.partition(" ")[0].casefold().replace("_", "-") == distribution_name
            for line in result.stdout.splitlines()
        )
        if result.returncode == 0:
            dependencies = "ok"
        elif result.returncode == 1 and result.stdout:
            dependencies = "missing_or_incompatible" if own_failure else "ok"
        else:
            dependencies = "unavailable"
    except (OSError, subprocess.TimeoutExpired):
        dependencies = "unavailable"
    return (
        DoctorCheck("runtime", "ok" if _runtime_supported() else "unsupported"),
        DoctorCheck("package", package),
        DoctorCheck("dependencies", dependencies),
    )


def _listener_status(root: Path, port: int | None) -> str:
    if port is None:
        return "not_requested"
    try:
        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=2)) as connection:
            connection.request("GET", "/health")
            response = connection.getresponse()
            raw = response.read(4097)
            if response.status != 200 or len(raw) > 4096:
                return "invalid_response"
            payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 1
            or payload.get("status") != "ok"
            or payload.get("model_calls") is not False
            or payload.get("external_actions") is not False
        ):
            return "invalid_response"
        expected = hashlib.sha256(str(root).encode()).hexdigest()
        return "ok" if payload.get("instance_root_hash") == expected else "different_workspace"
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        return "unavailable"


class LocalInstanceRepository:
    def __init__(self, instance_root: str | Path) -> None:
        # Defer the boundary check so doctor can report an invalid path without echoing it.
        self.requested_root = instance_root

    def initialize(self) -> InstanceReceipt:
        if not _runtime_supported():
            raise ValueError("Unsupported runtime; no workspace was created")
        root = recovery._root(self.requested_root)
        if root.exists() or not root.parent.is_dir():
            raise ValueError("Choose a new workspace beneath an existing parent directory")
        recovery._space(root, 0)
        recovery._create_marked_target(
            root, b"Incomplete initialization; do not use this workspace.\n"
        )
        for name in LAYOUT:
            (root / name).mkdir(mode=0o700)
        recovery._write_new(root / ".gitignore", PRIVATE_IGNORE)
        vault = EvidenceVault(root)
        vault.initialize()
        try:
            with closing(sqlite3.connect(vault.path)) as connection:
                try:
                    evidence_storage.mark_initialized_storage(connection, root)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except sqlite3.Error as exc:
            raise OSError("Private workspace identity could not be recorded") from exc
        recovery._write_new(root / LAYOUT_MARKER, LAYOUT_MARKER_CONTENT)
        for name in reversed(LAYOUT):
            _fsync_directory(root / name)
        (root / recovery.INCOMPLETE).unlink()
        _fsync_directory(root)
        _fsync_directory(root.parent)
        return InstanceReceipt("init", evidence_storage.EVIDENCE_STORAGE_VERSION, True)

    def doctor(self, listener_port: int | None) -> DoctorReport:
        checks = list(_environment_checks())
        try:
            root = recovery._root(self.requested_root)
        except (ValueError, OSError):
            return DoctorReport(
                tuple(
                    checks
                    + [
                        DoctorCheck("workspace", "unsafe_path"),
                        DoctorCheck("layout", "unavailable"),
                        DoctorCheck("evidence_storage", "unavailable"),
                        DoctorCheck("listener", "unavailable"),
                    ]
                )
            )
        if not root.is_dir():
            checks.append(DoctorCheck("workspace", "missing"))
            checks.append(DoctorCheck("layout", "setup_required"))
            checks.append(DoctorCheck("evidence_storage", "missing"))
        elif (root / recovery.INCOMPLETE).exists():
            checks.append(DoctorCheck("workspace", "incomplete"))
            checks.append(DoctorCheck("layout", "unavailable"))
            checks.append(DoctorCheck("evidence_storage", "unavailable"))
        else:
            try:
                inventory = recovery.LocalRecoveryRepository(root).inventory()
                version = inventory.evidence_storage_version
                layout_identity = None
                if version == evidence_storage.EVIDENCE_STORAGE_VERSION:
                    database = _safe_regular_file_path(root / recovery.DATABASE)
                    try:
                        with closing(
                            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0)
                        ) as connection:
                            connection.row_factory = sqlite3.Row
                            connection.execute("PRAGMA foreign_keys = ON")
                            connection.execute("PRAGMA trusted_schema = OFF")
                            connection.execute("BEGIN")
                            layout_identity = evidence_storage.validate_current_storage(
                                connection, root
                            )
                    except sqlite3.Error as exc:
                        raise OSError("Evidence storage inspection failed") from exc
                checks.append(DoctorCheck("workspace", "ok"))
                entries = {entry.path: entry for entry in inventory.entries}
                layout_ok = all(
                    name in entries and entries[name].kind == "directory" for name in LAYOUT
                )
                ignore = entries.get(".gitignore")
                marker = entries.get(LAYOUT_MARKER)
                marker_valid = (
                    marker is not None
                    and marker.kind == "file"
                    and marker.sha256 == hashlib.sha256(LAYOUT_MARKER_CONTENT).hexdigest()
                )
                initialized_layout = (
                    layout_ok
                    and ignore is not None
                    and ignore.sha256 == hashlib.sha256(PRIVATE_IGNORE).hexdigest()
                    and marker_valid
                    and layout_identity == evidence_storage.INSTANCE_LAYOUT_IDENTITY
                )
                existing_layout_safe = all(
                    name not in entries or entries[name].kind == "directory" for name in LAYOUT
                )
                legacy_layout = (
                    version == evidence_storage.EVIDENCE_STORAGE_VERSION
                    and layout_identity is None
                    and marker is None
                    and ignore is None
                    and existing_layout_safe
                )
                checks.append(
                    DoctorCheck(
                        "layout",
                        "ok"
                        if initialized_layout
                        else ("legacy_compatible" if legacy_layout else "setup_required"),
                    )
                )
                checks.append(
                    DoctorCheck(
                        "evidence_storage",
                        "missing"
                        if version is None
                        else (
                            "ok"
                            if version == evidence_storage.EVIDENCE_STORAGE_VERSION
                            else "upgrade_required"
                        ),
                    )
                )
            except (ValueError, OSError):
                checks.append(DoctorCheck("workspace", "invalid_or_busy"))
                checks.append(DoctorCheck("layout", "unavailable"))
                checks.append(DoctorCheck("evidence_storage", "unavailable"))
        checks.append(DoctorCheck("listener", _listener_status(root, listener_port)))
        try:
            recovery._task_history(root, hashlib.sha256(str(root).encode()).hexdigest())
            checks.append(DoctorCheck("planned_tasks", "ok"))
        except (ValueError, OSError):
            checks.append(DoctorCheck("planned_tasks", "invalid_or_unavailable"))
        try:
            inspect_document_archive(root)
            checks.append(DoctorCheck("content_documents", "ok"))
        except (ValueError, OSError):
            checks.append(DoctorCheck("content_documents", "invalid_or_unavailable"))
        try:
            LocalEventRepository(root).list()
            checks.append(DoctorCheck("confirmed_events", "ok"))
        except (ValueError, OSError):
            checks.append(DoctorCheck("confirmed_events", "invalid_or_unavailable"))
        return DoctorReport(tuple(checks))

    def preview_upgrade(self, backup_destination: str) -> UpgradePreview:
        if not _runtime_supported():
            raise ValueError("Unsupported upgrade runtime")
        root = recovery._root(self.requested_root)
        inventory = recovery.LocalRecoveryRepository(root).inventory()
        version = inventory.evidence_storage_version
        if version is None:
            raise ValueError("No evidence database to upgrade")
        target = recovery._root(backup_destination)
        if target.is_relative_to(root) or root.is_relative_to(target):
            raise ValueError("Upgrade backup must be outside the workspace")
        if version < evidence_storage.EVIDENCE_STORAGE_VERSION:
            target = recovery._new_target(target, root)
        return UpgradePreview(
            str(target),
            inventory,
            evidence_storage.EVIDENCE_STORAGE_VERSION,
            tuple(
                identity
                for number, identity in evidence_storage.MIGRATION_IDENTITIES.items()
                if number > version
            ),
        )

    def upgrade(self, backup_destination: str, confirmation_fingerprint: str) -> InstanceReceipt:
        preview = self.preview_upgrade(backup_destination)
        if preview.inventory.evidence_storage_version == preview.target_version:
            return InstanceReceipt("upgrade", preview.target_version, False)
        if confirmation_fingerprint != preview.confirmation_fingerprint:
            raise PermissionError("Upgrade preview changed; confirm the exact workspace and backup")
        root = recovery._root(self.requested_root)
        repository = recovery.LocalRecoveryRepository(root)
        repository.backup(preview.backup_destination)
        if repository.verify(preview.backup_destination) != preview.inventory:
            raise ValueError("Workspace changed before backup; preview the upgrade again")
        database = _safe_regular_file_path(root / recovery.DATABASE)
        recovery._space_on_volume(database, database.stat().st_size)
        try:
            # Reacquire exclusivity after backup, then compare the entire source before any
            # migration writes. The user must still stop file-only writers for this workflow.
            with closing(
                sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=0)
            ) as connection:
                try:
                    connection.row_factory = sqlite3.Row
                    connection.execute("PRAGMA foreign_keys = ON")
                    connection.execute("PRAGMA trusted_schema = OFF")
                    connection.execute("BEGIN EXCLUSIVE")
                    version = evidence_storage.validate_storage(connection, root)
                    if (
                        version != preview.inventory.evidence_storage_version
                        or recovery._scan(root) != preview.inventory.entries
                    ):
                        raise ValueError("Workspace changed after backup; upgrade was not applied")
                    evidence_storage.validate_storage_objects(connection)
                    evidence_storage.migrate_storage(connection, root)
                    evidence_storage.validate_current_storage(connection, root)
                    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                        raise ValueError("Upgraded evidence relationships are invalid")
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except sqlite3.Error as exc:
            raise OSError(
                "Upgrade failed; source transaction rolled back and backup retained"
            ) from exc
        return InstanceReceipt(
            "upgrade", preview.target_version, True, preview.inventory.fingerprint
        )


def create_instance_service(instance_root: str | Path) -> InstanceService:
    return InstanceService(LocalInstanceRepository(instance_root))

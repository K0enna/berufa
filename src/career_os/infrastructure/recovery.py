"""Bounded offline directory snapshots; no live-backup or encryption claim."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import shutil
import sqlite3
import stat
import unicodedata
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from pathlib import Path, PurePosixPath, PureWindowsPath

from career_os.application.recovery import (
    InstanceRecoveryService,
    RecoveryEntry,
    RecoveryInventory,
    RecoveryReceipt,
    RestorePreview,
    recovery_json,
)
from career_os.application.tasks import TaskRecord, parse_task_ledger, serialize_task_ledger
from career_os.file_transactions import _fsync_directory, _safe_regular_file_path
from career_os.infrastructure.documents import inspect_document_archive
from career_os.infrastructure.evidence import read_private_json
from career_os.infrastructure.evidence_storage import (
    EVIDENCE_STORAGE_VERSION,
    rebind_restored_storage,
    validate_current_storage_identity,
    validate_storage_identity,
)

MAX_ENTRIES = 10_000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
SPACE_MARGIN_BYTES = 16 * 1024 * 1024
DATABASE = "data/evidence-vault.db"
INCOMPLETE = ".career-os-recovery-incomplete"
MANIFEST = "backup.json"
PAYLOAD = "workspace"
TASK_LEDGER = "data/runtime/planned-tasks.json"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
# Python 3.11/3.12 need the pathlib fallback; newer runtimes provide ntpath.isreserved.
_windows_reserved = getattr(ntpath, "isreserved", lambda name: PureWindowsPath(name).is_reserved())


def _root(path: str | Path) -> Path:
    requested = Path(path).expanduser().absolute()
    if ".." in requested.parts:
        raise ValueError("Recovery paths must not contain parent traversal")
    _safe_regular_file_path(requested / INCOMPLETE)
    selected = requested.resolve(strict=False)
    if selected == Path(selected.anchor) or selected == Path.home().resolve():
        raise ValueError("Select a dedicated workspace, not a filesystem or home root")
    installed_code = Path(__file__).resolve().parents[2]
    if selected.is_relative_to(installed_code):
        raise ValueError("Private recovery data must stay outside installed code")
    for parent in (selected, *selected.parents):
        if (parent / ".git").exists() or (parent / "pyvenv.cfg").exists():
            raise ValueError(
                "Private recovery data must stay outside repositories and environments"
            )
    return selected


def _new_target(path: str | Path, source: Path) -> Path:
    selected = _root(path)
    if selected.is_relative_to(source) or source.is_relative_to(selected):
        raise ValueError("Recovery source and destination must be separate directories")
    if selected.exists():
        raise ValueError("Recovery destination already exists; choose a new directory")
    if not selected.parent.is_dir():
        raise ValueError("Create the destination parent directory before recovery")
    return selected


def _portable_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("Invalid recovery inventory path")
    value.encode("utf-8")
    parts = value.split("/")
    if any(
        part in {"", ".", "..", ".git", INCOMPLETE}
        or part.startswith(".prepare-")
        or part[-1:] in {" ", "."}
        or _windows_reserved(part)
        or any(ord(char) < 32 or char in '\\:<>"|?*' for char in part)
        for part in parts
    ):
        raise ValueError("Workspace contains an unsafe, incomplete or nonportable path")
    if PurePosixPath(value).as_posix() != value:
        raise ValueError("Invalid recovery inventory path")
    return value


def _signature(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


def _file_entry(path: Path, relative: str) -> RecoveryEntry:
    _safe_regular_file_path(path)
    before = path.stat()
    if before.st_nlink != 1 or before.st_size > MAX_FILE_BYTES:
        raise ValueError("Linked or oversized workspace file cannot be backed up")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        header = stream.read(32)
        if header.startswith(b"SQLite format 3\x00") and relative != DATABASE:
            raise ValueError("An unsupported database needs its own explicit recovery contract")
        digest.update(header)
        size += len(header)
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise ValueError("Workspace file exceeds the recovery size limit")
            digest.update(chunk)
    if _signature(before) != _signature(path.stat()) or size != before.st_size:
        raise ValueError("Workspace changed during recovery; stop every writer and retry")
    return RecoveryEntry(relative, "file", size, digest.hexdigest())


def _scan(root: Path, *, restoring: bool = False) -> tuple[RecoveryEntry, ...]:
    if not root.is_dir() or ((root / INCOMPLETE).exists() and not restoring):
        raise ValueError("Workspace is missing or recovery is incomplete")
    entries: list[RecoveryEntry] = []
    names: set[str] = set()
    pending = [root]
    total = 0
    while pending:
        parent = pending.pop()
        _safe_regular_file_path(parent / INCOMPLETE)
        with os.scandir(parent) as children:
            for child in children:
                if restoring and parent == root and child.name == INCOMPLETE:
                    continue
                if len(entries) >= MAX_ENTRIES:
                    raise ValueError("Workspace exceeds the recovery entry limit")
                path = Path(child.path)
                relative = _portable_path(path.relative_to(root).as_posix())
                key = unicodedata.normalize("NFC", relative).casefold()
                if key in names:
                    raise ValueError("Workspace names collide on supported filesystems")
                names.add(key)
                if relative in {DATABASE + suffix for suffix in ("-wal", "-shm", "-journal")}:
                    raise ValueError(
                        "SQLite sidecar state must be resolved before offline recovery"
                    )
                metadata = child.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    _safe_regular_file_path(path / INCOMPLETE)
                    pending.append(path)
                    entry = RecoveryEntry(relative, "directory", 0, None)
                elif stat.S_ISREG(metadata.st_mode):
                    entry = _file_entry(path, relative)
                else:
                    raise ValueError("Workspace contains a linked or special filesystem entry")
                entries.append(entry)
                total += entry.size_bytes
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("Workspace exceeds the total recovery byte limit")
    return tuple(sorted(entries, key=lambda entry: entry.path))


@contextmanager
def _database_guard(root: Path, instance_hash: str) -> Iterator[int | None]:
    database = _safe_regular_file_path(root / DATABASE)
    if not database.exists():
        yield None
        return
    if database.stat().st_nlink != 1:
        raise ValueError("Evidence database must not be hard linked")
    for suffix in ("-wal", "-shm", "-journal"):
        if database.with_name(database.name + suffix).exists():
            raise ValueError("Resolve SQLite sidecar state before offline recovery")
    with database.open("rb") as stream:
        header = stream.read(32)
    if not header.startswith(b"SQLite format 3\x00") or header[18:20] != b"\x01\x01":
        raise ValueError("Evidence database is invalid or is not in offline rollback-journal mode")
    try:
        # No SQL writes: the exclusive transaction detects busy SQLite users and prevents
        # cooperative vault writes while the exact original database bytes are copied.
        connection = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=0)
        try:
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("BEGIN EXCLUSIVE")
            version = validate_storage_identity(connection, instance_hash)
            if version == EVIDENCE_STORAGE_VERSION:
                validate_current_storage_identity(connection, instance_hash)
            elif connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Evidence database integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Evidence database relationships are invalid")
            yield version
        finally:
            connection.rollback()
            connection.close()
    except sqlite3.Error as exc:
        raise OSError(
            "Evidence database is busy, corrupt or unavailable; no source data was changed"
        ) from exc


def _space_on_volume(path: Path, size: int) -> None:
    if shutil.disk_usage(path).free < size * 2 + SPACE_MARGIN_BYTES:
        raise OSError("Insufficient free space for recovery and its database transaction")


def _space(target: Path, size: int) -> None:
    _space_on_volume(target.parent, size)


def _write_new(path: Path, content: bytes) -> None:
    _safe_regular_file_path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _create_marked_target(target: Path, message: bytes) -> None:
    target.mkdir(mode=0o700)
    try:
        _write_new(target / INCOMPLETE, message)
    except OSError:
        # Only remove our new directory if it is still empty. A partial marker or
        # any concurrently introduced file is retained; never recursively clean data.
        with suppress(OSError):
            target.rmdir()
        raise
    _fsync_directory(target)
    _fsync_directory(target.parent)


def _copy(source: Path, target: Path, entries: tuple[RecoveryEntry, ...]) -> None:
    for entry in entries:
        destination = target / entry.path
        if entry.kind == "directory":
            destination.mkdir(mode=0o700)
            continue
        origin = _safe_regular_file_path(source / entry.path)
        _safe_regular_file_path(destination)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output, origin.open("rb") as incoming:
            digest = hashlib.sha256()
            size = 0
            while chunk := incoming.read(1024 * 1024):
                size += len(chunk)
                if size > entry.size_bytes:
                    raise ValueError("Recovery source changed after its inventory")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != entry.size_bytes or digest.hexdigest() != entry.sha256:
            raise ValueError("Recovery source does not match its inventory")
    for entry in reversed(entries):
        if entry.kind == "directory":
            _fsync_directory(target / entry.path)
    _fsync_directory(target)


def _read_inventory(backup: Path) -> RecoveryInventory:
    if not backup.is_dir() or {p.name for p in backup.iterdir()} != {MANIFEST, PAYLOAD}:
        raise ValueError("Backup is incomplete or has unexpected entries")
    path = _safe_regular_file_path(backup / MANIFEST)
    if path.stat().st_nlink != 1:
        raise ValueError("Backup manifest must not be hard linked")
    with path.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Backup manifest exceeds the byte limit")
    try:
        value = json.loads(raw)
    except RecursionError as exc:
        raise ValueError("Backup manifest nesting exceeds the parser limit") from exc
    if (
        not isinstance(value, dict)
        or set(value)
        != {"schema_version", "source_instance_hash", "evidence_storage_version", "entries"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("Unsupported backup manifest")
    digest = value["source_instance_hash"]
    version = value["evidence_storage_version"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError("Invalid backup instance binding")
    if version is not None and (type(version) is not int or version not in {1, 2, 3, 4}):
        raise ValueError("Unsupported backup evidence schema")
    rows = value["entries"]
    if not isinstance(rows, list) or len(rows) > MAX_ENTRIES:
        raise ValueError("Invalid backup inventory")
    entries = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "kind", "size_bytes", "sha256"}:
            raise ValueError("Invalid backup inventory entry")
        path = _portable_path(row["path"])
        kind, size, checksum = row["kind"], row["size_bytes"], row["sha256"]
        if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES:
            raise ValueError("Invalid backup inventory size")
        if not (
            (kind == "directory" and size == 0 and checksum is None)
            or (kind == "file" and isinstance(checksum, str) and _DIGEST.fullmatch(checksum))
        ):
            raise ValueError("Invalid backup inventory kind or checksum")
        entries.append(RecoveryEntry(path, kind, size, checksum))
    inventory = RecoveryInventory(digest, version, tuple(entries))
    if inventory.size_bytes > MAX_TOTAL_BYTES or recovery_json(inventory.to_dict()) != raw:
        raise ValueError("Backup manifest is noncanonical or exceeds the total byte limit")
    return inventory


def _task_history(root: Path, instance_hash: str) -> tuple[TaskRecord, ...]:
    """Validate retained plans independently of live consent/restore readiness."""
    try:
        _, content = read_private_json(
            root, root / TASK_LEDGER, label="Local task history", maximum_bytes=1024 * 1024
        )
    except FileNotFoundError:
        return ()
    return parse_task_ledger(content, instance_hash)


class LocalRecoveryRepository:
    def __init__(self, instance_root: str | Path) -> None:
        self.root = _root(instance_root)

    def inventory(self) -> RecoveryInventory:
        instance_hash = hashlib.sha256(str(self.root).encode()).hexdigest()
        with _database_guard(self.root, instance_hash) as version:
            entries = _scan(self.root)
            _task_history(self.root, instance_hash)
            inspect_document_archive(self.root)
            return RecoveryInventory(instance_hash, version, entries)

    def backup(self, destination: str) -> RecoveryReceipt:
        target = _new_target(destination, self.root)
        instance_hash = hashlib.sha256(str(self.root).encode()).hexdigest()
        with _database_guard(self.root, instance_hash) as version:
            _task_history(self.root, instance_hash)
            inspect_document_archive(self.root)
            inventory = RecoveryInventory(instance_hash, version, _scan(self.root))
            if len(recovery_json(inventory.to_dict())) > MAX_MANIFEST_BYTES:
                raise ValueError("Workspace inventory exceeds the manifest byte limit")
            _space(target, inventory.size_bytes)
            _create_marked_target(target, b"Incomplete backup; do not restore.\n")
            payload = target / PAYLOAD
            payload.mkdir(mode=0o700)
            _copy(self.root, payload, inventory.entries)
            if _scan(self.root) != inventory.entries or _scan(payload) != inventory.entries:
                raise ValueError(
                    "Workspace changed during backup; the incomplete copy was retained"
                )
            _write_new(target / MANIFEST, recovery_json(inventory.to_dict()))
            (target / INCOMPLETE).unlink()
            _fsync_directory(target)
            _fsync_directory(target.parent)
        return RecoveryReceipt(
            "backup",
            str(target),
            inventory.fingerprint,
            len(inventory.entries),
            inventory.size_bytes,
        )

    def verify(self, backup: str) -> RecoveryInventory:
        source = _root(backup)
        inventory = _read_inventory(source)
        payload = source / PAYLOAD
        with _database_guard(payload, inventory.source_instance_hash) as version:
            if version != inventory.evidence_storage_version or _scan(payload) != inventory.entries:
                raise ValueError("Backup payload does not match its complete inventory")
            _task_history(payload, inventory.source_instance_hash)
            inspect_document_archive(payload)
        return inventory

    def preview_restore(self, backup: str) -> RestorePreview:
        source = _root(backup)
        _new_target(self.root, source)
        inventory = self.verify(str(source))
        return RestorePreview(
            str(self.root),
            inventory,
            rebinds_planned_task_instance=any(
                entry.path == TASK_LEDGER for entry in inventory.entries
            ),
        )

    def restore(self, backup: str, confirmation_fingerprint: str) -> RecoveryReceipt:
        source = _root(backup)
        preview = self.preview_restore(str(source))
        if confirmation_fingerprint != preview.confirmation_fingerprint:
            raise PermissionError(
                "Restore preview changed; confirm the exact backup and destination"
            )
        inventory = preview.inventory
        _space(self.root, inventory.size_bytes)
        _create_marked_target(self.root, b"Incomplete restore; do not run Rolevane here.\n")
        # An incomplete destination is deliberately retained for inspection. Neither the
        # original workspace nor backup is deleted or overwritten on any failure path.
        _copy(source / PAYLOAD, self.root, inventory.entries)
        if _scan(self.root, restoring=True) != inventory.entries:
            raise ValueError("Restored files do not match the backup inventory")
        if self.verify(str(source)) != inventory:
            raise ValueError("Backup changed during restore")
        if inventory.evidence_storage_version is not None:
            database = _safe_regular_file_path(self.root / DATABASE)
            try:
                with closing(sqlite3.connect(database)) as connection, connection:
                    rebind_restored_storage(connection, inventory.source_instance_hash, self.root)
            except sqlite3.Error as exc:
                raise OSError("Restored evidence database could not be rebound") from exc
        task_history = _task_history(self.root, inventory.source_instance_hash)
        inspect_document_archive(self.root)
        task_path = _safe_regular_file_path(self.root / TASK_LEDGER)
        if task_path.exists():
            # Restore owns this new, marked destination exclusively. Rebind only
            # security metadata; immutable task requests and history stay intact.
            content = serialize_task_ledger(
                task_history, hashlib.sha256(str(self.root).encode()).hexdigest()
            )
            with task_path.open("wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            _task_history(self.root, hashlib.sha256(str(self.root).encode()).hexdigest())
        (self.root / INCOMPLETE).unlink()
        _fsync_directory(self.root)
        _fsync_directory(self.root.parent)
        return RecoveryReceipt(
            "restore",
            str(self.root),
            inventory.fingerprint,
            len(inventory.entries),
            inventory.size_bytes,
            inventory.evidence_storage_version is not None,
        )


def create_recovery_service(instance_root: str | Path) -> InstanceRecoveryService:
    return InstanceRecoveryService(LocalRecoveryRepository(instance_root))

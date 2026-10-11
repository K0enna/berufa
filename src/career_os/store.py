from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from career_os.models import Compensation, JobPosting, ScoreResult, utc_now_iso

_JOB_CONTENT_HASH_VERSION = "2"
_JOB_CONTENT_HASH_VERSION_KEY = "job_content_hash_version"
_JOB_MATCH_FINGERPRINT_VERSION = "1"
_JOB_MATCH_FINGERPRINT_VERSION_KEY = "job_match_fingerprint_version"
_JOB_RELATION_LAYOUT_VERSION = "2"
_JOB_RELATION_LAYOUT_VERSION_KEY = "job_relation_layout_version"
_DEFAULT_PRODUCTION_DATABASE = Path("data/runtime/career.db")
_LEGACY_PRODUCTION_SCHEMA_V5 = {
    "sources": (
        "source_id",
        "provider",
        "company",
        "last_success_at",
        "last_error_at",
        "last_error",
    ),
    "jobs": (
        "job_key",
        "provider",
        "source_id",
        "external_id",
        "company",
        "title",
        "url",
        "apply_url",
        "location",
        "description",
        "department",
        "employment_type",
        "workplace_type",
        "is_remote",
        "posted_at",
        "updated_at",
        "compensation_json",
        "content_hash",
        "first_seen_at",
        "last_seen_at",
        "active",
        "missing_observations",
        "missing_observed_at",
        "raw_json",
    ),
    "job_history": ("id", "job_key", "observed_at", "content_hash", "snapshot_json"),
    "scores": (
        "job_key",
        "total",
        "confidence",
        "recommendation",
        "dimensions_json",
        "reasons_json",
        "risks_json",
        "hard_gates_json",
        "scored_at",
    ),
    "discovery_runs": (
        "run_id",
        "started_at",
        "sources_attempted",
        "sources_succeeded",
        "jobs_seen",
        "jobs_new",
        "jobs_changed",
        "errors_json",
    ),
}
_LEGACY_PRODUCTION_SCHEMA_V29 = {
    **_LEGACY_PRODUCTION_SCHEMA_V5,
    "jobs": (
        "job_key",
        "provider",
        "source_id",
        "external_id",
        "company",
        "title",
        "url",
        "apply_url",
        "location",
        "description",
        "department",
        "employment_type",
        "workplace_type",
        "is_remote",
        "posted_at",
        "updated_at",
        "compensation_json",
        "content_hash",
        "first_seen_at",
        "last_seen_at",
        "active",
        "missing_observations",
        "missing_observed_at",
        "raw_json",
        "match_fingerprint",
    ),
    "scores": (
        "job_key",
        "total",
        "confidence",
        "recommendation",
        "dimensions_json",
        "reasons_json",
        "risks_json",
        "hard_gates_json",
        "scored_at",
        "scorecard_version",
        "job_content_hash",
        "assessment_stage",
        "details_json",
    ),
    "discovery_runs": (
        "run_id",
        "started_at",
        "completed_at",
        "sources_attempted",
        "sources_succeeded",
        "jobs_seen",
        "jobs_new",
        "jobs_changed",
        "errors_json",
    ),
    "job_relations": (
        "relation_id",
        "left_job_key",
        "right_job_key",
        "relation_type",
        "confidence",
        "rule",
        "state",
        "created_at",
        "reviewed_at",
        "reviewed_by",
    ),
    "source_observations": (
        "observation_id",
        "source_id",
        "run_id",
        "observed_at",
        "success",
        "jobs_seen",
        "error",
    ),
    "radar_leads": (
        "url_key",
        "input_url",
        "canonical_url",
        "company",
        "title",
        "first_seen_at",
        "last_seen_at",
        "times_seen",
        "last_status",
        "result_json",
    ),
    "pipeline_projection": (
        "pipeline_key",
        "section",
        "priority",
        "company",
        "role",
        "status",
        "fit_summary",
        "next_action",
        "due_date",
        "source_line",
        "source_file",
        "source_hash",
        "projected_at",
        "owner",
        "last_contact",
        "waiting_period_days",
        "documents",
        "open_questions",
    ),
    "system_metadata": ("key", "value", "updated_at"),
}
_LEGACY_PRODUCTION_SCHEMAS = {
    5: _LEGACY_PRODUCTION_SCHEMA_V5,
    29: _LEGACY_PRODUCTION_SCHEMA_V29,
}
_DATABASE_IDENTITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS database_identity (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    role TEXT NOT NULL CHECK (role IN ('production', 'shadow')),
    created_at TEXT NOT NULL
)
"""
_DATABASE_IDENTITY_COLUMNS = frozenset(
    {
        ("singleton", "INTEGER", 0, None, 1),
        ("role", "TEXT", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0),
    }
)
_ACTION_INTENT_COLUMN_DEFINITION = ("action_intent", "TEXT", 1, "''", 0)
_RELATION_TYPES = {
    "exact_duplicate",
    "repost",
    "cross_listing",
    "possible_duplicate",
    "not_duplicate",
}
_RELATION_STATES = {"proposed", "confirmed", "rejected", "reverted"}


def _radar_url_key(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").casefold().rstrip(".")
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    return urlunsplit(("https", host, path, parsed.query, ""))


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    company TEXT NOT NULL,
    last_success_at TEXT,
    last_error_at TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    job_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    apply_url TEXT,
    location TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    department TEXT,
    employment_type TEXT,
    workplace_type TEXT,
    is_remote INTEGER,
    posted_at TEXT,
    updated_at TEXT,
    compensation_json TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL,
    match_fingerprint TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    missing_observations INTEGER NOT NULL DEFAULT 0,
    missing_observed_at TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(source_id) REFERENCES sources(source_id)
);
CREATE INDEX IF NOT EXISTS jobs_active_idx ON jobs(active, last_seen_at);
CREATE INDEX IF NOT EXISTS jobs_company_idx ON jobs(company);
CREATE TABLE IF NOT EXISTS job_relations (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    left_job_key TEXT NOT NULL,
    right_job_key TEXT NOT NULL,
    relation_type TEXT NOT NULL CHECK(relation_type IN (
        'exact_duplicate', 'repost', 'cross_listing', 'possible_duplicate', 'not_duplicate'
    )),
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    rule TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'proposed'
        CHECK(state IN ('proposed', 'confirmed', 'rejected', 'reverted')),
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewed_by TEXT,
    CHECK(left_job_key < right_job_key),
    UNIQUE(left_job_key, right_job_key, relation_type),
    FOREIGN KEY(left_job_key) REFERENCES jobs(job_key) ON DELETE CASCADE,
    FOREIGN KEY(right_job_key) REFERENCES jobs(job_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS job_relations_left_idx ON job_relations(left_job_key, state);
CREATE INDEX IF NOT EXISTS job_relations_right_idx ON job_relations(right_job_key, state);
CREATE TABLE IF NOT EXISTS job_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    UNIQUE(job_key, content_hash),
    FOREIGN KEY(job_key) REFERENCES jobs(job_key)
);
CREATE TABLE IF NOT EXISTS scores (
    job_key TEXT PRIMARY KEY,
    total REAL NOT NULL,
    confidence REAL NOT NULL,
    recommendation TEXT NOT NULL,
    dimensions_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    hard_gates_json TEXT NOT NULL,
    scorecard_version TEXT NOT NULL DEFAULT '1.0',
    job_content_hash TEXT,
    assessment_stage TEXT NOT NULL DEFAULT 'machine_triage',
    details_json TEXT NOT NULL DEFAULT '{}',
    scored_at TEXT NOT NULL,
    FOREIGN KEY(job_key) REFERENCES jobs(job_key)
);
CREATE TABLE IF NOT EXISTS discovery_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    sources_attempted INTEGER NOT NULL DEFAULT 0,
    sources_succeeded INTEGER NOT NULL DEFAULT 0,
    jobs_seen INTEGER NOT NULL DEFAULT 0,
    jobs_new INTEGER NOT NULL DEFAULT 0,
    jobs_changed INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS source_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    run_id INTEGER,
    observed_at TEXT NOT NULL,
    success INTEGER NOT NULL CHECK(success IN (0, 1)),
    jobs_seen INTEGER NOT NULL DEFAULT 0 CHECK(jobs_seen >= 0),
    error TEXT,
    FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES discovery_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS source_observations_source_time_idx
    ON source_observations(source_id, observed_at DESC);
CREATE TABLE IF NOT EXISTS radar_leads (
    url_key TEXT PRIMARY KEY,
    input_url TEXT NOT NULL,
    canonical_url TEXT,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1,
    last_status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS pipeline_projection (
    pipeline_key TEXT PRIMARY KEY,
    section TEXT NOT NULL,
    priority INTEGER,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    fit_summary TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    due_date TEXT,
    owner TEXT NOT NULL DEFAULT '',
    last_contact TEXT,
    waiting_period_days INTEGER,
    documents TEXT NOT NULL DEFAULT '',
    open_questions TEXT NOT NULL DEFAULT '',
    action_intent TEXT NOT NULL DEFAULT '',
    source_line INTEGER NOT NULL,
    source_file TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    projected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pipeline_projection_due_idx
    ON pipeline_projection(due_date, priority);
CREATE TABLE IF NOT EXISTS system_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS database_identity (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    role TEXT NOT NULL CHECK (role IN ('production', 'shadow')),
    created_at TEXT NOT NULL
);
"""


@lru_cache(maxsize=1)
def _legacy_v29_column_definitions() -> dict[str, frozenset[tuple[object, ...]]]:
    """Derive the v29 column contract without making physical column order authoritative."""

    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SCHEMA)
        definitions = {
            table: frozenset(
                tuple(row[1:6]) for row in connection.execute(f"PRAGMA table_info({table})")
            )
            for table in _LEGACY_PRODUCTION_SCHEMA_V29
        }
        definitions["pipeline_projection"] = frozenset(
            column for column in definitions["pipeline_projection"] if column[0] != "action_intent"
        )
        return definitions
    finally:
        connection.close()


class DatabaseRoleError(sqlite3.DatabaseError):
    """The database cannot prove it belongs to the requested execution role."""


@dataclass(slots=True)
class _DatabasePathGuard:
    """Windows handles which pin a database's lexical path until the store dies."""

    handles: list[int]
    existed: bool

    def close(self) -> None:
        if os.name != "nt":
            return
        import ctypes

        close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
        for handle in reversed(self.handles):
            close_handle(handle)
        self.handles.clear()


class CareerStore:
    _BUSY_TIMEOUT_MS = 30_000

    def __init__(
        self,
        path: str | Path,
        *,
        read_only: bool = False,
        expected_role: str = "production",
        default_production_path: str | Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.read_only = read_only
        self.default_production_path = (
            Path(default_production_path)
            if default_production_path is not None
            else _DEFAULT_PRODUCTION_DATABASE
        )
        if default_production_path is not None and not self.default_production_path.is_absolute():
            raise ValueError("Configured default production database path must be absolute")
        self._database_path_guard: _DatabasePathGuard | None = None
        self._database_file_identity: tuple[int, int] | None = None
        if expected_role not in {"production", "shadow"}:
            raise ValueError("Career database role must be production or shadow")
        self.expected_role = expected_role
        self._assert_database_path_boundary()
        if read_only:
            self._database_path_guard = self._pin_database_path(create=False)
            if not self._database_path_guard.existed:
                raise FileNotFoundError(f"Career database does not exist: {self.path}")
            self._bind_database_file_identity()
            self._verify_database_role()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._assert_database_path_boundary()
            self._database_path_guard = self._pin_database_path(create=True)
            self._bind_database_file_identity()
            legacy_schema_version: int | None = None
            if self._database_path_guard.existed:
                if not self.path.is_file():
                    raise DatabaseRoleError(f"Career database is not a regular file: {self.path}")
                legacy_schema_version = self._legacy_production_schema_version()
                if legacy_schema_version is None:
                    self._verify_database_role()
            if legacy_schema_version == 29:
                self._establish_legacy_v29_production_identity()
                self._initialize(seed_legacy_production_identity=False)
            else:
                self._initialize(seed_legacy_production_identity=legacy_schema_version == 5)

    def __del__(self) -> None:
        self.close()

    def close(self) -> None:
        """Release any path-identity handles owned by this store."""
        guard = getattr(self, "_database_path_guard", None)
        if guard is not None:
            guard.close()
            self._database_path_guard = None
        self._database_file_identity = None

    def __enter__(self) -> CareerStore:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def _legacy_production_schema_version(self) -> int | None:
        """Recognize only exact, known pre-identity schemas at the default path.

        The recognition query examines only SQLite's schema metadata.  In particular, it
        never reads a job, score, pipeline, or other domain record to infer a database role.
        """

        if self.expected_role != "production" or not self._is_requested_default_production_path():
            return None
        try:
            connection = self._open_database_connection(read_only=True)
            try:
                schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
                return (
                    schema_version
                    if self._matches_legacy_production_schema(connection, schema_version)
                    else None
                )
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise DatabaseRoleError(
                f"Career database role cannot be verified for {self.path}"
            ) from exc

    @staticmethod
    def _matches_legacy_production_schema(
        connection: sqlite3.Connection, schema_version: int
    ) -> bool:
        schema = _LEGACY_PRODUCTION_SCHEMAS.get(schema_version)
        if schema is None:
            return False
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table', 'view', 'trigger') AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != set(schema):
            return False
        expected_definitions = _legacy_v29_column_definitions() if schema_version == 29 else None
        for table, expected_columns in schema.items():
            columns = tuple(connection.execute(f"PRAGMA table_info({table})"))
            if expected_definitions is not None:
                if frozenset(tuple(row[1:6]) for row in columns) != expected_definitions[table]:
                    return False
            elif tuple(row[1] for row in columns) != expected_columns:
                return False
        return True

    def _establish_legacy_v29_production_identity(self) -> None:
        """Atomically add identity and action intent to the known v29 production schema."""

        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._matches_legacy_production_schema(connection, 29):
                if not self._matches_concurrently_identified_legacy_v29_schema(connection):
                    raise DatabaseRoleError(
                        f"Career database role cannot be verified for {self.path}"
                    )
                identity = connection.execute(
                    "SELECT role FROM database_identity WHERE singleton = 1"
                ).fetchall()
                if len(identity) == 1 and identity[0][0] == "production":
                    return
                raise DatabaseRoleError(f"Career database role cannot be verified for {self.path}")
            connection.execute(_DATABASE_IDENTITY_SCHEMA)
            identity = connection.execute(
                "SELECT role FROM database_identity WHERE singleton = 1"
            ).fetchall()
            if identity:
                raise DatabaseRoleError(f"Career database role cannot be verified for {self.path}")
            connection.execute(
                "INSERT INTO database_identity(singleton, role, created_at) VALUES (1, 'production', ?)",
                (utc_now_iso(),),
            )
            connection.execute(
                "ALTER TABLE pipeline_projection ADD COLUMN action_intent TEXT NOT NULL DEFAULT ''"
            )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.DatabaseError(
                    "SQLite foreign-key integrity check failed; repair the local database before use"
                )

    @staticmethod
    def _matches_concurrently_identified_legacy_v29_schema(
        connection: sqlite3.Connection,
    ) -> bool:
        """Accept only the exact atomically upgraded v29 schema.

        A competing initializer may establish the production identity and intent column after this
        process recognizes the legacy schema but before it acquires the write lock.
        Recheck the full post-migration shape under that lock instead of treating the
        valid concurrent completion as an ambiguous database.
        """

        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table', 'view', 'trigger') AND name NOT LIKE 'sqlite_%'"
            )
        }
        if objects != set(_LEGACY_PRODUCTION_SCHEMA_V29) | {"database_identity"}:
            return False
        expected_definitions = _legacy_v29_column_definitions()
        for table in _LEGACY_PRODUCTION_SCHEMA_V29:
            columns = tuple(connection.execute(f"PRAGMA table_info({table})"))
            actual_columns = frozenset(tuple(row[1:6]) for row in columns)
            expected_columns = expected_definitions[table]
            if table == "pipeline_projection":
                expected_columns = expected_columns | {_ACTION_INTENT_COLUMN_DEFINITION}
            if actual_columns != expected_columns:
                return False
        identity_columns = frozenset(
            tuple(row[1:6]) for row in connection.execute("PRAGMA table_info(database_identity)")
        )
        return identity_columns == _DATABASE_IDENTITY_COLUMNS

    def _is_requested_default_production_path(self) -> bool:
        """Require the literal default path and reject every reparse indirection."""

        requested = self.path.absolute()
        default = getattr(self, "default_production_path", _DEFAULT_PRODUCTION_DATABASE).absolute()
        return requested == default and not self._has_reparse_indirection(requested)

    def _assert_database_path_boundary(self) -> None:
        """Reject database paths that cross a filesystem indirection boundary."""

        if self._has_reparse_indirection(self.path.absolute()):
            raise DatabaseRoleError(
                f"Career database path crosses a symlink, junction, or reparse boundary: {self.path}"
            )

    def _bind_database_file_identity(self) -> None:
        """Record the regular file identity that every SQLite open must retain."""

        try:
            metadata = self.path.stat()
        except OSError as exc:
            raise DatabaseRoleError(
                f"Career database could not be bound safely: {self.path}"
            ) from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise DatabaseRoleError(f"Career database is not a regular file: {self.path}")
        self._database_file_identity = metadata.st_dev, metadata.st_ino

    def _assert_bound_database_file_identity(self) -> None:
        self._assert_database_path_boundary()
        if self._database_file_identity is None:
            raise DatabaseRoleError(f"Career database was not bound safely: {self.path}")
        try:
            metadata = self.path.stat()
        except OSError as exc:
            raise DatabaseRoleError(
                f"Career database path changed after validation: {self.path}"
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != self._database_file_identity
        ):
            raise DatabaseRoleError(f"Career database path changed after validation: {self.path}")

    def _pin_database_path(self, *, create: bool) -> _DatabasePathGuard:
        """Bind the Windows path before SQLite can observe or mutate it.

        Windows lacks ``dir_fd``.  We therefore open every lexical directory
        component with ``FILE_FLAG_OPEN_REPARSE_POINT`` and retain its handle
        without delete sharing until the store is collected.  Once pinned, a
        parent or the database file cannot be swapped for a reparse target
        between validation and SQLite's own open of the literal path.
        """

        if os.name != "nt":
            existed = self.path.is_file()
            if create and not existed:
                try:
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                except FileExistsError:
                    existed = self.path.is_file()
                else:
                    os.close(descriptor)
            return _DatabasePathGuard([], existed)

        import ctypes
        from ctypes import wintypes

        generic_read = 0x80000000
        generic_write = 0x40000000
        file_share_read = 0x00000001
        file_share_write = 0x00000002
        open_existing = 3
        open_always = 4
        file_flag_backup_semantics = 0x02000000
        file_flag_open_reparse_point = 0x00200000
        file_attribute_reparse_point = 0x00000400
        error_file_exists = 183
        invalid_handle = ctypes.c_void_p(-1).value
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        class FileInformation(ctypes.Structure):
            _fields_ = [
                ("attributes", wintypes.DWORD),
                ("creation_time", wintypes.FILETIME),
                ("last_access_time", wintypes.FILETIME),
                ("last_write_time", wintypes.FILETIME),
                ("volume_serial", wintypes.DWORD),
                ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD),
                ("number_of_links", wintypes.DWORD),
                ("file_index_high", wintypes.DWORD),
                ("file_index_low", wintypes.DWORD),
            ]

        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(FileInformation))
        get_information.restype = wintypes.BOOL
        handles: list[int] = []

        def open_component(path: Path, *, directory: bool, disposition: int) -> tuple[int, bool]:
            ctypes.set_last_error(0)
            handle = create_file(
                str(path),
                generic_read if directory or self.read_only else generic_read | generic_write,
                file_share_read | file_share_write,
                None,
                disposition,
                file_flag_open_reparse_point | (file_flag_backup_semantics if directory else 0),
                None,
            )
            if handle == invalid_handle:
                raise OSError(ctypes.get_last_error(), "CreateFileW failed", str(path))
            existed = disposition == open_existing or ctypes.get_last_error() == error_file_exists
            information = FileInformation()
            if not get_information(handle, ctypes.byref(information)):
                error = ctypes.get_last_error()
                close_handle(handle)
                raise OSError(error, "GetFileInformationByHandle failed", str(path))
            if information.attributes & file_attribute_reparse_point:
                close_handle(handle)
                raise DatabaseRoleError(
                    f"Career database path crosses a symlink, junction, or reparse boundary: {self.path}"
                )
            return handle, existed

        try:
            absolute_path = self.path.absolute()
            current = Path(absolute_path.anchor)
            for component in absolute_path.parent.parts[1:]:
                current /= component
                handle, _existed = open_component(
                    current, directory=True, disposition=open_existing
                )
                handles.append(handle)
            handle, existed = open_component(
                absolute_path,
                directory=False,
                disposition=open_always if create else open_existing,
            )
            handles.append(handle)
            return _DatabasePathGuard(handles, existed)
        except OSError as exc:
            for handle in reversed(handles):
                close_handle(handle)
            if not create and (getattr(exc, "winerror", None) == 2 or exc.errno == 2):
                return _DatabasePathGuard([], False)
            raise DatabaseRoleError(
                f"Career database could not be opened without a reparse boundary: {self.path}"
            ) from exc

    @staticmethod
    def _has_reparse_indirection(path: Path) -> bool:
        """Treat file and parent symlinks, junctions, and reparse points as untrusted."""

        current = path.absolute()
        while True:
            try:
                attributes = getattr(current.lstat(), "st_file_attributes", 0)
                is_junction = getattr(current, "is_junction", None)
                if (
                    current.is_symlink()
                    or (is_junction() if callable(is_junction) else False)
                    or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                ):
                    return True
            except FileNotFoundError:
                # A not-yet-created database or parent is not itself an indirection.
                # Continue to the first existing lexical ancestor and revalidate after mkdir.
                pass
            except OSError:
                return True
            if current.parent == current:
                return False
            current = current.parent

    def _verify_database_role(self) -> None:
        """Read the role without initializing or migrating a database."""

        try:
            connection = self._open_database_connection(read_only=True)
            try:
                rows = connection.execute(
                    "SELECT role FROM database_identity WHERE singleton = 1"
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise DatabaseRoleError(
                f"Career database role cannot be verified for {self.path}"
            ) from exc
        if len(rows) != 1 or rows[0][0] != self.expected_role:
            raise DatabaseRoleError(
                f"Career database role does not match requested {self.expected_role} role"
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._open_database_connection(read_only=self.read_only)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        if foreign_keys != 1:
            connection.close()
            raise sqlite3.DatabaseError("SQLite foreign-key enforcement could not be enabled")
        connection.execute(f"PRAGMA busy_timeout={self._BUSY_TIMEOUT_MS}")
        try:
            yield connection
            if not self.read_only:
                connection.commit()
        except Exception:
            if not self.read_only:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _open_database_connection(self, *, read_only: bool) -> sqlite3.Connection:
        """Open only after, and immediately recheck, the durable database identity."""

        self._assert_bound_database_file_identity()
        descriptors_before = self._open_file_descriptors() if os.name != "nt" else None
        try:
            if read_only:
                connection = sqlite3.connect(
                    f"{self.path.absolute().as_uri()}?mode=ro",
                    uri=True,
                    timeout=self._BUSY_TIMEOUT_MS / 1000,
                )
            else:
                connection = sqlite3.connect(self.path, timeout=self._BUSY_TIMEOUT_MS / 1000)
            # SQLite may defer opening the main database until its first
            # statement.  Materialize a read-only schema query before taking
            # the descriptor identity proof below.
            if descriptors_before is not None:
                connection.execute("PRAGMA schema_version").fetchone()
        except sqlite3.Error as exc:
            raise DatabaseRoleError(
                f"Career database could not be opened safely: {self.path}"
            ) from exc
        try:
            self._assert_bound_database_file_identity()
            if descriptors_before is not None and not self._connection_holds_bound_database(
                descriptors_before
            ):
                raise DatabaseRoleError(
                    f"Career database connection did not open the validated file: {self.path}"
                )
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _open_file_descriptors() -> dict[int, tuple[int, int]]:
        """Return observable descriptor identities or fail closed when unavailable."""

        for directory in (Path("/proc/self/fd"), Path("/dev/fd")):
            try:
                descriptors: dict[int, tuple[int, int]] = {}
                for entry in directory.iterdir():
                    if not entry.name.isdigit():
                        continue
                    descriptor = int(entry.name)
                    try:
                        metadata = os.fstat(descriptor)
                    except OSError:
                        continue
                    descriptors[descriptor] = metadata.st_dev, metadata.st_ino
                return descriptors
            except OSError:
                continue
        raise DatabaseRoleError(
            "Career database connection cannot be bound safely on this platform"
        )

    def _connection_holds_bound_database(
        self, descriptors_before: dict[int, tuple[int, int]]
    ) -> bool:
        """Prove SQLite opened the same inode that passed role/path validation."""

        if self._database_file_identity is None:
            return False
        for descriptor, identity in self._open_file_descriptors().items():
            if descriptors_before.get(descriptor) == identity:
                continue
            if identity == self._database_file_identity:
                return True
        return False

    def _initialize(self, *, seed_legacy_production_identity: bool = False) -> None:
        with self.connect() as connection:
            connection.executescript(f"BEGIN IMMEDIATE;\n{SCHEMA}")
            identity = connection.execute(
                "SELECT role FROM database_identity WHERE singleton = 1"
            ).fetchall()
            if not identity:
                connection.execute(
                    "INSERT INTO database_identity(singleton, role, created_at) VALUES (1, ?, ?)",
                    (
                        "production" if seed_legacy_production_identity else self.expected_role,
                        utc_now_iso(),
                    ),
                )
            elif len(identity) != 1 or identity[0]["role"] != self.expected_role:
                raise DatabaseRoleError(
                    f"Career database role does not match requested {self.expected_role} role"
                )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "missing_observations" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN missing_observations INTEGER NOT NULL DEFAULT 0"
                )
            if "missing_observed_at" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN missing_observed_at TEXT")
            if "match_fingerprint" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN match_fingerprint TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS jobs_match_fingerprint_idx "
                "ON jobs(match_fingerprint, active)"
            )
            score_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(scores)").fetchall()
            }
            if "scorecard_version" not in score_columns:
                connection.execute(
                    "ALTER TABLE scores ADD COLUMN scorecard_version TEXT NOT NULL DEFAULT '1.0'"
                )
            if "job_content_hash" not in score_columns:
                connection.execute("ALTER TABLE scores ADD COLUMN job_content_hash TEXT")
            if "assessment_stage" not in score_columns:
                connection.execute(
                    "ALTER TABLE scores ADD COLUMN assessment_stage TEXT NOT NULL "
                    "DEFAULT 'machine_triage'"
                )
            if "details_json" not in score_columns:
                connection.execute(
                    "ALTER TABLE scores ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}'"
                )
            pipeline_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(pipeline_projection)").fetchall()
            }
            for column, definition in (
                ("owner", "TEXT NOT NULL DEFAULT ''"),
                ("last_contact", "TEXT"),
                ("waiting_period_days", "INTEGER"),
                ("documents", "TEXT NOT NULL DEFAULT ''"),
                ("open_questions", "TEXT NOT NULL DEFAULT ''"),
                ("action_intent", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in pipeline_columns:
                    connection.execute(
                        f"ALTER TABLE pipeline_projection ADD COLUMN {column} {definition}"
                    )
            hash_version = connection.execute(
                "SELECT value FROM system_metadata WHERE key=?",
                (_JOB_CONTENT_HASH_VERSION_KEY,),
            ).fetchone()
            if not hash_version or hash_version["value"] != _JOB_CONTENT_HASH_VERSION:
                self._refresh_job_content_hashes(connection)
                connection.execute(
                    """
                    INSERT INTO system_metadata(key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (
                        _JOB_CONTENT_HASH_VERSION_KEY,
                        _JOB_CONTENT_HASH_VERSION,
                        utc_now_iso(),
                    ),
                )
            match_version = connection.execute(
                "SELECT value FROM system_metadata WHERE key=?",
                (_JOB_MATCH_FINGERPRINT_VERSION_KEY,),
            ).fetchone()
            if not match_version or match_version["value"] != _JOB_MATCH_FINGERPRINT_VERSION:
                self._refresh_job_match_fingerprints(connection)
                connection.execute(
                    """
                    INSERT INTO system_metadata(key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (
                        _JOB_MATCH_FINGERPRINT_VERSION_KEY,
                        _JOB_MATCH_FINGERPRINT_VERSION,
                        utc_now_iso(),
                    ),
                )
            relation_version = connection.execute(
                "SELECT value FROM system_metadata WHERE key=?",
                (_JOB_RELATION_LAYOUT_VERSION_KEY,),
            ).fetchone()
            if not relation_version or relation_version["value"] != _JOB_RELATION_LAYOUT_VERSION:
                self._backfill_job_relations(connection)
                connection.execute(
                    """
                    INSERT INTO system_metadata(key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (
                        _JOB_RELATION_LAYOUT_VERSION_KEY,
                        _JOB_RELATION_LAYOUT_VERSION,
                        utc_now_iso(),
                    ),
                )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.DatabaseError(
                    "SQLite foreign-key integrity check failed; repair the local database before use"
                )

    def _refresh_job_content_hashes(self, connection: sqlite3.Connection) -> int:
        updates = []
        for row in connection.execute("SELECT * FROM jobs").fetchall():
            current_hash = self._row_to_job(row).content_hash
            if current_hash != row["content_hash"]:
                updates.append((current_hash, row["job_key"]))
        connection.executemany(
            "UPDATE jobs SET content_hash=? WHERE job_key=?",
            updates,
        )
        return len(updates)

    def _refresh_job_match_fingerprints(self, connection: sqlite3.Connection) -> int:
        updates = [
            (self._row_to_job(row).match_fingerprint, row["job_key"])
            for row in connection.execute("SELECT * FROM jobs").fetchall()
        ]
        connection.executemany(
            "UPDATE jobs SET match_fingerprint=? WHERE job_key=?",
            updates,
        )
        return len(updates)

    @staticmethod
    def _backfill_job_relations(connection: sqlite3.Connection) -> None:
        jobs = connection.execute(
            "SELECT job_key, url, match_fingerprint FROM jobs ORDER BY job_key"
        ).fetchall()
        url_groups: dict[str, list[sqlite3.Row]] = {}
        fingerprint_groups: dict[str, list[sqlite3.Row]] = {}
        for row in jobs:
            if row["url"]:
                url_groups.setdefault(str(row["url"]), []).append(row)
            if row["match_fingerprint"]:
                fingerprint_groups.setdefault(str(row["match_fingerprint"]), []).append(row)

        desired_by_type: dict[str, set[tuple[str, str]]] = {
            "exact_duplicate": set(),
            "cross_listing": set(),
        }
        for rows in url_groups.values():
            representative = str(rows[0]["job_key"])
            desired_by_type["exact_duplicate"].update(
                (representative, str(row["job_key"])) for row in rows[1:]
            )
        for rows in fingerprint_groups.values():
            representative = rows[0]
            desired_by_type["cross_listing"].update(
                (str(representative["job_key"]), str(row["job_key"]))
                for row in rows[1:]
                if row["url"] != representative["url"]
            )

        relation_rules = {
            "exact_duplicate": (1.0, "canonical_url"),
            "cross_listing": (0.98, "normalized_content_fingerprint"),
        }
        existing = connection.execute(
            """
            SELECT relation_id, left_job_key, right_job_key, relation_type, rule, state
            FROM job_relations
            WHERE relation_type IN ('exact_duplicate', 'cross_listing')
            """
        ).fetchall()
        existing_pairs: dict[str, set[tuple[str, str]]] = {
            relation_type: set() for relation_type in relation_rules
        }
        obsolete_relation_ids: list[tuple[int]] = []
        for row in existing:
            relation_type = str(row["relation_type"])
            pair = (str(row["left_job_key"]), str(row["right_job_key"]))
            existing_pairs[relation_type].add(pair)
            _confidence, canonical_rule = relation_rules[relation_type]
            if (
                row["state"] == "proposed"
                and row["rule"] == canonical_rule
                and pair not in desired_by_type[relation_type]
            ):
                obsolete_relation_ids.append((int(row["relation_id"]),))
        connection.executemany(
            "DELETE FROM job_relations WHERE relation_id=?",
            obsolete_relation_ids,
        )
        for relation_type, desired_pairs in desired_by_type.items():
            confidence, rule = relation_rules[relation_type]
            for left, right in sorted(desired_pairs - existing_pairs[relation_type]):
                CareerStore._insert_job_relation(
                    connection,
                    left,
                    right,
                    relation_type,
                    confidence,
                    rule,
                )

    def start_run(self, sources_attempted: int) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO discovery_runs(started_at, sources_attempted) VALUES (?, ?)",
                (utc_now_iso(), sources_attempted),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, stats: dict[str, Any], errors: list[dict[str, str]]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE discovery_runs
                SET completed_at=?, sources_succeeded=?, jobs_seen=?, jobs_new=?, jobs_changed=?,
                    errors_json=?
                WHERE run_id=?
                """,
                (
                    utc_now_iso(),
                    stats.get("sources_succeeded", 0),
                    stats.get("jobs_seen", 0),
                    stats.get("jobs_new", 0),
                    stats.get("jobs_changed", 0),
                    json.dumps(errors, ensure_ascii=False),
                    run_id,
                ),
            )

    def record_source_success(
        self,
        source_id: str,
        provider: str,
        company: str,
        *,
        run_id: int | None = None,
        jobs_seen: int = 0,
    ) -> None:
        with self.connect() as connection:
            self._record_source_success(
                connection,
                source_id,
                provider,
                company,
                run_id=run_id,
                jobs_seen=jobs_seen,
            )

    @staticmethod
    def _record_source_success(
        connection: sqlite3.Connection,
        source_id: str,
        provider: str,
        company: str,
        *,
        run_id: int | None = None,
        jobs_seen: int = 0,
    ) -> None:
        if jobs_seen < 0:
            raise ValueError("jobs_seen cannot be negative")
        observed_at = utc_now_iso()
        connection.execute(
            """
            INSERT INTO sources(
                source_id, provider, company, last_success_at, last_error, last_error_at
            )
            VALUES (?, ?, ?, ?, NULL, NULL)
            ON CONFLICT(source_id) DO UPDATE SET
                provider=excluded.provider, company=excluded.company,
                last_success_at=excluded.last_success_at, last_error=NULL, last_error_at=NULL
            """,
            (source_id, provider, company, observed_at),
        )
        connection.execute(
            """
            INSERT INTO source_observations(
                source_id, run_id, observed_at, success, jobs_seen, error
            ) VALUES (?, ?, ?, 1, ?, NULL)
            """,
            (source_id, run_id, observed_at, jobs_seen),
        )

    def record_source_error(
        self,
        source_id: str,
        provider: str,
        company: str,
        error: str,
        *,
        run_id: int | None = None,
    ) -> None:
        with self.connect() as connection:
            observed_at = utc_now_iso()
            connection.execute(
                """
                INSERT INTO sources(source_id, provider, company, last_error_at, last_error)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    provider=excluded.provider, company=excluded.company,
                    last_error_at=excluded.last_error_at, last_error=excluded.last_error
                """,
                (source_id, provider, company, observed_at, error[:2000]),
            )
            connection.execute(
                """
                INSERT INTO source_observations(
                    source_id, run_id, observed_at, success, jobs_seen, error
                ) VALUES (?, ?, ?, 0, 0, ?)
                """,
                (source_id, run_id, observed_at, error[:2000]),
            )

    def upsert_job(self, job: JobPosting) -> str:
        with self.connect() as connection:
            return self._upsert_job(connection, job)

    def record_verified_job(self, job: JobPosting, score: ScoreResult) -> str:
        """Atomically persist one verified official posting and its machine score."""

        if score.job_key != job.job_key:
            raise ValueError("Verified job score does not match the posting identity")
        if score.job_content_hash != job.content_hash:
            raise ValueError("Verified job score is not bound to the posting content hash")
        with self.connect() as connection:
            self._record_source_success(
                connection,
                job.source_id,
                job.provider,
                job.company,
                jobs_seen=1,
            )
            state = self._upsert_job(connection, job)
            self._save_scores(connection, [score])
        return state

    @staticmethod
    def _upsert_job(connection: sqlite3.Connection, job: JobPosting) -> str:
        snapshot = job.to_dict()
        compensation_json = json.dumps(job.compensation.to_dict(), ensure_ascii=False)
        raw_json = json.dumps(job.raw, ensure_ascii=False, default=str)
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=str)
        existing = connection.execute(
            "SELECT content_hash FROM jobs WHERE job_key=?", (job.job_key,)
        ).fetchone()
        state = (
            "new"
            if existing is None
            else ("changed" if existing["content_hash"] != job.content_hash else "unchanged")
        )
        connection.execute(
            """
            INSERT INTO jobs(
                job_key, provider, source_id, external_id, company, title, url, apply_url,
                location, description, department, employment_type, workplace_type, is_remote,
                posted_at, updated_at, compensation_json, content_hash, match_fingerprint,
                first_seen_at,
                last_seen_at, active, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(job_key) DO UPDATE SET
                company=excluded.company, title=excluded.title, url=excluded.url,
                apply_url=excluded.apply_url, location=excluded.location,
                description=excluded.description, department=excluded.department,
                employment_type=excluded.employment_type,
                workplace_type=excluded.workplace_type, is_remote=excluded.is_remote,
                posted_at=excluded.posted_at, updated_at=excluded.updated_at,
                compensation_json=excluded.compensation_json,
                content_hash=excluded.content_hash,
                match_fingerprint=excluded.match_fingerprint,
                last_seen_at=excluded.last_seen_at,
                active=1, missing_observations=0, missing_observed_at=NULL,
                raw_json=excluded.raw_json
            """,
            (
                job.job_key,
                job.provider,
                job.source_id,
                job.external_id,
                job.company,
                job.title,
                job.url,
                job.apply_url,
                job.location,
                job.description,
                job.department,
                job.employment_type,
                job.workplace_type,
                None if job.is_remote is None else int(job.is_remote),
                job.posted_at,
                job.updated_at,
                compensation_json,
                job.content_hash,
                job.match_fingerprint,
                job.observed_at,
                job.observed_at,
                raw_json,
            ),
        )
        if state in {"new", "changed"}:
            connection.execute(
                """
                INSERT OR IGNORE INTO job_history(
                    job_key, observed_at, content_hash, snapshot_json
                )
                VALUES (?, ?, ?, ?)
                """,
                (job.job_key, job.observed_at, job.content_hash, snapshot_json),
            )
        CareerStore._propose_job_relations(connection, job)
        return state

    @staticmethod
    def _insert_job_relation(
        connection: sqlite3.Connection,
        left_job_key: str,
        right_job_key: str,
        relation_type: str,
        confidence: float,
        rule: str,
        *,
        state: str = "proposed",
    ) -> None:
        if left_job_key == right_job_key:
            raise ValueError("A job relation cannot connect a job to itself")
        if relation_type not in _RELATION_TYPES:
            raise ValueError(f"Unsupported job relation type: {relation_type}")
        if state not in _RELATION_STATES:
            raise ValueError(f"Unsupported job relation state: {state}")
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            raise ValueError("Job relation confidence must be numeric")
        if not 0 <= float(confidence) <= 1:
            raise ValueError("Job relation confidence must be between 0 and 1")
        if not rule.strip():
            raise ValueError("Job relation rule cannot be empty")
        left, right = sorted((left_job_key, right_job_key))
        connection.execute(
            """
            INSERT INTO job_relations(
                left_job_key, right_job_key, relation_type, confidence, rule, state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(left_job_key, right_job_key, relation_type) DO UPDATE SET
                confidence=MAX(job_relations.confidence, excluded.confidence),
                rule=excluded.rule
            """,
            (left, right, relation_type, float(confidence), rule.strip(), state, utc_now_iso()),
        )

    @staticmethod
    def _propose_job_relations(connection: sqlite3.Connection, job: JobPosting) -> None:
        groups = (
            ("url", job.url, "exact_duplicate", 1.0, "canonical_url"),
            (
                "match_fingerprint",
                job.match_fingerprint,
                "cross_listing",
                0.98,
                "normalized_content_fingerprint",
            ),
        )
        for column, value, relation_type, confidence, rule in groups:
            if not value:
                continue
            rows = connection.execute(
                f"SELECT job_key, url FROM jobs WHERE active=1 AND {column}=? ORDER BY job_key",
                (value,),
            ).fetchall()
            if len(rows) < 2:
                continue
            representative = rows[0]
            desired: set[tuple[str, str]] = set()
            for row in rows[1:]:
                if relation_type == "cross_listing" and row["url"] == representative["url"]:
                    continue
                desired.add(tuple(sorted((representative["job_key"], row["job_key"]))))
            existing = connection.execute(
                f"""
                SELECT relation_id, left_job_key, right_job_key, state
                FROM job_relations
                WHERE relation_type=?
                  AND left_job_key IN (SELECT job_key FROM jobs WHERE active=1 AND {column}=? )
                  AND right_job_key IN (SELECT job_key FROM jobs WHERE active=1 AND {column}=? )
                """,
                (relation_type, value, value),
            ).fetchall()
            existing_pairs = {
                (str(row["left_job_key"]), str(row["right_job_key"])) for row in existing
            }
            connection.executemany(
                "DELETE FROM job_relations WHERE relation_id=? AND state='proposed'",
                [
                    (row["relation_id"],)
                    for row in existing
                    if row["state"] == "proposed"
                    and (row["left_job_key"], row["right_job_key"]) not in desired
                ],
            )
            for left, right in desired - existing_pairs:
                CareerStore._insert_job_relation(
                    connection, left, right, relation_type, confidence, rule
                )

    def propose_job_relation(
        self,
        left_job_key: str,
        right_job_key: str,
        relation_type: str,
        confidence: float,
        rule: str,
    ) -> None:
        with self.connect() as connection:
            self._insert_job_relation(
                connection,
                left_job_key,
                right_job_key,
                relation_type,
                confidence,
                rule,
            )

    def review_job_relation(
        self,
        relation_id: int,
        *,
        state: str,
        reviewed_by: str,
    ) -> None:
        if state not in {"confirmed", "rejected", "reverted"}:
            raise ValueError("Reviewed job relation state must be confirmed, rejected, or reverted")
        if not reviewed_by.strip():
            raise ValueError("reviewed_by cannot be empty")
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE job_relations
                SET state=?, reviewed_at=?, reviewed_by=?
                WHERE relation_id=?
                """,
                (state, utc_now_iso(), reviewed_by.strip(), relation_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Unknown job relation: {relation_id}")

    def job_relations(self, job_key: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM job_relations"
        params: tuple[Any, ...] = ()
        if job_key:
            query += " WHERE left_job_key=? OR right_job_key=?"
            params = (job_key, job_key)
        query += " ORDER BY created_at DESC, relation_id DESC"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def mark_missing_inactive(
        self,
        source_id: str,
        seen_keys: set[str],
        *,
        closure_threshold: int = 2,
        minimum_absence_seconds: int = 21_600,
    ) -> int:
        """Close only after repeated normal absences from a successfully parsed source."""
        if closure_threshold < 1:
            raise ValueError("closure_threshold must be at least 1")
        if minimum_absence_seconds < 0:
            raise ValueError("minimum_absence_seconds cannot be negative")
        with self.connect() as connection:
            return self._mark_missing_inactive(
                connection,
                source_id,
                seen_keys,
                closure_threshold=closure_threshold,
                minimum_absence_seconds=minimum_absence_seconds,
            )

    @staticmethod
    def _mark_missing_inactive(
        connection: sqlite3.Connection,
        source_id: str,
        seen_keys: set[str],
        *,
        closure_threshold: int,
        minimum_absence_seconds: int,
    ) -> int:
        observed_at = utc_now_iso()
        cutoff = (
            (datetime.now(UTC) - timedelta(seconds=minimum_absence_seconds))
            .replace(microsecond=0)
            .isoformat()
        )
        if seen_keys:
            placeholders = ",".join("?" for _ in seen_keys)
            params: list[Any] = [observed_at, source_id, *sorted(seen_keys), cutoff]
            connection.execute(
                f"UPDATE jobs SET missing_observations=missing_observations+1, "
                f"missing_observed_at=? "
                f"WHERE source_id=? AND active=1 "
                f"AND job_key NOT IN ({placeholders}) "  # noqa: S608 - placeholders only
                f"AND (missing_observed_at IS NULL OR missing_observed_at<=?)",
                params,
            )
        else:
            connection.execute(
                "UPDATE jobs SET missing_observations=missing_observations+1, "
                "missing_observed_at=? WHERE source_id=? AND active=1 "
                "AND (missing_observed_at IS NULL OR missing_observed_at<=?)",
                (observed_at, source_id, cutoff),
            )
        cursor = connection.execute(
            "UPDATE jobs SET active=0 WHERE source_id=? AND active=1 AND missing_observations>=?",
            (source_id, closure_threshold),
        )
        return int(cursor.rowcount)

    def ingest_source(
        self,
        source_id: str,
        provider: str,
        company: str,
        jobs: list[JobPosting],
        *,
        run_id: int | None = None,
        minimum_absence_seconds: int = 21_600,
    ) -> tuple[list[str], int]:
        """Validate and ingest one complete source in a single transaction."""
        for job in jobs:
            if job.source_id != source_id:
                raise ValueError(
                    f"Job source {job.source_id!r} does not match expected source {source_id!r}"
                )
        with self.connect() as connection:
            self._record_source_success(
                connection,
                source_id,
                provider,
                company,
                run_id=run_id,
                jobs_seen=len(jobs),
            )
            states = [self._upsert_job(connection, job) for job in jobs]
            closed = self._mark_missing_inactive(
                connection,
                source_id,
                {job.job_key for job in jobs},
                closure_threshold=2,
                minimum_absence_seconds=minimum_absence_seconds,
            )
        return states, closed

    def active_jobs(self, *, unscored_only: bool = False) -> list[JobPosting]:
        query = "SELECT jobs.* FROM jobs"
        if unscored_only:
            query += " LEFT JOIN scores ON scores.job_key=jobs.job_key"
        query += " WHERE jobs.active=1"
        if unscored_only:
            query += (
                " AND (scores.job_key IS NULL OR scores.job_content_hash IS NULL"
                " OR scores.job_content_hash != jobs.content_hash)"
            )
        query += " ORDER BY jobs.last_seen_at DESC"
        with self.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [self._row_to_job(row) for row in rows]

    def active_job_count(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM jobs WHERE active=1").fetchone()
        return int(row["count"])

    def get_job(self, job_key: str) -> JobPosting | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_key=?", (job_key,)).fetchone()
        return self._row_to_job(row) if row else None

    def find_active_job_by_url(self, url: str) -> JobPosting | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE active=1 AND url=? ORDER BY last_seen_at DESC LIMIT 1",
                (url,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def find_radar_lead_by_url(self, url: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='radar_leads'"
            ).fetchone()
            if not table:
                return None
            row = connection.execute(
                "SELECT * FROM radar_leads WHERE url_key=?",
                (_radar_url_key(url),),
            ).fetchone()
        return dict(row) if row else None

    def record_radar_results(self, results: list[dict[str, Any]]) -> int:
        if self.read_only:
            raise ValueError("Cannot record radar results through a read-only store")
        observed_at = utc_now_iso()
        rows_by_url_key: dict[str, tuple[Any, ...]] = {}
        for result in results:
            input_url = str(result.get("input_url") or "").strip()
            company = str(result.get("company") or "").strip()
            title = str(result.get("title") or "").strip()
            status = str(result.get("status") or "").strip()
            if not input_url or not company or not title or not status:
                raise ValueError("Radar result requires input_url, company, title, and status")
            if status == "duplicate_skipped":
                continue
            url_key = _radar_url_key(input_url)
            rows_by_url_key.setdefault(
                url_key,
                (
                    url_key,
                    input_url,
                    str(result.get("canonical_url") or "").strip() or None,
                    company,
                    title,
                    observed_at,
                    observed_at,
                    status,
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                ),
            )
        rows = list(rows_by_url_key.values())
        if not rows:
            return 0
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO radar_leads(
                    url_key, input_url, canonical_url, company, title, first_seen_at,
                    last_seen_at, last_status, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url_key) DO UPDATE SET
                    input_url=excluded.input_url,
                    canonical_url=COALESCE(excluded.canonical_url, radar_leads.canonical_url),
                    company=excluded.company,
                    title=excluded.title,
                    last_seen_at=excluded.last_seen_at,
                    times_seen=radar_leads.times_seen+1,
                    last_status=excluded.last_status,
                    result_json=excluded.result_json
                """,
                rows,
            )
        return len(rows)

    def save_score(self, result: ScoreResult) -> None:
        self.save_scores([result])

    def save_scores(self, results: list[ScoreResult]) -> None:
        if not results:
            return
        with self.connect() as connection:
            self._save_scores(connection, results)

    @staticmethod
    def _save_scores(connection: sqlite3.Connection, results: list[ScoreResult]) -> None:
        connection.executemany(
            """
                INSERT INTO scores(
                    job_key, total, confidence, recommendation, dimensions_json,
                    reasons_json, risks_json, hard_gates_json, scorecard_version,
                    job_content_hash, assessment_stage, details_json, scored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_key) DO UPDATE SET
                    total=excluded.total, confidence=excluded.confidence,
                    recommendation=excluded.recommendation,
                    dimensions_json=excluded.dimensions_json,
                    reasons_json=excluded.reasons_json, risks_json=excluded.risks_json,
                    hard_gates_json=excluded.hard_gates_json,
                    scorecard_version=excluded.scorecard_version,
                    job_content_hash=excluded.job_content_hash,
                    assessment_stage=excluded.assessment_stage,
                    details_json=excluded.details_json, scored_at=excluded.scored_at
                """,
            [
                (
                    result.job_key,
                    result.total,
                    result.confidence,
                    result.recommendation,
                    json.dumps(result.dimensions, ensure_ascii=False),
                    json.dumps(result.reasons, ensure_ascii=False),
                    json.dumps(result.risks, ensure_ascii=False),
                    json.dumps(result.hard_gates, ensure_ascii=False),
                    result.scorecard_version,
                    result.job_content_hash,
                    result.assessment_stage,
                    json.dumps(
                        {
                            "scorecard": result.scorecard,
                            "progression": result.progression,
                            "gate_results": result.gate_results,
                            "requirement_evidence": result.requirement_evidence,
                            "rule_trace": result.rule_trace,
                            "search_track": result.search_track,
                            "market_lane": result.market_lane,
                            "lead_kind": result.lead_kind,
                            "scorecard_kind": result.scorecard_kind,
                        },
                        ensure_ascii=False,
                    ),
                    result.scored_at,
                )
                for result in results
            ],
        )

    def get_metadata(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM system_metadata WHERE key=?", (key,)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_metadata(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO system_metadata(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, utc_now_iso()),
            )

    def replace_pipeline_projection(
        self,
        entries: list[dict[str, Any]],
        *,
        source_file: str,
        source_hash: str,
        projected_at: str,
    ) -> int:
        with self.connect() as connection:
            connection.execute("DELETE FROM pipeline_projection")
            connection.executemany(
                """
                INSERT INTO pipeline_projection(
                    pipeline_key, section, priority, company, role, status, fit_summary,
                    next_action, due_date, owner, last_contact, waiting_period_days, documents,
                    open_questions, action_intent, source_line, source_file, source_hash, projected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        entry["pipeline_key"],
                        entry["section"],
                        entry.get("priority"),
                        entry["company"],
                        entry["role"],
                        entry["status"],
                        entry.get("fit_summary", ""),
                        entry.get("next_action", ""),
                        entry.get("due_date"),
                        entry.get("owner", ""),
                        entry.get("last_contact"),
                        entry.get("waiting_period_days"),
                        entry.get("documents", ""),
                        entry.get("open_questions", ""),
                        entry.get("action_intent", ""),
                        entry["source_line"],
                        source_file,
                        source_hash,
                        projected_at,
                    )
                    for entry in entries
                ],
            )
        return len(entries)

    def pipeline_entries(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM pipeline_projection
                ORDER BY CASE WHEN priority IS NULL THEN 1 ELSE 0 END,
                         priority, company, role
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def source_health(self) -> list[dict[str, Any]]:
        cutoff = (datetime.now(UTC) - timedelta(days=30)).replace(microsecond=0).isoformat()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_id, provider, company, last_success_at, last_error_at, last_error
                FROM sources
                ORDER BY CASE WHEN last_error IS NULL THEN 1 ELSE 0 END,
                         company, source_id
                """
            ).fetchall()
            observations_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_observations'"
            ).fetchone()
            observations = (
                connection.execute(
                    """
                    SELECT source_id, observed_at, success, jobs_seen
                    FROM source_observations
                    WHERE observed_at>=?
                    ORDER BY source_id, observed_at DESC, observation_id DESC
                    """,
                    (cutoff,),
                ).fetchall()
                if observations_table
                else []
            )
            latest_observations = (
                connection.execute(
                    """
                    SELECT s.source_id, o.observed_at, o.success, o.jobs_seen
                    FROM sources AS s
                    LEFT JOIN source_observations AS o
                      ON o.observation_id = (
                          SELECT observation_id
                          FROM source_observations
                          WHERE source_id = s.source_id
                          ORDER BY observed_at DESC, observation_id DESC
                          LIMIT 1
                      )
                    """
                ).fetchall()
                if observations_table
                else []
            )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for observation in observations:
            grouped.setdefault(str(observation["source_id"]), []).append(dict(observation))
        latest_by_id = {
            str(observation["source_id"]): dict(observation)
            for observation in latest_observations
            if observation["observed_at"] is not None
        }
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            recent = grouped.get(str(row["source_id"]), [])
            latest = latest_by_id.get(str(row["source_id"]))
            if latest is None:
                latest_summary = {
                    "status": "unverified",
                    "observed_at": None,
                    "jobs_seen": None,
                }
            elif not latest["success"]:
                latest_summary = {
                    "status": "error",
                    "observed_at": latest["observed_at"],
                    "jobs_seen": None,
                }
            else:
                jobs_seen = int(latest["jobs_seen"])
                latest_summary = {
                    "status": "empty" if jobs_seen == 0 else "observed",
                    "observed_at": latest["observed_at"],
                    "jobs_seen": jobs_seen,
                }
            total = len(recent)
            successes = sum(int(observation["success"]) for observation in recent)
            consecutive_failures = 0
            for observation in recent:
                if observation["success"]:
                    break
                consecutive_failures += 1
            timestamps = [
                datetime.fromisoformat(str(observation["observed_at"]).replace("Z", "+00:00"))
                for observation in recent
            ]
            span = (
                (max(timestamps) - min(timestamps)).total_seconds() / 86_400
                if len(timestamps) >= 2
                else 0.0
            )
            rate = round(successes / total * 100, 1) if total else None
            healthy = bool(total and rate is not None and rate >= 95 and consecutive_failures < 2)
            item.update(
                {
                    "latest_observation": latest_summary,
                    "observations_30d": total,
                    "successful_observations_30d": successes,
                    "success_rate_pct_30d": rate,
                    "observation_span_days_30d": round(span, 1),
                    "consecutive_failures": consecutive_failures,
                    "jobs_seen_30d": sum(int(observation["jobs_seen"]) for observation in recent),
                    "healthy_30d": healthy,
                    "health_status": (
                        "unverified"
                        if not total
                        else "failing"
                        if consecutive_failures >= 2
                        else "healthy"
                        if healthy
                        else "degraded"
                    ),
                }
            )
            results.append(item)
        return results

    def source_utility(
        self,
        *,
        lookback_days: int = 180,
        review_minimum: float = 55,
        shortlist_minimum: float = 70,
    ) -> list[dict[str, Any]]:
        if isinstance(lookback_days, bool) or not isinstance(lookback_days, int):
            raise ValueError("lookback_days must be an integer")
        if not 1 <= lookback_days <= 365:
            raise ValueError("lookback_days must be between 1 and 365")
        if not 0 <= review_minimum <= shortlist_minimum <= 100:
            raise ValueError(
                "source utility thresholds must satisfy 0 <= review <= shortlist <= 100"
            )
        cutoff = (
            (datetime.now(UTC) - timedelta(days=lookback_days)).replace(microsecond=0).isoformat()
        )
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT sources.source_id, sources.provider, sources.company,
                       COUNT(DISTINCT CASE
                           WHEN jobs.last_seen_at>=? THEN jobs.job_key END
                       ) AS observed_jobs_180d,
                       COUNT(DISTINCT CASE
                           WHEN jobs.last_seen_at>=?
                            AND scores.job_content_hash=jobs.content_hash
                            AND scores.total>=?
                           THEN jobs.job_key END
                       ) AS review_jobs_180d,
                       COUNT(DISTINCT CASE
                           WHEN jobs.last_seen_at>=?
                            AND scores.job_content_hash=jobs.content_hash
                            AND scores.total>=?
                           THEN jobs.job_key END
                       ) AS shortlist_jobs_180d,
                       MAX(CASE
                           WHEN scores.job_content_hash=jobs.content_hash
                            AND scores.total>=?
                           THEN jobs.last_seen_at END
                       ) AS latest_review_job_at
                FROM sources
                LEFT JOIN jobs ON jobs.source_id=sources.source_id
                LEFT JOIN scores ON scores.job_key=jobs.job_key
                GROUP BY sources.source_id, sources.provider, sources.company
                ORDER BY review_jobs_180d DESC, observed_jobs_180d DESC, sources.company
                """,
                (
                    cutoff,
                    cutoff,
                    review_minimum,
                    cutoff,
                    shortlist_minimum,
                    review_minimum,
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_changed_job_keys(self, since: str) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT recent.job_key
                FROM job_history AS recent
                WHERE recent.observed_at>=?
                  AND EXISTS (
                      SELECT 1 FROM job_history AS older
                      WHERE older.job_key=recent.job_key AND older.id<recent.id
                  )
                """,
                (since,),
            ).fetchall()
        return {str(row["job_key"]) for row in rows}

    def recent_discovery_errors(self, *, limit: int = 2) -> list[set[str]]:
        if limit < 1 or limit > 20:
            raise ValueError("Discovery error history limit must be between 1 and 20")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT errors_json FROM discovery_runs
                WHERE completed_at IS NOT NULL
                ORDER BY run_id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        runs: list[set[str]] = []
        for row in rows:
            errors = json.loads(row["errors_json"] or "[]")
            runs.append(
                {
                    str(error.get("source_id"))
                    for error in errors
                    if isinstance(error, dict) and error.get("source_id")
                }
            )
        return runs

    def ranked_jobs(self, *, minimum: float = 0) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT jobs.*, scores.total, scores.confidence, scores.recommendation,
                       scores.dimensions_json, scores.reasons_json, scores.risks_json,
                       scores.hard_gates_json, scores.scorecard_version,
                       scores.job_content_hash, scores.assessment_stage, scores.details_json,
                       scores.scored_at
                FROM jobs JOIN scores ON scores.job_key=jobs.job_key
                WHERE jobs.active=1 AND scores.job_content_hash=jobs.content_hash
                      AND scores.total>=?
                ORDER BY scores.total DESC, scores.confidence DESC, jobs.last_seen_at DESC
                """,
                (minimum,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for field in (
                "dimensions_json",
                "reasons_json",
                "risks_json",
                "hard_gates_json",
                "compensation_json",
            ):
                item[field.removesuffix("_json")] = json.loads(item.pop(field) or "{}")
            details = json.loads(item.pop("details_json") or "{}")
            item.update(details)
            results.append(item)
        return results

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> JobPosting:
        compensation = Compensation(**json.loads(row["compensation_json"] or "{}"))
        return JobPosting(
            provider=row["provider"],
            source_id=row["source_id"],
            external_id=row["external_id"],
            company=row["company"],
            title=row["title"],
            url=row["url"],
            apply_url=row["apply_url"],
            location=row["location"],
            description=row["description"],
            department=row["department"],
            employment_type=row["employment_type"],
            workplace_type=row["workplace_type"],
            is_remote=None if row["is_remote"] is None else bool(row["is_remote"]),
            posted_at=row["posted_at"],
            updated_at=row["updated_at"],
            compensation=compensation,
            raw=json.loads(row["raw_json"] or "{}"),
            observed_at=row["last_seen_at"],
        )

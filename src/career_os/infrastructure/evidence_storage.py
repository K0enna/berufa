"""Versioned, transactional migrations for the instance-owned evidence database."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

from career_os.application.companion import (
    COMPANION_META_KEY,
    companion_json,
    validate_companion_state,
)
from career_os.document_intake import MAX_EXTRACTED_BYTES, MAX_EXTRACTED_LINES

EVIDENCE_STORAGE_VERSION = 4
INSTANCE_LAYOUT_IDENTITY = "private-workspace-layout-v1"
MIGRATION_IDENTITIES = {
    3: "managed-source-provenance-and-audit-reason-v3",
    4: "revision-extracted-source-binding-v4",
}

_TABLES = (
    """CREATE TABLE IF NOT EXISTS vault_meta (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS consent_receipts (
        receipt_id TEXT PRIMARY KEY, boundary_version TEXT NOT NULL,
        accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
        actor TEXT NOT NULL, occurred_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS source_documents (
        source_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
        media_type TEXT NOT NULL, source_hash TEXT NOT NULL,
        size_bytes INTEGER NOT NULL, line_count INTEGER NOT NULL,
        imported_at TEXT NOT NULL, content BLOB NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS evidence_revisions (
        evidence_id TEXT NOT NULL, revision INTEGER NOT NULL,
        kind TEXT NOT NULL, statement TEXT NOT NULL, details_json TEXT NOT NULL,
        source_id TEXT NOT NULL REFERENCES source_documents(source_id),
        source_hash TEXT NOT NULL, source_start_line INTEGER NOT NULL,
        source_end_line INTEGER NOT NULL, ownership TEXT NOT NULL,
        confidence TEXT NOT NULL, approximation TEXT NOT NULL,
        publication_boundary TEXT NOT NULL, content_hash TEXT NOT NULL,
        created_at TEXT NOT NULL, supersedes_revision INTEGER,
        PRIMARY KEY (evidence_id, revision)
    )""",
    """CREATE TABLE IF NOT EXISTS evidence_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
        evidence_id TEXT NOT NULL, revision INTEGER NOT NULL, action TEXT NOT NULL,
        actor TEXT NOT NULL, occurred_at TEXT NOT NULL, content_hash TEXT NOT NULL,
        FOREIGN KEY (evidence_id, revision)
            REFERENCES evidence_revisions(evidence_id, revision)
    )""",
)
_COLUMNS = {
    "evidence_revisions": {"contract_json": "TEXT NOT NULL DEFAULT '{}'"},
    "source_documents": {
        "extracted_hash": "TEXT NOT NULL DEFAULT ''",
        "input_format": "TEXT NOT NULL DEFAULT 'text'",
        "locations_json": "TEXT NOT NULL DEFAULT '[]'",
        "page_count": "INTEGER",
        "original_content": "BLOB",
    },
    "evidence_events": {"reason": "TEXT"},
}
_CURRENT_COLUMNS = {
    "vault_meta": (("key", "TEXT", 0, None, 1), ("value", "TEXT", 1, None, 0)),
    "consent_receipts": (
        ("receipt_id", "TEXT", 0, None, 1),
        ("boundary_version", "TEXT", 1, None, 0),
        ("accepted", "INTEGER", 1, None, 0),
        ("actor", "TEXT", 1, None, 0),
        ("occurred_at", "TEXT", 1, None, 0),
    ),
    "source_documents": (
        ("source_id", "TEXT", 0, None, 1),
        ("display_name", "TEXT", 1, None, 0),
        ("media_type", "TEXT", 1, None, 0),
        ("source_hash", "TEXT", 1, None, 0),
        ("size_bytes", "INTEGER", 1, None, 0),
        ("line_count", "INTEGER", 1, None, 0),
        ("imported_at", "TEXT", 1, None, 0),
        ("content", "BLOB", 1, None, 0),
        ("extracted_hash", "TEXT", 1, "''", 0),
        ("input_format", "TEXT", 1, "'text'", 0),
        ("locations_json", "TEXT", 1, "'[]'", 0),
        ("page_count", "INTEGER", 0, None, 0),
        ("original_content", "BLOB", 0, None, 0),
    ),
    "evidence_revisions": (
        ("evidence_id", "TEXT", 1, None, 1),
        ("revision", "INTEGER", 1, None, 2),
        ("kind", "TEXT", 1, None, 0),
        ("statement", "TEXT", 1, None, 0),
        ("details_json", "TEXT", 1, None, 0),
        ("source_id", "TEXT", 1, None, 0),
        ("source_hash", "TEXT", 1, None, 0),
        ("source_start_line", "INTEGER", 1, None, 0),
        ("source_end_line", "INTEGER", 1, None, 0),
        ("ownership", "TEXT", 1, None, 0),
        ("confidence", "TEXT", 1, None, 0),
        ("approximation", "TEXT", 1, None, 0),
        ("publication_boundary", "TEXT", 1, None, 0),
        ("content_hash", "TEXT", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0),
        ("supersedes_revision", "INTEGER", 0, None, 0),
        ("contract_json", "TEXT", 1, "'{}'", 0),
        ("extracted_hash", "TEXT", 0, None, 0),
    ),
    "evidence_events": (
        ("sequence", "INTEGER", 0, None, 1),
        ("event_id", "TEXT", 1, None, 0),
        ("evidence_id", "TEXT", 1, None, 0),
        ("revision", "INTEGER", 1, None, 0),
        ("action", "TEXT", 1, None, 0),
        ("actor", "TEXT", 1, None, 0),
        ("occurred_at", "TEXT", 1, None, 0),
        ("content_hash", "TEXT", 1, None, 0),
        ("reason", "TEXT", 0, None, 0),
    ),
}
_CURRENT_FOREIGN_KEYS = {
    "evidence_revisions": {
        (0, 0, "source_documents", "source_id", "source_id", "NO ACTION", "NO ACTION", "NONE")
    },
    "evidence_events": {
        (
            0,
            0,
            "evidence_revisions",
            "evidence_id",
            "evidence_id",
            "NO ACTION",
            "NO ACTION",
            "NONE",
        ),
        (
            0,
            1,
            "evidence_revisions",
            "revision",
            "revision",
            "NO ACTION",
            "NO ACTION",
            "NONE",
        ),
    },
}
_CURRENT_INDEXES = {
    "vault_meta": {(1, "pk", 0, ("key",))},
    "consent_receipts": {(1, "pk", 0, ("receipt_id",))},
    "source_documents": {(1, "pk", 0, ("source_id",))},
    "evidence_revisions": {(1, "pk", 0, ("evidence_id", "revision"))},
    "evidence_events": {(1, "u", 0, ("event_id",))},
}
_CURRENT_CHECK_CONSTRAINTS = {
    "consent_receipts": ("ACCEPTEDIN(0,1)",),
}


def _compiled_check_constraints(
    statement: str, table: str, columns: tuple[str, ...]
) -> tuple[str, ...]:
    """Return CHECK expressions from SQLite's compiled insert program."""
    try:
        with closing(sqlite3.connect(":memory:")) as probe:
            probe.execute("PRAGMA trusted_schema = OFF")
            probe.execute(statement)
            placeholders = ", ".join("?" for _ in columns)
            column_names = ", ".join(columns)
            program = probe.execute(
                f"EXPLAIN INSERT INTO {table} ({column_names}) VALUES ({placeholders})",
                (None,) * len(columns),
            )
            return tuple(
                "".join(str(row[5]).upper().split())
                for row in program
                if row[1] == "Halt" and row[2] == sqlite3.SQLITE_CONSTRAINT_CHECK
            )
    except sqlite3.Error as exc:
        raise ValueError("Current evidence storage constraints are missing or unsupported") from exc


def validate_storage(
    connection: sqlite3.Connection,
    instance_root: Path,
    *,
    allow_empty: bool = False,
) -> int:
    """Check version and instance ownership before any schema or data mutation."""
    return validate_storage_identity(
        connection,
        hashlib.sha256(str(instance_root).encode()).hexdigest(),
        allow_empty=allow_empty,
    )


def validate_storage_identity(
    connection: sqlite3.Connection, instance_hash: str, *, allow_empty: bool = False
) -> int:
    """Validate a retained binding without reconstructing another OS's absolute path."""
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not tables and allow_empty:
        return 0
    if "vault_meta" not in tables:
        raise ValueError("Unrecognized evidence database; explicit recovery is required")
    metadata = dict(connection.execute("SELECT key, value FROM vault_meta"))
    version_text = metadata.get("schema_version")
    if version_text not in {"1", "2", "3", str(EVIDENCE_STORAGE_VERSION)}:
        raise ValueError("Unsupported evidence storage version; database was not changed")
    if metadata.get("instance_root_hash") != instance_hash:
        raise ValueError(
            "Evidence database belongs to another or unrecognized participant instance"
        )
    version = int(version_text)
    for target, identity in MIGRATION_IDENTITIES.items():
        if version >= target and metadata.get(f"migration_{target}") != identity:
            raise ValueError("Evidence storage migration identity is missing or invalid")
    return version


def _validate_current_shape(connection: sqlite3.Connection) -> str | None:
    validate_storage_objects(connection)
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if tables != set(_CURRENT_COLUMNS):
        raise ValueError("Current evidence storage tables are missing or unsupported")
    for table, expected in _CURRENT_COLUMNS.items():
        columns = {
            str(row[1]): (str(row[2]).upper(), int(row[3]), row[4], int(row[5]))
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        expected_columns = {
            name: (kind, required, default, primary)
            for name, kind, required, default, primary in expected
        }
        # A v1-v3 initializer could add this default before its transaction failed.
        # Preserve that one known legacy shape; all other current columns stay exact.
        if table == "source_documents" and columns.get("input_format") == ("TEXT", 0, "'text'", 0):
            expected_columns["input_format"] = ("TEXT", 0, "'text'", 0)
        if columns != expected_columns:
            raise ValueError("Current evidence storage columns are missing or unsupported")
        foreign_keys = {
            (
                int(row[0]),
                int(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]).upper(),
                str(row[6]).upper(),
                str(row[7]).upper(),
            )
            for row in connection.execute(f"PRAGMA foreign_key_list({table})")
        }
        if foreign_keys != _CURRENT_FOREIGN_KEYS.get(table, set()):
            raise ValueError("Current evidence storage relationships are missing or unsupported")
        indexes = set()
        for index in connection.execute(f"PRAGMA index_list({table})"):
            indexes.add(
                (
                    int(index[2]),
                    str(index[3]),
                    int(index[4]),
                    tuple(
                        str(column[2])
                        for column in connection.execute(f"PRAGMA index_info({index[1]})")
                    ),
                )
            )
        if indexes != _CURRENT_INDEXES[table]:
            raise ValueError("Current evidence storage keys are missing or unsupported")
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if table_sql is None or _compiled_check_constraints(
            str(table_sql[0]), table, tuple(expected_columns)
        ) != _CURRENT_CHECK_CONSTRAINTS.get(table, ()):
            raise ValueError("Current evidence storage constraints are missing or unsupported")
    metadata = dict(connection.execute("SELECT key, value FROM vault_meta"))
    layout = metadata.get("instance_layout_identity")
    if layout not in {None, INSTANCE_LAYOUT_IDENTITY}:
        raise ValueError("Current evidence storage layout identity is unsupported")
    if COMPANION_META_KEY in metadata:
        validate_companion_state(metadata[COMPANION_META_KEY], metadata["instance_root_hash"])
    integrity = connection.execute("PRAGMA quick_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise ValueError("Evidence database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Evidence database relationships are invalid")
    return layout


def validate_current_storage(connection: sqlite3.Connection, instance_root: Path) -> str | None:
    """Validate exact managed v4 structure and return its optional init-layout identity."""
    instance_hash = hashlib.sha256(str(instance_root).encode()).hexdigest()
    return validate_current_storage_identity(connection, instance_hash)


def validate_current_storage_identity(
    connection: sqlite3.Connection, instance_hash: str
) -> str | None:
    """Validate current structure while retaining an original backup root binding."""
    if validate_storage_identity(connection, instance_hash) != EVIDENCE_STORAGE_VERSION:
        raise ValueError("Current evidence storage version is required")
    return _validate_current_shape(connection)


def mark_initialized_storage(connection: sqlite3.Connection, instance_root: Path) -> None:
    """Record that the empty private layout was created by the explicit init workflow."""
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("BEGIN EXCLUSIVE")
    if validate_current_storage(connection, instance_root) is not None:
        raise ValueError("Evidence storage already has an initialization identity")
    connection.execute(
        "INSERT INTO vault_meta(key, value) VALUES('instance_layout_identity', ?)",
        (INSTANCE_LAYOUT_IDENTITY,),
    )
    if validate_current_storage(connection, instance_root) != INSTANCE_LAYOUT_IDENTITY:
        raise ValueError("Evidence storage initialization identity was not retained")


def rebind_restored_storage(
    connection: sqlite3.Connection, previous_instance_hash: str, destination_root: Path
) -> None:
    """Rebind only an isolated restored copy; never migrate or rewrite evidence history."""
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("BEGIN EXCLUSIVE")
    version = validate_storage_identity(connection, previous_instance_hash)
    validate_storage_objects(connection)
    if version == EVIDENCE_STORAGE_VERSION:
        _validate_current_shape(connection)
    else:
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise ValueError("Evidence database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Evidence database relationships are invalid")
    connection.execute(
        "UPDATE vault_meta SET value = ? WHERE key = 'instance_root_hash'",
        (hashlib.sha256(str(destination_root).encode()).hexdigest(),),
    )
    bridge = connection.execute(
        "SELECT value FROM vault_meta WHERE key=?", (COMPANION_META_KEY,)
    ).fetchone()
    if bridge is not None:
        state = validate_companion_state(str(bridge[0]), previous_instance_hash)
        state["instance_hash"] = hashlib.sha256(str(destination_root).encode()).hexdigest()
        state["generation"] = secrets.token_hex(32)
        state["enabled"] = False
        state["grants"] = {}
        for record in state["pending"].values():
            record["confirmation"] = None
            record["restored"] = True
            if record["state"] == "prepared":
                record["state"] = "cancelled"
        for record in state.get("archive", {}).values():
            record["confirmation"] = None
            record["restored"] = True
        content = companion_json(state).decode()
        validate_companion_state(content, state["instance_hash"])
        connection.execute(
            "UPDATE vault_meta SET value=? WHERE key=?", (content, COMPANION_META_KEY)
        )
    if version == EVIDENCE_STORAGE_VERSION:
        validate_current_storage(connection, destination_root)
    else:
        validate_storage(connection, destination_root)


def validate_storage_objects(connection: sqlite3.Connection) -> None:
    """Refuse executable or foreign schema objects before recovery or migration writes."""
    allowed_tables = {
        "vault_meta",
        "consent_receipts",
        "source_documents",
        "evidence_revisions",
        "evidence_events",
        "sqlite_sequence",
    }
    for kind, name, sql in connection.execute("SELECT type, name, sql FROM sqlite_master"):
        if kind in {"trigger", "view"} or (kind == "table" and name not in allowed_tables):
            raise ValueError("Unsupported evidence schema cannot be changed")
        if sql and "CREATE VIRTUAL" in sql.upper():
            raise ValueError("Unsupported evidence schema cannot be changed")


def _backfill_sources(connection: sqlite3.Connection) -> None:
    for row in connection.execute("SELECT * FROM source_documents"):
        complete = (
            bool(row["extracted_hash"])
            and str(row["locations_json"]) != "[]"
            and row["original_content"] is not None
        )
        if complete:
            if (
                row["media_type"] == "text/markdown"
                and row["input_format"] == "text"
                and row["extracted_hash"] == row["source_hash"]
                and bytes(row["original_content"]) == bytes(row["content"])
                and hashlib.sha256(bytes(row["content"])).hexdigest() == row["source_hash"]
            ):
                connection.execute(
                    "UPDATE source_documents SET input_format = 'markdown' WHERE source_id = ?",
                    (row["source_id"],),
                )
            continue
        content = bytes(row["content"])
        if not 1 <= len(content) <= MAX_EXTRACTED_BYTES or len(content) != row["size_bytes"]:
            raise ValueError("Legacy source byte inventory is invalid or exceeds the limit")
        if str(row["media_type"]) not in {"text/plain", "text/markdown"}:
            raise ValueError("Incomplete document provenance requires explicit recovery")
        if hashlib.sha256(content).hexdigest() != str(row["source_hash"]):
            raise ValueError("Legacy source content does not match its recorded hash")
        if row["original_content"] is not None and bytes(row["original_content"]) != content:
            raise ValueError("Legacy managed original does not match its source content")
        if row["extracted_hash"] and row["extracted_hash"] != row["source_hash"]:
            raise ValueError("Legacy extracted hash does not match its source content")
        try:
            line_count = len(content.decode("utf-8").splitlines())
        except UnicodeDecodeError as exc:
            raise ValueError("Legacy source content is not valid UTF-8") from exc
        if line_count != int(row["line_count"]) or not 1 <= line_count <= MAX_EXTRACTED_LINES:
            raise ValueError("Legacy source line inventory is invalid or exceeds the limit")
        locations = [
            {"kind": "line", "ordinal": index, "start_line": index, "end_line": index}
            for index in range(1, line_count + 1)
        ]
        connection.execute(
            """UPDATE source_documents
               SET extracted_hash = ?, input_format = ?, locations_json = ?, original_content = ?
               WHERE source_id = ?""",
            (
                str(row["source_hash"]),
                "markdown" if str(row["media_type"]) == "text/markdown" else "text",
                json.dumps(locations, sort_keys=True, separators=(",", ":"))
                if str(row["locations_json"]) == "[]"
                else str(row["locations_json"]),
                content,
                str(row["source_id"]),
            ),
        )


def migrate_storage(connection: sqlite3.Connection, instance_root: Path) -> None:
    """Apply numbered additive upgrades, keeping all approved bytes unchanged.

    Migration identities are immutable. Future upgrades append a new numbered
    identity and step instead of changing an already-recorded migration.
    """
    # Explicit upgrades already hold an exclusive transaction; its caller owns commit/rollback.
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    version = validate_storage(connection, instance_root, allow_empty=True)
    if version == EVIDENCE_STORAGE_VERSION:
        return
    if version < 3:
        for statement in _TABLES:
            connection.execute(statement)
        for table, additions in _COLUMNS.items():
            columns = {
                str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for column, declaration in additions.items():
                if column not in columns:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        _backfill_sources(connection)
    if version < 4:
        columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(evidence_revisions)")
        }
        if "extracted_hash" not in columns:
            # NULL marks legacy hashes; migration cannot invent an earlier approval binding.
            connection.execute("ALTER TABLE evidence_revisions ADD COLUMN extracted_hash TEXT")
    metadata = {
        "schema_version": str(EVIDENCE_STORAGE_VERSION),
        "migration_3": MIGRATION_IDENTITIES[3],
        "migration_4": MIGRATION_IDENTITIES[4],
        "instance_root_hash": hashlib.sha256(str(instance_root).encode()).hexdigest(),
    }
    connection.executemany(
        "INSERT INTO vault_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        metadata.items(),
    )

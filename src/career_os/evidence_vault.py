"""Generic, local evidence authority with immutable revisions and exact approvals."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote_from_bytes

from career_os.authority_ports import (
    CandidateEvidenceRecord,
    EvidenceStatus,
    PublicationUse,
)
from career_os.document_intake import (
    MAX_DOCUMENT_BYTES,
    MAX_EXTRACTED_LINES,
    DocumentFormat,
    DocumentLocation,
    ExtractedCandidateDocument,
)
from career_os.evidence_contract import (
    APPROXIMATION_VALUES,
    CONFIDENCE_VALUES,
    EVIDENCE_CONTRACT_VERSION,
    EVIDENCE_KINDS,
    OWNERSHIP_VALUES,
    PUBLICATION_BOUNDARIES,
    Approximation,
    Confidence,
    EvidenceContractMetadata,
    EvidenceKind,
    Ownership,
    PublicationBoundary,
    PublicationReadiness,
    PublicationRequest,
    RoleEvidenceSummary,
    TimelineItem,
    ValidationIssue,
    publication_boundary_allows,
    validate_evidence_identifier,
    validate_metric_details,
    validate_statement_precision,
    validate_timeline,
)
from career_os.evidence_records import (
    PROPOSAL_ACTORS as PROPOSAL_ACTORS,
)
from career_os.evidence_records import (
    EvidenceReadiness as EvidenceReadiness,
)
from career_os.evidence_records import (
    EvidenceRevision as EvidenceRevision,
)
from career_os.evidence_records import (
    ProposalActor as ProposalActor,
)
from career_os.evidence_records import (
    ReviewAction as ReviewAction,
)
from career_os.evidence_records import (
    SourceDocument as SourceDocument,
)
from career_os.file_transactions import (
    _safe_regular_file_path,
    file_identity,
    metadata_identity,
    open_anchored_binary,
    open_posix_anchored_directory,
)
from career_os.infrastructure.evidence_storage import (
    EVIDENCE_STORAGE_VERSION,
    migrate_storage,
    validate_storage,
)

MAX_SOURCE_BYTES = 1_048_576
MAX_CURRENT_EVIDENCE = 1_000
MAX_STATEMENT_CHARS = 4_000
MAX_DETAIL_VALUE_CHARS = 2_000
MAX_DETAILS = 24
BOUNDARY_VERSION = "local-private-v1"


_DETAIL_CONTRACTS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "candidate_profile": (
        frozenset({"field", "value"}),
        frozenset({"field", "value", "language", "geography", "work_arrangement"}),
    ),
    "role_timeline": (
        frozenset({"employer", "title", "start_date", "end_date"}),
        frozenset({"employer", "title", "start_date", "end_date"}),
    ),
    "responsibility": (
        frozenset({"responsibility", "decision_right"}),
        frozenset({"responsibility", "decision_right"}),
    ),
    "achievement": (
        frozenset(
            {
                "outcome",
                "metric_value",
                "metric_unit",
                "metric_period",
                "metric_scope",
            }
        ),
        frozenset(
            {
                "outcome",
                "metric_value",
                "metric_unit",
                "metric_period",
                "metric_scope",
                "currency",
                "population",
            }
        ),
    ),
    "interview_story": (
        frozenset({"situation", "task", "action", "result"}),
        frozenset({"situation", "task", "action", "result", "learning"}),
    ),
    "metric": (
        frozenset({"value", "unit", "period", "scope"}),
        frozenset({"value", "unit", "period", "scope", "currency", "population"}),
    ),
    "stakeholder_seniority": (
        frozenset({"stakeholder", "seniority", "interaction"}),
        frozenset({"stakeholder", "seniority", "interaction"}),
    ),
    "skill_domain": (
        frozenset({"skill", "domain", "evidence_level"}),
        frozenset({"skill", "domain", "evidence_level", "industry", "customer_environment"}),
    ),
    "leadership_team": (
        frozenset({"team_scope", "leadership_type"}),
        frozenset({"team_scope", "leadership_type", "team_size", "decision_rights"}),
    ),
    "compensation_constraint": (
        frozenset({"constraint", "value", "currency"}),
        frozenset({"constraint", "value", "currency", "period", "work_arrangement", "geography"}),
    ),
    "career_objective": (
        frozenset({"objective", "horizon"}),
        frozenset({"objective", "horizon", "priority"}),
    ),
    "development_priority": (
        frozenset({"priority", "target_state"}),
        frozenset({"priority", "target_state", "horizon"}),
    ),
    "education": (
        frozenset({"institution", "program", "completion_status"}),
        frozenset({"institution", "program", "completion_status"}),
    ),
    "gap": (frozenset({"gap"}), frozenset({"gap"})),
    "forbidden_claim": (frozenset({"claim"}), frozenset({"claim"})),
    "correction_note": (
        frozenset({"incorrect_formulation", "correction"}),
        frozenset({"incorrect_formulation", "correction", "effective_date"}),
    ),
}
_COMPLETION_STATUSES = frozenset({"coursework", "partial", "certificate", "awarded_degree"})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clean_text(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum or "\x00" in cleaned:
        raise ValueError(f"{field} must contain 1-{maximum} safe characters")
    return cleaned


def _validated_details(
    kind: str,
    details: Mapping[str, str],
    *,
    strict_metrics: bool = True,
) -> dict[str, str]:
    if kind not in _DETAIL_CONTRACTS:
        raise ValueError("Unsupported evidence kind")
    if not isinstance(details, Mapping) or len(details) > MAX_DETAILS:
        raise ValueError("Evidence details are invalid or too large")
    required, allowed = _DETAIL_CONTRACTS[kind]
    if not required.issubset(details) or set(details) - allowed:
        raise ValueError(f"Evidence details do not satisfy the {kind} contract")
    cleaned = {
        _clean_text(key, "detail key", maximum=80): _clean_text(
            value, f"details.{key}", maximum=MAX_DETAIL_VALUE_CHARS
        )
        for key, value in details.items()
    }
    if kind == "education" and cleaned["completion_status"] not in _COMPLETION_STATUSES:
        raise ValueError("Education completion status is invalid")
    if strict_metrics and kind in {"achievement", "metric"}:
        validate_metric_details(cleaned)
    return cleaned


def _content_hash(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _approved_conflicting_ids(records: Mapping[str, EvidenceRevision]) -> tuple[str, ...]:
    conflicts: set[str] = set()
    for evidence_id, revision in records.items():
        if revision.status != "approved" or revision.extracted_hash is None:
            continue
        for relationship in revision.metadata.relationships:
            target = records.get(relationship.target_evidence_id)
            if (
                relationship.kind == "conflicts_with"
                and target is not None
                and target.status == "approved"
            ):
                conflicts.update((evidence_id, target.evidence_id))
    return tuple(sorted(conflicts))


def _boundary_path(path: Path) -> Path:
    # Windows resolve() can retain its generated namespace prefix during creation.
    # Normalize comparison spelling only; never retarget the actual filesystem path.
    if os.name == "nt":
        value = str(path)
        if value.startswith("\\\\?\\UNC\\"):
            return Path("\\\\" + value[8:])
        if value.startswith("\\\\?\\") and len(path.drive) == 6:
            return Path(value[4:])
    return path


def _is_beneath(path: Path, parent: Path) -> bool:
    path, parent = _boundary_path(path), _boundary_path(parent)
    return path != parent and parent in path.parents


@dataclass(frozen=True, slots=True)
class EvidenceAuditEvent:
    event_id: str
    evidence_id: str
    revision: int
    action: str
    actor: str
    occurred_at: str
    content_hash: str
    reason: str | None = None


class EvidenceVault:
    """One instance-bound SQLite evidence vault.

    Construction validates paths but performs no filesystem or database effect.
    Mutations open short transactions and every approval-relevant transition is
    represented by an append-only event bound to the immutable revision hash.
    """

    def __init__(
        self,
        instance_root: str | Path,
        *,
        database_path: str | Path | None = None,
    ) -> None:
        self.instance_root = Path(instance_root).expanduser().resolve(strict=False)
        requested = (
            Path(database_path).expanduser()
            if database_path is not None
            else self.instance_root / "data" / "evidence-vault.db"
        )
        selected = requested.absolute()
        if ".." in requested.parts or not _is_beneath(selected, self.instance_root / "data"):
            raise ValueError("Evidence database must remain inside the instance data root")
        self.path = selected
        self._instance_identity: tuple[int, int] | None = None
        self._data_identity: tuple[int, int] | None = None
        self._assert_database_boundary()
        self._transaction_connection: ContextVar[sqlite3.Connection | None] = ContextVar(
            "evidence_transaction", default=None
        )

    def _assert_database_boundary(self) -> None:
        """Keep the lexical database path under the independently bound instance root."""

        data_root = self.instance_root / "data"
        if not _is_beneath(self.path, data_root):
            raise ValueError("Evidence database crossed the participant instance boundary")
        try:
            _safe_regular_file_path(self.path)
            if self.instance_root.exists():
                identity = file_identity(self.instance_root)
                if self._instance_identity is not None and identity != self._instance_identity:
                    raise ValueError("Evidence instance root changed after binding")
                self._instance_identity = identity
            if data_root.exists():
                identity = file_identity(data_root)
                if self._data_identity is not None and identity != self._data_identity:
                    raise ValueError("Evidence data root changed after binding")
                self._data_identity = identity
        except (OSError, ValueError) as exc:
            raise ValueError("Evidence database crossed the participant instance boundary") from exc

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit one evidence use case, including source and proposal, atomically."""
        if self._transaction_connection.get() is not None:
            raise ValueError("Nested evidence transactions are not supported")
        self.initialize()
        with self._connect(create=True) as connection:
            token = self._transaction_connection.set(connection)
            try:
                yield
            finally:
                self._transaction_connection.reset(token)

    @contextmanager
    def read_transaction(self) -> Iterator[None]:
        """Read one existing vault snapshot without initializing or migrating it."""
        if self._transaction_connection.get() is not None:
            yield
            return
        with self._connect(create=False) as connection:
            connection.execute("BEGIN")
            token = self._transaction_connection.set(connection)
            try:
                yield
            finally:
                self._transaction_connection.reset(token)

    @contextmanager
    def existing_transaction(self) -> Iterator[None]:
        """Write an existing current instance; never initialize or upgrade one."""
        from career_os.infrastructure.evidence_storage import validate_current_storage

        if self._transaction_connection.get() is not None:
            raise ValueError("Nested evidence transactions are not supported")
        with self._connect(create=False) as connection:
            validate_current_storage(connection, self.instance_root)
        with self._connect(create=True, existing_only=True) as connection:
            validate_current_storage(connection, self.instance_root)
            token = self._transaction_connection.set(connection)
            try:
                yield
            finally:
                self._transaction_connection.reset(token)

    def companion_metadata(self, *, content: str | None = None) -> str | None:
        """Access the one managed companion namespace in an owner's transaction."""
        from career_os.application.companion import COMPANION_META_KEY, validate_companion_state

        connection = self._transaction_connection.get()
        if connection is None:
            raise ValueError("Companion metadata requires an existing transaction")
        if content is not None:
            validate_companion_state(
                content, hashlib.sha256(str(self.instance_root).encode()).hexdigest()
            )
            connection.execute(
                "INSERT INTO vault_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (COMPANION_META_KEY, content),
            )
        row = connection.execute(
            "SELECT value FROM vault_meta WHERE key=?", (COMPANION_META_KEY,)
        ).fetchone()
        return str(row[0]) if row else None

    @contextmanager
    def _connect(
        self, *, create: bool, initializing: bool = False, existing_only: bool = False
    ) -> Iterator[sqlite3.Connection]:
        active = self._transaction_connection.get()
        if active is not None:
            yield active
            return
        data_root = self.instance_root / "data"
        self._assert_database_boundary()
        try:
            if create and not existing_only:
                data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._assert_database_boundary()
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._assert_database_boundary()
                if os.name == "posix":
                    data_root.chmod(0o700)
                    self.path.parent.chmod(0o700)
                self._assert_database_boundary()
                try:
                    descriptor = os.open(
                        self.path,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                    )
                except FileExistsError:
                    pass
                else:
                    os.close(descriptor)
                self._assert_database_boundary()
                if os.name == "posix":
                    self.path.chmod(0o600)
            else:
                if not self.path.is_file() or self.path.is_symlink():
                    raise ValueError("Evidence vault has not been initialized")
            self._assert_database_boundary()
            relative = self.path.relative_to(self.instance_root)
            pin = (
                open_anchored_binary(
                    self.instance_root,
                    self._instance_identity,
                    relative,
                    label="Evidence database",
                )
                if os.name == "nt"
                else open_posix_anchored_directory(
                    self.instance_root,
                    self._instance_identity,
                    relative.parent,
                    label="Evidence database parent",
                )
            )
            with pin as pinned:
                if os.name == "nt":
                    expected_identity = metadata_identity(os.fstat(pinned.fileno()))
                else:
                    metadata = os.stat(relative.name, dir_fd=pinned, follow_symlinks=False)
                    if not stat.S_ISREG(metadata.st_mode):
                        raise ValueError("Evidence database is not a regular file")
                    expected_identity = metadata_identity(metadata)
                mode = "rw" if create else "ro"
                uri = f"file:{quote_from_bytes(os.fsencode(self.path), safe='')}?mode={mode}"
                connection = sqlite3.connect(uri, uri=True, timeout=10)
                try:
                    self._assert_database_boundary()
                    if file_identity(self.path) != expected_identity:
                        raise ValueError("Evidence database changed while opening")
                    connection.row_factory = sqlite3.Row
                    try:
                        connection.execute("PRAGMA foreign_keys = ON")
                        version = validate_storage(
                            connection, self.instance_root, allow_empty=initializing
                        )
                        if create and not initializing:
                            if version < EVIDENCE_STORAGE_VERSION:
                                raise ValueError(
                                    "Explicit backed-up instance upgrade required before writing"
                                )
                            connection.execute("BEGIN IMMEDIATE")
                        yield connection
                        if create:
                            connection.commit()
                    except BaseException:
                        if create:
                            connection.rollback()
                        raise
                finally:
                    connection.close()
        except (sqlite3.IntegrityError, sqlite3.ProgrammingError):
            raise
        except sqlite3.DatabaseError as exc:
            raise OSError("Local evidence storage is unavailable or invalid") from exc

    def initialize(self) -> None:
        if self._transaction_connection.get() is not None:
            return
        with self._connect(create=True, initializing=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            version = validate_storage(connection, self.instance_root, allow_empty=True)
            if version not in {0, EVIDENCE_STORAGE_VERSION}:
                raise ValueError("Explicit backed-up instance upgrade required before writing")
            migrate_storage(connection, self.instance_root)

    def record_consent(self, *, accepted: bool, actor: str = "local_user") -> str:
        if actor != "local_user":
            raise PermissionError("Only the local user can record document-processing consent")
        self.initialize()
        occurred_at = _utc_now()
        receipt_id = (
            "consent-"
            + hashlib.sha256(f"{BOUNDARY_VERSION}\0{accepted}\0{occurred_at}".encode()).hexdigest()[
                :24
            ]
        )
        with self._connect(create=True) as connection:
            connection.execute(
                """
                INSERT INTO consent_receipts(
                    receipt_id, boundary_version, accepted, actor, occurred_at
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (receipt_id, BOUNDARY_VERSION, int(accepted), actor, occurred_at),
            )
        return receipt_id

    def has_consent(self) -> bool:
        if not self.path.exists():
            return False
        with self._connect(create=False) as connection:
            row = connection.execute(
                "SELECT accepted FROM consent_receipts ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return bool(row and row["accepted"])

    def import_document(
        self,
        *,
        display_name: str,
        media_type: str,
        content: bytes,
    ) -> SourceDocument:
        if not self.has_consent():
            raise PermissionError("Document import requires explicit local consent")
        if media_type not in {"text/plain", "text/markdown"}:
            raise ValueError("The first product slice supports plain text and Markdown only")
        if not content or len(content) > MAX_SOURCE_BYTES or b"\x00" in content:
            raise ValueError("Source document must contain 1 byte to 1 MiB of bounded text")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Source document must be valid UTF-8") from exc
        if not text.strip():
            raise ValueError("Source document contains no reviewable text")
        line_count = len(text.splitlines())
        if line_count > MAX_EXTRACTED_LINES:
            raise ValueError("Source document exceeds the extracted line limit")
        input_format: DocumentFormat = "markdown" if media_type == "text/markdown" else "text"
        locations = tuple(
            DocumentLocation(
                kind="line",
                ordinal=index,
                start_line=index,
                end_line=index,
            )
            for index in range(1, line_count + 1)
        )
        source_hash = hashlib.sha256(content).hexdigest()
        return self._store_source(
            display_name=display_name,
            media_type=media_type,
            input_format=input_format,
            original_content=content,
            source_hash=source_hash,
            extracted_text=text,
            extracted_hash=source_hash,
            locations=locations,
            page_count=None,
        )

    def import_extracted_document(
        self,
        document: ExtractedCandidateDocument,
    ) -> SourceDocument:
        """Persist one parser result as an immutable managed local source."""
        if not self.has_consent():
            raise PermissionError("Document import requires explicit local consent")
        original_content = bytes(document.original_content)
        extracted_content = document.text.encode("utf-8")
        if (
            not original_content
            or len(original_content) > MAX_DOCUMENT_BYTES
            or len(original_content) != document.original_size_bytes
            or hashlib.sha256(original_content).hexdigest() != document.original_sha256
        ):
            raise ValueError("Parsed document original content no longer matches its provenance")
        if (
            not extracted_content
            or len(extracted_content) > MAX_SOURCE_BYTES
            or hashlib.sha256(extracted_content).hexdigest() != document.extracted_sha256
            or len(document.text.splitlines()) != document.line_count
            or not 1 <= document.line_count <= MAX_EXTRACTED_LINES
        ):
            raise ValueError("Parsed document text no longer matches its provenance")
        return self._store_source(
            display_name=document.display_name,
            media_type=document.media_type,
            input_format=document.input_format,
            original_content=original_content,
            source_hash=document.original_sha256,
            extracted_text=document.text,
            extracted_hash=document.extracted_sha256,
            locations=document.locations,
            page_count=document.page_count,
        )

    def _store_source(
        self,
        *,
        display_name: str,
        media_type: str,
        input_format: DocumentFormat,
        original_content: bytes,
        source_hash: str,
        extracted_text: str,
        extracted_hash: str,
        locations: tuple[DocumentLocation, ...],
        page_count: int | None,
    ) -> SourceDocument:
        self.initialize()
        name = _clean_text(Path(display_name).name, "display_name", maximum=160)
        extracted_content = extracted_text.encode("utf-8")
        line_count = len(extracted_text.splitlines()) or 1
        if not locations or any(location.end_line > line_count for location in locations):
            raise ValueError("Parsed document locations are outside the extracted text")
        source_id = f"source-{source_hash[:24]}"
        imported_at = _utc_now()
        with self._connect(create=True) as connection:
            existing = connection.execute(
                "SELECT * FROM source_documents WHERE source_id = ?", (source_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO source_documents(
                        source_id, display_name, media_type, source_hash, extracted_hash,
                        size_bytes, line_count, input_format, locations_json, page_count,
                        imported_at, content, original_content
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        name,
                        media_type,
                        source_hash,
                        extracted_hash,
                        len(original_content),
                        line_count,
                        input_format,
                        _canonical_json([location.to_dict() for location in locations]),
                        page_count,
                        imported_at,
                        extracted_content,
                        original_content,
                    ),
                )
            else:
                stored = self._validated_source_from_row(existing)
                legacy_text_retry = (
                    input_format in {"text", "markdown"}
                    and bytes(existing["content"]) == original_content
                    and bytes(existing["original_content"]) == original_content
                    and original_content.decode("utf-8-sig")
                    .replace("\r\n", "\n")
                    .replace("\r", "\n")
                    == extracted_text
                )
                if (
                    stored.source_hash != source_hash
                    or (stored.extracted_hash != extracted_hash and not legacy_text_retry)
                    or stored.input_format != input_format
                ):
                    raise ValueError("Source identity already belongs to different content")
                return stored
        return self.source(source_id)

    def source(self, source_id: str) -> SourceDocument:
        with self._connect(create=False) as connection:
            row = connection.execute(
                "SELECT * FROM source_documents WHERE source_id = ?", (source_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Unknown source document")
        return self._validated_source_from_row(row)

    def sources(self) -> tuple[SourceDocument, ...]:
        if not self.path.exists():
            return ()
        with self._connect(create=False) as connection:
            rows = connection.execute(
                "SELECT * FROM source_documents ORDER BY imported_at, source_id"
            ).fetchall()
        return tuple(self._validated_source_from_row(row) for row in rows)

    def source_lines(self, source_id: str, start_line: int, end_line: int) -> tuple[str, ...]:
        with self._connect(create=False) as connection:
            row = connection.execute(
                "SELECT * FROM source_documents WHERE source_id = ?", (source_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Unknown source document")
        self._validate_source_content(row)
        if start_line < 1 or end_line < start_line or end_line > int(row["line_count"]):
            raise ValueError("Source span is outside the imported document")
        lines = bytes(row["content"]).decode("utf-8").splitlines()
        return tuple(lines[start_line - 1 : end_line])

    def source_locations(
        self,
        source_id: str,
        start_line: int,
        end_line: int,
    ) -> tuple[DocumentLocation, ...]:
        source = self.source(source_id)
        if start_line < 1 or end_line < start_line or end_line > source.line_count:
            raise ValueError("Source span is outside the imported document")
        locations = tuple(
            location
            for location in source.locations
            if location.end_line >= start_line and location.start_line <= end_line
        )
        if not locations:
            raise ValueError("Source span has no parser provenance")
        return locations

    def propose(
        self,
        *,
        source_id: str,
        kind: EvidenceKind,
        statement: str,
        details: Mapping[str, str],
        source_start_line: int,
        source_end_line: int,
        ownership: Ownership,
        confidence: Confidence,
        approximation: Approximation,
        publication_boundary: PublicationBoundary,
        metadata: EvidenceContractMetadata | None = None,
        evidence_id: str | None = None,
        actor: ProposalActor = "synthetic_fixture",
        reason: str | None = None,
    ) -> EvidenceRevision:
        if actor not in PROPOSAL_ACTORS:
            raise ValueError("Evidence proposal actor is invalid")
        selected_reason = (
            None
            if reason is None and actor == "synthetic_fixture"
            else _clean_text(reason, "proposal reason", maximum=2_000)
        )
        source = self.source(source_id)
        source_excerpt = self.source_lines(source_id, source_start_line, source_end_line)
        selected_metadata = metadata or EvidenceContractMetadata(origin_ownership=ownership)
        if selected_metadata.origin_ownership != ownership:
            raise ValueError("origin_ownership must match the initial evidence ownership")
        payload = self._revision_payload(
            kind=kind,
            statement=statement,
            details=details,
            source=source,
            source_start_line=source_start_line,
            source_end_line=source_end_line,
            ownership=ownership,
            confidence=confidence,
            approximation=approximation,
            publication_boundary=publication_boundary,
            metadata=selected_metadata,
            source_excerpt=source_excerpt,
        )
        selected_id = (
            evidence_id
            or "evidence-"
            + hashlib.sha256(
                _canonical_json(
                    {
                        "source_id": source_id,
                        "kind": kind,
                        "source_start_line": source_start_line,
                        "source_end_line": source_end_line,
                        "statement": payload["statement"],
                    }
                ).encode()
            ).hexdigest()[:24]
        )
        selected_id = validate_evidence_identifier(selected_id)
        with self._connect(create=True) as connection:
            if not self.has_consent():
                raise PermissionError("Evidence proposals require explicit local consent")
            self._validate_relationships(
                connection,
                selected_id,
                cast(EvidenceContractMetadata, payload["metadata_object"]),
            )
            existing = connection.execute(
                "SELECT * FROM evidence_revisions WHERE evidence_id = ? AND revision = 1",
                (selected_id,),
            ).fetchone()
            if existing is not None:
                revision = self._revision_from_row(connection, existing)
                retry_hash = payload["content_hash"]
                if existing["extracted_hash"] is None and revision.extracted_hash is not None:
                    legacy_payload = {
                        key: value
                        for key, value in payload.items()
                        if key not in {"content_hash", "metadata_object", "extracted_hash"}
                    }
                    if json.loads(existing["contract_json"]) in ({}, None):
                        legacy_payload.pop("metadata")
                    if revision.metadata == selected_metadata:
                        retry_hash = _content_hash(legacy_payload)
                if revision.content_hash != retry_hash:
                    raise ValueError("Evidence identity already belongs to different content")
                origin = connection.execute(
                    "SELECT actor, reason FROM evidence_events "
                    "WHERE evidence_id = ? AND revision = 1 AND action = 'proposed' "
                    "ORDER BY sequence LIMIT 1",
                    (selected_id,),
                ).fetchone()
                if (
                    origin is None
                    or origin["actor"] != actor
                    or origin["reason"] != selected_reason
                ):
                    raise ValueError("Evidence retry does not match its recorded proposal origin")
                return revision
            created_at = _utc_now()
            self._insert_revision(connection, selected_id, 1, payload, created_at, None)
            self._insert_event(
                connection,
                evidence_id=selected_id,
                revision=1,
                action="proposed",
                actor=actor,
                content_hash=cast(str, payload["content_hash"]),
                reason=selected_reason,
            )
            row = connection.execute(
                "SELECT * FROM evidence_revisions WHERE evidence_id = ? AND revision = 1",
                (selected_id,),
            ).fetchone()
            assert row is not None
            return self._revision_from_row(connection, row)

    def edit(
        self,
        evidence_id: str,
        *,
        expected_revision: int,
        statement: str | None = None,
        details: Mapping[str, str] | None = None,
        ownership: Ownership | None = None,
        confidence: Confidence | None = None,
        approximation: Approximation | None = None,
        publication_boundary: PublicationBoundary | None = None,
        metadata: EvidenceContractMetadata | None = None,
        actor: str = "local_user",
    ) -> EvidenceRevision:
        if actor not in {"local_user", "operator", "model"}:
            raise ValueError("Evidence edit actor is invalid")
        with self._connect(create=True) as connection:
            latest = connection.execute(
                """
                SELECT * FROM evidence_revisions
                WHERE evidence_id = ? ORDER BY revision DESC LIMIT 1
                """,
                (evidence_id,),
            ).fetchone()
            if latest is None or int(latest["revision"]) != expected_revision:
                raise ValueError("Evidence changed after this revision was loaded")
            current = self._revision_from_row(connection, latest)
            selected_metadata = metadata or current.metadata
            if selected_metadata.origin_ownership != current.metadata.origin_ownership:
                raise ValueError("origin_ownership is immutable across evidence revisions")
            source = self.source(current.source_id)
            source_excerpt = self.source_lines(
                current.source_id,
                current.source_start_line,
                current.source_end_line,
            )
            payload = self._revision_payload(
                kind=current.kind,
                statement=statement if statement is not None else current.statement,
                details=details if details is not None else current.details,
                source=source,
                source_start_line=current.source_start_line,
                source_end_line=current.source_end_line,
                ownership=ownership if ownership is not None else current.ownership,
                confidence=confidence if confidence is not None else current.confidence,
                approximation=(
                    approximation if approximation is not None else current.approximation
                ),
                publication_boundary=(
                    publication_boundary
                    if publication_boundary is not None
                    else current.publication_boundary
                ),
                metadata=selected_metadata,
                source_excerpt=source_excerpt,
            )
            selected_ownership = cast(Ownership, payload["ownership"])
            if (
                current.metadata.origin_ownership in {"team", "company", "unknown"}
                and selected_ownership == "personal"
            ):
                raise ValueError(
                    "Team or company evidence cannot be elevated to personal ownership"
                )
            self._validate_relationships(
                connection,
                evidence_id,
                cast(EvidenceContractMetadata, payload["metadata_object"]),
            )
            new_revision = expected_revision + 1
            created_at = _utc_now()
            self._insert_revision(
                connection,
                evidence_id,
                new_revision,
                payload,
                created_at,
                expected_revision,
            )
            self._insert_event(
                connection,
                evidence_id=evidence_id,
                revision=new_revision,
                action="proposed",
                actor=actor,
                content_hash=cast(str, payload["content_hash"]),
            )
            row = connection.execute(
                "SELECT * FROM evidence_revisions WHERE evidence_id = ? AND revision = ?",
                (evidence_id, new_revision),
            ).fetchone()
            assert row is not None
            return self._revision_from_row(connection, row)

    def review(
        self,
        evidence_id: str,
        revision: int,
        *,
        action: ReviewAction,
        expected_content_hash: str,
        actor: str = "local_user",
        reason: str | None = None,
    ) -> EvidenceRevision:
        if actor != "local_user":
            raise PermissionError("Only the local user can approve or dispose evidence")
        if action not in {"approve", "reject", "defer", "revoke"}:
            raise ValueError("Evidence review action is invalid")
        with self._connect(create=True) as connection:
            row = connection.execute(
                "SELECT * FROM evidence_revisions WHERE evidence_id = ? AND revision = ?",
                (evidence_id, revision),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown evidence revision")
            current = self._revision_from_row(connection, row)
            if current.content_hash != expected_content_hash:
                raise ValueError("Evidence content changed after it was reviewed")
            if action == "approve" and current.extracted_hash is None:
                raise ValueError("Legacy source binding requires a new revision and review")
            source_row = connection.execute(
                "SELECT * FROM source_documents WHERE source_id = ?",
                (current.source_id,),
            ).fetchone()
            if source_row is None:
                raise ValueError("Evidence source is missing")
            source = self._validated_source_from_row(source_row)
            if source.source_hash != current.source_hash:
                raise ValueError("Evidence source changed after this revision was proposed")
            latest = connection.execute(
                "SELECT MAX(revision) AS revision FROM evidence_revisions WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            if latest is None or int(latest["revision"]) != revision:
                raise ValueError("Only the latest evidence revision can be reviewed")
            allowed = {
                "approve": {"unapproved", "deferred"},
                "reject": {"unapproved", "deferred"},
                "defer": {"unapproved"},
                "revoke": {"approved"},
            }
            if current.status not in allowed[action]:
                raise ValueError(f"Cannot {action} evidence in {current.status} status")
            if action == "approve":
                approved = connection.execute(
                    """
                    SELECT r.* FROM evidence_revisions r
                    WHERE r.evidence_id = ? AND r.revision < ?
                    ORDER BY r.revision DESC
                    """,
                    (evidence_id, revision),
                ).fetchall()
                for candidate in approved:
                    previous = self._revision_from_row(connection, candidate)
                    if previous.status == "approved":
                        self._insert_event(
                            connection,
                            evidence_id=evidence_id,
                            revision=previous.revision,
                            action="superseded",
                            actor=actor,
                            content_hash=previous.content_hash,
                        )
            event_action = {
                "approve": "approved",
                "reject": "rejected",
                "defer": "deferred",
                "revoke": "revoked",
            }[action]
            cleaned_reason = (
                _clean_text(reason, "review reason", maximum=2_000) if reason is not None else None
            )
            self._insert_event(
                connection,
                evidence_id=evidence_id,
                revision=revision,
                action=event_action,
                actor=actor,
                content_hash=current.content_hash,
                reason=cleaned_reason,
            )
            row = connection.execute(
                "SELECT * FROM evidence_revisions WHERE evidence_id = ? AND revision = ?",
                (evidence_id, revision),
            ).fetchone()
            assert row is not None
            return self._revision_from_row(connection, row)

    def verify(
        self,
        evidence_id: str,
        revision: int,
        *,
        expected_content_hash: str,
        actor: str = "local_user",
    ) -> EvidenceRevision:
        """Append an exact, user-owned verification event without changing disposition."""
        if actor != "local_user":
            raise PermissionError("Only the local user can verify evidence")
        with self._connect(create=True) as connection:
            row = connection.execute(
                "SELECT * FROM evidence_revisions WHERE evidence_id = ? AND revision = ?",
                (evidence_id, revision),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown evidence revision")
            current = self._revision_from_row(connection, row)
            if current.status != "approved":
                raise ValueError("Only approved evidence can be verified")
            if current.extracted_hash is None:
                raise ValueError("Legacy source binding requires a new revision and review")
            if current.content_hash != expected_content_hash:
                raise ValueError("Evidence content changed after it was loaded")
            latest = connection.execute(
                "SELECT MAX(revision) AS revision FROM evidence_revisions WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            if latest is None or int(latest["revision"]) != revision:
                raise ValueError("Only the latest evidence revision can be verified")
            source_row = connection.execute(
                "SELECT * FROM source_documents WHERE source_id = ?", (current.source_id,)
            ).fetchone()
            if source_row is None:
                raise ValueError("Evidence source is missing")
            source = self._validated_source_from_row(source_row)
            if source.source_hash != current.source_hash:
                raise ValueError("Evidence source changed after this revision was proposed")
            self._insert_event(
                connection,
                evidence_id=evidence_id,
                revision=revision,
                action="verified",
                actor=actor,
                content_hash=current.content_hash,
            )
            return current

    def resolve(self, evidence_id: str) -> CandidateEvidenceRecord:
        revision = self.latest(evidence_id)
        if revision.extracted_hash is None:
            raise ValueError("Legacy source binding requires a new revision and review")
        return self._candidate_record(revision)

    def latest(self, evidence_id: str) -> EvidenceRevision:
        with self._connect(create=False) as connection:
            row = connection.execute(
                """
                SELECT * FROM evidence_revisions
                WHERE evidence_id = ? ORDER BY revision DESC LIMIT 1
                """,
                (evidence_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown evidence identity")
            return self._revision_from_row(connection, row)

    def list_current(self, *, limit: int, cursor: str | None):
        from career_os.application.evidence import EvidenceCard
        from career_os.application.inventory import InventoryEntry, inventory_page

        with self.read_transaction():
            if not self.has_consent():
                raise PermissionError("Local processing consent is required")
            with self._connect(create=False) as connection:
                rows = connection.execute(
                    "SELECT r.* FROM evidence_revisions r JOIN "
                    "(SELECT evidence_id, MAX(revision) revision FROM evidence_revisions "
                    "GROUP BY evidence_id) latest ON latest.evidence_id=r.evidence_id "
                    "AND latest.revision=r.revision ORDER BY r.evidence_id LIMIT ?",
                    (MAX_CURRENT_EVIDENCE + 1,),
                ).fetchall()
                if len(rows) > MAX_CURRENT_EVIDENCE:
                    raise ValueError("Current evidence inventory exceeds 1000 records")
                entries = []
                for row in rows:
                    identity = str(row["evidence_id"])
                    try:
                        item = self._revision_from_row(connection, row)
                        self.source(item.source_id)
                        card = EvidenceCard(
                            item.revision,
                            item.content_hash,
                            item.kind,
                            item.statement[:320],
                            item.status,
                            item.ownership,
                            item.confidence,
                            item.approximation,
                            item.publication_boundary,
                        )
                        entries.append(InventoryEntry(identity, card))
                    except (ValueError, OSError):
                        entries.append(InventoryEntry(identity, None, "invalid_or_unavailable"))
                return inventory_page(
                    tuple(entries),
                    scope=f"evidence:{self.instance_root}",
                    limit=limit,
                    cursor=cursor,
                )

    def revisions(self, evidence_id: str) -> tuple[EvidenceRevision, ...]:
        with self._connect(create=False) as connection:
            rows = connection.execute(
                "SELECT * FROM evidence_revisions WHERE evidence_id = ? ORDER BY revision",
                (evidence_id,),
            ).fetchall()
            return tuple(self._revision_from_row(connection, row) for row in rows)

    def review_queue(self) -> tuple[EvidenceRevision, ...]:
        if not self.path.exists():
            return ()
        with self._connect(create=False) as connection:
            rows = connection.execute(
                """
                SELECT r.* FROM evidence_revisions r
                JOIN (
                    SELECT evidence_id, MAX(revision) AS revision
                    FROM evidence_revisions GROUP BY evidence_id
                ) latest
                ON latest.evidence_id = r.evidence_id AND latest.revision = r.revision
                ORDER BY r.created_at, r.evidence_id
                """
            ).fetchall()
            records = tuple(self._revision_from_row(connection, row) for row in rows)
        return tuple(
            item
            for item in records
            if item.status in {"unapproved", "deferred"}
            or (item.status == "approved" and item.extracted_hash is None)
        )

    def enumerate(
        self,
        *,
        statuses: Collection[EvidenceStatus] | None = None,
        publication_use: PublicationUse | None = None,
    ) -> tuple[CandidateEvidenceRecord, ...]:
        if not self.path.exists():
            return ()
        self.sources()
        selected_statuses = set(statuses) if statuses is not None else None
        with self._connect(create=False) as connection:
            rows = connection.execute(
                """
                SELECT r.* FROM evidence_revisions r
                JOIN (
                    SELECT evidence_id, MAX(revision) AS revision
                    FROM evidence_revisions GROUP BY evidence_id
                ) latest
                ON latest.evidence_id = r.evidence_id AND latest.revision = r.revision
                ORDER BY r.evidence_id
                """
            ).fetchall()
            revisions = tuple(self._revision_from_row(connection, row) for row in rows)
        return tuple(
            self._candidate_record(item)
            for item in revisions
            if (selected_statuses is None or item.status in selected_statuses)
            and not (
                item.status == "approved"
                and item.extracted_hash is None
                and (selected_statuses is not None or publication_use not in {None, "internal"})
            )
            and self._publication_allows(item.publication_boundary, publication_use)
        )

    def context_fingerprints(self) -> Mapping[str, str]:
        fingerprints = {source.source_id: source.source_hash for source in self.sources()}
        fingerprints["approved-evidence"] = _content_hash(
            {
                record.evidence_id: {
                    "revision": record.revision,
                    "content_hash": record.content_hash,
                }
                for record in self.enumerate(statuses=("approved",))
            }
        )
        return fingerprints

    def export_records(self) -> tuple[CandidateEvidenceRecord, ...]:
        return self.enumerate(statuses=("approved",))

    def audit_events(self) -> tuple[EvidenceAuditEvent, ...]:
        if not self.path.exists():
            return ()
        with self._connect(create=False) as connection:
            rows = connection.execute(
                """
                SELECT e.*, r.content_hash AS revision_content_hash
                FROM evidence_events e
                LEFT JOIN evidence_revisions r
                  ON r.evidence_id = e.evidence_id AND r.revision = e.revision
                ORDER BY e.sequence
                """
            ).fetchall()
            events: list[EvidenceAuditEvent] = []
            for row in rows:
                revision_hash = row["revision_content_hash"]
                if revision_hash is None:
                    raise ValueError("Evidence audit event references a missing revision")
                self._validate_event_row(row, str(revision_hash))
                events.append(
                    EvidenceAuditEvent(
                        event_id=str(row["event_id"]),
                        evidence_id=str(row["evidence_id"]),
                        revision=int(row["revision"]),
                        action=str(row["action"]),
                        actor=str(row["actor"]),
                        occurred_at=str(row["occurred_at"]),
                        content_hash=str(row["content_hash"]),
                        reason=str(row["reason"]) if row["reason"] is not None else None,
                    )
                )
        return tuple(events)

    def readiness(self) -> EvidenceReadiness:
        self.sources()
        revisions = self._latest_revisions()
        counts = {
            "approved": 0,
            "unapproved": 0,
            "rejected": 0,
            "deferred": 0,
            "revoked": 0,
        }
        gaps = forbidden = unknown = 0
        blockers: list[str] = []
        for revision in revisions:
            counts[revision.status] = counts.get(revision.status, 0) + 1
            if revision.extracted_hash is None and revision.status in {
                "approved",
                "unapproved",
                "deferred",
            }:
                blockers.append(
                    f"{revision.evidence_id}: legacy source binding requires a new revision and review"
                )
            if revision.kind == "gap":
                gaps += 1
            if revision.kind == "forbidden_claim":
                forbidden += 1
            if revision.status not in {"rejected", "revoked"} and revision.confidence in {
                "unknown",
                "disputed",
            }:
                unknown += 1
        if counts["unapproved"]:
            blockers.append(f"{counts['unapproved']} evidence item(s) still need review")
        if counts["deferred"]:
            blockers.append(f"{counts['deferred']} evidence item(s) are deferred")
        if unknown:
            blockers.append(f"{unknown} evidence item(s) remain unknown or disputed")
        if counts["approved"] == 0:
            blockers.append("No evidence revision has been approved")
        return EvidenceReadiness(
            ready=not blockers,
            approved=counts["approved"],
            unapproved=counts["unapproved"],
            rejected=counts["rejected"],
            deferred=counts["deferred"],
            revoked=counts["revoked"],
            gaps=gaps,
            forbidden_claims=forbidden,
            unknown_or_disputed=unknown,
            blockers=tuple(blockers),
        )

    def validate_publication(self, request: PublicationRequest) -> PublicationReadiness:
        """Validate one proposed formulation against exact, current evidence."""
        issues: list[ValidationIssue] = []
        references: list[str] = []
        selected: dict[str, EvidenceRevision] = {}
        current = {item.evidence_id: item for item in self._latest_revisions()}
        folded_statement = request.statement.casefold()
        for evidence_id in request.evidence_ids:
            try:
                revision = self.latest(evidence_id)
            except ValueError as exc:
                issues.append(ValidationIssue("missing_evidence", str(exc), evidence_id))
                continue
            selected[evidence_id] = revision
            if revision.extracted_hash is None:
                issues.append(
                    ValidationIssue(
                        "source_binding_required",
                        "Legacy source binding requires a new revision and review",
                        evidence_id,
                    )
                )
            references.append(
                f"{revision.source_id}:{revision.source_start_line}-{revision.source_end_line}"
            )
            if revision.status != "approved":
                issues.append(
                    ValidationIssue(
                        "not_approved",
                        f"Evidence is {revision.status}, not approved",
                        evidence_id,
                    )
                )
            if revision.kind in {"gap", "forbidden_claim", "correction_note"}:
                issues.append(
                    ValidationIssue(
                        "private_evidence_kind",
                        f"{revision.kind} evidence cannot be published",
                        evidence_id,
                    )
                )
            if not publication_boundary_allows(revision.publication_boundary, request.use):
                issues.append(
                    ValidationIssue(
                        "publication_boundary",
                        f"Evidence is not approved for {request.use} use",
                        evidence_id,
                    )
                )
            if (
                revision.metadata.intended_channels
                and request.use not in revision.metadata.intended_channels
            ):
                issues.append(
                    ValidationIssue(
                        "unintended_channel",
                        f"Evidence is not intended for {request.use}",
                        evidence_id,
                    )
                )
            if revision.confidence in {"unknown", "disputed", "superseded"}:
                issues.append(
                    ValidationIssue(
                        "unresolved_confidence",
                        f"Evidence confidence is {revision.confidence}",
                        evidence_id,
                    )
                )
            if request.ownership == "personal" and revision.metadata.origin_ownership in {
                "team",
                "company",
                "unknown",
            }:
                issues.append(
                    ValidationIssue(
                        "ownership_elevation",
                        "Team, company, or unknown-origin evidence cannot support a "
                        "sole-personal claim",
                        evidence_id,
                    )
                )
            if request.approximation == "exact" and revision.approximation != "exact":
                issues.append(
                    ValidationIssue(
                        "approximation_elevation",
                        "Approximate evidence cannot support an exact claim",
                        evidence_id,
                    )
                )
            for phrase in revision.metadata.prohibited_formulations:
                if phrase.casefold() in folded_statement:
                    issues.append(
                        ValidationIssue(
                            "prohibited_formulation",
                            "The proposed statement uses a prohibited formulation",
                            evidence_id,
                        )
                    )
            if revision.metadata.permitted_formulations and not any(
                phrase.casefold() in folded_statement
                for phrase in revision.metadata.permitted_formulations
            ):
                issues.append(
                    ValidationIssue(
                        "unapproved_formulation",
                        "The proposed statement does not use a permitted formulation",
                        evidence_id,
                    )
                )
        for control in current.values():
            if control.status != "approved":
                continue
            prohibited = list(control.metadata.prohibited_formulations)
            if control.kind == "forbidden_claim":
                prohibited.append(control.details["claim"])
            for phrase in prohibited:
                if phrase.casefold() in folded_statement:
                    issues.append(
                        ValidationIssue(
                            "vault_forbidden_claim",
                            "The proposed statement conflicts with an approved vault-wide "
                            "publication control",
                            control.evidence_id,
                        )
                    )
        conflicting_ids = set(_approved_conflicting_ids(current))
        for evidence_id, revision in selected.items():
            if (
                revision.status == "approved"
                and revision.extracted_hash is not None
                and evidence_id in conflicting_ids
            ):
                issues.append(
                    ValidationIssue(
                        "unresolved_conflict",
                        "Selected evidence has an unresolved conflict with approved evidence",
                        evidence_id,
                    )
                )
        try:
            validate_statement_precision(request.statement, request.approximation)
        except ValueError as exc:
            issues.append(ValidationIssue("precision_language", str(exc)))
        return PublicationReadiness(
            ready=not issues,
            evidence_ids=request.evidence_ids,
            source_references=tuple(references),
            issues=tuple(issues),
        )

    def classify_for_role(
        self,
        role_family: str,
        *,
        required_evidence_ids: Collection[str] = (),
    ) -> RoleEvidenceSummary:
        """Classify approved evidence without turning missing data into a claim."""
        role = _clean_text(role_family, "role_family", maximum=160).casefold()
        strong: list[str] = []
        transferable: list[str] = []
        gaps: list[str] = []
        records = {item.evidence_id: item for item in self._latest_revisions()}
        conflicts = _approved_conflicting_ids(records)
        verified = {
            (event.evidence_id, event.revision)
            for event in self.audit_events()
            if event.action == "verified"
        }
        for evidence_id, revision in records.items():
            if revision.status != "approved" or revision.extracted_hash is None:
                continue
            if revision.kind == "gap":
                gaps.append(evidence_id)
            if revision.kind in {
                "gap",
                "forbidden_claim",
                "correction_note",
            }:
                continue
            relevant = {item.casefold() for item in revision.metadata.role_family_relevance}
            if (
                role in relevant
                and revision.confidence == "exact"
                and (revision.evidence_id, revision.revision) in verified
            ):
                strong.append(evidence_id)
            else:
                transferable.append(evidence_id)
        missing = sorted(set(required_evidence_ids) - set(records))
        return RoleEvidenceSummary(
            strong_verified=tuple(sorted(strong)),
            transferable=tuple(sorted(transferable)),
            genuine_gaps=tuple(sorted(gaps)),
            conflicts=tuple(sorted(conflicts)),
            missing=tuple(missing),
        )

    def conflicting_evidence_ids(self) -> tuple[str, ...]:
        """Use the same approved-conflict rule as role classification."""
        return _approved_conflicting_ids(
            {item.evidence_id: item for item in self._latest_revisions()}
        )

    def timeline_issues(self) -> tuple[ValidationIssue, ...]:
        items = tuple(
            TimelineItem(
                evidence_id=item.evidence_id,
                start_date=item.details["start_date"],
                end_date=item.details["end_date"],
                timeline_group=item.metadata.timeline_group,
                mutually_exclusive=item.metadata.mutually_exclusive_timeline,
            )
            for item in self._latest_revisions()
            if item.kind == "role_timeline" and item.status != "rejected"
        )
        return validate_timeline(items)

    def deletion_inventory(self) -> dict[str, object]:
        """Enumerate all user-owned vault records required for export or deletion."""
        if not self.path.exists():
            return {"database": str(self.path), "exists": False, "tables": {}}
        with self._connect(create=False) as connection:
            tables: dict[str, object] = {}
            for table, identifier in (
                ("vault_meta", "key"),
                ("consent_receipts", "receipt_id"),
                ("source_documents", "source_id"),
                ("evidence_revisions", "evidence_id || ':' || revision"),
                ("evidence_events", "event_id"),
            ):
                rows = connection.execute(
                    f"SELECT {identifier} AS identifier FROM {table} ORDER BY identifier"
                ).fetchall()
                tables[table] = {
                    "count": len(rows),
                    "identifiers": [str(row["identifier"]) for row in rows],
                }
        return {"database": str(self.path), "exists": True, "tables": tables}

    def _latest_revisions(self) -> tuple[EvidenceRevision, ...]:
        if not self.path.exists():
            return ()
        with self._connect(create=False) as connection:
            rows = connection.execute(
                """
                SELECT r.* FROM evidence_revisions r
                JOIN (
                    SELECT evidence_id, MAX(revision) AS revision
                    FROM evidence_revisions GROUP BY evidence_id
                ) latest
                ON latest.evidence_id = r.evidence_id AND latest.revision = r.revision
                ORDER BY r.evidence_id
                """
            ).fetchall()
            if not rows:
                return ()
            source_rows = connection.execute(
                """
                SELECT s.*
                FROM source_documents s
                JOIN (
                    SELECT DISTINCT r.source_id
                    FROM evidence_revisions r
                    JOIN (
                        SELECT evidence_id, MAX(revision) AS revision
                        FROM evidence_revisions GROUP BY evidence_id
                    ) latest
                      ON latest.evidence_id = r.evidence_id
                     AND latest.revision = r.revision
                ) referenced ON referenced.source_id = s.source_id
                ORDER BY s.source_id
                """
            ).fetchall()
            sources = {str(row["source_id"]): row for row in source_rows}
            status_rows = connection.execute(
                """
                SELECT e.*
                FROM evidence_events e
                JOIN (
                    SELECT evidence_id, revision, MAX(sequence) AS sequence
                    FROM evidence_events
                    WHERE action IN ('proposed', 'approved', 'rejected', 'deferred',
                                     'revoked', 'superseded')
                    GROUP BY evidence_id, revision
                ) latest_event
                  ON latest_event.evidence_id = e.evidence_id
                 AND latest_event.revision = e.revision
                 AND latest_event.sequence = e.sequence
                JOIN (
                    SELECT evidence_id, MAX(revision) AS revision
                    FROM evidence_revisions
                    GROUP BY evidence_id
                ) latest_revision
                  ON latest_revision.evidence_id = e.evidence_id
                 AND latest_revision.revision = e.revision
                ORDER BY e.sequence
                """
            ).fetchall()
            statuses = {(str(row["evidence_id"]), int(row["revision"])): row for row in status_rows}
            return tuple(
                self._revision_from_row(
                    connection,
                    row,
                    source_row=sources.get(str(row["source_id"])),
                    source_loaded=True,
                    status_row=statuses.get((str(row["evidence_id"]), int(row["revision"]))),
                    status_loaded=True,
                )
                for row in rows
            )

    def export_snapshot(self) -> dict[str, object]:
        return {
            "schema_version": EVIDENCE_CONTRACT_VERSION,
            "boundary_version": BOUNDARY_VERSION,
            "sources": [asdict(source) for source in self.sources()],
            "evidence": [asdict(record) for record in self._latest_revisions()],
            "audit_events": [asdict(event) for event in self.audit_events()],
            "deletion_inventory": self.deletion_inventory(),
            "credentials_included": False,
            "remote_telemetry": False,
        }

    def export_user_owned_data(self) -> dict[str, object]:
        """Return the complete validated user-owned vault data without storage metadata."""

        with self.read_transaction():
            connection = self._transaction_connection.get()
            if connection is None:
                raise RuntimeError("Evidence export requires one read transaction")
            consent_receipts: list[dict[str, object]] = []
            for row in connection.execute(
                "SELECT receipt_id, boundary_version, accepted, actor, occurred_at "
                "FROM consent_receipts ORDER BY rowid"
            ):
                accepted = int(row["accepted"])
                boundary = str(row["boundary_version"])
                occurred_at = str(row["occurred_at"])
                try:
                    instant = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError("Stored consent time is invalid") from exc
                expected = (
                    "consent-"
                    + hashlib.sha256(
                        f"{boundary}\0{bool(accepted)}\0{occurred_at}".encode()
                    ).hexdigest()[:24]
                )
                if (
                    accepted not in {0, 1}
                    or row["actor"] != "local_user"
                    or instant.tzinfo is None
                    or row["receipt_id"] != expected
                ):
                    raise ValueError("Stored consent receipt is invalid")
                consent_receipts.append(
                    {
                        "receipt_id": expected,
                        "boundary_version": boundary,
                        "accepted": bool(accepted),
                        "actor": "local_user",
                        "occurred_at": occurred_at,
                    }
                )

            sources: list[dict[str, object]] = []
            for row in connection.execute(
                "SELECT * FROM source_documents ORDER BY imported_at, source_id"
            ):
                source = self._validated_source_from_row(row)
                extracted = bytes(row["content"])
                original = (
                    bytes(row["original_content"])
                    if row["original_content"] is not None
                    else extracted
                )
                sources.append(
                    {
                        "metadata": asdict(source),
                        "extracted_text": extracted.decode("utf-8"),
                        "original_content_base64": base64.b64encode(original).decode("ascii"),
                    }
                )

            revision_rows = connection.execute(
                "SELECT * FROM evidence_revisions ORDER BY evidence_id, revision"
            ).fetchall()
            revisions = [asdict(self._revision_from_row(connection, row)) for row in revision_rows]
            events = [asdict(event) for event in self.audit_events()]
        return {
            "schema_version": 1,
            "consent_receipts": consent_receipts,
            "sources": sources,
            "evidence_revisions": revisions,
            "audit_events": events,
        }

    @staticmethod
    def _source_locations(row: sqlite3.Row, line_count: int) -> tuple[DocumentLocation, ...]:
        keys = set(row.keys())
        if "locations_json" not in keys or str(row["locations_json"]) == "[]":
            return tuple(
                DocumentLocation(
                    kind="line",
                    ordinal=index,
                    start_line=index,
                    end_line=index,
                )
                for index in range(1, line_count + 1)
            )
        try:
            payload = json.loads(str(row["locations_json"]))
            if not isinstance(payload, list) or not payload:
                raise ValueError("Imported source locations are invalid")
            locations = tuple(
                DocumentLocation(
                    kind=cast(Any, item["kind"]),
                    ordinal=item["ordinal"],
                    start_line=item["start_line"],
                    end_line=item["end_line"],
                    part=item.get("part"),
                )
                for item in payload
                if isinstance(item, dict)
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Imported source locations are invalid") from exc
        if len(locations) != len(payload) or any(
            location.end_line > line_count for location in locations
        ):
            raise ValueError("Imported source locations are outside the extracted text")
        return locations

    @classmethod
    def _validate_source_content(cls, row: sqlite3.Row) -> None:
        keys = set(row.keys())
        content = bytes(row["content"])
        source_hash = str(row["source_hash"])
        extracted_hash = (
            str(row["extracted_hash"])
            if "extracted_hash" in keys and str(row["extracted_hash"])
            else source_hash
        )
        original_content = (
            bytes(row["original_content"])
            if "original_content" in keys and row["original_content"] is not None
            else content
        )
        if hashlib.sha256(original_content).hexdigest() != source_hash:
            raise ValueError("Imported original content no longer matches its recorded hash")
        if hashlib.sha256(content).hexdigest() != extracted_hash:
            raise ValueError("Imported extracted text no longer matches its recorded hash")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Imported extracted text is no longer valid UTF-8") from exc
        line_count = int(row["line_count"])
        if not 1 <= line_count <= MAX_EXTRACTED_LINES:
            raise ValueError("Imported source line inventory exceeds the limit")
        if len(original_content) != int(row["size_bytes"]) or len(text.splitlines()) != line_count:
            raise ValueError("Imported source inventory no longer matches its content")
        input_format = (
            str(row["input_format"])
            if "input_format" in keys
            else ("markdown" if str(row["media_type"]) == "text/markdown" else "text")
        )
        if input_format not in {"text", "markdown", "pdf", "docx"}:
            raise ValueError("Imported source format is invalid")
        page_count = row["page_count"] if "page_count" in keys else None
        if page_count is not None and (isinstance(page_count, bool) or int(page_count) < 1):
            raise ValueError("Imported source page count is invalid")
        cls._source_locations(row, line_count)

    @classmethod
    def _validated_source_from_row(cls, row: sqlite3.Row) -> SourceDocument:
        cls._validate_source_content(row)
        keys = set(row.keys())
        source_hash = str(row["source_hash"])
        line_count = int(row["line_count"])
        return SourceDocument(
            source_id=str(row["source_id"]),
            display_name=str(row["display_name"]),
            media_type=str(row["media_type"]),
            source_hash=source_hash,
            extracted_hash=(
                str(row["extracted_hash"])
                if "extracted_hash" in keys and str(row["extracted_hash"])
                else source_hash
            ),
            size_bytes=int(row["size_bytes"]),
            line_count=line_count,
            input_format=cast(
                DocumentFormat,
                str(row["input_format"])
                if "input_format" in keys
                else ("markdown" if str(row["media_type"]) == "text/markdown" else "text"),
            ),
            locations=cls._source_locations(row, line_count),
            page_count=(
                int(row["page_count"])
                if "page_count" in keys and row["page_count"] is not None
                else None
            ),
            managed_copy=True,
            imported_at=str(row["imported_at"]),
        )

    @staticmethod
    def _revision_payload(
        *,
        kind: EvidenceKind,
        statement: str,
        details: Mapping[str, str],
        source: SourceDocument,
        source_start_line: int,
        source_end_line: int,
        ownership: Ownership,
        confidence: Confidence,
        approximation: Approximation,
        publication_boundary: PublicationBoundary,
        metadata: EvidenceContractMetadata,
        source_excerpt: tuple[str, ...],
    ) -> dict[str, object]:
        if kind not in EVIDENCE_KINDS:
            raise ValueError("Unsupported evidence kind")
        cleaned_statement = _clean_text(statement, "statement", maximum=MAX_STATEMENT_CHARS)
        cleaned_details = _validated_details(kind, details)
        if ownership not in OWNERSHIP_VALUES:
            raise ValueError("Evidence ownership is invalid")
        if confidence not in CONFIDENCE_VALUES:
            raise ValueError("Evidence confidence is invalid")
        if approximation not in APPROXIMATION_VALUES:
            raise ValueError("Evidence approximation is invalid")
        if publication_boundary not in PUBLICATION_BOUNDARIES:
            raise ValueError("Evidence publication boundary is invalid")
        if kind in {"gap", "forbidden_claim"} and publication_boundary != "private":
            raise ValueError("Gaps and forbidden claims must remain private")
        if kind == "correction_note" and publication_boundary != "private":
            raise ValueError("Correction notes must remain private")
        if approximation != "exact" and confidence == "exact":
            raise ValueError("Approximate evidence cannot claim exact confidence")
        validate_statement_precision(cleaned_statement, approximation)
        if kind == "role_timeline":
            timeline_issues = validate_timeline(
                (
                    TimelineItem(
                        evidence_id="proposed-role-timeline",
                        start_date=cleaned_details["start_date"],
                        end_date=cleaned_details["end_date"],
                        timeline_group=metadata.timeline_group,
                        mutually_exclusive=metadata.mutually_exclusive_timeline,
                    ),
                )
            )
            if timeline_issues:
                raise ValueError(timeline_issues[0].message)
        if (
            kind in {"achievement", "metric"}
            and ownership == "personal"
            and "team"
            in cleaned_details["metric_scope" if kind == "achievement" else "scope"].casefold()
        ):
            raise ValueError("A team outcome cannot be converted to sole personal ownership")
        if (
            kind == "education"
            and cleaned_details["completion_status"] == "awarded_degree"
            and "no degree" in " ".join(source_excerpt).casefold()
        ):
            raise ValueError("A source stating no degree cannot support an awarded degree")
        payload: dict[str, object] = {
            "kind": kind,
            "statement": cleaned_statement,
            "details": cleaned_details,
            "source_id": source.source_id,
            "source_hash": source.source_hash,
            "extracted_hash": source.extracted_hash,
            "source_start_line": source_start_line,
            "source_end_line": source_end_line,
            "ownership": ownership,
            "confidence": confidence,
            "approximation": approximation,
            "publication_boundary": publication_boundary,
            "metadata": metadata.to_dict(),
            "metadata_object": metadata,
        }
        payload["content_hash"] = _content_hash(
            {key: value for key, value in payload.items() if key != "metadata_object"}
        )
        return payload

    @staticmethod
    def _validate_relationships(
        connection: sqlite3.Connection,
        evidence_id: str,
        metadata: EvidenceContractMetadata,
    ) -> None:
        targets = {item.target_evidence_id for item in metadata.relationships}
        if evidence_id in targets:
            raise ValueError("Evidence cannot relate to itself")
        if not targets:
            return
        placeholders = ",".join("?" for _ in targets)
        rows = connection.execute(
            f"SELECT DISTINCT evidence_id FROM evidence_revisions "
            f"WHERE evidence_id IN ({placeholders})",
            tuple(sorted(targets)),
        ).fetchall()
        known = {str(row["evidence_id"]) for row in rows}
        if known != targets:
            raise ValueError("Evidence relationship references an unknown evidence identity")

    @staticmethod
    def _insert_revision(
        connection: sqlite3.Connection,
        evidence_id: str,
        revision: int,
        payload: Mapping[str, object],
        created_at: str,
        supersedes_revision: int | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO evidence_revisions(
                evidence_id, revision, kind, statement, details_json, source_id,
                source_hash, source_start_line, source_end_line, ownership,
                confidence, approximation, publication_boundary, content_hash,
                created_at, supersedes_revision, contract_json, extracted_hash
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                revision,
                payload["kind"],
                payload["statement"],
                _canonical_json(payload["details"]),
                payload["source_id"],
                payload["source_hash"],
                payload["source_start_line"],
                payload["source_end_line"],
                payload["ownership"],
                payload["confidence"],
                payload["approximation"],
                payload["publication_boundary"],
                payload["content_hash"],
                created_at,
                supersedes_revision,
                _canonical_json(payload["metadata"]),
                payload["extracted_hash"],
            ),
        )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        evidence_id: str,
        revision: int,
        action: str,
        actor: str,
        content_hash: str,
        reason: str | None = None,
    ) -> None:
        occurred_at = _utc_now()
        event_id = EvidenceVault._event_id(
            evidence_id=evidence_id,
            revision=revision,
            action=action,
            actor=actor,
            occurred_at=occurred_at,
            content_hash=content_hash,
            reason=reason,
        )
        connection.execute(
            """
            INSERT INTO evidence_events(
                event_id, evidence_id, revision, action, actor, occurred_at, content_hash, reason
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                evidence_id,
                revision,
                action,
                actor,
                occurred_at,
                content_hash,
                reason,
            ),
        )

    @staticmethod
    def _event_id(
        *,
        evidence_id: str,
        revision: int,
        action: str,
        actor: str,
        occurred_at: str,
        content_hash: str | None = None,
        reason: str | None = None,
    ) -> str:
        fields = [evidence_id, str(revision), action, actor, occurred_at]
        if content_hash is not None:
            fields.extend((content_hash, reason or ""))
        return "evidence-event-" + hashlib.sha256("\0".join(fields).encode()).hexdigest()[:24]

    @classmethod
    def _validate_event_row(
        cls,
        row: sqlite3.Row,
        expected_content_hash: str,
    ) -> None:
        if str(row["content_hash"]) != expected_content_hash:
            raise ValueError("Evidence audit event no longer matches its revision hash")
        action = str(row["action"])
        if action not in {
            "proposed",
            "approved",
            "verified",
            "rejected",
            "deferred",
            "revoked",
            "superseded",
        }:
            raise ValueError("Evidence audit event action is invalid")
        common = {
            "evidence_id": str(row["evidence_id"]),
            "revision": int(row["revision"]),
            "action": action,
            "actor": str(row["actor"]),
            "occurred_at": str(row["occurred_at"]),
        }
        reason = str(row["reason"]) if row["reason"] is not None else None
        exact = cls._event_id(
            **common,
            content_hash=expected_content_hash,
            reason=reason,
        )
        legacy = cls._event_id(**common)
        stored = str(row["event_id"])
        if stored != exact and not (stored == legacy and reason is None):
            raise ValueError("Evidence audit event no longer matches its integrity identity")

    @classmethod
    def _status(
        cls,
        connection: sqlite3.Connection,
        evidence_id: str,
        revision: int,
        expected_content_hash: str,
    ) -> EvidenceStatus:
        row = connection.execute(
            """
            SELECT * FROM evidence_events
            WHERE evidence_id = ? AND revision = ?
              AND action IN ('proposed', 'approved', 'rejected', 'deferred',
                             'revoked', 'superseded')
            ORDER BY sequence DESC LIMIT 1
            """,
            (evidence_id, revision),
        ).fetchone()
        return cls._status_from_row(row, expected_content_hash)

    @classmethod
    def _status_from_row(
        cls,
        row: sqlite3.Row | None,
        expected_content_hash: str,
    ) -> EvidenceStatus:
        if row is not None:
            cls._validate_event_row(row, expected_content_hash)
        action = str(row["action"]) if row is not None else "proposed"
        return cast(
            EvidenceStatus,
            {
                "proposed": "unapproved",
                "approved": "approved",
                "rejected": "rejected",
                "deferred": "deferred",
                "revoked": "revoked",
                "superseded": "superseded",
            }.get(action, "unapproved"),
        )

    @classmethod
    def _revision_from_row(
        cls,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        source_row: sqlite3.Row | None = None,
        source_loaded: bool = False,
        status_row: sqlite3.Row | None = None,
        status_loaded: bool = False,
    ) -> EvidenceRevision:
        if not source_loaded:
            source_row = connection.execute(
                "SELECT * FROM source_documents WHERE source_id = ?",
                (str(row["source_id"]),),
            ).fetchone()
        if source_row is None:
            raise ValueError("Evidence source is missing")
        source = cls._validated_source_from_row(source_row)
        if source.source_hash != str(row["source_hash"]):
            raise ValueError("Evidence source changed after this revision was proposed")
        extracted_hash = row["extracted_hash"] if "extracted_hash" in set(row.keys()) else None
        if extracted_hash is not None and extracted_hash != source.extracted_hash:
            raise ValueError("Evidence extracted source changed after this revision was proposed")
        kind = cast(EvidenceKind, str(row["kind"]))
        metadata_payload = json.loads(str(row["contract_json"]))
        details = cast(dict[str, str], json.loads(str(row["details_json"])))
        cleaned_details = _validated_details(
            kind,
            details,
            strict_metrics=metadata_payload not in ({}, None),
        )
        statement = _clean_text(row["statement"], "statement", maximum=MAX_STATEMENT_CHARS)
        ownership = cast(Ownership, str(row["ownership"]))
        confidence = cast(Confidence, str(row["confidence"]))
        approximation = cast(Approximation, str(row["approximation"]))
        publication_boundary = cast(PublicationBoundary, str(row["publication_boundary"]))
        if ownership not in OWNERSHIP_VALUES or confidence not in CONFIDENCE_VALUES:
            raise ValueError("Stored evidence ownership or confidence is invalid")
        if approximation not in APPROXIMATION_VALUES:
            raise ValueError("Stored evidence approximation is invalid")
        if publication_boundary not in PUBLICATION_BOUNDARIES:
            raise ValueError("Stored evidence publication boundary is invalid")
        start_line = int(row["source_start_line"])
        end_line = int(row["source_end_line"])
        if start_line < 1 or end_line < start_line or end_line > source.line_count:
            raise ValueError("Stored evidence source span is invalid")
        metadata = EvidenceContractMetadata.from_dict(
            metadata_payload,
            fallback_ownership=ownership,
        )
        hash_payload: dict[str, object] = {
            "kind": kind,
            "statement": statement,
            "details": cleaned_details,
            "source_id": source.source_id,
            "source_hash": source.source_hash,
            "source_start_line": start_line,
            "source_end_line": end_line,
            "ownership": ownership,
            "confidence": confidence,
            "approximation": approximation,
            "publication_boundary": publication_boundary,
        }
        if metadata_payload not in ({}, None):
            hash_payload["metadata"] = metadata.to_dict()
        if extracted_hash is not None:
            hash_payload["extracted_hash"] = extracted_hash
        stored_hash = str(row["content_hash"])
        if _content_hash(hash_payload) != stored_hash:
            raise ValueError("Stored evidence revision no longer matches its content hash")
        return EvidenceRevision(
            evidence_id=str(row["evidence_id"]),
            revision=int(row["revision"]),
            kind=kind,
            statement=statement,
            details=cleaned_details,
            source_id=str(row["source_id"]),
            source_hash=str(row["source_hash"]),
            source_start_line=start_line,
            source_end_line=end_line,
            ownership=ownership,
            confidence=confidence,
            approximation=approximation,
            publication_boundary=publication_boundary,
            status=(
                cls._status_from_row(status_row, stored_hash)
                if status_loaded
                else cls._status(
                    connection,
                    str(row["evidence_id"]),
                    int(row["revision"]),
                    stored_hash,
                )
            ),
            content_hash=stored_hash,
            created_at=str(row["created_at"]),
            supersedes_revision=(
                int(row["supersedes_revision"]) if row["supersedes_revision"] is not None else None
            ),
            metadata=metadata,
            extracted_hash=(
                str(extracted_hash)
                if extracted_hash is not None
                else source.source_hash
                if source.source_hash == source.extracted_hash
                else None
            ),
        )

    @staticmethod
    def _candidate_record(revision: EvidenceRevision) -> CandidateEvidenceRecord:
        return CandidateEvidenceRecord(
            evidence_id=revision.evidence_id,
            statement=revision.statement,
            provenance=f"{revision.source_id}:lines-{revision.source_start_line}-{revision.source_end_line}",
            source_hash=revision.source_hash,
            publication_boundary=revision.publication_boundary,
            status=revision.status,
            source_heading=f"lines {revision.source_start_line}-{revision.source_end_line}",
            supersedes=(
                f"{revision.evidence_id}@{revision.supersedes_revision}"
                if revision.supersedes_revision is not None
                else None
            ),
            revision=revision.revision,
            content_hash=revision.content_hash,
            ownership=revision.ownership,
            confidence=revision.confidence,
            approximation=revision.approximation,
            source_id=revision.source_id,
            source_start_line=revision.source_start_line,
            source_end_line=revision.source_end_line,
        )

    @staticmethod
    def _publication_allows(
        boundary: PublicationBoundary, publication_use: PublicationUse | None
    ) -> bool:
        if publication_use in {None, "internal"}:
            return True
        allowed = {
            "application": {"application", "application_and_interview", "public"},
            "interview": {"interview", "application_and_interview", "public"},
            "public": {"public"},
        }
        return boundary in allowed[publication_use]


__all__ = [
    "BOUNDARY_VERSION",
    "EvidenceAuditEvent",
    "EvidenceReadiness",
    "EvidenceRevision",
    "EvidenceVault",
    "PROPOSAL_ACTORS",
    "ProposalActor",
    "SourceDocument",
]

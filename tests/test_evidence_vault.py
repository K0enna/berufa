from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from threading import Barrier

import pytest

import career_os.evidence_vault as vault_module
from career_os.document_intake import MAX_EXTRACTED_LINES, extract_candidate_document
from career_os.evidence_contract import EvidenceContractMetadata, PublicationRequest
from career_os.evidence_vault import EvidenceVault
from career_os.infrastructure import evidence as evidence_module
from career_os.infrastructure import evidence_storage, recovery
from career_os.infrastructure.evidence import create_evidence_service, read_private_json
from career_os.infrastructure.focus import LocalFocusRepository
from career_os.infrastructure.instances import create_instance_service
from career_os.infrastructure.opportunities import LocalOpportunityRepository
from career_os.infrastructure.recovery import create_recovery_service
from career_os.instance_workflow import main as instance_main
from career_os.synthetic_evidence import SYNTHETIC_SOURCE_BYTES, seed_synthetic_evidence
from tools import evidence_performance


def _seed(root: Path) -> EvidenceVault:
    vault = EvidenceVault(root)
    vault.record_consent(accepted=True)
    seed_synthetic_evidence(vault)
    return vault


@pytest.mark.parametrize("valid_statement", [True, False])
def test_schema_constraint_probe_closes_its_database(
    monkeypatch: pytest.MonkeyPatch, valid_statement: bool
) -> None:
    connection = sqlite3.connect(":memory:")
    monkeypatch.setattr(evidence_storage.sqlite3, "connect", lambda _path: connection)
    try:
        if valid_statement:
            assert evidence_storage._compiled_check_constraints(
                "CREATE TABLE sample (accepted INTEGER CHECK (accepted IN (0, 1)))",
                "sample",
                ("accepted",),
            ) == ("ACCEPTEDIN(0,1)",)
        else:
            with pytest.raises(ValueError, match="constraints"):
                evidence_storage._compiled_check_constraints("invalid SQL", "sample", ("accepted",))
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    finally:
        connection.close()


def _link_directory(link: Path, target: Path) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pytest.skip("Directory junction creation is unavailable")
    else:
        link.symlink_to(target, target_is_directory=True)


def test_private_json_open_rejects_an_ancestor_replaced_after_root_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "participant"
    safe_parent = root / "data"
    safe_parent.mkdir(parents=True)
    request = safe_parent / "request.json"
    request.write_text('{"source":"inside"}\n', encoding="utf-8")
    outside_parent = tmp_path / "outside"
    outside_parent.mkdir()
    (outside_parent / "request.json").write_text('{"source":"outside"}\n', encoding="utf-8")
    original_parent = root / "original-data"
    original_open = evidence_module.open_anchored_binary

    def raced_open(*args, **kwargs):  # noqa: ANN002,ANN003
        safe_parent.rename(original_parent)
        _link_directory(safe_parent, outside_parent)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(evidence_module, "open_anchored_binary", raced_open)
    try:
        with pytest.raises(ValueError, match="reparse|safely"):
            read_private_json(root, request, label="Synthetic request")
    finally:
        if os.name == "nt" and safe_parent.exists():
            safe_parent.rmdir()
        elif safe_parent.is_symlink():
            safe_parent.unlink()

    assert (original_parent / "request.json").read_text(encoding="utf-8") == (
        '{"source":"inside"}\n'
    )
    assert (outside_parent / "request.json").read_text(encoding="utf-8") == (
        '{"source":"outside"}\n'
    )


def test_private_json_open_preserves_missing_file_signal(tmp_path: Path) -> None:
    root = tmp_path / "participant"
    root.mkdir()

    with pytest.raises(FileNotFoundError):
        read_private_json(root, root / "missing.json", label="Optional decision")


def _seed_query_budget_vault(root: Path, record_count: int = 64) -> EvidenceVault:
    vault = EvidenceVault(root / "performance")
    vault.record_consent(accepted=True)
    source = vault.import_document(
        display_name="synthetic-query-budget.txt",
        media_type="text/plain",
        content=b"Synthetic query-budget fixture; all records are fictional.\n",
    )
    metadata = EvidenceContractMetadata(origin_ownership="personal")
    for index in range(record_count):
        vault.propose(
            source_id=source.source_id,
            evidence_id=f"evidence-query-budget-{index:04d}",
            kind="responsibility",
            statement=f"Synthetic query-budget responsibility {index}.",
            details={
                "responsibility": f"Own synthetic responsibility {index}",
                "decision_right": "Synthetic decision right",
            },
            source_start_line=1,
            source_end_line=1,
            ownership="personal",
            confidence="exact",
            approximation="exact",
            publication_boundary="application_and_interview",
            metadata=metadata,
        )
    return vault


def _measure_application_queries(monkeypatch, operation):
    statements: list[str] = []
    original_connect = sqlite3.connect

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", connect)
        result = operation()
    query_count = sum(
        statement.lstrip().upper().startswith("SELECT") and "SQLITE_MASTER" not in statement.upper()
        for statement in statements
    )
    return result, query_count, statements


def _materialized_blobs(monkeypatch: pytest.MonkeyPatch, operation):
    blobs: list[bytes] = []
    original_connect = sqlite3.connect

    class ObservingCursor(sqlite3.Cursor):
        def fetchall(self):
            rows = super().fetchall()
            blobs.extend(value for row in rows for value in row if isinstance(value, bytes))
            return rows

    class ObservingConnection(sqlite3.Connection):
        def execute(self, statement, parameters=()):
            return self.cursor(factory=ObservingCursor).execute(statement, parameters)

    def connect(*args, **kwargs):
        return original_connect(*args, factory=ObservingConnection, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", connect)
        result = operation()
    return result, blobs


def test_readiness_and_audit_events_have_fixed_query_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _seed_query_budget_vault(tmp_path)

    readiness, readiness_queries, _ = _measure_application_queries(monkeypatch, vault.readiness)
    events, audit_queries, _ = _measure_application_queries(monkeypatch, vault.audit_events)

    assert readiness.approved == 0
    assert readiness.unapproved == 64
    assert readiness_queries <= 6
    assert len(events) == 64
    assert audit_queries <= 2


def test_latest_revisions_does_not_load_unreferenced_source_blobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _seed_query_budget_vault(tmp_path, record_count=1)
    unused_content = b"Synthetic unused source content.\n"
    vault.import_document(
        display_name="unused-source.txt",
        media_type="text/plain",
        content=unused_content,
    )

    def unrestricted_source_read():
        with vault._connect(create=False) as connection:
            return connection.execute(
                "SELECT content FROM source_documents ORDER BY source_id"
            ).fetchall()

    _, probe_blobs = _materialized_blobs(monkeypatch, unrestricted_source_read)
    records, loaded_blobs = _materialized_blobs(monkeypatch, vault._latest_revisions)

    assert unused_content in probe_blobs
    assert len(records) == 1
    assert unused_content not in loaded_blobs


def test_evidence_performance_command_fails_a_breached_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(evidence_performance.QUERY_BUDGETS, "audit_events", 0)

    assert evidence_performance.main(["--sizes", "1"]) == 1


def test_evidence_performance_command_fails_a_breached_export_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evidence_performance, "EXPORT_QUERY_BUDGET_BASE", 0)

    assert evidence_performance.main(["--sizes", "1"]) == 1


def test_evidence_performance_accepts_repeated_sizes() -> None:
    report = evidence_performance.measure_sizes((1, 1))

    assert report["all_budgets_passed"] is True
    assert [item["records"] for item in report["measurements"]] == [1, 1, 1, 1, 1, 1]
    assert [item["operation"] for item in report["measurements"]] == [
        "readiness",
        "audit_events",
        "export_user_data",
        "readiness",
        "audit_events",
        "export_user_data",
    ]


def test_evidence_performance_export_is_complete_and_size_bounded() -> None:
    report = evidence_performance.measure_sizes((100,))
    measurement = next(
        item for item in report["measurements"] if item["operation"] == "export_user_data"
    )

    # One fixed namespace read adds no per-evidence queries or budget increase.
    assert measurement["query_count"] == 221
    assert measurement["query_budget"] == 224
    assert measurement["within_budget"] is True
    assert measurement["exported_counts"] == {
        "consent_receipts": 1,
        "sources": 1,
        "evidence_revisions": 100,
        "evidence_audit_events": 100,
    }
    assert measurement["identities_match"] is True
    assert measurement["export_complete"] is True
    assert measurement["size_budget"] == 152_304
    assert 0 < measurement["size_bytes"] <= measurement["size_budget"]
    assert measurement["within_size_budget"] is True


@pytest.mark.parametrize(
    "history_key",
    ["planned_task_history", "content_document_versions", "content_document_reviews"],
)
@pytest.mark.parametrize("mutation", ["missing", "nonempty"])
def test_evidence_performance_completeness_refuses_missing_or_nonempty_extra_histories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, history_key: str, mutation: str
) -> None:
    from dataclasses import replace

    original_factory = evidence_performance.create_instance_data_service

    class AlteredExport:
        def __init__(self, root: Path):
            self.service = original_factory(root)

        def export_user_data(self, destination: str, **kwargs):
            receipt = self.service.export_user_data(destination, **kwargs)
            path = Path(destination) / receipt.relative_file
            payload = json.loads(path.read_bytes())
            if mutation == "missing":
                del payload["data"][history_key]
            else:
                payload["data"][history_key] = [{}]
            content = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            path.write_bytes(content)
            # Keep receipt, evidence identities, manifest counts and numeric budgets
            # intact so refusal proves the additional serialized-history oracle.
            return replace(
                receipt, sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content)
            )

    monkeypatch.setattr(evidence_performance, "create_instance_data_service", AlteredExport)
    vault, expectations = evidence_performance._seed_vault(tmp_path / "fixture", 100)
    measured = evidence_performance._measure_export(vault, 100, tmp_path / "export", expectations)
    assert measured["identities_match"] is True
    assert measured["within_budget"] is True
    assert measured["within_size_budget"] is True
    assert measured["export_complete"] is False


def test_export_identity_match_accepts_lexical_ids_after_five_digits() -> None:
    expected = evidence_performance._ExportExpectations(
        sources=(("source-a", "source-hash", "extracted-hash"),),
        revisions=(
            ("synthetic-performance-99999", 1, "content-99999"),
            ("synthetic-performance-100000", 1, "content-100000"),
        ),
        audit_events=(
            (
                "event-99999",
                "synthetic-performance-99999",
                1,
                "proposed",
                "synthetic_fixture",
                "content-99999",
            ),
            (
                "event-100000",
                "synthetic-performance-100000",
                1,
                "proposed",
                "synthetic_fixture",
                "content-100000",
            ),
        ),
    )

    assert evidence_performance._export_identities_match(
        expected,
        expected.sources,
        tuple(reversed(expected.revisions)),
        expected.audit_events,
    )


def test_export_identity_match_rejects_same_length_duplicate() -> None:
    expected = evidence_performance._ExportExpectations(
        sources=(("source-a", "source-hash", "extracted-hash"),),
        revisions=(("evidence-a", 1, "content-a"), ("evidence-b", 1, "content-b")),
        audit_events=(
            ("event-a", "evidence-a", 1, "proposed", "synthetic_fixture", "content-a"),
            ("event-b", "evidence-b", 1, "proposed", "synthetic_fixture", "content-b"),
        ),
    )

    assert not evidence_performance._export_identities_match(
        expected,
        expected.sources,
        (expected.revisions[0], expected.revisions[0]),
        expected.audit_events,
    )


def _upgrade(vault: EvidenceVault, backup_name: str = "upgrade-backup"):
    service = create_instance_service(vault.instance_root)
    backup = str(vault.instance_root.parent / backup_name)
    preview = service.upgrade(backup)
    return service.upgrade(
        backup,
        confirmation_fingerprint=preview.confirmation_fingerprint,
        offline=True,
        actor="local_user",
    )


@pytest.mark.parametrize(
    "database_name",
    [
        "evidence-vault.db",
        pytest.param(
            "evidence-\udcff.db",
            marks=pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem bytes"),
        ),
    ],
)
def test_evidence_vault_construction_is_side_effect_free_and_instance_bound(
    tmp_path: Path,
    database_name: str,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    first = EvidenceVault(first_root, database_path=first_root / "data" / database_name)
    second = EvidenceVault(second_root)

    assert not first_root.exists()
    assert not second_root.exists()
    assert first.path != second.path
    with pytest.raises(ValueError, match="inside the instance data root"):
        EvidenceVault(first_root, database_path=second.path)
    first.record_consent(accepted=True)
    assert first.has_consent()


def test_vault_accepts_inbound_nested_database_with_uri_characters(tmp_path: Path) -> None:
    root = tmp_path / "participant"
    database = root / "data" / "custom" / "evidence-%23#vault.db"
    vault = EvidenceVault(root, database_path=database)

    assert not root.exists()
    assert vault.path == database
    vault.record_consent(accepted=True)
    assert vault.has_consent()
    assert database.is_file()


def test_existing_vault_read_does_not_raw_open_database_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = EvidenceVault(tmp_path / "participant")
    vault.record_consent(accepted=True)
    original_open = os.open
    original_file_pin = vault_module.open_anchored_binary
    original_directory_pin = vault_module.open_posix_anchored_directory
    file_pins: list[Path] = []
    directory_pins: list[Path] = []

    def checked_open(path, flags, mode=0o777, *, dir_fd=None):  # noqa: ANN001
        if os.name == "posix" and str(path) in {str(vault.path), vault.path.name}:
            pytest.fail("Existing SQLite database was independently raw-opened")
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def file_pin(root, root_identity, relative_path, *, label):  # noqa: ANN001
        file_pins.append(relative_path)
        return original_file_pin(root, root_identity, relative_path, label=label)

    def directory_pin(root, root_identity, relative_path, *, label):  # noqa: ANN001
        directory_pins.append(relative_path)
        return original_directory_pin(root, root_identity, relative_path, label=label)

    monkeypatch.setattr(vault_module.os, "open", checked_open)
    monkeypatch.setattr(vault_module, "open_anchored_binary", file_pin)
    monkeypatch.setattr(vault_module, "open_posix_anchored_directory", directory_pin)

    assert vault.has_consent()
    if os.name == "nt":
        assert file_pins == [Path("data/evidence-vault.db")]
        assert directory_pins == []
    else:
        assert file_pins == []
        assert directory_pins == [Path("data")]


@pytest.mark.skipif(os.name == "nt", reason="POSIX advisory-lock close semantics")
def test_nested_vault_read_preserves_outer_sqlite_lock_across_processes(tmp_path: Path) -> None:
    vault = EvidenceVault(tmp_path / "participant")
    vault.record_consent(accepted=True)
    contender = (
        "import sqlite3, sys; "
        "connection = sqlite3.connect(sys.argv[1], timeout=0.2); "
        "connection.execute('BEGIN IMMEDIATE'); connection.rollback()"
    )

    with vault._connect(create=True) as connection:
        assert connection.in_transaction
        assert vault.has_consent()
        result = subprocess.run(
            [sys.executable, "-c", contender, str(vault.path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode != 0
        assert "database is locked" in result.stderr


@pytest.mark.parametrize("linked_component", ["data", "nested"])
def test_vault_rejects_existing_redirected_directory_without_outside_effects(
    tmp_path: Path, linked_component: str
) -> None:
    root = tmp_path / "participant"
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = root if linked_component == "data" else root / "data"
    parent.mkdir(parents=True)
    link = parent / ("data" if linked_component == "data" else "nested")
    _link_directory(link, outside)
    database = root / "data" / "nested" / "evidence-vault.db"
    database_path = None if linked_component == "data" else database
    outside_mode = outside.stat().st_mode

    try:
        with pytest.raises(ValueError, match="boundary|data root|symlink|reparse"):
            EvidenceVault(root, database_path=database_path).record_consent(accepted=True)
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()

    assert list(outside.iterdir()) == []
    assert outside.stat().st_mode == outside_mode


def test_vault_rejects_data_directory_redirected_after_construction(tmp_path: Path) -> None:
    root = tmp_path / "participant"
    data = root / "data"
    data.mkdir(parents=True)
    vault = EvidenceVault(root)
    original = root / "original-data"
    data.rename(original)
    outside = tmp_path / "outside"
    outside.mkdir()
    _link_directory(data, outside)
    outside_mode = outside.stat().st_mode

    try:
        with pytest.raises(ValueError, match="boundary|symlink|reparse"):
            vault.record_consent(accepted=True)
    finally:
        if os.name == "nt":
            data.rmdir()
        else:
            data.unlink()

    assert list(outside.iterdir()) == []
    assert outside.stat().st_mode == outside_mode
    assert list(original.iterdir()) == []


def test_vault_open_cannot_create_outside_after_data_redirect_during_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "participant"
    data = root / "data"
    data.mkdir(parents=True)
    vault = EvidenceVault(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    original_data = root / "original-data"
    original_connect = sqlite3.connect
    attempted = False
    redirect_blocked = False
    redirected = False

    def redirect_before_sqlite_open(path, *args, **kwargs):  # noqa: ANN001,ANN002,ANN003
        nonlocal attempted, redirect_blocked, redirected
        if not attempted and "evidence-vault.db" in str(path):
            attempted = True
            try:
                data.rename(original_data)
            except OSError:
                redirect_blocked = True
            else:
                _link_directory(data, outside)
                redirected = True
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(vault_module.sqlite3, "connect", redirect_before_sqlite_open)
    try:
        if os.name == "nt":
            vault.record_consent(accepted=True)
            assert redirect_blocked is True
        else:
            with pytest.raises((OSError, ValueError)):
                vault.record_consent(accepted=True)
            assert redirected is True
    finally:
        if redirected:
            if os.name == "nt":
                data.rmdir()
            else:
                data.unlink()
            original_data.rename(data)

    assert attempted is True
    assert list(outside.iterdir()) == []


def test_document_import_requires_consent_and_preserves_exact_source_hash(
    tmp_path: Path,
) -> None:
    vault = EvidenceVault(tmp_path / "participant")

    with pytest.raises(PermissionError, match="explicit local consent"):
        vault.import_document(
            display_name="synthetic.txt",
            media_type="text/plain",
            content=SYNTHETIC_SOURCE_BYTES,
        )
    vault.record_consent(accepted=True)
    source, proposals = seed_synthetic_evidence(vault)

    assert source.source_hash == vault.context_fingerprints()[source.source_id]
    assert source.line_count == 12
    assert len(proposals) == 6
    assert {proposal.status for proposal in proposals} == {"unapproved"}
    assert len(vault.review_queue()) == 6
    assert vault.source_lines(source.source_id, 9, 9) == (
        "Education: coursework toward a B.Sc. in Information Systems at Fictional Metropolitan "
        "Institute; no degree was awarded.",
    )
    education = next(item for item in proposals if item.evidence_id == "synthetic-education")
    assert education.details["institution"] in vault.source_lines(source.source_id, 9, 9)[0]


def test_read_transaction_never_initializes_migrates_or_writes(tmp_path: Path) -> None:
    absent = EvidenceVault(tmp_path / "absent")
    with pytest.raises(ValueError, match="not been initialized"), absent.read_transaction():
        pass
    assert not absent.instance_root.exists()
    legacy = _legacy_vault(tmp_path / "legacy")
    before = legacy.path.read_bytes()
    with legacy.read_transaction():
        assert legacy.has_consent()
    assert legacy.path.read_bytes() == before
    assert LocalFocusRepository(legacy.instance_root).read().evidence_readiness is None
    assert legacy.path.read_bytes() == before
    with pytest.raises((ValueError, OSError)):
        LocalOpportunityRepository(legacy.instance_root).resolve_supplied("example_role")
    with sqlite3.connect(legacy.path) as connection:
        assert (
            connection.execute(
                "SELECT value FROM vault_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            == "1"
        )
    with pytest.raises(ValueError, match="Explicit backed-up instance upgrade"):
        legacy.record_consent(accepted=False)
    assert legacy.path.read_bytes() == before
    vault = _seed(tmp_path / "current")
    before = {path: path.read_bytes() for path in vault.instance_root.rglob("*") if path.is_file()}
    with vault.read_transaction(), vault.read_transaction():
        assert len(vault.review_queue()) == 6
    with pytest.raises(OSError, match="storage is unavailable"), vault.read_transaction():
        vault.record_consent(accepted=False)
    assert vault.has_consent()
    assert {
        path: path.read_bytes() for path in vault.instance_root.rglob("*") if path.is_file()
    } == before


def test_parser_result_persists_original_extracted_and_location_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "career.md"
    original = b"# Synthetic role\r\n\r\nSynthetic responsibility\r\n"
    path.write_bytes(original)
    parsed = extract_candidate_document(path)
    vault = EvidenceVault(tmp_path / "participant")
    vault.record_consent(accepted=True)

    source = vault.import_extracted_document(parsed)
    repeated = vault.import_extracted_document(parsed)
    exported = vault.export_snapshot()["sources"][0]

    assert repeated == source
    assert source.source_hash == parsed.original_sha256
    assert source.extracted_hash == parsed.extracted_sha256
    assert source.source_hash != source.extracted_hash
    assert source.input_format == "markdown"
    assert source.managed_copy is True
    assert [location.kind for location in source.locations] == ["line", "line", "line"]
    assert vault.source_lines(source.source_id, 1, 3) == (
        "# Synthetic role",
        "",
        "Synthetic responsibility",
    )
    assert "original_content" not in exported
    assert "content" not in exported
    assert exported["source_hash"] == parsed.original_sha256
    assert exported["extracted_hash"] == parsed.extracted_sha256

    with sqlite3.connect(vault.path) as connection:
        connection.execute(
            "UPDATE source_documents SET original_content = ? WHERE source_id = ?",
            (b"tampered", source.source_id),
        )
    with pytest.raises(ValueError, match="original content"):
        vault.sources()


def _legacy_vault(
    tmp_path: Path, content: bytes = b"Synthetic legacy source\n", media_type: str = "text/plain"
) -> EvidenceVault:
    root = tmp_path / "participant"
    database = root / "data" / "evidence-vault.db"
    database.parent.mkdir(parents=True)
    source_hash = hashlib.sha256(content).hexdigest()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE vault_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE consent_receipts (
                receipt_id TEXT PRIMARY KEY, boundary_version TEXT NOT NULL,
                accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
                actor TEXT NOT NULL, occurred_at TEXT NOT NULL
            );
            CREATE TABLE source_documents (
                source_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                media_type TEXT NOT NULL, source_hash TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, line_count INTEGER NOT NULL,
                imported_at TEXT NOT NULL, content BLOB NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO vault_meta VALUES(?, ?)",
            [
                ("schema_version", "1"),
                ("instance_root_hash", hashlib.sha256(str(root.resolve()).encode()).hexdigest()),
            ],
        )
        connection.execute(
            "INSERT INTO consent_receipts VALUES(?, ?, ?, ?, ?)",
            ("consent-legacy", "local-private-v1", 1, "local_user", "2026-01-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO source_documents VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"source-{source_hash[:24]}",
                "legacy.txt",
                media_type,
                source_hash,
                len(content),
                len(content.decode("utf-8").splitlines()),
                "2026-01-01T00:00:00Z",
                content,
            ),
        )

    return EvidenceVault(root)


def _v3_approved_vault(tmp_path: Path, *, normalized: bool = False) -> EvidenceVault:
    path = tmp_path / "source.md"
    path.write_bytes(
        b"Synthetic service reviews.\r\n" if normalized else b"Synthetic service reviews.\n"
    )
    vault = EvidenceVault(tmp_path / "participant")
    vault.record_consent(accepted=True)
    source = vault.import_extracted_document(extract_candidate_document(path))
    proposal = vault.propose(
        source_id=source.source_id,
        evidence_id="legacy-responsibility",
        kind="responsibility",
        statement="Own service reviews.",
        details={"responsibility": "Service reviews", "decision_right": "Own reviews"},
        source_start_line=1,
        source_end_line=1,
        ownership="personal",
        confidence="exact",
        approximation="exact",
        publication_boundary="application",
    )
    vault.review(
        proposal.evidence_id, 1, action="approve", expected_content_hash=proposal.content_hash
    )
    # Reproduce the v3 record and audit identities, before extracted hashes were bound.
    payload = asdict(proposal)
    for key in (
        "evidence_id",
        "revision",
        "status",
        "content_hash",
        "created_at",
        "supersedes_revision",
        "extracted_hash",
    ):
        payload.pop(key)
    old_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    events = vault.audit_events()
    with sqlite3.connect(vault.path) as connection:
        connection.execute("UPDATE evidence_revisions SET content_hash = ?", (old_hash,))
        for event in events:
            identity = EvidenceVault._event_id(
                evidence_id=event.evidence_id,
                revision=event.revision,
                action=event.action,
                actor=event.actor,
                occurred_at=event.occurred_at,
                content_hash=old_hash,
                reason=event.reason,
            )
            connection.execute(
                "UPDATE evidence_events SET content_hash = ?, event_id = ? WHERE event_id = ?",
                (old_hash, identity, event.event_id),
            )
        connection.execute("ALTER TABLE evidence_revisions DROP COLUMN extracted_hash")
        connection.execute("UPDATE vault_meta SET value = '3' WHERE key = 'schema_version'")
        connection.execute("DELETE FROM vault_meta WHERE key = 'migration_4'")
    return vault


@pytest.mark.parametrize("normalized", [False, True])
def test_offline_restore_preserves_legacy_v3_without_migration(
    tmp_path: Path, normalized: bool
) -> None:
    vault = _v3_approved_vault(tmp_path, normalized=normalized)
    before = vault.path.read_bytes()
    revision = vault.latest("legacy-responsibility")
    events = vault.audit_events()
    backup = tmp_path / "backup"
    create_recovery_service(vault.instance_root).backup(
        str(backup), offline=True, actor="local_user"
    )
    restored_root = tmp_path / "restored"
    service = create_recovery_service(restored_root)
    preview = service.restore(str(backup))
    assert preview.inventory.evidence_storage_version == 3
    service.restore(
        str(backup),
        confirmation_fingerprint=preview.confirmation_fingerprint,
        offline=True,
        actor="local_user",
    )
    restored = EvidenceVault(restored_root)
    assert restored.latest(revision.evidence_id) == revision
    assert restored.audit_events() == events
    with sqlite3.connect(restored.path) as connection:
        assert connection.execute(
            "SELECT value FROM vault_meta WHERE key = 'schema_version'"
        ).fetchone() == ("3",)
    assert vault.path.read_bytes() == before


@pytest.mark.parametrize("normalized", [False, True])
def test_v3_approval_binding_is_preserved_or_requires_explicit_re_review(
    tmp_path: Path,
    normalized: bool,
) -> None:
    vault = _v3_approved_vault(tmp_path, normalized=normalized)
    before = vault.path.read_bytes()
    legacy = vault.latest("legacy-responsibility")
    events = vault.audit_events()
    assert vault.path.read_bytes() == before
    assert legacy.status == "approved"
    assert (legacy.extracted_hash is None) is normalized

    _upgrade(vault)
    assert vault.latest(legacy.evidence_id) == legacy
    assert vault.audit_events() == events
    with sqlite3.connect(vault.path) as connection:
        assert connection.execute("SELECT extracted_hash FROM evidence_revisions").fetchone() == (
            None,
        )
        metadata = dict(connection.execute("SELECT key, value FROM vault_meta"))
    assert metadata["migration_3"] == evidence_storage.MIGRATION_IDENTITIES[3]
    assert metadata["migration_4"] == evidence_storage.MIGRATION_IDENTITIES[4]
    migrated = vault.path.read_bytes()
    vault.initialize()
    assert vault.path.read_bytes() == migrated
    request = PublicationRequest(
        statement=legacy.statement,
        evidence_ids=(legacy.evidence_id,),
        use="application",
        ownership="personal",
        approximation="exact",
    )
    assert vault.validate_publication(request).ready is not normalized
    assert bool(vault.export_records()) is not normalized
    assert bool(vault.classify_for_role("operations").transferable) is not normalized
    assert bool(vault.review_queue()) is normalized
    if not normalized:
        assert vault.resolve(legacy.evidence_id).content_hash == legacy.content_hash
        retry = vault.propose(
            **{
                key: getattr(legacy, key)
                for key in (
                    "source_id",
                    "evidence_id",
                    "kind",
                    "statement",
                    "details",
                    "source_start_line",
                    "source_end_line",
                    "ownership",
                    "confidence",
                    "approximation",
                    "publication_boundary",
                    "metadata",
                )
            }
        )
        assert retry == legacy
        assert vault.audit_events() == events
        return

    assert any("legacy source binding" in blocker for blocker in vault.readiness().blockers)
    assert "source_binding_required" in {
        issue.code for issue in vault.validate_publication(request).issues
    }
    with pytest.raises(ValueError, match="new revision and review"):
        vault.resolve(legacy.evidence_id)
    with pytest.raises(ValueError, match="new revision and review"):
        vault.verify(legacy.evidence_id, 1, expected_content_hash=legacy.content_hash)
    with pytest.raises(ValueError, match="new revision and review"):
        vault.review(
            legacy.evidence_id, 1, action="approve", expected_content_hash=legacy.content_hash
        )
    assert vault.audit_events() == events
    revised = vault.edit(legacy.evidence_id, expected_revision=1)
    assert revised.status == "unapproved"
    assert revised.extracted_hash == vault.source(legacy.source_id).extracted_hash
    assert revised.content_hash != legacy.content_hash
    assert not vault.validate_publication(request).ready
    vault.review(
        revised.evidence_id, 2, action="approve", expected_content_hash=revised.content_hash
    )
    assert vault.validate_publication(request).ready
    assert [item.status for item in vault.revisions(legacy.evidence_id)] == [
        "superseded",
        "approved",
    ]
    assert vault.audit_events()[: len(events)] == events


def test_v4_upgrade_refuses_executable_schema_and_requires_explicit_retry(tmp_path: Path) -> None:
    vault = _v3_approved_vault(tmp_path)
    with sqlite3.connect(vault.path) as connection:
        connection.execute("""CREATE TRIGGER fail_upgrade BEFORE UPDATE ON vault_meta
            WHEN NEW.key = 'schema_version' AND NEW.value = '4'
            BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
    before = vault.path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported evidence schema"):
        _upgrade(vault)
    assert vault.path.read_bytes() == before
    with sqlite3.connect(vault.path) as connection:
        connection.execute("DROP TRIGGER fail_upgrade")
    with pytest.raises(ValueError, match="Explicit backed-up instance upgrade"):
        vault.edit("legacy-responsibility", expected_revision=1)
    _upgrade(vault, "retry-backup")
    revised = vault.edit("legacy-responsibility", expected_revision=1)
    assert revised.revision == 2
    assert revised.extracted_hash is not None
    with sqlite3.connect(vault.path) as connection:
        assert (
            dict(connection.execute("SELECT key, value FROM vault_meta"))["schema_version"] == "4"
        )


@pytest.mark.parametrize("revision_binding", ["unchanged", "changed", "removed"])
def test_approval_cannot_survive_changed_extraction_even_with_updated_source_digest(
    tmp_path: Path,
    revision_binding: str,
) -> None:
    vault = _seed(tmp_path / "participant")
    original = vault.latest("synthetic-role-timeline")
    approved = vault.review(
        original.evidence_id,
        original.revision,
        action="approve",
        expected_content_hash=original.content_hash,
    )
    assert approved.extracted_hash == hashlib.sha256(SYNTHETIC_SOURCE_BYTES).hexdigest()
    content = SYNTHETIC_SOURCE_BYTES.replace(b"Northstar", b"Otherstar")
    changed_hash = hashlib.sha256(content).hexdigest()
    events = vault.audit_events()
    with sqlite3.connect(vault.path) as connection:
        connection.execute(
            "UPDATE source_documents SET content = ?, extracted_hash = ?", (content, changed_hash)
        )
        if revision_binding != "unchanged":
            connection.execute(
                "UPDATE evidence_revisions SET extracted_hash = ? WHERE evidence_id = ?",
                (changed_hash if revision_binding == "changed" else None, original.evidence_id),
            )
    assert vault.sources()[0].source_hash == original.source_hash
    with pytest.raises(ValueError, match="extracted source changed|content hash"):
        vault.latest(original.evidence_id)
    with pytest.raises(ValueError, match="extracted source changed|content hash"):
        vault.export_records()
    with pytest.raises(ValueError, match="extracted source changed|content hash"):
        vault.verify(original.evidence_id, 1, expected_content_hash=original.content_hash)
    assert vault.audit_events() == events


@pytest.mark.parametrize(
    ("content", "media_type", "input_format", "completed_retry"),
    [
        (b"Synthetic legacy source\n", "text/plain", "text", False),
        (b"# Synthetic legacy source\r\n", "text/markdown", "markdown", False),
        (b"\xef\xbb\xbf# Synthetic legacy source\r\n", "text/markdown", "markdown", False),
        (b"# Synthetic completed retry\r\n", "text/markdown", "markdown", True),
    ],
)
def test_legacy_text_source_is_migrated_to_managed_provenance(
    tmp_path: Path, content: bytes, media_type: str, input_format: str, completed_retry: bool
) -> None:
    vault = _legacy_vault(tmp_path, content, media_type)
    # A prior non-transactional initializer could leave this default behind.
    with sqlite3.connect(vault.path) as connection:
        connection.execute(
            "ALTER TABLE source_documents ADD COLUMN input_format TEXT DEFAULT 'text'"
        )
    _upgrade(vault)
    if completed_retry:
        with sqlite3.connect(vault.path) as connection:
            connection.execute("UPDATE vault_meta SET value = '2' WHERE key = 'schema_version'")
            connection.execute("DELETE FROM vault_meta WHERE key = 'migration_3'")
            connection.execute("UPDATE source_documents SET input_format = 'text'")
        _upgrade(vault, "retry-backup")
    source = vault.sources()[0]

    assert source.input_format == input_format
    assert source.source_hash == source.extracted_hash == hashlib.sha256(content).hexdigest()
    assert source.managed_copy is True
    assert len(source.locations) == 1
    before_retry = vault.path.read_bytes()
    vault.initialize()
    assert vault.path.read_bytes() == before_retry
    document = tmp_path / ("source.md" if input_format == "markdown" else "source.txt")
    document.write_bytes(content)
    repeated = vault.import_extracted_document(extract_candidate_document(document))
    assert repeated == source
    assert len(vault.sources()) == 1


@pytest.mark.parametrize("corruption", ["newer", "foreign", "unbound", "migration"])
def test_unsupported_or_foreign_store_is_rejected_without_mutation(
    tmp_path: Path, corruption: str
) -> None:
    vault = _seed(tmp_path / "participant")
    with sqlite3.connect(vault.path) as connection:
        if corruption == "unbound":
            connection.execute("DELETE FROM vault_meta WHERE key = 'instance_root_hash'")
        else:
            key, value = {
                "newer": ("schema_version", "99"),
                "foreign": ("instance_root_hash", "another-instance"),
                "migration": ("migration_3", "different-migration"),
            }[corruption]
            connection.execute("UPDATE vault_meta SET value = ? WHERE key = ?", (value, key))
    before = vault.path.read_bytes()
    with pytest.raises(ValueError, match="version|instance|migration"):
        vault.initialize()
    with pytest.raises(ValueError, match="version|instance|migration"):
        vault.has_consent()
    assert vault.path.read_bytes() == before


def test_failed_upgrade_rolls_back_schema_and_source_data_then_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _legacy_vault(tmp_path)
    original = evidence_storage._backfill_sources

    def fail_after_backfill(connection: sqlite3.Connection) -> None:
        original(connection)
        raise OSError("simulated storage failure")

    monkeypatch.setattr(evidence_storage, "_backfill_sources", fail_after_backfill)
    before = vault.path.read_bytes()
    with pytest.raises(OSError, match="simulated storage failure"):
        _upgrade(vault)
    assert vault.path.read_bytes() == before
    assert (
        vault.instance_root.parent / "upgrade-backup" / "workspace" / "data" / "evidence-vault.db"
    ).read_bytes() == before
    monkeypatch.setattr(evidence_storage, "_backfill_sources", original)
    _upgrade(vault, "retry-backup")
    assert vault.has_consent()
    assert len(vault.sources()) == 1
    assert vault.audit_events() == ()


@pytest.mark.parametrize("extended_resolution", [False, True])
def test_concurrent_initializers_share_one_versioned_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extended_resolution: bool
) -> None:
    if extended_resolution and os.name != "nt":
        pytest.skip("Extended-length path resolution is specific to Windows")
    root = tmp_path / "participant"
    outside = tmp_path / "other-participant"
    if extended_resolution:
        original_resolve = Path.resolve

        def resolve(path: Path, strict: bool = False) -> Path:
            resolved = original_resolve(path, strict=strict)
            if path.name == "evidence-vault.db" and not str(resolved).startswith("\\\\?\\"):
                return Path("\\\\?\\" + str(resolved))
            return resolved

        monkeypatch.setattr(Path, "resolve", resolve)

    with pytest.raises(ValueError, match="inside the instance data root"):
        EvidenceVault(root, database_path=outside / "data" / "evidence-vault.db")
    assert not root.exists()
    assert not outside.exists()
    barrier = Barrier(2)

    def initialize(_: int) -> None:
        barrier.wait(timeout=10)
        EvidenceVault(root).initialize()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(initialize, range(2)))
    vault = EvidenceVault(root)
    with sqlite3.connect(vault.path) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM vault_meta"))
    assert metadata["schema_version"] == str(evidence_storage.EVIDENCE_STORAGE_VERSION)
    assert metadata["migration_3"] == evidence_storage.MIGRATION_IDENTITIES[3]
    assert vault.audit_events() == ()
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink", lambda path: path == vault.path or original_is_symlink(path)
    )
    with pytest.raises(ValueError, match="crossed the participant instance boundary"):
        vault.audit_events()
    monkeypatch.setattr(Path, "is_symlink", original_is_symlink)
    vault.path = outside / "data" / "evidence-vault.db"
    with pytest.raises(ValueError, match="crossed the participant instance boundary"):
        vault.initialize()
    assert not outside.exists()


def test_direct_and_legacy_source_line_expansion_is_bounded(tmp_path: Path) -> None:
    content = b"x\n" * (MAX_EXTRACTED_LINES + 1)
    vault = EvidenceVault(tmp_path / "fresh")
    vault.record_consent(accepted=True)
    with pytest.raises(ValueError, match="line limit"):
        vault.import_document(display_name="bounded.txt", media_type="text/plain", content=content)
    assert vault.sources() == ()
    legacy = _legacy_vault(tmp_path, content)
    before = legacy.path.read_bytes()
    with pytest.raises(ValueError, match="line inventory"):
        _upgrade(legacy)
    assert legacy.path.read_bytes() == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_vault_private_permissions_cover_new_and_existing_files(tmp_path: Path) -> None:
    vault = EvidenceVault(tmp_path / "participant")
    vault.initialize()
    for _ in range(2):
        assert vault.path.stat().st_mode & 0o777 == 0o600
        assert vault.path.parent.stat().st_mode & 0o777 == 0o700
        vault.path.chmod(0o666)
        vault.path.parent.chmod(0o755)
        vault.initialize()


def test_approval_is_local_user_only_and_binds_to_exact_revision_hash(tmp_path: Path) -> None:
    vault = _seed(tmp_path / "participant")
    proposal = vault.latest("synthetic-role-timeline")

    with pytest.raises(PermissionError, match="Only the local user"):
        vault.review(
            proposal.evidence_id,
            proposal.revision,
            action="approve",
            expected_content_hash=proposal.content_hash,
            actor="operator",
        )
    with pytest.raises(ValueError, match="content changed"):
        vault.review(
            proposal.evidence_id,
            proposal.revision,
            action="approve",
            expected_content_hash="0" * 64,
        )

    approved = vault.review(
        proposal.evidence_id,
        proposal.revision,
        action="approve",
        expected_content_hash=proposal.content_hash,
    )

    assert approved.status == "approved"
    assert vault.resolve(proposal.evidence_id).content_hash == proposal.content_hash
    assert vault.audit_events()[-1].content_hash == proposal.content_hash
    snapshot = vault.export_snapshot()
    with sqlite3.connect(vault.path) as connection:
        connection.execute("UPDATE vault_meta SET value = '2' WHERE key = 'schema_version'")
        connection.execute("DELETE FROM vault_meta WHERE key = 'migration_3'")
    _upgrade(vault)
    assert vault.export_snapshot() == snapshot


def test_upgrade_confirmation_binds_all_files_and_destination_and_preserves_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = _v3_approved_vault(tmp_path)
    service = create_instance_service(vault.instance_root)
    backup = tmp_path / "backup"
    before = vault.path.read_bytes()
    preview = service.upgrade(str(backup))
    assert preview.inventory.evidence_storage_version == 3
    assert preview.migrations == (evidence_storage.MIGRATION_IDENTITIES[4],)
    assert vault.path.read_bytes() == before and not backup.exists()
    for actor, offline in [("agent", True), ("local_user", False)]:
        with pytest.raises(PermissionError):
            service.upgrade(
                str(backup),
                confirmation_fingerprint=preview.confirmation_fingerprint,
                offline=offline,
                actor=actor,
            )
    with pytest.raises(PermissionError, match="preview changed"):
        service.upgrade(
            str(tmp_path / "other-backup"),
            confirmation_fingerprint=preview.confirmation_fingerprint,
            offline=True,
            actor="local_user",
        )
    (vault.instance_root / "notes.txt").write_text("Synthetic changed input", encoding="utf-8")
    with pytest.raises(PermissionError, match="preview changed"):
        service.upgrade(
            str(backup),
            confirmation_fingerprint=preview.confirmation_fingerprint,
            offline=True,
            actor="local_user",
        )
    assert vault.path.read_bytes() == before and not backup.exists()
    database_size = vault.path.stat().st_size
    space_checks = []
    original_space = recovery._space_on_volume

    def check_space(target, size):
        space_checks.append((target, size))
        original_space(target, size)

    monkeypatch.setattr(recovery, "_space_on_volume", check_space)
    receipt = _upgrade(vault, "backup")
    assert receipt.changed and receipt.evidence_storage_version == 4
    inventory = create_recovery_service(vault.instance_root).verify(str(backup))
    assert inventory.fingerprint == receipt.backup_fingerprint
    assert (backup / "workspace" / "data" / "evidence-vault.db").read_bytes() == before
    assert space_checks[-1] == (vault.path, database_size)
    layout = {
        check.name: check.status
        for check in create_instance_service(vault.instance_root).doctor().checks
    }
    assert layout["layout"] == "legacy_compatible"
    assert layout["evidence_storage"] == "ok"
    after = vault.path.read_bytes()
    retry = service.upgrade(
        str(backup),
        confirmation_fingerprint=preview.confirmation_fingerprint,
        offline=True,
        actor="local_user",
    )
    assert not retry.changed and retry.backup_fingerprint is None
    assert vault.path.read_bytes() == after


def test_changed_workspace_after_backup_cannot_be_migrated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from career_os.infrastructure.recovery import LocalRecoveryRepository

    vault = _legacy_vault(tmp_path)
    original = LocalRecoveryRepository.verify
    before = vault.path.read_bytes()

    def verify(repository, backup):
        inventory = original(repository, backup)
        (vault.instance_root / "changed.txt").write_text(
            "Synthetic concurrent edit", encoding="utf-8"
        )
        return inventory

    monkeypatch.setattr(LocalRecoveryRepository, "verify", verify)
    with pytest.raises(ValueError, match="changed after backup"):
        _upgrade(vault)
    assert vault.path.read_bytes() == before


def test_installed_instance_cli_previews_and_confirms_legacy_upgrade(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault = _legacy_vault(tmp_path)
    backup = tmp_path / "backup"
    args = [
        "--instance-root",
        str(vault.instance_root),
        "upgrade",
        "--backup-destination",
        str(backup),
    ]
    assert instance_main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["from_version"] == 1 and preview["target_version"] == 4
    assert (
        instance_main(
            args + ["--confirm-upgrade", preview["confirmation_fingerprint"], "--offline"]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["changed"] and create_recovery_service(vault.instance_root).verify(str(backup))


def test_editing_approved_evidence_creates_unapproved_revision_then_supersedes(
    tmp_path: Path,
) -> None:
    vault = _seed(tmp_path / "participant")
    original = vault.latest("synthetic-responsibility")
    vault.review(
        original.evidence_id,
        original.revision,
        action="approve",
        expected_content_hash=original.content_hash,
    )

    edited = vault.edit(
        original.evidence_id,
        expected_revision=1,
        statement=original.statement + " This wording was explicitly clarified.",
    )

    assert edited.revision == 2
    assert edited.status == "unapproved"
    assert edited.content_hash != original.content_hash
    assert [item.status for item in vault.revisions(original.evidence_id)] == [
        "approved",
        "unapproved",
    ]
    approved_edit = vault.review(
        edited.evidence_id,
        edited.revision,
        action="approve",
        expected_content_hash=edited.content_hash,
    )
    assert approved_edit.status == "approved"
    assert [item.status for item in vault.revisions(original.evidence_id)] == [
        "superseded",
        "approved",
    ]


def test_team_outcome_and_partial_education_fail_closed(tmp_path: Path) -> None:
    vault = _seed(tmp_path / "participant")
    achievement = vault.latest("synthetic-achievement")
    education = vault.latest("synthetic-education")

    with pytest.raises(ValueError, match="team outcome"):
        vault.edit(
            achievement.evidence_id,
            expected_revision=achievement.revision,
            ownership="personal",
        )
    with pytest.raises(ValueError, match="no degree"):
        vault.edit(
            education.evidence_id,
            expected_revision=education.revision,
            details={**education.details, "completion_status": "awarded_degree"},
        )


def test_confidential_approximate_and_unknown_changes_require_new_review(
    tmp_path: Path,
) -> None:
    vault = _seed(tmp_path / "participant")
    role = vault.latest("synthetic-role-timeline")

    revised = vault.edit(
        role.evidence_id,
        expected_revision=role.revision,
        publication_boundary="private",
        confidence="unknown",
        approximation="unknown",
    )

    assert revised.status == "unapproved"
    assert revised.publication_boundary == "private"
    assert revised.confidence == "unknown"
    assert revised.approximation == "unknown"
    assert vault.enumerate(statuses=("approved",), publication_use="application") == ()


@pytest.mark.parametrize(
    ("confidence", "disposition", "expected_ready"),
    [
        ("unknown", "approved", False),
        ("disputed", "unapproved", False),
        ("unknown", "deferred", False),
        ("unknown", "rejected", True),
        ("disputed", "rejected", True),
        ("unknown", "revoked", True),
        ("disputed", "revoked", True),
    ],
)
def test_readiness_only_blocks_active_unknown_or_disputed_latest_revision(
    tmp_path: Path,
    confidence: str,
    disposition: str,
    expected_ready: bool,
) -> None:
    vault = _seed(tmp_path / "participant")
    target_id = "synthetic-uncertain-disposition"

    for card in vault.review_queue():
        vault.review(
            card.evidence_id,
            card.revision,
            action="reject" if card.kind == "forbidden_claim" else "approve",
            expected_content_hash=card.content_hash,
        )
    baseline = vault.readiness()
    assert baseline.ready is True
    source = vault.sources()[0]
    target = vault.propose(
        source_id=source.source_id,
        evidence_id=target_id,
        kind="candidate_profile",
        statement="The scope of this synthetic evidence is not settled.",
        details={"field": "scope", "value": "not settled"},
        source_start_line=1,
        source_end_line=1,
        ownership="personal",
        confidence=confidence,  # type: ignore[arg-type]
        approximation="unknown",
        publication_boundary="application",
    )
    review_actions = {
        "approved": ("approve",),
        "deferred": ("defer",),
        "rejected": ("reject",),
        "revoked": ("approve", "revoke"),
    }
    for action in review_actions.get(disposition, ()):
        target = vault.review(
            target.evidence_id,
            target.revision,
            action=action,  # type: ignore[arg-type]
            expected_content_hash=target.content_hash,
        )

    readiness = vault.readiness()
    assert readiness.ready is expected_ready
    assert readiness.unknown_or_disputed == int(expected_ready is False)
    assert getattr(readiness, disposition) == getattr(baseline, disposition) + 1
    assert create_evidence_service(vault.instance_root).readiness() == readiness
    assert LocalFocusRepository(vault.instance_root).read().evidence_readiness == readiness
    assert any(
        record.evidence_id == target_id and record.status == disposition
        for record in vault.enumerate()
    )
    assert [record.status for record in vault.revisions(target_id)] == [disposition]
    assert ("remain unknown or disputed" in " ".join(readiness.blockers)) is not expected_ready

    if expected_ready:
        snapshot_record = next(
            record
            for record in vault.export_snapshot()["evidence"]
            if record["evidence_id"] == target_id
        )
        assert snapshot_record["status"] == disposition
        assert any(
            record["evidence_id"] == target_id
            for record in vault.export_user_owned_data()["evidence_revisions"]
        )
        expected_actions = (
            ("proposed", "rejected")
            if disposition == "rejected"
            else ("proposed", "approved", "revoked")
        )
        assert (
            tuple(event.action for event in vault.audit_events() if event.evidence_id == target_id)
            == expected_actions
        )


def test_source_tampering_blocks_readiness_and_approval(tmp_path: Path) -> None:
    vault = _seed(tmp_path / "participant")
    proposal = vault.latest("synthetic-role-timeline")
    with sqlite3.connect(vault.path) as connection:
        connection.execute(
            "UPDATE source_documents SET content = ? WHERE source_id = ?",
            (b"tampered\n", proposal.source_id),
        )

    with pytest.raises(ValueError, match="recorded hash"):
        vault.readiness()
    with pytest.raises(ValueError, match="recorded hash"):
        vault.review(
            proposal.evidence_id,
            proposal.revision,
            action="approve",
            expected_content_hash=proposal.content_hash,
        )


def test_two_synthetic_vaults_do_not_share_state(tmp_path: Path) -> None:
    first = _seed(tmp_path / "first")
    second = _seed(tmp_path / "second")
    first_role = first.latest("synthetic-role-timeline")
    first.review(
        first_role.evidence_id,
        first_role.revision,
        action="approve",
        expected_content_hash=first_role.content_hash,
    )

    assert first.path != second.path
    assert first.latest(first_role.evidence_id).status == "approved"
    assert second.latest(first_role.evidence_id).status == "unapproved"
    assert second.review_queue() != ()


def test_export_is_user_owned_and_credential_free(tmp_path: Path) -> None:
    vault = _seed(tmp_path / "participant")
    snapshot = vault.export_snapshot()

    assert snapshot["credentials_included"] is False
    assert snapshot["remote_telemetry"] is False
    assert len(snapshot["sources"]) == 1
    assert len(snapshot["evidence"]) == 6
    assert "content" not in snapshot["sources"][0]

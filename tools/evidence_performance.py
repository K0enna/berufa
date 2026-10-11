"""Measure the synthetic EvidenceVault read-query baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from career_os.evidence_contract import EvidenceContractMetadata
from career_os.evidence_vault import EvidenceVault
from career_os.infrastructure.instance_data import create_instance_data_service
from career_os.infrastructure.instances import create_instance_service

DEFAULT_SIZES = (100, 1_000, 10_000)
QUERY_BUDGETS = {
    "readiness": 6,
    "audit_events": 2,
}
EXPORT_QUERY_BUDGET_BASE = 24
EXPORT_QUERY_BUDGET_PER_RECORD = 2
# Task/document history inventories add fixed serialization metadata even when empty.
# The expanded schema receives 256 bytes of fixed allowance; per-record scaling is unchanged.
EXPORT_SIZE_BUDGET_BASE = 2_304
EXPORT_SIZE_BUDGET_PER_RECORD = 1_500
_SYNTHETIC_SOURCE = b"Synthetic performance fixture; all records are fictional.\n"


_SourceIdentity = tuple[str, str, str]
_RevisionIdentity = tuple[str, int, str]
_EventIdentity = tuple[str, str, int, str, str, str]


@dataclass(frozen=True, slots=True)
class _ExportExpectations:
    sources: tuple[_SourceIdentity, ...]
    revisions: tuple[_RevisionIdentity, ...]
    audit_events: tuple[_EventIdentity, ...]


def _seed_vault(root: Path, record_count: int) -> tuple[EvidenceVault, _ExportExpectations]:
    create_instance_service(root).initialize(actor="local_user")
    vault = EvidenceVault(root)
    vault.record_consent(accepted=True)
    source = vault.import_document(
        display_name="synthetic-performance.txt",
        media_type="text/plain",
        content=_SYNTHETIC_SOURCE,
    )
    metadata = EvidenceContractMetadata(origin_ownership="personal")
    revisions: list[_RevisionIdentity] = []
    event_identities: tuple[_EventIdentity, ...]
    with vault.transaction():
        connection = vault._transaction_connection.get()
        assert connection is not None
        for index in range(record_count):
            evidence_id = f"synthetic-performance-{index:05d}"
            payload = vault._revision_payload(
                kind="responsibility",
                statement=f"Synthetic performance responsibility {index}.",
                details={
                    "responsibility": f"Own synthetic responsibility {index}",
                    "decision_right": "Synthetic decision right",
                },
                source=source,
                source_start_line=1,
                source_end_line=1,
                ownership="personal",
                confidence="exact",
                approximation="exact",
                publication_boundary="application_and_interview",
                metadata=metadata,
                source_excerpt=("Synthetic performance fixture; all records are fictional.",),
            )
            vault._insert_revision(
                connection,
                evidence_id,
                1,
                payload,
                "2026-01-01T00:00:00+00:00",
                None,
            )
            revisions.append((evidence_id, 1, str(payload["content_hash"])))
            vault._insert_event(
                connection,
                evidence_id=evidence_id,
                revision=1,
                action="proposed",
                actor="synthetic_fixture",
                content_hash=str(payload["content_hash"]),
            )
        event_identities = tuple(
            (
                str(row["event_id"]),
                str(row["evidence_id"]),
                int(row["revision"]),
                str(row["action"]),
                str(row["actor"]),
                str(row["content_hash"]),
            )
            for row in connection.execute(
                "SELECT event_id, evidence_id, revision, action, actor, content_hash "
                "FROM evidence_events ORDER BY sequence"
            )
        )
    if source.extracted_hash is None:
        raise AssertionError("Synthetic source must have an extracted-content identity")
    expectations = _ExportExpectations(
        sources=((source.source_id, source.source_hash, source.extracted_hash),),
        revisions=tuple(revisions),
        audit_events=event_identities,
    )
    return vault, expectations


def _is_application_select(statement: str) -> bool:
    normalized = statement.lstrip().upper()
    return normalized.startswith("SELECT") and "SQLITE_MASTER" not in normalized


@contextmanager
def _trace_queries() -> Iterator[list[str]]:
    original_connect = sqlite3.connect
    statements: list[str] = []

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    sqlite3.connect = connect
    try:
        yield statements
    finally:
        sqlite3.connect = original_connect


def _measure(vault: EvidenceVault, operation: str, record_count: int) -> dict[str, object]:
    started = time.perf_counter()
    with _trace_queries() as statements:
        result = getattr(vault, operation)()
    query_count = sum(_is_application_select(statement) for statement in statements)
    budget = QUERY_BUDGETS[operation]
    result_count = len(result) if isinstance(result, (tuple, list)) else None
    return {
        "records": record_count,
        "operation": operation,
        "query_count": query_count,
        "query_budget": budget,
        "within_budget": query_count <= budget,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "result_count": result_count,
    }


def _expected_export_record_counts(record_count: int) -> dict[str, int]:
    return {
        "consent_receipts": 1,
        "sources": 1,
        "evidence_revisions": record_count,
        "evidence_audit_events": record_count,
        "role_preferences": 0,
        "opportunities": 0,
        "opportunity_decisions": 0,
        "offer_comparisons": 0,
        "confirmed_outcome_events": 0,
        "application_versions": 0,
        "interview_versions": 0,
        "artifact_reviews": 0,
        "local_metric_sets": 0,
        "planned_tasks": 0,
        "planned_task_revisions": 0,
        "content_documents": 0,
        "content_document_versions": 0,
        "content_document_reviews": 0,
    }


def _identity_tuple_matches(expected: tuple[object, ...], exported: tuple[object, ...]) -> bool:
    return (
        len(expected) == len(set(expected))
        and len(exported) == len(set(exported))
        and set(exported) == set(expected)
    )


def _export_identities_match(
    expectations: _ExportExpectations,
    exported_sources: tuple[_SourceIdentity, ...],
    exported_revisions: tuple[_RevisionIdentity, ...],
    exported_events: tuple[_EventIdentity, ...],
) -> bool:
    return (
        _identity_tuple_matches(expectations.sources, exported_sources)
        and _identity_tuple_matches(expectations.revisions, exported_revisions)
        and _identity_tuple_matches(expectations.audit_events, exported_events)
    )


def _measure_export(
    vault: EvidenceVault,
    record_count: int,
    destination: Path,
    expectations: _ExportExpectations,
) -> dict[str, object]:
    started = time.perf_counter()
    with _trace_queries() as statements:
        receipt = create_instance_data_service(vault.instance_root).export_user_data(
            str(destination), offline=True, actor="local_user"
        )
    elapsed_seconds = round(time.perf_counter() - started, 6)
    query_count = sum(_is_application_select(statement) for statement in statements)
    query_budget = EXPORT_QUERY_BUDGET_BASE + EXPORT_QUERY_BUDGET_PER_RECORD * record_count
    size_budget = EXPORT_SIZE_BUDGET_BASE + EXPORT_SIZE_BUDGET_PER_RECORD * record_count
    content = (destination / receipt.relative_file).read_bytes()
    payload = json.loads(content)
    evidence = payload["data"]["evidence_vault"]
    exported_sources = tuple(
        (
            item["metadata"]["source_id"],
            item["metadata"]["source_hash"],
            item["metadata"]["extracted_hash"],
        )
        for item in evidence["sources"]
    )
    exported_revisions = tuple(
        (item["evidence_id"], item["revision"], item["content_hash"])
        for item in evidence["evidence_revisions"]
    )
    exported_events = tuple(
        (
            item["event_id"],
            item["evidence_id"],
            item["revision"],
            item["action"],
            item["actor"],
            item["content_hash"],
        )
        for item in evidence["audit_events"]
    )
    identities_match = _export_identities_match(
        expectations, exported_sources, exported_revisions, exported_events
    )
    exported_counts = {
        "consent_receipts": len(evidence["consent_receipts"]),
        "sources": len(evidence["sources"]),
        "evidence_revisions": len(evidence["evidence_revisions"]),
        "evidence_audit_events": len(evidence["audit_events"]),
    }
    expected_counts = _expected_export_record_counts(record_count)
    manifest_counts = payload["export_manifest"]["record_counts"]
    receipt_counts = dict(receipt.record_counts)
    export_complete = (
        manifest_counts == expected_counts
        and receipt_counts == expected_counts
        and all(exported_counts[key] == expected_counts[key] for key in exported_counts)
        and identities_match
        # The evidence-only fixture owns no task or document histories. Do not trust
        # zero manifest counts if serialized history is missing or nonempty.
        and all(
            payload["data"].get(key) == []
            for key in (
                "planned_task_history",
                "content_document_versions",
                "content_document_reviews",
            )
        )
        and receipt.sha256 == hashlib.sha256(content).hexdigest()
        and receipt.size_bytes == len(content)
    )
    return {
        "records": record_count,
        "operation": "export_user_data",
        "query_count": query_count,
        "query_budget": query_budget,
        "within_budget": query_count <= query_budget,
        "elapsed_seconds": elapsed_seconds,
        "exported_counts": exported_counts,
        "identities_match": identities_match,
        "export_complete": export_complete,
        "size_bytes": len(content),
        "size_budget": size_budget,
        "within_size_budget": len(content) <= size_budget,
    }


def _measurement_passed(measurement: dict[str, object]) -> bool:
    return bool(
        measurement["within_budget"]
        and measurement.get("export_complete", True)
        and measurement.get("within_size_budget", True)
    )


def measure_sizes(sizes: tuple[int, ...]) -> dict[str, object]:
    measurements: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="career-os-evidence-performance-") as temporary_root:
        root = Path(temporary_root)
        for index, size in enumerate(sizes):
            vault, expectations = _seed_vault(root / f"{index}-{size}", size)
            measurements.extend(
                (
                    _measure(vault, "readiness", size),
                    _measure(vault, "audit_events", size),
                    _measure_export(
                        vault,
                        size,
                        root / f"{index}-{size}-export",
                        expectations,
                    ),
                )
            )
    return {
        "fixture": (
            "synthetic initialized Career OS instance with one source and one proposed revision "
            "per record"
        ),
        "query_count_definition": (
            "SELECT statements excluding SQLite schema introspection; elapsed time is informational"
        ),
        "export_query_budget_definition": (
            "2 application SELECTs per evidence record plus a fixed 24-SELECT budget; "
            "the four-SELECT fixed tolerance covers small implementation drift"
        ),
        "export_size_budget_definition": (
            "1,500 serialized bytes per evidence record plus a fixed 2,304-byte budget; "
            "the expanded task/document history inventory adds 256 bytes of fixed allowance; "
            "the observed fixture is approximately 1,414 bytes per record"
        ),
        "measurements": measurements,
        "all_budgets_passed": all(_measurement_passed(item) for item in measurements),
    }


def _positive_size(value: str) -> int:
    size = int(value)
    if size < 1:
        raise argparse.ArgumentTypeError("sizes must be positive integers")
    return size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=_positive_size,
        default=DEFAULT_SIZES,
        help="synthetic record counts to measure (default: 100 1000 10000)",
    )
    args = parser.parse_args(argv)
    report = measure_sizes(tuple(args.sizes))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["all_budgets_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

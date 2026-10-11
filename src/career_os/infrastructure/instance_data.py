"""Credential-filtered export and exact selected-workspace deletion."""

from __future__ import annotations

import base64
import hashlib
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from career_os.application.applications import application_fingerprint
from career_os.application.companion import validate_companion_state
from career_os.application.instance_data import (
    InstanceDataService,
    InstanceDeletePreview,
    InstanceDeleteReceipt,
    UserDataExportReceipt,
    user_data_json,
)
from career_os.application.offers import (
    OfferComparisonRequest,
    OfferService,
    require_offer_result,
)
from career_os.application.opportunities import (
    MAX_ROLE_REQUEST_BYTES,
    SuppliedRoleRequest,
    _identifier,
)
from career_os.application.recovery import RecoveryEntry, RecoveryInventory
from career_os.application.workspaces import WorkspaceRef
from career_os.evidence_vault import EvidenceVault
from career_os.file_transactions import _fsync_directory, _safe_regular_file_path
from career_os.infrastructure import evidence_storage, instances, recovery
from career_os.infrastructure.applications import (
    _ARTIFACT_NAMES as APPLICATION_ARTIFACTS,
)
from career_os.infrastructure.applications import (
    APPLICATION_WORKSPACE_MANIFEST,
    LocalApplicationRepository,
)
from career_os.infrastructure.content_artifacts import MAX_ARTIFACT_BYTES
from career_os.infrastructure.documents import inspect_document_archive
from career_os.infrastructure.evidence import read_private_json
from career_os.infrastructure.interviews import (
    _ARTIFACT_NAMES as INTERVIEW_ARTIFACTS,
)
from career_os.infrastructure.interviews import (
    INTERVIEW_WORKSPACE_MANIFEST,
    LocalInterviewRepository,
)
from career_os.infrastructure.opportunities import (
    MAX_OPPORTUNITY_STATE_BYTES,
    _current_supplied_dossier,
    _load_decision_events,
)
from career_os.infrastructure.outcomes import LocalEventRepository
from career_os.infrastructure.workspaces import (
    MAX_WORKSPACE_REVIEW_BYTES,
    _parse_reviews,
)

EXPORT_FILE = "career-os-user-data.json"
MAX_EXPORT_BYTES = recovery.MAX_TOTAL_BYTES
_INTERNAL_PATHS = {
    ".gitignore",
    instances.LAYOUT_MARKER,
    recovery.DATABASE,
    *instances.LAYOUT,
}
_EXCLUDED_PREFIXES = {
    "configuration_or_credentials": ("config",),
    "inbox_documents": ("inbox",),
    "prior_exports": ("exports",),
    "temporary_files": ("tmp",),
    "logs": ("data/logs", "data/runtime/logs"),
}
_METRIC_FIELDS = {
    "schema_version",
    "model_assistance",
    "remote_telemetry",
    "onboarding_started_at",
    "onboarding_completed_at",
    "first_approved_evidence_at",
    "first_useful_decision_at",
    "evidence_actions",
    "unresolved_high_impact_questions",
    "recommendation",
    "decision",
    "override_reason",
    "privacy_failures",
    "identity_failures",
    "external_authority_failures",
    "operator_minutes",
}
_METRIC_TIMESTAMPS = {
    "onboarding_started_at",
    "onboarding_completed_at",
    "first_approved_evidence_at",
    "first_useful_decision_at",
}
_METRIC_COUNTS = {
    "unresolved_high_impact_questions",
    "privacy_failures",
    "identity_failures",
    "external_authority_failures",
    "operator_minutes",
}


def _entry_map(inventory: RecoveryInventory) -> dict[str, RecoveryEntry]:
    return {entry.path: entry for entry in inventory.entries}


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _validate_lock_metadata(root: Path, relative: str, entry: RecoveryEntry) -> None:
    path = _safe_regular_file_path(root / relative)
    if entry.kind != "file" or not 1 <= entry.size_bytes <= 32:
        raise ValueError("Managed writer lock metadata is invalid")
    with path.open("rb") as stream:
        content = stream.read(33)
    if len(content) != entry.size_bytes or not content.isdigit():
        raise ValueError("Managed writer lock metadata is invalid")


def _require_initialized(root: Path, inventory: RecoveryInventory) -> None:
    expected_hash = hashlib.sha256(str(root).encode()).hexdigest()
    entries = _entry_map(inventory)
    if (
        inventory.source_instance_hash != expected_hash
        or inventory.evidence_storage_version != evidence_storage.EVIDENCE_STORAGE_VERSION
        or any(
            entries.get(name, RecoveryEntry("", "", 0, None)).kind != "directory"
            for name in instances.LAYOUT
        )
        or entries.get(".gitignore") is None
        or entries[".gitignore"].sha256 != hashlib.sha256(instances.PRIVATE_IGNORE).hexdigest()
        or entries.get(instances.LAYOUT_MARKER) is None
        or entries[instances.LAYOUT_MARKER].sha256
        != hashlib.sha256(instances.LAYOUT_MARKER_CONTENT).hexdigest()
    ):
        raise ValueError("Export and deletion require one intact initialized workspace")
    database = _safe_regular_file_path(root / recovery.DATABASE)
    try:
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0)
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("BEGIN")
            identity = evidence_storage.validate_current_storage(connection, root)
    except sqlite3.Error as exc:
        raise OSError("Initialized evidence storage is unavailable") from exc
    if identity != evidence_storage.INSTANCE_LAYOUT_IDENTITY:
        raise ValueError("Initialized workspace identity is invalid")


def _opportunity_export(
    root: Path,
    vault: EvidenceVault,
    entries: dict[str, RecoveryEntry],
) -> tuple[list[dict[str, object]], list[dict[str, object]], set[str]]:
    prefix = "data/runtime/opportunities"
    actual = {path for path in entries if _under(path, prefix)}
    if not actual:
        return [], [], set()
    if entries[prefix].kind != "directory":
        raise ValueError("Opportunity namespace is invalid")
    identifiers = {
        PurePosixPath(path).parts[3] for path in actual if len(PurePosixPath(path).parts) >= 4
    }
    exported: list[dict[str, object]] = []
    preferences: list[dict[str, object]] = []
    expected = {prefix}
    for identifier in sorted(identifiers):
        if _identifier(identifier, "opportunity_id") != identifier:
            raise ValueError("Opportunity path identity is invalid")
        directory = f"{prefix}/{identifier}"
        allowed = {
            directory,
            f"{directory}/supplied-request.json",
            f"{directory}/decision-dossier.json",
        }
        decisions_path = f"{directory}/decisions.json"
        if decisions_path in actual:
            allowed.add(decisions_path)
        if not allowed <= actual or any(
            _under(path, directory) and path not in allowed for path in actual
        ):
            raise ValueError("Opportunity inventory is incomplete or unknown")
        request_payload, _ = read_private_json(
            root,
            root / directory / "supplied-request.json",
            label="Supplied opportunity request",
            maximum_bytes=MAX_OPPORTUNITY_STATE_BYTES,
        )
        request = SuppliedRoleRequest.from_dict(request_payload)
        if request.opportunity_id != identifier:
            raise ValueError("Opportunity request belongs to a different path")
        _, dossier = _current_supplied_dossier(root, identifier, vault)
        _, decisions = _load_decision_events(root, identifier)
        exported.append(
            {
                "opportunity_id": identifier,
                "request": request_payload,
                "dossier": dossier,
                "decisions": decisions,
            }
        )
        preferences.append(
            {
                "opportunity_id": identifier,
                "constraints": request.constraints.to_dict(),
            }
        )
        expected.update(allowed)
    if actual != expected:
        raise ValueError("Opportunity namespace contains unsupported state")
    return exported, preferences, expected


def _archived_json_file(
    *,
    kind: str,
    relative_path: str,
    content: bytes,
    entries: dict[str, RecoveryEntry],
) -> dict[str, object]:
    digest = hashlib.sha256(content).hexdigest()
    entry = entries.get(relative_path)
    if (
        entry is None
        or entry.kind != "file"
        or entry.size_bytes != len(content)
        or entry.sha256 != digest
    ):
        raise ValueError("Opportunity state changed during archival export")
    return {
        "kind": kind,
        "relative_path": relative_path,
        "transfer_encoding": "base64",
        "original_encoding": "utf-8",
        "size_bytes": len(content),
        "sha256": digest,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _historical_opportunity_archive(
    root: Path,
    entries: dict[str, RecoveryEntry],
) -> tuple[dict[str, object], set[str]]:
    prefix = "data/runtime/opportunities"
    actual = {path for path in entries if _under(path, prefix)}
    envelope: dict[str, object] = {
        "schema_version": 1,
        "historical_integrity": "unverified",
        "current_readiness": "not_evaluated",
        "current_decision_eligible": False,
        "provenance_limitation": "original_evaluation_inputs_not_retained",
        "entries": [],
    }
    if not actual:
        return envelope, set()
    if entries[prefix].kind != "directory":
        raise ValueError("Opportunity namespace is invalid")
    identifiers = {
        PurePosixPath(path).parts[3] for path in actual if len(PurePosixPath(path).parts) >= 4
    }
    archived: list[dict[str, object]] = []
    expected = {prefix}
    for identifier in sorted(identifiers):
        if _identifier(identifier, "opportunity_id") != identifier:
            raise ValueError("Opportunity path identity is invalid")
        directory = f"{prefix}/{identifier}"
        request_relative = f"{directory}/supplied-request.json"
        dossier_relative = f"{directory}/decision-dossier.json"
        allowed = {directory, request_relative, dossier_relative}
        decisions_path = f"{directory}/decisions.json"
        if decisions_path in actual:
            allowed.add(decisions_path)
        if not allowed <= actual or any(
            _under(path, directory) and path not in allowed for path in actual
        ):
            raise ValueError("Opportunity inventory is incomplete or unknown")
        request_payload, request_content = read_private_json(
            root,
            root / request_relative,
            label="Archived supplied opportunity request",
            maximum_bytes=MAX_ROLE_REQUEST_BYTES,
        )
        request = SuppliedRoleRequest.from_dict(request_payload)
        if request.opportunity_id != identifier:
            raise ValueError("Opportunity request belongs to a different path")
        dossier_payload, dossier_content = read_private_json(
            root,
            root / dossier_relative,
            label="Archived supplied opportunity dossier",
            maximum_bytes=MAX_OPPORTUNITY_STATE_BYTES,
        )
        if not isinstance(dossier_payload, dict):
            raise ValueError("Archived supplied opportunity dossier must be a JSON object")
        _, decisions = _load_decision_events(root, identifier)
        archived.append(
            {
                "opportunity_id": identifier,
                "captured_files": [
                    _archived_json_file(
                        kind="supplied_request",
                        relative_path=request_relative,
                        content=request_content,
                        entries=entries,
                    ),
                    _archived_json_file(
                        kind="decision_dossier",
                        relative_path=dossier_relative,
                        content=dossier_content,
                        entries=entries,
                    ),
                ],
                "decisions": decisions,
            }
        )
        expected.update(allowed)
    if actual != expected:
        raise ValueError("Opportunity namespace contains unsupported state")
    envelope["entries"] = archived
    return envelope, expected


def _offer_export(
    root: Path, entries: dict[str, RecoveryEntry]
) -> tuple[list[dict[str, object]], set[str]]:
    prefix = "data/runtime/offer-comparisons"
    actual = {path for path in entries if _under(path, prefix)}
    if not actual:
        return [], set()
    if entries[prefix].kind != "directory":
        raise ValueError("Offer namespace is invalid")
    expected = {prefix}
    exported: list[dict[str, object]] = []
    data_files = sorted(
        path
        for path in actual
        if entries[path].kind == "file" and not PurePosixPath(path).name.endswith(".lock")
    )
    for relative in data_files:
        parts = PurePosixPath(relative).parts
        if len(parts) != 5 or parts[:3] != ("data", "runtime", "offer-comparisons"):
            raise ValueError("Offer comparison path is invalid")
        identifier, filename = parts[3], parts[4]
        if not filename.endswith(".json"):
            raise ValueError("Offer comparison file is invalid")
        fingerprint = filename.removesuffix(".json")
        application_fingerprint(fingerprint)
        payload, _ = read_private_json(
            root,
            root / relative,
            label="Offer comparison",
            maximum_bytes=128 * 1024,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("request"), dict):
            raise ValueError("Offer comparison payload is invalid")
        request = OfferComparisonRequest.from_dict(payload["request"])
        validated = require_offer_result(OfferService().compare(request)).to_dict()
        if (
            request.comparison_id != identifier
            or validated != payload
            or payload.get("confirmation_fingerprint") != fingerprint
        ):
            raise ValueError("Offer comparison identity or content is invalid")
        exported.append(payload)
        expected.update({f"{prefix}/{identifier}", relative})
        lock = f"{prefix}/{identifier}/.{filename}.lock"
        if lock in actual:
            _validate_lock_metadata(root, lock, entries[lock])
            expected.add(lock)
    if actual != expected:
        raise ValueError("Offer namespace contains empty or unsupported state")
    return exported, expected


def _workspace_export(
    root: Path,
    vault: EvidenceVault,
    entries: dict[str, RecoveryEntry],
) -> tuple[list[dict[str, object]], list[dict[str, object]], set[str]]:
    actual = {path for path in entries if _under(path, "applications")}
    expected = {"applications"}
    if actual == expected:
        versions: list[dict[str, object]] = []
    else:
        application_repository = LocalApplicationRepository(root, vault=vault)
        interview_repository = LocalInterviewRepository(root, vault=vault)
        manifests = sorted(
            path
            for path in actual
            if PurePosixPath(path).name
            in {APPLICATION_WORKSPACE_MANIFEST, INTERVIEW_WORKSPACE_MANIFEST}
        )
        versions = []
        for manifest_path in manifests:
            parts = PurePosixPath(manifest_path).parts
            if len(parts) == 4 and parts[3] == APPLICATION_WORKSPACE_MANIFEST:
                ref = WorkspaceRef("application", parts[1], parts[2])
                target = application_repository._target(ref.opportunity_id, ref.fingerprint)
                plan = application_repository._read(target).to_dict()
                names = APPLICATION_ARTIFACTS
            elif (
                len(parts) == 6
                and parts[2] == "interview-content"
                and parts[5] == INTERVIEW_WORKSPACE_MANIFEST
            ):
                ref = WorkspaceRef("interview", parts[1], parts[4], parts[3])
                target = interview_repository._target(
                    ref.opportunity_id, ref.round_id, ref.fingerprint
                )
                plan = interview_repository._read(target).to_dict()
                names = INTERVIEW_ARTIFACTS
            else:
                raise ValueError("Prepared workspace manifest path is invalid")
            manifest, _ = read_private_json(
                root,
                root / manifest_path,
                label="Prepared workspace manifest",
                maximum_bytes=MAX_WORKSPACE_REVIEW_BYTES,
            )
            documents: dict[str, str] = {}
            for name in sorted(names):
                path = _safe_regular_file_path(target / name)
                with path.open("rb") as stream:
                    content = stream.read(MAX_ARTIFACT_BYTES + 1)
                if (
                    not 1 <= len(content) <= MAX_ARTIFACT_BYTES
                    or hashlib.sha256(content).hexdigest() != manifest["artifacts"][name]["sha256"]
                ):
                    raise ValueError("Prepared workspace changed during export")
                documents[name] = content.decode("utf-8")
            versions.append(
                {
                    "workspace": ref.to_dict(),
                    "relative_path": target.relative_to(root).as_posix(),
                    "plan": plan,
                    "manifest": manifest,
                    "documents": documents,
                }
            )
            target_relative = target.relative_to(root).as_posix()
            expected.update(
                {f"{target_relative}/{name}" for name in names} | {manifest_path, target_relative}
            )
            parent = PurePosixPath(target_relative)
            while len(parent.parts) > 1:
                parent = parent.parent
                expected.add(parent.as_posix())
        if actual != expected:
            raise ValueError("Prepared workspace inventory is incomplete or unknown")

    review_path = "data/runtime/artifact-reviews.jsonl"
    review_lock = "data/runtime/.artifact-reviews.jsonl.lock"
    reviews: list[dict[str, object]] = []
    review_paths: set[str] = set()
    if review_path in entries:
        path = _safe_regular_file_path(root / review_path)
        with path.open("rb") as stream:
            content = stream.read(MAX_WORKSPACE_REVIEW_BYTES + 1)
        reviews = [record.to_dict() for record in _parse_reviews(content)]
        review_paths.add(review_path)
        if review_lock in entries:
            _validate_lock_metadata(root, review_lock, entries[review_lock])
            review_paths.add(review_lock)
    return versions, reviews, expected | review_paths


def _local_metrics(root: Path, entries: dict[str, RecoveryEntry]) -> tuple[object, set[str]]:
    relative = "data/participant-flow.json"
    if relative not in entries:
        return None, set()
    payload, _ = read_private_json(
        root,
        root / relative,
        label="Local participant metrics",
        maximum_bytes=2 * 1024 * 1024,
    )
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Local participant metrics state is invalid")
    metrics = payload.get("metrics")
    if (
        not isinstance(metrics, dict)
        or set(metrics) != _METRIC_FIELDS
        or metrics.get("schema_version") != 1
        or metrics.get("model_assistance") != "disabled"
        or metrics.get("remote_telemetry") is not False
    ):
        raise ValueError("Local participant metrics are invalid")
    actions = metrics.get("evidence_actions")
    if (
        not isinstance(actions, dict)
        or set(actions) != {"approved", "edited", "rejected", "deferred"}
        or any(
            type(value) is not int or not 0 <= value <= 1_000_000_000 for value in actions.values()
        )
        or any(
            type(metrics[field]) is not int or not 0 <= metrics[field] <= 1_000_000_000
            for field in _METRIC_COUNTS
        )
    ):
        raise ValueError("Local participant metric counters are invalid")
    for field in _METRIC_TIMESTAMPS:
        value = metrics[field]
        if value is None:
            continue
        if not isinstance(value, str) or not value or len(value) > 100:
            raise ValueError("Local participant metric time is invalid")
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Local participant metric time is invalid") from exc
        if instant.tzinfo is None:
            raise ValueError("Local participant metric time is invalid")
    for field in ("recommendation", "decision"):
        if metrics[field] is not None and metrics[field] not in {
            "pursue",
            "conditional",
            "hold",
            "skip",
        }:
            raise ValueError("Local participant metric decision is invalid")
    reason = metrics["override_reason"]
    if reason is not None and (
        not isinstance(reason, str) or len(reason) > 2_000 or "\x00" in reason
    ):
        raise ValueError("Local participant metric reason is invalid")
    encoded = user_data_json(metrics)
    if b"\x00" in encoded or len(encoded) > 64 * 1024:
        raise ValueError("Local participant metrics exceed their export bound")
    return metrics, {relative}


def _excluded_inventory(
    inventory: RecoveryInventory, accounted: set[str]
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for path, entry in _entry_map(inventory).items():
        if path in accounted:
            continue
        category = next(
            (
                name
                for name, prefixes in _EXCLUDED_PREFIXES.items()
                if any(_under(path, prefix) for prefix in prefixes)
            ),
            None,
        )
        if category is None:
            raise ValueError("Workspace contains unsupported managed state")
        counts = result.setdefault(category, {"entries": 0, "size_bytes": 0})
        counts["entries"] += 1
        counts["size_bytes"] += entry.size_bytes
    return result


def _unlink_verified(root: Path, entry: RecoveryEntry) -> None:
    if recovery._file_entry(root / entry.path, entry.path) != entry:
        raise ValueError("Workspace changed during deletion")
    (root / entry.path).unlink()


def _delete_directory(path: Path) -> None:
    metadata = path.stat(follow_symlinks=False)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or bool(getattr(metadata, "st_file_attributes", 0) & reparse)
    ):
        raise ValueError("Workspace changed during deletion")
    _fsync_directory(path)
    path.rmdir()


class LocalInstanceDataRepository:
    def __init__(self, instance_root: str | Path) -> None:
        self.root = recovery._root(instance_root)

    def _inventory(self) -> RecoveryInventory:
        inventory = recovery.LocalRecoveryRepository(self.root).inventory()
        _require_initialized(self.root, inventory)
        return inventory

    def export_user_data(
        self, destination: str, *, historical_archive: bool = False
    ) -> UserDataExportReceipt:
        target = recovery._new_target(destination, self.root)
        if any((parent / instances.LAYOUT_MARKER).exists() for parent in target.parents):
            raise ValueError("Export destination must stay outside every Rolevane workspace")
        before = self._inventory()
        entries = _entry_map(before)
        vault = EvidenceVault(self.root)
        with vault.read_transaction():
            evidence = vault.export_user_owned_data()
            archive: dict[str, object] | None = None
            if historical_archive:
                archive, opportunity_paths = _historical_opportunity_archive(self.root, entries)
                opportunities: list[dict[str, object]] = []
                preferences: list[dict[str, object]] = []
            else:
                opportunities, preferences, opportunity_paths = _opportunity_export(
                    self.root, vault, entries
                )
            offers, offer_paths = _offer_export(self.root, entries)
            versions, reviews, workspace_paths = _workspace_export(self.root, vault, entries)
            events = [event.to_dict() for event in LocalEventRepository(self.root).list()]
            event_path = "data/runtime/outcome-learning-v2.jsonl"
            event_paths = {event_path} if event_path in entries else set()
            event_lock = "data/runtime/.outcome-learning-v2.jsonl.lock"
            if event_lock in entries:
                if event_path not in entries:
                    raise ValueError("Outcome lock metadata is invalid")
                _validate_lock_metadata(self.root, event_lock, entries[event_lock])
                event_paths.add(event_lock)
            metrics, metric_paths = _local_metrics(self.root, entries)
            task_history = recovery._task_history(self.root, before.source_instance_hash)
            documents = inspect_document_archive(self.root)
            document_paths = set(documents.managed_paths)
            for relative in document_paths:
                entry = entries.get(relative)
                if entry is None:
                    raise ValueError("Document inventory changed during export")
            document_versions = [
                {
                    "ref": version.ref.to_dict(),
                    "manifest": version.manifest,
                    "artifacts": {
                        format: {
                            **version.manifest["artifacts"][format],
                            "transfer_encoding": "base64",
                            "content_base64": base64.b64encode(content).decode("ascii"),
                        }
                        for format, content in version.artifacts.items()
                    },
                }
                for version in documents.versions
            ]
            task_paths = {recovery.TASK_LEDGER} if recovery.TASK_LEDGER in entries else set()
            task_lock = "data/runtime/.planned-tasks.json.lock"
            if task_lock in entries:
                _validate_lock_metadata(self.root, task_lock, entries[task_lock])
                task_paths.add(task_lock)
            companion_raw = vault.companion_metadata()
            companion = (
                validate_companion_state(companion_raw, before.source_instance_hash)
                if companion_raw is not None
                else None
            )
        accounted = set(_INTERNAL_PATHS)
        accounted.update(
            opportunity_paths
            | offer_paths
            | workspace_paths
            | event_paths
            | metric_paths
            | task_paths
            | document_paths
        )
        excluded = _excluded_inventory(before, accounted)
        application_count = sum(item["workspace"]["kind"] == "application" for item in versions)
        interview_count = len(versions) - application_count
        record_counts = {
            "consent_receipts": len(evidence["consent_receipts"]),
            "sources": len(evidence["sources"]),
            "evidence_revisions": len(evidence["evidence_revisions"]),
            "evidence_audit_events": len(evidence["audit_events"]),
            "role_preferences": len(preferences),
            "opportunities": len(opportunities),
            "opportunity_decisions": sum(len(item["decisions"]) for item in opportunities),
            "offer_comparisons": len(offers),
            "confirmed_outcome_events": len(events),
            "application_versions": application_count,
            "interview_versions": interview_count,
            "artifact_reviews": len(reviews),
            "local_metric_sets": int(metrics is not None),
            "planned_tasks": len({record.task_id for record in task_history}),
            "planned_task_revisions": len(task_history),
            "content_documents": len({version.ref.document_id for version in documents.versions}),
            "content_document_versions": len(documents.versions),
            "content_document_reviews": len(documents.reviews),
        }
        if archive is not None:
            archived_entries = archive["entries"]
            if not isinstance(archived_entries, list):
                raise AssertionError("Historical archive entries must be a list")
            record_counts.update(
                {
                    "historical_opportunity_archives": len(archived_entries),
                    "historical_opportunity_archive_files": sum(
                        len(item["captured_files"]) for item in archived_entries
                    ),
                    "historical_opportunity_decisions": sum(
                        len(item["decisions"]) for item in archived_entries
                    ),
                }
            )
        exported_data = {
            "evidence_vault": evidence,
            "role_preferences": preferences,
            "opportunities": opportunities,
            "offer_comparisons": offers,
            "confirmed_outcomes": events,
            "prepared_workspaces": versions,
            "artifact_reviews": reviews,
            "local_metrics": metrics,
            "planned_task_history": [record.to_dict() for record in task_history],
            "content_document_versions": document_versions,
            "content_document_reviews": list(documents.reviews),
        }
        if companion is not None:
            # Inspectable user content, never capabilities, grants, keys or sessions.
            exported_data["companion_pending"] = [
                {
                    "pending_id": identity,
                    **{
                        key: record[key]
                        for key in (
                            "operation",
                            "digest",
                            "request",
                            "basis",
                            "created_at",
                            "expires_at",
                            "state",
                            "receipt",
                            "restored",
                        )
                    },
                }
                for identity, record in sorted(companion["pending"].items())
            ]
            record_counts["companion_pending"] = len(companion["pending"])
            if companion["version"] == 2:
                exported_data["companion_archive"] = [
                    {
                        "pending_id": identity,
                        "retired_at": record["retirement"]["at"],
                        **{
                            key: record[key]
                            for key in (
                                "operation",
                                "digest",
                                "request",
                                "basis",
                                "created_at",
                                "expires_at",
                                "state",
                                "receipt",
                                "restored",
                            )
                        },
                    }
                    for identity, record in sorted(companion["archive"].items())
                ]
                record_counts["companion_archive"] = len(companion["archive"])
        export_manifest = {
            "record_counts": record_counts,
            "excluded_workspace_categories": excluded,
            "excluded_content": [
                "dedicated credentials and configuration",
                "unimported inbox documents",
                "prior exports, temporary files and logs",
                "database layout, instance binding and internal security metadata",
            ],
            "free_text_secret_scrubbing_claimed": False,
            "credentials_included": False,
            "internal_security_metadata_included": False,
            "remote_telemetry": False,
            "external_actions": False,
            "portable": True,
            "inspectable": True,
            "restorable": False,
        }
        if archive is not None:
            exported_data["historical_opportunity_archive"] = archive
            export_manifest.update(
                {
                    "export_mode": "historical_opportunity_archive",
                    "historical_archive_scope": "supplied_request_and_dossier_bytes",
                    "non_opportunity_record_validation": "unchanged_strict",
                }
            )
        payload = {
            "schema_version": 1,
            "product": "Rolevane",
            "career_os_version": instances.__version__,
            "exported_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "evidence_storage_version": before.evidence_storage_version,
            "data": exported_data,
            "export_manifest": export_manifest,
        }
        if self._inventory() != before:
            raise ValueError("Workspace changed during export; stop every writer and retry")
        content = user_data_json(payload)
        if not 1 <= len(content) <= MAX_EXPORT_BYTES:
            raise ValueError("User-data export exceeds the supported byte limit")
        recovery._space(target, len(content))
        recovery._create_marked_target(target, b"Incomplete user-data export; do not use.\n")
        recovery._write_new(target / EXPORT_FILE, content)
        if self._inventory() != before:
            raise ValueError("Workspace changed during export; incomplete output was retained")
        (target / recovery.INCOMPLETE).unlink()
        _fsync_directory(target)
        _fsync_directory(target.parent)
        return UserDataExportReceipt(
            str(target),
            EXPORT_FILE,
            hashlib.sha256(content).hexdigest(),
            len(content),
            tuple(sorted(record_counts.items())),
            historical_opportunity_archive=historical_archive,
        )

    def preview_delete(self) -> InstanceDeletePreview:
        return InstanceDeletePreview(self._inventory())

    def delete(self, confirmation_fingerprint: str) -> InstanceDeleteReceipt:
        preview = self.preview_delete()
        if confirmation_fingerprint != preview.confirmation_fingerprint:
            raise PermissionError(
                "Deletion preview changed; confirm the exact initialized workspace inventory"
            )
        inventory = preview.inventory
        database_entry = next(
            (entry for entry in inventory.entries if entry.path == recovery.DATABASE), None
        )
        if database_entry is None:
            raise ValueError("Initialized evidence database is missing")
        with recovery._database_guard(self.root, inventory.source_instance_hash) as version:
            if (
                version != inventory.evidence_storage_version
                or recovery._scan(self.root) != inventory.entries
            ):
                raise ValueError("Workspace changed before deletion")
            for entry in sorted(
                (
                    item
                    for item in inventory.entries
                    if item.kind == "file" and item.path != recovery.DATABASE
                ),
                key=lambda item: (item.path.count("/"), item.path),
                reverse=True,
            ):
                _unlink_verified(self.root, entry)
        _unlink_verified(self.root, database_entry)
        for entry in sorted(
            (item for item in inventory.entries if item.kind == "directory"),
            key=lambda item: (item.path.count("/"), item.path),
            reverse=True,
        ):
            _delete_directory(self.root / entry.path)
        _delete_directory(self.root)
        _fsync_directory(self.root.parent)
        return InstanceDeleteReceipt(
            len(inventory.entries), inventory.size_bytes, not self.root.exists()
        )


def create_instance_data_service(instance_root: str | Path) -> InstanceDataService:
    return InstanceDataService(LocalInstanceDataRepository(instance_root))

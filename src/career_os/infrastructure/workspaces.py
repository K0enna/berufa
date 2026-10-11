"""Bounded prepared-version discovery and one exact artifact-review append owner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from career_os.application.opportunities import _text
from career_os.application.workspaces import (
    MAX_WORKSPACE_REVIEW_BYTES,
    MAX_WORKSPACE_REVIEWS,
    ArtifactReview,
    WorkspaceDiagnostic,
    WorkspaceInspection,
    WorkspaceInventory,
    WorkspaceRef,
    WorkspaceReviewProposal,
    WorkspaceReviewReceipt,
    WorkspaceReviewRequest,
    WorkspaceService,
    WorkspaceSummary,
    build_review_proposal,
)
from career_os.evidence_vault import EvidenceVault
from career_os.file_transactions import (
    FileConflictError,
    _safe_regular_file_path,
    locked_compare_and_swap,
)
from career_os.infrastructure.applications import _ARTIFACT_NAMES as APPLICATION_ARTIFACTS
from career_os.infrastructure.applications import (
    APPLICATION_WORKSPACE_MANIFEST,
    LocalApplicationRepository,
)
from career_os.infrastructure.content_artifacts import MAX_ARTIFACT_BYTES, _directory
from career_os.infrastructure.evidence import read_private_json
from career_os.infrastructure.interviews import _ARTIFACT_NAMES as INTERVIEW_ARTIFACTS
from career_os.infrastructure.interviews import (
    INTERVIEW_WORKSPACE_MANIFEST,
    LocalInterviewRepository,
)
from career_os.infrastructure.outcomes import LocalEventRepository
from career_os.outcome_records import _timestamp_instant

MAX_WORKSPACE_VERSIONS = 500
MAX_WORKSPACE_ENTRIES = 2000


def _parse_reviews(content: bytes) -> tuple[ArtifactReview, ...]:
    if len(content) > MAX_WORKSPACE_REVIEW_BYTES:
        raise ValueError("Artifact review history exceeds its size bound")
    records: list[ArtifactReview] = []
    latest: dict[str, ArtifactReview] = {}
    seen: set[str] = set()
    try:
        for line in content.decode("utf-8").splitlines():
            if not line.strip():
                continue
            if len(records) >= MAX_WORKSPACE_REVIEWS:
                raise ValueError("Artifact review history exceeds its record bound")
            record = ArtifactReview.from_dict(json.loads(line))
            key = record.request.workspace.key
            previous = latest.get(key)
            if (
                record.previous_review_id != (previous.review_id if previous else None)
                or record.review_id in seen
            ):
                raise ValueError("Artifact review history is duplicated or disconnected")
            records.append(record)
            latest[key] = record
            seen.add(record.review_id)
    except (UnicodeError, TypeError, RecursionError, OverflowError) as exc:
        raise ValueError("Artifact review history is invalid") from exc
    return tuple(records)


def _latest_reviews(records: tuple[ArtifactReview, ...]) -> dict[str, ArtifactReview]:
    return {item.request.workspace.key: item for item in records}


class LocalWorkspaceRepository:
    def __init__(self, instance_root: str | Path, *, vault: EvidenceVault | None = None) -> None:
        lexical = Path(instance_root).expanduser().absolute()
        _directory(lexical)
        self._vault = vault if vault is not None else EvidenceVault(lexical)
        if self._vault.instance_root != lexical.resolve(strict=False):
            raise ValueError("Workspace repository vault belongs to a different instance")
        self.root = self._vault.instance_root
        self._applications = LocalApplicationRepository(self.root, vault=self._vault)
        self._interviews = LocalInterviewRepository(self.root, vault=self._vault)
        self.review_path = self.root / "data/runtime/artifact-reviews.jsonl"

    def has_consent(self) -> bool:
        return self._vault.has_consent()

    def _reviews(self) -> tuple[ArtifactReview, ...]:
        path = _safe_regular_file_path(self.review_path)
        try:
            with path.open("rb") as stream:
                content = stream.read(MAX_WORKSPACE_REVIEW_BYTES + 1)
        except FileNotFoundError:
            content = b""
        return _parse_reviews(content)

    def _snapshot(
        self,
        ref: WorkspaceRef,
        latest: dict[str, ArtifactReview],
        *,
        check_current: bool,
        documents: bool = False,
    ) -> WorkspaceInspection:
        if not self.has_consent():
            raise PermissionError("Local-processing consent required")
        if ref.kind == "application":
            repository = self._applications
            target = repository._target(ref.opportunity_id, ref.fingerprint)
            manifest_name, names = APPLICATION_WORKSPACE_MANIFEST, APPLICATION_ARTIFACTS
        else:
            repository = self._interviews
            target = repository._target(ref.opportunity_id, ref.round_id, ref.fingerprint)
            manifest_name, names = INTERVIEW_WORKSPACE_MANIFEST, INTERVIEW_ARTIFACTS
        plan = repository._read(target).to_dict()
        manifest, manifest_bytes = read_private_json(
            self.root,
            _safe_regular_file_path(target / manifest_name),
            label="Workspace manifest",
            maximum_bytes=MAX_ARTIFACT_BYTES,
        )
        current = None
        if check_current:
            validation = (
                repository.validate(ref.opportunity_id, ref.fingerprint)
                if ref.kind == "application"
                else repository.validate(ref.opportunity_id, ref.round_id, ref.fingerprint)
            )
            current = validation.current
        opportunity = plan.get("opportunity")
        if (
            not isinstance(opportunity, dict)
            or opportunity.get("opportunity_id") != ref.opportunity_id
        ):
            raise ValueError("Workspace opportunity display identity is invalid")
        company = _text(opportunity.get("company"), "company")
        role = _text(opportunity.get("title"), "title")
        summary = WorkspaceSummary(
            ref,
            target.relative_to(self.root).as_posix(),
            manifest["prepared_at"],
            company,
            role,
            hashlib.sha256(manifest_bytes).hexdigest(),
            True,
            current,
            round_type=plan["request"].get("round_type"),
            review=latest.get(ref.key),
        )
        content: dict[str, str] = {}
        if documents:
            for name in sorted(names):
                with _safe_regular_file_path(target / name).open("rb") as stream:
                    raw = stream.read(MAX_ARTIFACT_BYTES + 1)
                if (
                    not 1 <= len(raw) <= MAX_ARTIFACT_BYTES
                    or hashlib.sha256(raw).hexdigest() != manifest["artifacts"][name]["sha256"]
                ):
                    raise ValueError("Workspace changed during inspection")
                content[name] = raw.decode("utf-8")
        return WorkspaceInspection(summary, plan, content)

    def inspect(self, workspace: WorkspaceRef) -> WorkspaceInspection:
        with self._vault.read_transaction():
            return self._snapshot(
                workspace, _latest_reviews(self._reviews()), check_current=True, documents=True
            )

    def list(self) -> WorkspaceInventory:
        with self._vault.read_transaction():
            if not self.has_consent():
                raise PermissionError("Local-processing consent required")
            latest = _latest_reviews(self._reviews())
            namespace = _directory(self.root / "applications")
            if not namespace.exists():
                return WorkspaceInventory(())
            diagnostics: list[WorkspaceDiagnostic] = []
            versions: list[WorkspaceSummary] = []
            entry_count = 0

            def children(directory: Path) -> list[Path]:
                nonlocal entry_count
                _directory(directory)
                result = []
                for path in directory.iterdir():
                    entry_count += 1
                    if entry_count > MAX_WORKSPACE_ENTRIES:
                        raise ValueError("Workspace layout exceeds the bounded discovery limit")
                    result.append(path)
                return sorted(result)

            def version(path: Path, kind: str, identity: str, round_id: str | None = None) -> None:
                if path.name.startswith(".prepare-"):
                    diagnostics.append(
                        WorkspaceDiagnostic(
                            "uncommitted_stage",
                            "Uncommitted preparation staging exists; it is not an authoritative version.",
                        )
                    )
                    return
                if len(versions) >= MAX_WORKSPACE_VERSIONS:
                    raise ValueError("Workspace version bound exceeded")
                ref = None
                try:
                    ref = WorkspaceRef(kind, identity, path.name, round_id)
                    versions.append(self._snapshot(ref, latest, check_current=False).summary)
                except (ValueError, OSError):
                    if ref is not None:
                        versions.append(
                            WorkspaceSummary(
                                ref,
                                path.relative_to(self.root).as_posix(),
                                None,
                                "Rolevane",
                                "Unreadable prepared content",
                                None,
                                False,
                            )
                        )
                    diagnostics.append(
                        WorkspaceDiagnostic(
                            "invalid_version",
                            "A preparation version has invalid or incomplete identity, inventory or content.",
                            ref,
                        )
                    )

            for opportunity in children(namespace):
                try:
                    # Legacy application storage owns year/slug trees; public IDs start with a letter.
                    if (
                        len(opportunity.name) == 4
                        and opportunity.name.isascii()
                        and opportunity.name.isdecimal()
                    ):
                        _directory(opportunity)
                        continue
                    entries = children(opportunity)
                except (ValueError, OSError):
                    if entry_count > MAX_WORKSPACE_ENTRIES:
                        raise
                    diagnostics.append(
                        WorkspaceDiagnostic(
                            "unknown_layout",
                            "An applications entry is not a readable public workspace directory.",
                        )
                    )
                    continue
                for entry in entries:
                    if entry.name != "interview-content":
                        version(entry, "application", opportunity.name)
                        continue
                    try:
                        rounds = children(entry)
                    except (ValueError, OSError):
                        if entry_count > MAX_WORKSPACE_ENTRIES:
                            raise
                        diagnostics.append(
                            WorkspaceDiagnostic(
                                "unknown_layout",
                                "An interview root is not a readable public directory.",
                            )
                        )
                        continue
                    for round_directory in rounds:
                        try:
                            paths = children(round_directory)
                        except (ValueError, OSError):
                            if entry_count > MAX_WORKSPACE_ENTRIES:
                                raise
                            diagnostics.append(
                                WorkspaceDiagnostic(
                                    "unknown_layout",
                                    "An interview entry is not a readable public round directory.",
                                )
                            )
                            continue
                        for path in paths:
                            version(path, "interview", opportunity.name, round_directory.name)
            groups: dict[tuple[str, str, str | None], list[int]] = {}
            for index, item in enumerate(versions):
                if item.intact:
                    ref = item.workspace
                    groups.setdefault((ref.kind, ref.opportunity_id, ref.round_id), []).append(
                        index
                    )
            for indexes in groups.values():
                newest = max(
                    _timestamp_instant(versions[index].prepared_at, "prepared_at")
                    for index in indexes
                )
                selected = [
                    index
                    for index in indexes
                    if _timestamp_instant(versions[index].prepared_at, "prepared_at") == newest
                ]
                if len(selected) != 1:
                    diagnostics.append(
                        WorkspaceDiagnostic(
                            "ambiguous_latest",
                            "Multiple versions have the same newest preparation time; no active version was chosen.",
                            versions[selected[0]].workspace,
                        )
                    )
                    continue
                index = selected[0]
                ref = versions[index].workspace
                try:
                    item = self._snapshot(ref, latest, check_current=True).summary
                    versions[index] = replace(item, selected=True)
                except (ValueError, OSError):
                    versions[index] = replace(versions[index], intact=False, current=False)
                    diagnostics.append(
                        WorkspaceDiagnostic(
                            "invalid_current",
                            "Current preparation authority is unreadable or invalid.",
                            ref,
                        )
                    )
            return WorkspaceInventory(tuple(versions), tuple(diagnostics))

    def _proposal(
        self, request: WorkspaceReviewRequest, records: tuple[ArtifactReview, ...]
    ) -> WorkspaceReviewProposal:
        inspection = self._snapshot(
            request.workspace, _latest_reviews(records), check_current=True, documents=True
        )
        event = None
        if request.interview_event_id is not None:
            event = next(
                (
                    item
                    for item in LocalEventRepository(self.root).list()
                    if item.event_id == request.interview_event_id
                ),
                None,
            )
        return build_review_proposal(request, inspection, event)

    @staticmethod
    def _confirmed_retry(
        proposal: WorkspaceReviewProposal, fingerprint: str
    ) -> ArtifactReview | None:
        prior = proposal.inspection.summary.review
        if (
            prior
            and prior.confirmation_fingerprint == fingerprint
            and prior.request == proposal.request
            and prior.manifest_sha256 == proposal.inspection.summary.manifest_sha256
            and prior.event_record_sha256
            == (proposal.event.record_sha256 if proposal.event else None)
        ):
            return prior
        return None

    def review(
        self, request: WorkspaceReviewRequest, fingerprint: str | None
    ) -> WorkspaceReviewProposal | WorkspaceReviewReceipt:
        with self._vault.read_transaction():
            proposal = self._proposal(request, self._reviews())
            if fingerprint is None:
                return proposal
            if prior := self._confirmed_retry(proposal, fingerprint):
                return WorkspaceReviewReceipt(prior, False)
            if proposal.confirmation_fingerprint != fingerprint:
                raise PermissionError("Workspace review confirmation is stale")
        receipt = None

        def append(current: bytes) -> bytes:
            nonlocal receipt
            records = _parse_reviews(current)
            current_proposal = self._proposal(request, records)
            if prior := self._confirmed_retry(current_proposal, fingerprint):
                receipt = WorkspaceReviewReceipt(prior, False)
                return current
            if current_proposal.confirmation_fingerprint != fingerprint:
                raise PermissionError("Workspace review confirmation is stale")
            if len(records) >= MAX_WORKSPACE_REVIEWS:
                raise ValueError("Workspace review history exceeds its record bound")
            record = ArtifactReview.create(
                request,
                current_proposal.inspection.summary.manifest_sha256,
                current_proposal.previous_review_id,
                current_proposal.event.record_sha256 if current_proposal.event else None,
                datetime.now(UTC).isoformat(),
            )
            encoded = (json.dumps(record.to_dict(), sort_keys=True) + "\n").encode("utf-8")
            updated = (
                current + (b"\n" if current and not current.endswith(b"\n") else b"") + encoded
            )
            if len(updated) > MAX_WORKSPACE_REVIEW_BYTES:
                raise ValueError("Workspace review history exceeds its size bound")
            receipt = WorkspaceReviewReceipt(record, True)
            return updated

        try:
            with self._vault.transaction():
                locked_compare_and_swap(
                    self.review_path, append, maximum_bytes=MAX_WORKSPACE_REVIEW_BYTES
                )
        except FileConflictError as exc:
            raise OSError("Workspace review storage changed; retry the confirmed request") from exc
        assert receipt is not None
        return receipt


def create_workspace_service(instance_root: str | Path) -> WorkspaceService:
    return WorkspaceService(LocalWorkspaceRepository(instance_root))

"""Bounded, read-only composition of the existing local Focus authorities."""

from __future__ import annotations

import stat
from pathlib import Path

from career_os.application.focus import FocusInputs, FocusInputStatus, FocusService
from career_os.application.opportunities import (
    OpportunityOperationError,
    OpportunityService,
    SuppliedOpportunityState,
    _identifier,
)
from career_os.evidence_vault import EvidenceVault
from career_os.file_transactions import _is_link_or_reparse, _safe_regular_file_path
from career_os.infrastructure.opportunities import LocalOpportunityRepository
from career_os.infrastructure.outcomes import LocalEventRepository
from career_os.infrastructure.tasks import create_task_service
from career_os.infrastructure.workspaces import LocalWorkspaceRepository

MAX_FOCUS_OPPORTUNITIES = 100
_UNSUPPORTED = tuple(
    FocusInputStatus(name, "unsupported", detail)
    for name, detail in (
        (
            "official_roles",
            "The installed Focus interface cannot read verified official-role dossiers.",
        ),
    )
)


def _existing_directory(path: Path) -> bool:
    for component in (*reversed(path.parents), path):
        if _is_link_or_reparse(component):
            raise ValueError("Focus directories must not redirect through links")
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("Focus input must be a directory")
    return True


class LocalFocusRepository:
    def __init__(self, instance_root: str | Path) -> None:
        self.root = Path(instance_root).expanduser().absolute()

    def read(self) -> FocusInputs:
        statuses: list[FocusInputStatus] = []
        try:
            _existing_directory(self.root)
        except (ValueError, OSError):
            return FocusInputs(
                statuses=(
                    FocusInputStatus(
                        "instance", "unavailable", "The local instance cannot be read safely."
                    ),
                    FocusInputStatus(
                        "confirmed_events", "unavailable", "Confirmed event storage is unavailable."
                    ),
                    *_UNSUPPORTED,
                )
            )
        events = ()
        try:
            events = LocalEventRepository(self.root).list()
            statuses.append(
                FocusInputStatus(
                    "confirmed_events",
                    "ready" if events else "empty",
                    "Canonical V2 confirmed event ledger.",
                )
            )
        except (ValueError, OSError) as exc:
            statuses.append(
                FocusInputStatus(
                    "confirmed_events",
                    "invalid" if isinstance(exc, ValueError) else "unavailable",
                    "Confirmed event storage is invalid or unavailable; history is unknown.",
                )
            )

        readiness = None
        reviews = conflicts = timeline = ()
        opportunities: tuple[SuppliedOpportunityState, ...] = ()
        workspaces = None
        tasks = ()
        try:
            tasks = create_task_service(self.root).list().current
            statuses.append(
                FocusInputStatus(
                    "planned_actions", "ready" if tasks else "empty", "User-owned local tasks."
                )
            )
        except PermissionError as exc:
            statuses.append(
                FocusInputStatus(
                    "planned_actions",
                    "unsupported"
                    if str(exc) == "Task storage requires an initialized private instance"
                    else "consent_required",
                    "Local tasks require an initialized instance and local-processing consent.",
                )
            )
        except (ValueError, OSError) as exc:
            try:
                legacy_without_tasks = (
                    not _safe_regular_file_path(self.root / ".career-os-instance.json").exists()
                    and not _safe_regular_file_path(
                        self.root / "data/runtime/planned-tasks.json"
                    ).exists()
                )
            except (ValueError, OSError):
                legacy_without_tasks = False
            statuses.append(
                FocusInputStatus(
                    "planned_actions",
                    "unsupported"
                    if legacy_without_tasks
                    else "invalid"
                    if isinstance(exc, ValueError)
                    else "unavailable",
                    "Local task state is invalid or unavailable; the planned queue is unknown.",
                )
            )
        try:
            database = _safe_regular_file_path(self.root / "data/evidence-vault.db")
            if not database.exists():
                statuses.append(
                    FocusInputStatus(
                        "evidence",
                        "not_initialized",
                        "Initialize the local evidence vault and review consent.",
                    )
                )
            else:
                vault = EvidenceVault(self.root)
                with vault.read_transaction():
                    if not vault.has_consent():
                        statuses.append(
                            FocusInputStatus(
                                "evidence",
                                "consent_required",
                                "Local document-processing consent is required before evidence review.",
                            )
                        )
                    else:
                        readiness = vault.readiness()
                        reviews = vault.review_queue()
                        conflicts = vault.conflicting_evidence_ids()
                        timeline = vault.timeline_issues()
                        statuses.append(
                            FocusInputStatus(
                                "evidence",
                                "ready",
                                "Current evidence revisions, review state and conflicts.",
                            )
                        )
                        opportunities = self._opportunities(vault, statuses)
                        try:
                            workspaces = LocalWorkspaceRepository(self.root, vault=vault).list()
                        except (ValueError, OSError):
                            statuses.append(
                                FocusInputStatus(
                                    "prepared_artifacts",
                                    "invalid",
                                    "Prepared artifact storage or review history is unreadable; readiness is unknown.",
                                )
                            )
                        else:
                            statuses.extend(
                                FocusInputStatus(
                                    f"{kind}_artifacts",
                                    "ready"
                                    if any(v.workspace.kind == kind for v in workspaces.versions)
                                    else "empty",
                                    "Bounded prepared versions and exact local artifact reviews.",
                                )
                                for kind in ("application", "interview")
                            )
        except (ValueError, OSError) as exc:
            readiness = None
            reviews = conflicts = timeline = ()
            statuses.append(
                FocusInputStatus(
                    "evidence",
                    "invalid" if isinstance(exc, ValueError) else "unavailable",
                    "Evidence storage is invalid or unavailable; review state is unknown.",
                )
            )
        if not any(item.name == "supplied_roles" for item in statuses):
            statuses.append(
                FocusInputStatus(
                    "supplied_roles",
                    "unavailable",
                    "Supplied-role decisions require readable, consented evidence; their current basis is unknown.",
                )
            )
        if workspaces is None and not any(item.name == "prepared_artifacts" for item in statuses):
            statuses.append(
                FocusInputStatus(
                    "prepared_artifacts",
                    "unavailable",
                    "Prepared content requires readable, consented evidence; readiness is unknown.",
                )
            )
        return FocusInputs(
            readiness,
            reviews,
            conflicts,
            timeline,
            opportunities,
            events,
            (*statuses, *_UNSUPPORTED),
            workspaces,
            tasks,
        )

    def _opportunities(
        self, vault: EvidenceVault, statuses: list[FocusInputStatus]
    ) -> tuple[SuppliedOpportunityState, ...]:
        try:
            namespace = self.root / "data/runtime/opportunities"
            if not _existing_directory(namespace):
                statuses.append(
                    FocusInputStatus(
                        "supplied_roles", "empty", "No supplied-role directory exists."
                    )
                )
                return ()
            directories: list[Path] = []
            for path in namespace.iterdir():
                directories.append(path)
                if len(directories) > MAX_FOCUS_OPPORTUNITIES:
                    raise ValueError("Focus opportunity bound exceeded")
            service = OpportunityService(
                LocalOpportunityRepository(self.root, vault=vault, read_only_resolution=True)
            )
            result: list[SuppliedOpportunityState] = []
            for index, directory in enumerate(sorted(directories), start=1):
                source = f"supplied_role_record:{index}"
                try:
                    identity = _identifier(directory.name, "opportunity_id")
                    source = f"supplied_role:{identity}"
                    if not _existing_directory(directory):
                        raise ValueError("Opportunity directory disappeared")
                    for name in (
                        "supplied-request.json",
                        "decision-dossier.json",
                        "decisions.json",
                    ):
                        _safe_regular_file_path(directory / name)
                    state = service.resolve_supplied(identity)
                    if isinstance(state, OpportunityOperationError):
                        raise ValueError("Supplied opportunity cannot be resolved")
                    result.append(state)
                except (ValueError, OSError):
                    statuses.append(
                        FocusInputStatus(
                            source,
                            "invalid",
                            "A supplied-role dossier or decision is unreadable, stale or invalid; resolve its basis before acting.",
                        )
                    )
            statuses.append(
                FocusInputStatus(
                    "supplied_roles",
                    "ready" if directories else "empty",
                    "Bounded exact-identity supplied-role evaluation and decision reads.",
                )
            )
            return tuple(result)
        except (ValueError, OSError):
            statuses.append(
                FocusInputStatus(
                    "supplied_roles",
                    "unavailable",
                    "Supplied-role storage is unreadable or exceeds the 100-opportunity limit; no complete queue can be inferred.",
                )
            )
            return ()


def create_focus_service(instance_root: str | Path) -> FocusService:
    return FocusService(LocalFocusRepository(instance_root))

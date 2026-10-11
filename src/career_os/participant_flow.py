"""Deterministic role decision and lifecycle flow for the synthetic participant."""

from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import stat
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from career_os.evidence_vault import EvidenceVault
from career_os.role_packs import (
    EvidenceOutcome,
    Recommendation,
    RequirementImpact,
    RequirementLevel,
    RolePackId,
    RoleSelection,
    bundled_role_pack_registry,
)
from career_os.synthetic_role_pack_fixtures import (
    SYNTHETIC_POSTING,
    SYNTHETIC_POSTING_FIXTURE_VERSION,
    SYNTHETIC_POSTING_PROFILE,
    synthetic_candidate_overlay,
)

UserDecision = Literal["pursue", "conditional", "hold", "skip"]

FLOW_SCHEMA_VERSION = 1
_ROLE_PACK_REGISTRY = bundled_role_pack_registry()
ROLE_PACKS = _ROLE_PACK_REGISTRY.packs
ROLE_PACK_VERSION = ROLE_PACKS["customer_post_sales_leadership"].version
PARTICIPANT_ROLE_PACK_IDS: tuple[RolePackId, ...] = (
    "customer_post_sales_leadership",
    "generic_fallback",
)
POSTING_FIXTURE_VERSION = SYNTHETIC_POSTING_FIXTURE_VERSION
RESET_PHRASE = "DELETE SYNTHETIC INSTANCE"
STATE_RELATIVE_PATH = Path("data") / "participant-flow.json"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Participant timestamps must include a timezone")
    return value.astimezone(UTC).isoformat()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_text(value: object, field: str, *, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ValueError(f"{field} is empty or exceeds its size limit")
    return normalized


def _inside(path: Path, parent: Path) -> bool:
    resolved = path.resolve(strict=False)
    root = parent.resolve(strict=False)
    return resolved == root or root in resolved.parents


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


@dataclass(frozen=True, slots=True)
class RequirementAssessment:
    requirement_id: str
    level: RequirementLevel
    impact: RequirementImpact
    statement: str
    outcome: EvidenceOutcome
    candidate_evidence_ids: tuple[str, ...]
    transferable_evidence_ids: tuple[str, ...]
    posting_evidence_id: str
    rationale: str


@dataclass(frozen=True, slots=True)
class DecisionDossier:
    opportunity_id: str
    posting_hash: str
    source_id: str
    official_source_verified: bool
    role_pack_id: RolePackId
    role_pack_version: str
    role_pack_fingerprint: str
    selection_status: str
    adjacent_pack_ids: tuple[str, ...]
    adjacent_archetype_ids: tuple[str, ...]
    recommendation: Recommendation
    confidence: float
    assessments: tuple[RequirementAssessment, ...]
    supported_claim_ids: tuple[str, ...]
    transferable_evidence_ids: tuple[str, ...]
    genuine_gap_ids: tuple[str, ...]
    unknown_requirement_ids: tuple[str, ...]
    application_ready: bool
    evaluation_fingerprint: str
    explanation: tuple[str, ...]
    external_actions: bool = False
    model_calls: bool = False


@dataclass(frozen=True, slots=True)
class ResetReport:
    removed_records: tuple[str, ...]
    removed_count: int
    instance_removed: bool
    retained_installation_files: tuple[str, ...]


def _default_state(now: datetime) -> dict[str, object]:
    return {
        "schema_version": FLOW_SCHEMA_VERSION,
        "revision": 0,
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "role_selection": None,
        "evaluation": None,
        "decision": None,
        "artifacts": [],
        "lifecycle_events": [],
        "metrics": {
            "schema_version": 1,
            "model_assistance": "disabled",
            "remote_telemetry": False,
            "onboarding_started_at": None,
            "onboarding_completed_at": None,
            "first_approved_evidence_at": None,
            "first_useful_decision_at": None,
            "evidence_actions": {
                "approved": 0,
                "edited": 0,
                "rejected": 0,
                "deferred": 0,
            },
            "unresolved_high_impact_questions": 0,
            "recommendation": None,
            "decision": None,
            "override_reason": None,
            "privacy_failures": 0,
            "identity_failures": 0,
            "external_authority_failures": 0,
            "operator_minutes": 0,
        },
    }


class ParticipantFlow:
    """Persistent deterministic product flow composed over an approved evidence vault."""

    def __init__(
        self,
        instance_root: str | Path,
        evidence: EvidenceVault,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.instance_root = Path(instance_root).expanduser().resolve(strict=False)
        self.evidence = evidence
        if self.evidence.instance_root != self.instance_root:
            raise ValueError("Participant flow and evidence vault belong to different instances")
        self.path = self.instance_root / STATE_RELATIVE_PATH
        self._now = now
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, object]:
        if not self.path.exists():
            return _default_state(self._now())
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != FLOW_SCHEMA_VERSION:
            raise ValueError("Participant flow state has an unsupported schema")
        selection = payload.get("role_selection")
        if isinstance(selection, dict) and selection.get("evidence_fingerprints") != dict(
            self.evidence.context_fingerprints()
        ):
            # Invalidate the current projection, never delete retained artifact files.
            payload = {
                **payload,
                "role_selection": None,
                "evaluation": None,
                "decision": None,
                "artifacts": [],
                "lifecycle_events": [],
            }
        elif isinstance(payload.get("evaluation"), dict):
            evaluation = cast(dict[str, object], payload["evaluation"])
            if (
                evaluation.get("opportunity_id") != SYNTHETIC_POSTING.job_key
                or evaluation.get("posting_hash") != SYNTHETIC_POSTING.content_hash
            ):
                payload = {
                    **payload,
                    "evaluation": None,
                    "decision": None,
                    "artifacts": [],
                    "lifecycle_events": [],
                }
        return payload

    def _write(self, payload: dict[str, object]) -> dict[str, object]:
        payload = dict(payload)
        payload["schema_version"] = FLOW_SCHEMA_VERSION
        payload["revision"] = int(payload.get("revision", 0)) + 1
        payload["updated_at"] = _iso(self._now())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="participant-flow-", suffix=".json.tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(_canonical_json(payload))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return payload

    def start_onboarding(self) -> None:
        with self._lock:
            state = self.snapshot()
            metrics = cast(dict[str, object], state["metrics"])
            if metrics["onboarding_started_at"] is None:
                metrics["onboarding_started_at"] = _iso(self._now())
                self._write(state)

    def record_evidence_action(self, action: str) -> None:
        key = {
            "approve": "approved",
            "save": "edited",
            "reject": "rejected",
            "defer": "deferred",
        }.get(action)
        if key is None:
            raise ValueError("Unsupported evidence metric action")
        with self._lock:
            state = self.snapshot()
            metrics = cast(dict[str, object], state["metrics"])
            counts = cast(dict[str, int], metrics["evidence_actions"])
            counts[key] = int(counts[key]) + 1
            if key == "approved" and metrics["first_approved_evidence_at"] is None:
                metrics["first_approved_evidence_at"] = _iso(self._now())
            self._write(state)

    def select_role(
        self,
        pack_id: RolePackId,
        *,
        location: str,
        work_model: str,
        maximum_travel_percent: int,
        currency: str,
    ) -> dict[str, object]:
        readiness = self.evidence.readiness()
        if not readiness.ready:
            raise ValueError("Evidence review must be ready before selecting a role family")
        if pack_id not in PARTICIPANT_ROLE_PACK_IDS:
            raise ValueError("Role pack is not supported by this synthetic participant fixture")
        if work_model not in {"remote", "hybrid", "onsite", "flexible"}:
            raise ValueError("Unsupported work model")
        if not 0 <= maximum_travel_percent <= 100:
            raise ValueError("Maximum travel must be between 0 and 100 percent")
        normalized_currency = _safe_text(currency, "currency", maximum=3).upper()
        if normalized_currency != "EUR":
            raise ValueError("The synthetic journey uses EUR only")
        selection = _ROLE_PACK_REGISTRY.select_for(pack_id, SYNTHETIC_POSTING_PROFILE.signals)
        with self._lock:
            state = self.snapshot()
            state["role_selection"] = {
                "pack_id": pack_id,
                "pack_version": selection.pack_version,
                "pack_fingerprint": selection.pack_fingerprint,
                "selection_status": selection.status,
                "matched_signals": list(selection.matched_signals),
                "missing_required_signals": list(selection.missing_required_signals),
                "adjacent_pack_ids": list(selection.adjacent_pack_ids),
                "adjacent_archetype_ids": list(selection.adjacent_archetype_ids),
                "selection_explanation": list(selection.explanation),
                "candidate_preferences": {
                    "location": _safe_text(location, "location", maximum=200),
                    "work_model": work_model,
                    "maximum_travel_percent": maximum_travel_percent,
                    "currency": normalized_currency,
                },
                "evidence_fingerprints": dict(self.evidence.context_fingerprints()),
                "selected_at": _iso(self._now()),
            }
            state["evaluation"] = None
            state["decision"] = None
            state["artifacts"] = []
            state["lifecycle_events"] = []
            metrics = cast(dict[str, object], state["metrics"])
            metrics["onboarding_completed_at"] = _iso(self._now())
            return self._write(state)

    def evaluate(self) -> DecisionDossier:
        with self._lock:
            state = self.snapshot()
            selection = state.get("role_selection")
            if not isinstance(selection, dict):
                raise ValueError("Select a role family before evaluating the posting")
            pack_id = cast(RolePackId, selection.get("pack_id"))
            pack = ROLE_PACKS.get(pack_id)
            if (
                pack is None
                or selection.get("pack_version") != pack.version
                or selection.get("pack_fingerprint") != pack.fingerprint
            ):
                raise ValueError("Selected role pack is stale or unsupported")
            if selection.get("evidence_fingerprints") != dict(self.evidence.context_fingerprints()):
                raise ValueError("Approved evidence changed; review the role selection again")
            approved = {
                record.evidence_id: record
                for record in self.evidence.enumerate(statuses=("approved",))
            }
            role_selection = RoleSelection(
                status=selection["selection_status"],
                pack_id=pack_id,
                pack_version=selection["pack_version"],
                pack_fingerprint=selection["pack_fingerprint"],
                matched_signals=tuple(selection["matched_signals"]),
                missing_required_signals=tuple(selection["missing_required_signals"]),
                adjacent_pack_ids=tuple(selection["adjacent_pack_ids"]),
                adjacent_archetype_ids=tuple(selection["adjacent_archetype_ids"]),
                explanation=tuple(selection["selection_explanation"]),
            )
            overlay = synthetic_candidate_overlay(
                constraints=cast(dict[str, object], selection["candidate_preferences"]),
                context_fingerprints=dict(self.evidence.context_fingerprints()),
            )
            evaluation = _ROLE_PACK_REGISTRY.evaluate(
                role_selection,
                SYNTHETIC_POSTING_PROFILE,
                overlay,
                approved_evidence_ids=approved,
            )
            assessments = tuple(
                RequirementAssessment(
                    requirement_id=item.requirement_id,
                    level=item.level,
                    impact=item.impact,
                    statement=item.statement,
                    outcome=item.outcome,
                    candidate_evidence_ids=item.candidate_evidence_ids,
                    transferable_evidence_ids=item.transferable_evidence_ids,
                    posting_evidence_id=item.posting_evidence_id,
                    rationale=item.rationale,
                )
                for item in evaluation.assessments
            )
            dossier = DecisionDossier(
                opportunity_id=SYNTHETIC_POSTING.job_key,
                posting_hash=SYNTHETIC_POSTING.content_hash,
                source_id=SYNTHETIC_POSTING.source_id,
                official_source_verified=True,
                role_pack_id=pack_id,
                role_pack_version=pack.version,
                role_pack_fingerprint=pack.fingerprint,
                selection_status=evaluation.selection.status,
                adjacent_pack_ids=evaluation.selection.adjacent_pack_ids,
                adjacent_archetype_ids=evaluation.selection.adjacent_archetype_ids,
                recommendation=evaluation.recommendation,
                confidence=evaluation.confidence,
                assessments=assessments,
                supported_claim_ids=evaluation.supported_claim_ids,
                transferable_evidence_ids=evaluation.transferable_evidence_ids,
                genuine_gap_ids=evaluation.genuine_gap_ids,
                unknown_requirement_ids=evaluation.unknown_requirement_ids,
                application_ready=evaluation.application_ready,
                evaluation_fingerprint=evaluation.evaluation_fingerprint,
                explanation=evaluation.explanation,
            )
            state["evaluation"] = asdict(dossier)
            state["decision"] = None
            state["artifacts"] = []
            state["lifecycle_events"] = []
            metrics = cast(dict[str, object], state["metrics"])
            metrics["unresolved_high_impact_questions"] = len(evaluation.unknown_requirement_ids)
            metrics["recommendation"] = evaluation.recommendation
            self._write(state)
            return dossier

    def dossier(self) -> DecisionDossier | None:
        payload = self.snapshot().get("evaluation")
        if not isinstance(payload, dict):
            return None
        current_pack = ROLE_PACKS.get(cast(RolePackId, payload.get("role_pack_id")))
        current_evaluation = bool(
            current_pack is not None
            and payload.get("role_pack_version") == current_pack.version
            and payload.get("role_pack_fingerprint") == current_pack.fingerprint
            and payload.get("selection_status") == "selected"
            and payload.get("evaluation_fingerprint")
        )
        assessments = tuple(
            RequirementAssessment(
                requirement_id=item["requirement_id"],
                level=item["level"],
                impact=item.get("impact", "evidence"),
                statement=item["statement"],
                outcome=item["outcome"],
                candidate_evidence_ids=tuple(item["candidate_evidence_ids"]),
                transferable_evidence_ids=tuple(item.get("transferable_evidence_ids", ())),
                posting_evidence_id=item["posting_evidence_id"],
                rationale=item["rationale"],
            )
            for item in payload["assessments"]
        )
        return DecisionDossier(
            opportunity_id=payload["opportunity_id"],
            posting_hash=payload["posting_hash"],
            source_id=payload["source_id"],
            official_source_verified=payload["official_source_verified"],
            role_pack_id=payload["role_pack_id"],
            role_pack_version=payload["role_pack_version"],
            role_pack_fingerprint=payload.get("role_pack_fingerprint", "legacy-unversioned"),
            selection_status=payload.get("selection_status", "manual_review"),
            adjacent_pack_ids=tuple(payload.get("adjacent_pack_ids", ())),
            adjacent_archetype_ids=tuple(payload.get("adjacent_archetype_ids", ())),
            recommendation=payload["recommendation"],
            confidence=payload["confidence"],
            assessments=assessments,
            supported_claim_ids=tuple(payload["supported_claim_ids"]),
            transferable_evidence_ids=tuple(payload.get("transferable_evidence_ids", ())),
            genuine_gap_ids=tuple(payload["genuine_gap_ids"]),
            unknown_requirement_ids=tuple(payload["unknown_requirement_ids"]),
            application_ready=bool(payload["application_ready"] and current_evaluation),
            evaluation_fingerprint=payload.get("evaluation_fingerprint", "legacy-unversioned"),
            explanation=tuple(payload.get("explanation", ())),
            external_actions=payload["external_actions"],
            model_calls=payload["model_calls"],
        )

    def record_decision(
        self,
        decision: UserDecision,
        *,
        override_reason: str | None = None,
    ) -> dict[str, object]:
        with self._lock:
            dossier = self.dossier()
            if dossier is None:
                raise ValueError("Evaluate the exact posting before recording a decision")
            if decision not in {"pursue", "conditional", "hold", "skip"}:
                raise ValueError("Unsupported role decision")
            reason = " ".join((override_reason or "").split()) or None
            if decision != dossier.recommendation and reason not in {
                "misunderstood_mandate",
                "missing_evidence",
                "practical_constraint",
                "company_preference",
                "scoring_defect",
            }:
                raise ValueError("A recommendation override requires a structured reason")
            state = self.snapshot()
            state["decision"] = {
                "opportunity_id": dossier.opportunity_id,
                "recommendation": dossier.recommendation,
                "decision": decision,
                "override_reason": reason,
                "recorded_at": _iso(self._now()),
                "application_event_created": False,
                "external_actions": False,
            }
            state["artifacts"] = []
            state["lifecycle_events"] = []
            metrics = cast(dict[str, object], state["metrics"])
            metrics["decision"] = decision
            metrics["override_reason"] = reason
            if metrics["first_useful_decision_at"] is None:
                metrics["first_useful_decision_at"] = _iso(self._now())
            return self._write(state)

    def prepare_bundle(self) -> tuple[dict[str, object], ...]:
        dossier = self.dossier()
        state = self.snapshot()
        decision = state.get("decision")
        if dossier is None or not isinstance(decision, dict):
            raise ValueError("Record a role decision before preparing content")
        if (
            decision.get("decision") not in {"pursue", "conditional"}
            or not dossier.application_ready
        ):
            raise ValueError("This decision is not ready for an application content package")
        approved = {
            record.evidence_id: record
            for record in self.evidence.enumerate(
                statuses=("approved",), publication_use="application"
            )
        }
        claim_ids = tuple(item for item in dossier.supported_claim_ids if item in approved)
        if not claim_ids:
            raise ValueError("No approved application-safe evidence supports this package")
        claims = [
            {
                "evidence_id": evidence_id,
                "revision": approved[evidence_id].revision,
                "content_hash": approved[evidence_id].content_hash,
                "statement": approved[evidence_id].statement,
            }
            for evidence_id in claim_ids
        ]
        opportunity = dossier.opportunity_id
        opportunity_component = f"synthetic-{_hash_bytes(opportunity.encode())[:16]}"
        application_root = self.instance_root / "applications" / opportunity_component
        exports_root = self.instance_root / "exports"
        requirement_map = {
            "schema_version": 1,
            "opportunity_id": opportunity,
            "posting_hash": dossier.posting_hash,
            "role_pack": {"id": dossier.role_pack_id, "version": dossier.role_pack_version},
            "requirements": [asdict(item) for item in dossier.assessments],
            "external_actions": False,
        }
        application_lines = [
            "# Synthetic application content package",
            "",
            "This is a truthful content brief, not a finished CV and not a submitted application.",
            "",
            f"Role: {SYNTHETIC_POSTING.title}",
            f"Company: {SYNTHETIC_POSTING.company} (fictional)",
            "",
            "## Approved claims",
            "",
        ]
        for claim in claims:
            application_lines.append(
                f"- {claim['statement']} [evidence:{claim['evidence_id']}#r{claim['revision']}]"
            )
        application_lines.extend(
            [
                "",
                "## Genuine gaps and questions",
                "",
                *[
                    f"- Verify or discuss: {item.statement} ({item.outcome})."
                    for item in dossier.assessments
                    if item.outcome != "proven"
                ],
            ]
        )
        interview_lines = [
            "# Synthetic interview briefing",
            "",
            "Use only approved claims below; do not imply an application or interview occurred.",
            "",
            "## Evidence-backed stories",
            "",
            *[
                f"- {claim['statement']} [evidence:{claim['evidence_id']}#r{claim['revision']}]"
                for claim in claims
            ],
            "",
            "## Questions to resolve",
            "",
            *[
                f"- Ask about {item.statement} Current evidence outcome: {item.outcome}."
                for item in dossier.assessments
                if item.outcome in {"disproven", "conflict", "unknown", "not_found"}
            ],
        ]
        dossier_html = self._render_dossier_html(dossier)
        outputs: Mapping[Path, bytes] = {
            application_root / "application-brief.md": (
                "\n".join(application_lines) + "\n"
            ).encode(),
            application_root / "interview-briefing.md": (
                "\n".join(interview_lines) + "\n"
            ).encode(),
            application_root / "requirement-evidence-map.json": _canonical_json(requirement_map),
            exports_root / "decision-dossier.html": dossier_html.encode("utf-8"),
        }
        artifact_records: list[dict[str, object]] = []
        for path, content in outputs.items():
            if not _inside(path, self.instance_root):
                raise ValueError("Generated artifact escaped the participant instance")
            _atomic_write(path, content)
            artifact_records.append(
                {
                    "relative_path": path.relative_to(self.instance_root).as_posix(),
                    "sha256": _hash_bytes(content),
                    "size_bytes": len(content),
                    "user_owned": True,
                }
            )
        with self._lock:
            latest = self.snapshot()
            latest["artifacts"] = artifact_records
            self._write(latest)
        return tuple(artifact_records)

    def confirm_synthetic_lifecycle(self, *, confirmed: bool) -> dict[str, object]:
        if not confirmed:
            raise PermissionError("The synthetic lifecycle event requires explicit confirmation")
        state = self.snapshot()
        if not state.get("artifacts"):
            raise ValueError("Prepare the content package before confirming its local review")
        decision = cast(dict[str, object], state["decision"])
        event = {
            "event_id": _hash_bytes(
                f"{decision['opportunity_id']}\0synthetic_bundle_reviewed".encode()
            ),
            "opportunity_id": decision["opportunity_id"],
            "kind": "synthetic_application_bundle_reviewed",
            "summary": "The local user confirmed review of the fictional content package.",
            "confirmed_by": "local_user",
            "confirmed_at": _iso(self._now()),
            "synthetic": True,
            "external_event": False,
            "application_submitted": False,
        }
        events = cast(list[dict[str, object]], state["lifecycle_events"])
        if not any(item.get("event_id") == event["event_id"] for item in events):
            events.append(event)
        self._write(state)
        return event

    def export_user_data(self) -> dict[str, object]:
        state = self.snapshot()
        artifact_inventory = []
        for item in cast(list[dict[str, object]], state["artifacts"]):
            path = self.instance_root / cast(str, item["relative_path"])
            if not path.is_file() or _hash_bytes(path.read_bytes()) != item["sha256"]:
                raise ValueError("Generated artifact inventory is stale or incomplete")
            artifact_inventory.append(dict(item))
        payload = {
            "schema_version": 1,
            "product": "Rolevane synthetic participant journey",
            "evidence_vault": self.evidence.export_snapshot(),
            "participant_flow": state,
            "export_manifest": {
                "user_owned_categories": [
                    "source_inventory",
                    "evidence_revisions",
                    "evidence_audit",
                    "role_selection",
                    "posting_evaluation",
                    "decision",
                    "generated_artifacts",
                    "confirmed_synthetic_lifecycle",
                    "local_metrics",
                ],
                "artifacts": artifact_inventory,
                "credentials_included": False,
                "remote_telemetry": False,
                "external_actions": False,
            },
        }
        target = self.instance_root / "exports" / "career-os-user-export.json"
        _atomic_write(target, _canonical_json(payload))
        return payload

    def reset_preview(self) -> tuple[str, ...]:
        if not self.instance_root.exists():
            return ()
        if _is_reparse_or_symlink(self.instance_root):
            raise ValueError("Reset refuses a symlink or reparse-point instance root")
        files: list[str] = []
        pending = [self.instance_root]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if _is_reparse_or_symlink(path):
                        raise ValueError("Reset refuses symlinks and reparse points")
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(path.relative_to(self.instance_root).as_posix())
                    else:
                        raise ValueError("Reset found an unsupported filesystem object")
        return tuple(sorted(files))

    def reset(self, confirmation: str) -> ResetReport:
        if confirmation != RESET_PHRASE:
            raise PermissionError(f"Reset requires the exact phrase {RESET_PHRASE}")
        if not self.path.is_file():
            raise ValueError("This directory is not a recognized participant instance")
        if _is_reparse_or_symlink(self.instance_root):
            raise ValueError("Reset refuses a symlink or reparse-point instance root")
        root = self.instance_root.resolve(strict=True)
        if root == Path(root.anchor) or root == Path.home().resolve(strict=False):
            raise ValueError("Refusing to reset a broad filesystem location")
        allowed_top_level = {"data", "applications", "exports"}
        unexpected = {path.name for path in root.iterdir()} - allowed_top_level
        if unexpected:
            raise ValueError(f"Reset found unrecognized top-level state: {sorted(unexpected)}")
        inventory = self.reset_preview()
        shutil.rmtree(root)
        return ResetReport(
            removed_records=inventory,
            removed_count=len(inventory),
            instance_removed=not root.exists(),
            retained_installation_files=(
                "Installed Rolevane package remains outside the participant data directory.",
            ),
        )

    @staticmethod
    def _render_dossier_html(dossier: DecisionDossier) -> str:
        rows = "".join(
            "<tr>"
            f"<td>{html.escape(item.level.title())} / {html.escape(item.impact.replace('_', ' ').title())}</td>"
            f"<td>{html.escape(item.statement)}</td>"
            f"<td>{html.escape(item.outcome.replace('_', ' ').title())}</td>"
            f"<td>{html.escape(', '.join(item.candidate_evidence_ids) or 'No direct proof')}"
            f"{html.escape(' | Transferable: ' + ', '.join(item.transferable_evidence_ids)) if item.transferable_evidence_ids else ''}</td>"
            "</tr>"
            for item in dossier.assessments
        )
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Synthetic role decision dossier</title>
<style>body{{font:16px/1.5 system-ui;max-width:960px;margin:3rem auto;padding:0 1rem;color:#18221f}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd5d0;padding:.65rem;text-align:left}}
.boundary{{padding:1rem;background:#e8f3ee;border-left:4px solid #126b55}}</style></head><body>
<p>SYNTHETIC OFFLINE ARTIFACT</p><h1>{html.escape(SYNTHETIC_POSTING.title)}</h1>
<p class="boundary">Recommendation: <strong>{html.escape(dossier.recommendation.title())}</strong>.
This is a decision aid, not an application or external status.</p>
<p>Official synthetic source: {html.escape(dossier.source_id)} · posting hash {html.escape(dossier.posting_hash)}</p>
<p>Role pack: {html.escape(dossier.role_pack_id)} v{html.escape(dossier.role_pack_version)} ·
fingerprint {html.escape(dossier.role_pack_fingerprint[:12])} · {html.escape(dossier.selection_status)}</p>
<table><thead><tr><th>Level / impact</th><th>Requirement</th><th>Outcome</th><th>Evidence route</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""

    def export_state(self) -> dict[str, object]:
        return {
            "schema_version": FLOW_SCHEMA_VERSION,
            "role_packs": [asdict(pack) for pack in ROLE_PACKS.values()],
            "synthetic_posting": {
                "fixture_version": POSTING_FIXTURE_VERSION,
                "opportunity_id": SYNTHETIC_POSTING.job_key,
                "provider": SYNTHETIC_POSTING.provider,
                "source_id": SYNTHETIC_POSTING.source_id,
                "external_id": SYNTHETIC_POSTING.external_id,
                "company": SYNTHETIC_POSTING.company,
                "title": SYNTHETIC_POSTING.title,
                "url": SYNTHETIC_POSTING.url,
                "location": SYNTHETIC_POSTING.location,
                "description": SYNTHETIC_POSTING.description,
                "observed_at": SYNTHETIC_POSTING.observed_at,
                "content_hash": SYNTHETIC_POSTING.content_hash,
                "official_source_verified": True,
            },
            "state": self.snapshot(),
            "model_calls": False,
            "external_actions": False,
        }


__all__ = [
    "DecisionDossier",
    "FLOW_SCHEMA_VERSION",
    "ParticipantFlow",
    "POSTING_FIXTURE_VERSION",
    "RESET_PHRASE",
    "ROLE_PACKS",
    "PARTICIPANT_ROLE_PACK_IDS",
    "ROLE_PACK_VERSION",
    "RequirementAssessment",
    "ResetReport",
    "RolePackId",
    "SYNTHETIC_POSTING",
    "UserDecision",
]

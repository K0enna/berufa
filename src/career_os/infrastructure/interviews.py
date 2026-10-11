"""Immutable interview content using the existing dossier and evidence authority lock."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from career_os.application.applications import application_fingerprint
from career_os.application.interviews import (
    InterviewContentPlan,
    InterviewContext,
    InterviewReceipt,
    InterviewRequest,
    InterviewService,
    InterviewValidation,
    build_interview_plan,
    interview_artifact_bytes,
    interview_evidence_text,
)
from career_os.application_records import _hash_payload
from career_os.evidence_contract import PublicationRequest
from career_os.evidence_vault import EvidenceVault
from career_os.file_transactions import _safe_regular_file_path
from career_os.infrastructure.content_artifacts import (
    MAX_ARTIFACT_BYTES,
    _directory,
    _inventory,
    publish_content_version,
    read_content_artifacts,
)
from career_os.infrastructure.evidence import read_private_json
from career_os.infrastructure.opportunities import LocalOpportunityRepository
from career_os.role_packs import bundled_role_pack_registry

INTERVIEW_WORKSPACE_MANIFEST = "interview-content-workspace.json"
_ARTIFACT_NAMES = {
    "interview-content-plan.json",
    "interview-brief.txt",
    "live-call-sheet.txt",
    "requirement-evidence-map.json",
}


class LocalInterviewRepository:
    def __init__(self, instance_root: str | Path, *, vault: EvidenceVault | None = None) -> None:
        lexical = Path(instance_root).expanduser().absolute()
        _directory(lexical)
        self._vault = vault if vault is not None else EvidenceVault(lexical)
        if self._vault.instance_root != lexical.resolve(strict=False):
            raise ValueError("Interview repository vault belongs to a different instance")
        self.instance_root = self._vault.instance_root
        self._opportunities = LocalOpportunityRepository(
            self.instance_root, vault=self._vault, read_only_resolution=True
        )

    def has_consent(self) -> bool:
        return self._vault.has_consent()

    def _plan(self, request: InterviewRequest) -> InterviewContentPlan:
        if not self.has_consent():
            raise PermissionError("Local-processing consent required")
        directory = (
            self.instance_root / "data" / "runtime" / "opportunities" / request.opportunity_id
        )
        for name in ("supplied-request.json", "decision-dossier.json", "decisions.json"):
            _safe_regular_file_path(directory / name)
        state = self._opportunities.resolve_supplied(request.opportunity_id)
        evidence = tuple(self._vault.latest(identifier) for identifier in request.evidence_ids)
        if set(request.evidence_ids) & set(self._vault.conflicting_evidence_ids()):
            raise ValueError("Selected evidence has an unresolved approved conflict")
        publication = tuple(
            {
                field: self._vault.validate_publication(
                    PublicationRequest(
                        text, (item.evidence_id,), "interview", item.ownership, item.approximation
                    )
                )
                for field, text in interview_evidence_text(item).items()
            }
            for item in evidence
        )
        selection = state.dossier.dossier["role_evaluation"]["selection"]
        pack = bundled_role_pack_registry().packs[selection["pack_id"]]
        questions = tuple(dict.fromkeys((*pack.diligence_prompts, *pack.economics_questions)))
        return build_interview_plan(
            request,
            InterviewContext(
                state, evidence, publication, self._vault.context_fingerprints(), questions
            ),
        )

    def plan(self, request: InterviewRequest) -> InterviewContentPlan:
        with self._vault.read_transaction():
            return self._plan(request)

    def _target(self, opportunity_id: str, round_id: str, fingerprint: str) -> Path:
        return _directory(
            self.instance_root
            / "applications"
            / opportunity_id
            / "interview-content"
            / round_id
            / fingerprint
        )

    def _read(self, target: Path) -> InterviewContentPlan:
        # Preserve the plan-only contract used by workspace inspection and export.
        return self._read_snapshot(target)[0]

    def _read_snapshot(self, target: Path) -> tuple[InterviewContentPlan, dict[str, bytes]]:
        _directory(target)
        names: set[str] = set()
        with os.scandir(target) as entries:
            for entry in entries:
                names.add(entry.name)
                if len(names) > 5:
                    raise ValueError("Interview artifact inventory is invalid")
        if names != _ARTIFACT_NAMES | {INTERVIEW_WORKSPACE_MANIFEST}:
            raise ValueError("Interview artifact inventory is incomplete or unknown")
        manifest, _ = read_private_json(
            self.instance_root,
            _safe_regular_file_path(target / INTERVIEW_WORKSPACE_MANIFEST),
            label="Interview workspace",
            maximum_bytes=MAX_ARTIFACT_BYTES,
        )
        if not isinstance(manifest, dict) or set(manifest) != {
            "schema_version",
            "opportunity_id",
            "round_id",
            "confirmation_fingerprint",
            "prepared_at",
            "confirmed_by",
            "artifacts",
            "external_actions",
            "external_event_recorded",
        }:
            raise ValueError("Interview workspace schema is invalid")
        if (
            type(manifest["schema_version"]) is not int
            or manifest["schema_version"] != 2
            or manifest["opportunity_id"] != target.parents[2].name
            or manifest["round_id"] != target.parent.name
            or manifest["confirmation_fingerprint"] != target.name
            or manifest["confirmed_by"] != "local_user"
            or manifest["external_actions"] is not False
            or manifest["external_event_recorded"] is not False
        ):
            raise ValueError("Interview workspace authority or version is invalid")
        if (
            not isinstance(manifest["prepared_at"], str)
            or datetime.fromisoformat(manifest["prepared_at"]).tzinfo is None
        ):
            raise ValueError("Interview preparation time must include a timezone")
        payload, plan_bytes = read_private_json(
            self.instance_root,
            _safe_regular_file_path(target / "interview-content-plan.json"),
            label="Interview plan",
            maximum_bytes=MAX_ARTIFACT_BYTES,
        )
        if (
            not isinstance(payload, dict)
            or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 2
        ):
            raise ValueError("Interview plan schema is invalid")
        request = InterviewRequest.from_dict(payload.get("request"))
        if (
            request.opportunity_id != target.parents[2].name
            or request.round_id != target.parent.name
        ):
            raise ValueError("Interview plan identity is invalid")
        fingerprint = application_fingerprint(payload.get("confirmation_fingerprint"))
        if (
            fingerprint != target.name
            or _hash_payload(
                {key: value for key, value in payload.items() if key != "confirmation_fingerprint"}
            )
            != fingerprint
        ):
            raise ValueError("Interview plan fingerprint is invalid")
        files = read_content_artifacts(
            target,
            _ARTIFACT_NAMES,
            plan_name="interview-content-plan.json",
            plan_bytes=plan_bytes,
            label="Interview",
        )
        if manifest["artifacts"] != _inventory(files):
            raise ValueError("Interview artifact content has changed")
        return InterviewContentPlan(payload), files

    def prepare(self, request: InterviewRequest, fingerprint: str) -> InterviewReceipt:
        # Reject stale confirmations without creating or migrating any local state.
        if self.plan(request).confirmation_fingerprint != fingerprint:
            raise PermissionError("Interview confirmation is stale")
        with self._vault.transaction():
            plan = self._plan(request)
            if plan.confirmation_fingerprint != fingerprint:
                raise PermissionError("Interview confirmation is stale")
            target = self._target(request.opportunity_id, request.round_id, fingerprint)
            created = not target.exists()
            files = interview_artifact_bytes(plan)
            if not created:
                stored, stored_files = self._read_snapshot(target)
                if stored.to_dict() != plan.to_dict():
                    raise ValueError("Existing interview version differs from current authority")
                if stored_files != files:
                    raise ValueError("Existing interview artifacts differ from the plan")
            else:
                manifest = {
                    "schema_version": 2,
                    "opportunity_id": request.opportunity_id,
                    "round_id": request.round_id,
                    "confirmation_fingerprint": fingerprint,
                    "prepared_at": datetime.now(UTC).isoformat(),
                    "confirmed_by": "local_user",
                    "artifacts": _inventory(files),
                    "external_actions": False,
                    "external_event_recorded": False,
                }
                files[INTERVIEW_WORKSPACE_MANIFEST] = (
                    json.dumps(manifest, sort_keys=True, indent=2) + "\n"
                ).encode()
                publish_content_version(target, files)
            return InterviewReceipt(
                request.opportunity_id,
                request.round_id,
                fingerprint,
                target.relative_to(self.instance_root).as_posix(),
                created,
            )

    def validate(self, opportunity_id: str, round_id: str, fingerprint: str) -> InterviewValidation:
        target = self._target(opportunity_id, round_id, fingerprint)
        with self._vault.read_transaction():
            if not self.has_consent():
                raise PermissionError("Local-processing consent required")
            stored, stored_files = self._read_snapshot(target)
            request = InterviewRequest.from_dict(stored.payload["request"])
            try:
                current_plan = self._plan(request)
            except ValueError:
                current = False
            else:
                current = stored.to_dict() == current_plan.to_dict()
                if current and stored_files != interview_artifact_bytes(current_plan):
                    raise ValueError("Interview artifacts differ from their current plan")
            return InterviewValidation(
                opportunity_id,
                round_id,
                fingerprint,
                True,
                current,
                current,
                ("Local supplied-role preparation only; no scheduling or debrief event recorded.",)
                + (
                    ()
                    if current
                    else (
                        "Current source, decision or evidence authority has changed or is invalid.",
                    )
                ),
            )


def create_interview_service(instance_root: str | Path) -> InterviewService:
    return InterviewService(LocalInterviewRepository(instance_root))

"""Immutable local application briefs over the existing vault and supplied dossier."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from career_os.application.applications import (
    ApplicationContentPlan,
    ApplicationContext,
    ApplicationReceipt,
    ApplicationRequest,
    ApplicationService,
    ApplicationValidation,
    application_artifact_bytes,
    application_fingerprint,
    build_application_plan,
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

# Legacy discovery recursively owns workspace.json, including below this shared root.
APPLICATION_WORKSPACE_MANIFEST = "content-workspace.json"
_ARTIFACT_NAMES = {
    "application-plan.json",
    "application-brief.txt",
    "requirement-evidence-map.json",
}


class LocalApplicationRepository:
    def __init__(self, instance_root: str | Path, *, vault: EvidenceVault | None = None) -> None:
        lexical = Path(instance_root).expanduser().absolute()
        _directory(lexical)
        self._vault = vault if vault is not None else EvidenceVault(lexical)
        if self._vault.instance_root != lexical.resolve(strict=False):
            raise ValueError("Application repository vault belongs to a different instance")
        self.instance_root = self._vault.instance_root
        self._opportunities = LocalOpportunityRepository(
            self.instance_root, vault=self._vault, read_only_resolution=True
        )

    def has_consent(self) -> bool:
        return self._vault.has_consent()

    def _plan(self, request: ApplicationRequest) -> ApplicationContentPlan:
        if not self.has_consent():
            raise PermissionError("Local-processing consent required")
        directory = (
            self.instance_root / "data" / "runtime" / "opportunities" / request.opportunity_id
        )
        for name in ("supplied-request.json", "decision-dossier.json", "decisions.json"):
            _safe_regular_file_path(directory / name)
        state = self._opportunities.resolve_supplied(request.opportunity_id)
        evidence = tuple(self._vault.latest(identifier) for identifier in request.evidence_ids)
        publication = tuple(
            self._vault.validate_publication(
                PublicationRequest(
                    item.statement,
                    (item.evidence_id,),
                    "application",
                    item.ownership,
                    item.approximation,
                )
            )
            for item in evidence
        )
        selection = state.dossier.dossier["role_evaluation"]["selection"]
        pack = bundled_role_pack_registry().packs[selection["pack_id"]]
        return build_application_plan(
            request,
            ApplicationContext(
                state,
                evidence,
                publication,
                self._vault.context_fingerprints(),
                pack.application_archetypes,
            ),
        )

    def plan(self, request: ApplicationRequest) -> ApplicationContentPlan:
        with self._vault.read_transaction():
            return self._plan(request)

    def _target(self, opportunity_id: str, fingerprint: str) -> Path:
        return _directory(self.instance_root / "applications" / opportunity_id / fingerprint)

    def _read(self, target: Path) -> ApplicationContentPlan:
        # Preserve the plan-only contract used by workspace inspection and export.
        return self._read_snapshot(target)[0]

    def _read_snapshot(self, target: Path) -> tuple[ApplicationContentPlan, dict[str, bytes]]:
        _directory(target)
        # Fixed, bounded inventory. Extra files are not silently accepted as this version.
        names: set[str] = set()
        with os.scandir(target) as entries:
            for entry in entries:
                names.add(entry.name)
                if len(names) > 4:
                    raise ValueError("Application artifact inventory is invalid")
        if names != _ARTIFACT_NAMES | {APPLICATION_WORKSPACE_MANIFEST}:
            raise ValueError("Application artifact inventory is incomplete or unknown")
        manifest, _ = read_private_json(
            self.instance_root,
            _safe_regular_file_path(target / APPLICATION_WORKSPACE_MANIFEST),
            label="Application workspace",
            maximum_bytes=MAX_ARTIFACT_BYTES,
        )
        if not isinstance(manifest, dict) or set(manifest) != {
            "schema_version",
            "opportunity_id",
            "confirmation_fingerprint",
            "prepared_at",
            "confirmed_by",
            "artifacts",
            "application_ready",
            "external_actions",
        }:
            raise ValueError("Application workspace schema is invalid")
        if (
            type(manifest["schema_version"]) is not int
            or manifest["schema_version"] != 3
            or manifest["opportunity_id"] != target.parent.name
            or manifest["confirmation_fingerprint"] != target.name
            or manifest["confirmed_by"] != "local_user"
            or manifest["application_ready"] is not False
            or manifest["external_actions"] is not False
        ):
            raise ValueError("Application workspace authority or version is invalid")
        if not isinstance(manifest["prepared_at"], str):
            raise ValueError("Application preparation time is invalid")
        if datetime.fromisoformat(manifest["prepared_at"]).tzinfo is None:
            raise ValueError("Application preparation time must include a timezone")
        payload, plan_bytes = read_private_json(
            self.instance_root,
            _safe_regular_file_path(target / "application-plan.json"),
            label="Application plan",
            maximum_bytes=MAX_ARTIFACT_BYTES,
        )
        if not isinstance(payload, dict):
            raise ValueError("Application plan must be an object")
        if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
            raise ValueError("Application plan schema is invalid")
        request = ApplicationRequest.from_dict(payload.get("request"))
        if request.opportunity_id != target.parent.name:
            raise ValueError("Application plan identity is invalid")
        fingerprint = application_fingerprint(payload.get("confirmation_fingerprint"))
        if (
            fingerprint != target.name
            or _hash_payload(
                {key: value for key, value in payload.items() if key != "confirmation_fingerprint"}
            )
            != fingerprint
        ):
            raise ValueError("Application plan fingerprint is invalid")
        files = read_content_artifacts(
            target,
            _ARTIFACT_NAMES,
            plan_name="application-plan.json",
            plan_bytes=plan_bytes,
            label="Application",
        )
        if manifest["artifacts"] != _inventory(files):
            raise ValueError("Application artifact content has changed")
        return ApplicationContentPlan(payload), files

    def prepare(self, request: ApplicationRequest, fingerprint: str) -> ApplicationReceipt:
        # Read-only preflight prevents rejected confirmations from creating or migrating state.
        if self.plan(request).confirmation_fingerprint != fingerprint:
            raise PermissionError("Application confirmation is stale")
        # All cooperative dossier, decision and evidence writers use this same vault lock.
        with self._vault.transaction():
            plan = self._plan(request)
            if plan.confirmation_fingerprint != fingerprint:
                raise PermissionError("Application confirmation is stale")
            target = self._target(request.opportunity_id, fingerprint)
            created = not target.exists()
            if not created:
                stored, stored_files = self._read_snapshot(target)
                if stored.to_dict() != plan.to_dict():
                    raise ValueError("Existing application version differs from its authority")
                if stored_files != application_artifact_bytes(plan):
                    raise ValueError("Existing application artifacts differ from the plan")
            else:
                files = application_artifact_bytes(plan)
                manifest = {
                    "schema_version": 3,
                    "opportunity_id": request.opportunity_id,
                    "confirmation_fingerprint": fingerprint,
                    "prepared_at": datetime.now(UTC).isoformat(),
                    "confirmed_by": "local_user",
                    "artifacts": _inventory(files),
                    "application_ready": False,
                    "external_actions": False,
                }
                files[APPLICATION_WORKSPACE_MANIFEST] = (
                    json.dumps(manifest, sort_keys=True, indent=2) + "\n"
                ).encode()
                publish_content_version(target, files)
            return ApplicationReceipt(
                request.opportunity_id,
                fingerprint,
                target.relative_to(self.instance_root).as_posix(),
                created,
            )

    def validate(self, opportunity_id: str, fingerprint: str) -> ApplicationValidation:
        target = self._target(opportunity_id, fingerprint)
        with self._vault.read_transaction():
            if not self.has_consent():
                raise PermissionError("Local-processing consent required")
            stored, stored_files = self._read_snapshot(target)
            request = ApplicationRequest.from_dict(stored.payload["request"])
            try:
                current_plan = self._plan(request)
            except ValueError:
                current = False
            else:
                current = stored.to_dict() == current_plan.to_dict()
                if current and stored_files != application_artifact_bytes(current_plan):
                    raise ValueError("Application artifacts differ from their current plan")
            return ApplicationValidation(
                opportunity_id,
                fingerprint,
                True,
                current,
                current,
                (
                    "Supplied-posting content brief only; not a finished or application-ready document.",
                )
                + (
                    ()
                    if current
                    else (
                        "Current source, decision, or evidence authority has changed or is invalid.",
                    )
                ),
            )


def create_application_service(instance_root: str | Path) -> ApplicationService:
    return ApplicationService(LocalApplicationRepository(instance_root))

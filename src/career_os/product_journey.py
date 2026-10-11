"""Thin participant journey over the generic evidence-vault authority."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from career_os.document_intake import extract_candidate_document
from career_os.evidence_vault import (
    Approximation,
    Confidence,
    EvidenceReadiness,
    EvidenceRevision,
    EvidenceVault,
    Ownership,
    PublicationBoundary,
    ReviewAction,
    SourceDocument,
)
from career_os.infrastructure.instance_data import create_instance_data_service
from career_os.infrastructure.instances import create_instance_service
from career_os.participant_flow import (
    RESET_PHRASE,
    DecisionDossier,
    ParticipantFlow,
    ResetReport,
    RolePackId,
    UserDecision,
)
from career_os.public_api import EvidenceService, require_evidence_result
from career_os.synthetic_evidence import seed_synthetic_evidence


@dataclass(frozen=True, slots=True)
class JourneyState:
    stage: str
    consent_recorded: bool
    source_count: int
    review_items: int
    approved_items: int
    next_route: str
    role_pack_selected: bool = False
    dossier_ready: bool = False
    decision_recorded: bool = False
    bundle_ready: bool = False
    lifecycle_confirmed: bool = False
    external_actions: bool = False
    model_calls: bool = False


class ProductJourney:
    """Resumable first product slice; construction has no filesystem effect."""

    def __init__(self, instance_root: str | Path) -> None:
        self.instance_root = Path(instance_root).expanduser().resolve(strict=False)
        self.evidence = EvidenceVault(self.instance_root)
        self.evidence_service = EvidenceService(self.evidence, extract_candidate_document)
        self.flow = ParticipantFlow(self.instance_root, self.evidence)

    def state(self) -> JourneyState:
        consent = self.evidence.has_consent()
        sources = self.evidence.sources() if consent else ()
        queue = self.evidence.review_queue() if sources else ()
        approved = len(self.evidence.enumerate(statuses=("approved",))) if sources else 0
        flow = self.flow.snapshot()
        role_selected = isinstance(flow.get("role_selection"), dict)
        dossier_ready = isinstance(flow.get("evaluation"), dict)
        decision_recorded = isinstance(flow.get("decision"), dict)
        bundle_ready = bool(flow.get("artifacts"))
        lifecycle_confirmed = bool(flow.get("lifecycle_events"))
        if not consent:
            stage, route = "boundary", "/"
        elif not sources:
            stage, route = "import", "/import"
        elif queue:
            stage, route = "review", "/review"
        elif not self.evidence.readiness().ready or not role_selected:
            stage, route = "readiness", "/readiness"
        elif not dossier_ready:
            stage, route = "posting", "/posting"
        elif not decision_recorded:
            stage, route = "decision", "/dossier"
        elif not bundle_ready:
            stage, route = "preparation", "/prepare"
        elif not lifecycle_confirmed:
            stage, route = "lifecycle", "/lifecycle"
        else:
            stage, route = "complete", "/complete"
        return JourneyState(
            stage=stage,
            consent_recorded=consent,
            source_count=len(sources),
            review_items=len(queue),
            approved_items=approved,
            next_route=route,
            role_pack_selected=role_selected,
            dossier_ready=dossier_ready,
            decision_recorded=decision_recorded,
            bundle_ready=bundle_ready,
            lifecycle_confirmed=lifecycle_confirmed,
        )

    def record_boundary_consent(self, *, accepted: bool) -> JourneyState:
        require_evidence_result(self.evidence_service.record_consent(accepted=accepted))
        self.flow.start_onboarding()
        return self.state()

    def import_synthetic_document(self) -> JourneyState:
        seed_synthetic_evidence(self.evidence)
        return self.state()

    def import_local_document(self, path: str | Path) -> SourceDocument:
        """Parse and persist one supported local document without proposing evidence."""
        return require_evidence_result(self.evidence_service.import_document(path))

    def next_review(self) -> EvidenceRevision | None:
        queue = self.evidence.review_queue()
        return queue[0] if queue else None

    def review(
        self,
        evidence_id: str,
        revision: int,
        *,
        action: ReviewAction,
        expected_content_hash: str,
    ) -> EvidenceRevision:
        result = require_evidence_result(
            self.evidence_service.review(
                evidence_id,
                revision,
                actor="local_user",
                action=action,
                expected_content_hash=expected_content_hash,
            )
        )
        self.flow.record_evidence_action(action)
        return result

    def edit(
        self,
        evidence_id: str,
        *,
        expected_revision: int,
        statement: str,
        ownership: Ownership,
        confidence: Confidence,
        approximation: Approximation,
        publication_boundary: PublicationBoundary,
    ) -> EvidenceRevision:
        result = require_evidence_result(
            self.evidence_service.edit(
                evidence_id,
                expected_revision=expected_revision,
                statement=statement,
                ownership=ownership,
                confidence=confidence,
                approximation=approximation,
                publication_boundary=publication_boundary,
            )
        )
        self.flow.record_evidence_action("save")
        return result

    def readiness(self) -> EvidenceReadiness:
        return require_evidence_result(self.evidence_service.readiness())

    def select_role(
        self,
        pack_id: RolePackId,
        *,
        location: str,
        work_model: str,
        maximum_travel_percent: int,
        currency: str = "EUR",
    ) -> JourneyState:
        self.flow.select_role(
            pack_id,
            location=location,
            work_model=work_model,
            maximum_travel_percent=maximum_travel_percent,
            currency=currency,
        )
        return self.state()

    def evaluate_posting(self) -> DecisionDossier:
        return self.flow.evaluate()

    def dossier(self) -> DecisionDossier | None:
        return self.flow.dossier()

    def record_decision(
        self,
        decision: UserDecision,
        *,
        override_reason: str | None = None,
    ) -> JourneyState:
        self.flow.record_decision(decision, override_reason=override_reason)
        return self.state()

    def prepare_bundle(self) -> tuple[dict[str, object], ...]:
        return self.flow.prepare_bundle()

    def confirm_synthetic_lifecycle(self, *, confirmed: bool) -> JourneyState:
        self.flow.confirm_synthetic_lifecycle(confirmed=confirmed)
        return self.state()

    def export_user_data(self) -> dict[str, object]:
        return self.flow.export_user_data()

    def reset_preview(self) -> tuple[str, ...]:
        return self.flow.reset_preview()

    def reset(self, confirmation: str) -> ResetReport:
        return self.flow.reset(confirmation)

    def export_snapshot(self) -> dict[str, object]:
        return {
            "product": "Rolevane synthetic evidence journey",
            "journey": asdict(self.state()),
            "vault": self.evidence.export_snapshot(),
            "participant_flow": self.flow.export_state(),
            "external_actions": False,
            "model_calls": False,
        }


def run_smoke() -> dict[str, object]:
    """Complete two model-disabled evidence journeys without checkout data."""
    database_paths: list[str] = []
    with tempfile.TemporaryDirectory(prefix="career-os-evidence-smoke-") as temporary:
        outer = Path(temporary).resolve()
        for tag in ("participant-1", "participant-2"):
            root = outer / tag
            journey = ProductJourney(root)
            if root.exists():
                raise AssertionError("Journey construction created participant state")
            journey.record_boundary_consent(accepted=True)
            journey.import_synthetic_document()
            while card := journey.next_review():
                journey.review(
                    card.evidence_id,
                    card.revision,
                    action="approve",
                    expected_content_hash=card.content_hash,
                )
            if not journey.readiness().ready:
                raise AssertionError("Synthetic evidence review did not become ready")
            if (root / "applications").exists() or (root / "pipeline").exists():
                raise AssertionError("Evidence review crossed into application authority")
            database_paths.append(str(journey.evidence.path.relative_to(outer)))
        if len(set(database_paths)) != 2:
            raise AssertionError("Synthetic evidence instances shared a database")
    return {
        "schema_version": 1,
        "instances": 2,
        "approved_items_per_instance": 6,
        "model_calls": False,
        "external_actions": False,
        "database_paths": database_paths,
    }


def run_full_smoke() -> dict[str, object]:
    """Complete two model-disabled journeys and two populated workspace closeouts."""
    proofs: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="career-os-full-journey-") as temporary:
        outer = Path(temporary).resolve()
        for tag in ("participant-1", "participant-2"):
            demo_root = outer / f"{tag}-demo"
            journey = ProductJourney(demo_root)
            journey.record_boundary_consent(accepted=True)
            journey.import_synthetic_document()
            first = journey.next_review()
            if first is None:
                raise AssertionError("Synthetic journey did not create review cards")
            journey.edit(
                first.evidence_id,
                expected_revision=first.revision,
                statement=first.statement + " This wording was reviewed locally.",
                ownership=first.ownership,
                confidence=first.confidence,
                approximation=first.approximation,
                publication_boundary=first.publication_boundary,
            )
            deferred_once = False
            while card := journey.next_review():
                if card.evidence_id == "synthetic-achievement" and not deferred_once:
                    journey.review(
                        card.evidence_id,
                        card.revision,
                        action="defer",
                        expected_content_hash=card.content_hash,
                    )
                    deferred_once = True
                    continue
                action: ReviewAction = (
                    "reject" if card.evidence_id == "synthetic-forbidden-degree" else "approve"
                )
                journey.review(
                    card.evidence_id,
                    card.revision,
                    action=action,
                    expected_content_hash=card.content_hash,
                )
            if not journey.readiness().ready:
                raise AssertionError("Synthetic evidence did not become ready")
            journey.select_role(
                "customer_post_sales_leadership",
                location="Germany",
                work_model="flexible",
                maximum_travel_percent=25,
            )
            dossier = journey.evaluate_posting()
            journey.record_decision(dossier.recommendation)
            artifacts = journey.prepare_bundle()
            journey.confirm_synthetic_lifecycle(confirmed=True)
            journey_export = journey.export_user_data()
            journey_export_path = demo_root / "exports" / "career-os-user-export.json"
            journey_export_content = journey_export_path.read_bytes()
            participant_state = cast(dict[str, object], journey_export["participant_flow"])
            participant_decision = cast(dict[str, object], participant_state["decision"])
            if (
                participant_decision["decision"] != dossier.recommendation
                or not participant_state["artifacts"]
                or not participant_state["lifecycle_events"]
            ):
                raise AssertionError("Completed synthetic journey was not exported")
            retained_journey_export = outer / f"{tag}-completed-journey.json"
            retained_journey_export.write_bytes(journey_export_content)
            journey_inventory = journey.reset_preview()
            journey_deleted = journey.reset(RESET_PHRASE)
            if demo_root.exists() or not journey_deleted.instance_removed:
                raise AssertionError("Synthetic journey reset did not remove its demo instance")
            if retained_journey_export.read_bytes() != journey_export_content:
                raise AssertionError("Completed synthetic journey export was not retained")

            root = outer / f"{tag}-workspace"
            instance_service = create_instance_service(root)
            instance_service.initialize(actor="local_user")
            doctor = instance_service.doctor()
            doctor_statuses = {check.name: check.status for check in doctor.checks}
            if any(
                doctor_statuses.get(name) != "ok"
                for name in ("workspace", "layout", "evidence_storage")
            ):
                raise AssertionError("Initialized workspace did not pass required storage checks")
            document_path = outer / f"{tag}-source.txt"
            document_path.write_text(
                "Synthetic local source for populated export proof.\n", encoding="utf-8"
            )
            evidence_service = EvidenceService(EvidenceVault(root), extract_candidate_document)
            require_evidence_result(evidence_service.record_consent(accepted=True))
            imported = require_evidence_result(evidence_service.import_document(document_path))
            export_destination = outer / f"{tag}-user-export"
            data_service = create_instance_data_service(root)
            exported = data_service.export_user_data(
                str(export_destination), offline=True, actor="local_user"
            )
            exported_path = export_destination / exported.relative_file
            exported_content = exported_path.read_bytes()
            if exported.sha256 != hashlib.sha256(exported_content).hexdigest():
                raise AssertionError("Real user-data export checksum did not verify")
            export_payload = json.loads(exported_content)
            if export_payload["export_manifest"]["external_actions"] is not False:
                raise AssertionError("Real user-data export crossed the external-action boundary")
            exported_counts = dict(exported.record_counts)
            exported_data = cast(dict[str, object], export_payload["data"])
            exported_vault = cast(dict[str, object], exported_data["evidence_vault"])
            exported_sources = cast(list[dict[str, object]], exported_vault["sources"])
            if (
                exported_counts["consent_receipts"] != 1
                or exported_counts["sources"] != 1
                or len(exported_sources) != 1
            ):
                raise AssertionError("Populated initialized workspace data was not exported")
            exported_source_metadata = cast(dict[str, object], exported_sources[0]["metadata"])
            if exported_source_metadata["source_id"] != imported.source_id:
                raise AssertionError("Populated initialized workspace source identity changed")
            delete_preview = data_service.delete()
            deleted = data_service.delete(
                confirmation_fingerprint=delete_preview.confirmation_fingerprint,
                offline=True,
                actor="local_user",
            )
            if root.exists() or not deleted.instance_removed:
                raise AssertionError("Real workspace deletion did not remove the selected instance")
            if not export_destination.is_dir():
                raise AssertionError(
                    "User-data export was not retained outside the deleted instance"
                )
            if not document_path.is_file():
                raise AssertionError("User-managed source outside the workspace was removed")
            proofs.append(
                {
                    "tag": tag,
                    "recommendation": dossier.recommendation,
                    "decision": dossier.recommendation,
                    "artifacts": len(artifacts),
                    "doctor": doctor_statuses,
                    "doctor_ready": doctor.ready,
                    "exported": True,
                    "export_stage": "populated_workspace",
                    "exported_sources": exported_counts["sources"],
                    "journey_exported": True,
                    "journey_export_stage": "completed_journey",
                    "journey_export_sha256": hashlib.sha256(journey_export_content).hexdigest(),
                    "journey_reset": True,
                    "journey_inventory_before_reset": len(journey_inventory),
                    "journey_removed": journey_deleted.removed_count,
                    "deleted": True,
                    "inventory_before_delete": len(delete_preview.inventory.entries),
                    # Keep the old result key for consumers of the pre-#151 smoke contract;
                    # the value now describes the real initialized-workspace deletion above.
                    "inventory_before_reset": len(delete_preview.inventory.entries),
                    "removed": deleted.removed_entries,
                }
            )
    return {
        "schema_version": 1,
        "instances": 2,
        "journeys_completed": len(proofs),
        "model_calls": False,
        "external_actions": False,
        "instances_removed": all(
            item["removed"] == item["inventory_before_delete"]
            and item["removed"] > 0
            and item["journey_removed"] == item["journey_inventory_before_reset"]
            and item["journey_removed"] > 0
            for item in proofs
        ),
        "proofs": proofs,
    }


def full_main() -> int:
    report = run_full_smoke()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["instances_removed"] else 1


def main() -> int:
    print(json.dumps(run_smoke(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "JourneyState",
    "ProductJourney",
    "full_main",
    "main",
    "run_full_smoke",
    "run_smoke",
]

"""Local supplied-opportunity evaluation and the existing hash-linked decision store."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from career_os.application.companion import companion_digest
from career_os.application.inventory import InventoryEntry, inventory_page
from career_os.application.opportunities import (
    _DECISIONS,
    MAX_ROLE_REQUEST_BYTES,
    OpportunityService,
    SuppliedDecisionInput,
    SuppliedDecisionReceipt,
    SuppliedOpportunityState,
    SuppliedRoleCard,
    SuppliedRolePreflight,
    SuppliedRoleProposal,
    SuppliedRoleRequest,
    SuppliedRoleResult,
)
from career_os.evidence_vault import EvidenceRevision, EvidenceVault
from career_os.file_transactions import _fsync_directory
from career_os.infrastructure.evidence import read_private_json as read_private_json
from career_os.role_packs import (
    CandidateOverlay,
    PostingProfile,
    PostingRequirement,
    RoleEvaluation,
    bundled_role_pack_registry,
)

MAX_OPPORTUNITY_STATE_BYTES = 8 * 1024 * 1024
SUPPLIED_DOSSIER_VERSION = 2
_DECISION_EVENT_FIELDS = {
    "decision_id",
    "sequence",
    "previous_decision_id",
    "opportunity_id",
    "source_hash",
    "evaluation_fingerprint",
    "decision",
    "reason_code",
    "note",
    "confirmed_by",
    "recorded_at",
    "application_event_created",
    "external_event_confirmed",
    "external_actions",
}
_DECISION_LOG_FIELDS = {"schema_version", "opportunity_id", "events"}


def _validate_evidence_routes(
    request: SuppliedRoleRequest | SuppliedRoleProposal, vault: EvidenceVault
) -> tuple[EvidenceRevision, ...]:
    revisions: dict[str, EvidenceRevision] = {}
    for route_name, routes in (
        ("direct", request.direct_evidence),
        ("transferable", request.transferable_evidence),
        ("gaps", request.gap_evidence),
    ):
        for evidence_ids in routes.values():
            for evidence_id in evidence_ids:
                if evidence_id not in revisions:
                    revisions[evidence_id] = vault.latest(evidence_id)
                revision = revisions[evidence_id]
                if route_name == "gaps" and revision.kind != "gap":
                    raise ValueError("Only gap evidence can disprove a supplied requirement")
                if route_name != "gaps" and revision.kind in {
                    "gap",
                    "forbidden_claim",
                    "correction_note",
                }:
                    raise ValueError("Private control evidence cannot support a requirement")
    return tuple(revisions[evidence_id] for evidence_id in sorted(revisions))


def _constraint_assessments(
    request: SuppliedRoleRequest | SuppliedRoleProposal,
) -> tuple[dict[str, object], ...]:
    constraints = request.constraints
    location_matches = request.location.casefold() == constraints.preferred_location.casefold()
    preference = constraints.preferred_work_model
    posting_model = request.work_model
    if "unknown" in (preference, posting_model) or (
        posting_model == "flexible" and preference != "flexible"
    ):
        model_outcome = "unknown"
    elif preference == "flexible" or preference == posting_model:
        model_outcome = "matched"
    else:
        model_outcome = "conflict"
    return (
        {
            "constraint": "preferred_location",
            "candidate_value": constraints.preferred_location,
            "posting_value": request.location,
            "outcome": "matched" if location_matches else "unknown",
            "explanation": (
                "The supplied location matches the preference."
                if location_matches
                else "Different location labels need confirmation; geographic compatibility is not inferred."
            ),
        },
        {
            "constraint": "preferred_work_model",
            "candidate_value": preference,
            "posting_value": posting_model,
            "outcome": model_outcome,
            "explanation": {
                "matched": "The supplied work model fits the stated preference.",
                "conflict": "The supplied work model conflicts with the stated preference.",
                "unknown": "The work-model terms need confirmation.",
            }[model_outcome],
        },
        {
            "constraint": "maximum_travel_percent",
            "candidate_value": constraints.maximum_travel_percent,
            "posting_value": None,
            "outcome": "unknown",
            "explanation": "The supplied structure has no travel percentage; ask the employer.",
        },
        {
            "constraint": "currency",
            "candidate_value": constraints.currency,
            "posting_value": None,
            "outcome": "unknown",
            "explanation": "The supplied structure has no compensation currency; ask the employer.",
        },
    )


def evaluate_supplied_role(
    request: SuppliedRoleRequest | SuppliedRoleProposal,
    *,
    source_hash: str,
    vault: EvidenceVault,
) -> tuple[RoleEvaluation, dict[str, object]]:
    readiness = vault.readiness()
    if not readiness.ready:
        raise ValueError("Evidence review is not ready: " + "; ".join(readiness.blockers))
    mapped_evidence = _validate_evidence_routes(request, vault)
    registry = bundled_role_pack_registry()
    selection = registry.select(request.role_signals)
    posting = PostingProfile(
        opportunity_id=request.opportunity_id,
        posting_hash=source_hash,
        source_id=f"user-supplied-{source_hash[:24]}",
        official_source_verified=False,
        signals=request.role_signals,
        requirements=tuple(
            PostingRequirement(
                requirement_id=item.requirement_id,
                level=item.level,
                statement=item.statement,
                posting_evidence_id=f"posting-{source_hash[:12]}-{item.requirement_id}",
            )
            for item in request.requirements
        ),
    )
    approved_ids = {record.evidence_id for record in vault.enumerate(statuses=("approved",))}
    constraint_assessments = _constraint_assessments(request)
    overlay = CandidateOverlay(
        positive_evidence=request.direct_evidence,
        negative_evidence=request.gap_evidence,
        transferable_evidence=request.transferable_evidence,
        constraints=request.constraints.to_dict(),
        context_fingerprints={
            **vault.context_fingerprints(),
            "supplied_workflow": hashlib.sha256(
                _canonical_json(
                    {
                        "version": SUPPLIED_DOSSIER_VERSION,
                        "constraint_assessments": constraint_assessments,
                    }
                )
            ).hexdigest(),
        },
    )
    evaluation = registry.evaluate(
        selection,
        posting,
        overlay,
        approved_evidence_ids=approved_ids,
    )
    constraint_conflict = any(item["outcome"] == "conflict" for item in constraint_assessments)
    recommendation = evaluation.recommendation
    if constraint_conflict and recommendation != "skip":
        recommendation = "hold"
    elif recommendation == "pursue" and any(
        item["outcome"] == "unknown" for item in constraint_assessments
    ):
        recommendation = "conditional"
    evaluation = replace(
        evaluation,
        recommendation=recommendation,
        confidence=min(evaluation.confidence, 0.5),
        explanation=(
            *evaluation.explanation,
            "User-supplied posting structure is lower authority, so confidence is capped at 0.5.",
            "Practical constraints conflict; resolve them before proceeding."
            if constraint_conflict
            else "Unconfirmed practical constraints remain visible for follow-up.",
        ),
    )
    if evaluation.application_ready:
        raise AssertionError("A user-supplied posting cannot authorize application readiness")
    dossier = {
        "schema_version": SUPPLIED_DOSSIER_VERSION,
        "authority": "user_supplied",
        "official_source_verified": False,
        "opportunity_id": request.opportunity_id,
        "company": request.company,
        "title": request.title,
        "location": request.location,
        "work_model": request.work_model,
        "source_url": request.source_url,
        "source_id": posting.source_id,
        "source_hash": source_hash,
        "mapping_confirmed_by": None,
        "evidence_routes": {
            "direct": request.direct_evidence,
            "transferable": request.transferable_evidence,
            "gaps": request.gap_evidence,
        },
        "mapped_evidence": [
            {
                "evidence_id": item.evidence_id,
                "revision": item.revision,
                "content_hash": item.content_hash,
                "statement": item.statement,
                "kind": item.kind,
                "status": item.status,
                "source_id": item.source_id,
                "source_hash": item.source_hash,
                "source_start_line": item.source_start_line,
                "source_end_line": item.source_end_line,
            }
            for item in mapped_evidence
        ],
        "constraints": request.constraints.to_dict(),
        "constraint_assessments": list(constraint_assessments),
        "practical_fit": "conflict" if constraint_conflict else "needs_confirmation",
        "role_evaluation": asdict(evaluation),
        "requires_user_confirmation": True,
        "decision_recorded": False,
        "application_created": False,
        "model_calls": False,
        "external_actions": False,
    }
    return evaluation, dossier


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _atomic_write(path: Path, content: bytes, *, instance_root: Path) -> None:
    if not 1 <= len(content) <= MAX_OPPORTUNITY_STATE_BYTES:
        raise ValueError(
            "Opportunity state exceeds the supported byte limit; existing state is unchanged"
        )
    if not _inside(path, instance_root):
        raise ValueError("Opportunity state escaped the private instance")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _inside(path, instance_root):
        raise ValueError("Opportunity state parent escaped the private instance")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Persist both the replacement and any newly created parent entries.
        directory = path.parent.resolve(strict=False)
        root = instance_root.resolve(strict=False)
        while True:
            _fsync_directory(directory)
            if directory == root:
                break
            directory = directory.parent
    finally:
        temporary.unlink(missing_ok=True)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _opportunity_directory(instance_root: Path, opportunity_id: str) -> Path:
    root = instance_root.expanduser().resolve(strict=False)
    directory = root / "data" / "runtime" / "opportunities" / opportunity_id
    if not _inside(directory, root):
        raise ValueError("Opportunity state escaped the private instance")
    return directory


def _current_supplied_dossier(
    instance_root: Path,
    opportunity_id: str,
    vault: EvidenceVault,
) -> tuple[RoleEvaluation, dict[str, object]]:
    directory = _opportunity_directory(instance_root, opportunity_id)
    request_path = directory / "supplied-request.json"
    dossier_path = directory / "decision-dossier.json"
    try:
        request_payload, source_content = read_private_json(
            instance_root,
            request_path,
            label="Persisted supplied role request",
            maximum_bytes=MAX_ROLE_REQUEST_BYTES,
        )
    except FileNotFoundError as exc:
        raise ValueError("Supplied opportunity request is missing") from exc
    request = SuppliedRoleRequest.from_dict(request_payload)
    if request.opportunity_id != opportunity_id:
        raise ValueError("Persisted supplied role request identity does not match its path")
    source_hash = hashlib.sha256(source_content).hexdigest()
    evaluation, expected = evaluate_supplied_role(request, source_hash=source_hash, vault=vault)
    expected["mapping_confirmed_by"] = "local_user"
    expected["dossier_relative_path"] = dossier_path.relative_to(
        instance_root.expanduser().resolve(strict=False)
    ).as_posix()
    try:
        stored, _ = read_private_json(
            instance_root,
            dossier_path,
            label="Persisted supplied role dossier",
            maximum_bytes=MAX_OPPORTUNITY_STATE_BYTES,
        )
    except FileNotFoundError as exc:
        raise ValueError("Supplied opportunity dossier is missing") from exc
    if _canonical_json(stored) != _canonical_json(expected):
        raise ValueError("Persisted supplied role dossier is stale or has been modified")
    return evaluation, expected


def _decision_id(event: dict[str, object]) -> str:
    identity = {key: value for key, value in event.items() if key != "decision_id"}
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


def _validate_recorded_at(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError("Decision recorded_at must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Decision recorded_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("Decision recorded_at must include a timezone")
    return value


def _validate_decision_event(
    payload: object,
    *,
    opportunity_id: str,
    sequence: int,
    previous_decision_id: str | None,
) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != _DECISION_EVENT_FIELDS:
        raise ValueError("Decision ledger event must contain exactly the documented fields")
    decision = SuppliedDecisionInput.from_dict(
        {
            "schema_version": 1,
            "opportunity_id": payload.get("opportunity_id"),
            "source_hash": payload.get("source_hash"),
            "evaluation_fingerprint": payload.get("evaluation_fingerprint"),
            "decision": payload.get("decision"),
            "reason_code": payload.get("reason_code"),
            "note": payload.get("note"),
            "confirmed_by": payload.get("confirmed_by"),
        }
    )
    if decision.opportunity_id != opportunity_id:
        raise ValueError("Decision ledger event belongs to a different opportunity")
    recorded_sequence = payload.get("sequence")
    if type(recorded_sequence) is not int or recorded_sequence != sequence:
        raise ValueError("Decision ledger sequence is invalid")
    if payload.get("previous_decision_id") != previous_decision_id:
        raise ValueError("Decision ledger predecessor link is invalid")
    if payload.get("note") != decision.note:
        raise ValueError("Decision ledger note is not canonical")
    _validate_recorded_at(payload.get("recorded_at"))
    if any(
        payload.get(field) is not False
        for field in (
            "application_event_created",
            "external_event_confirmed",
            "external_actions",
        )
    ):
        raise ValueError("Decision ledger cannot record application or external action authority")
    decision_id = payload.get("decision_id")
    if not isinstance(decision_id, str) or not re.fullmatch(r"[0-9a-f]{64}", decision_id):
        raise ValueError("Decision ledger id must be a lowercase SHA-256 value")
    if decision_id != _decision_id(payload):
        raise ValueError("Decision ledger event hash is invalid")
    return dict(payload)


def _load_decision_events(
    instance_root: Path,
    opportunity_id: str,
) -> tuple[Path, list[dict[str, object]]]:
    path = _opportunity_directory(instance_root, opportunity_id) / "decisions.json"
    if not path.exists():
        return path, []
    payload, _ = read_private_json(
        instance_root,
        path,
        label="Supplied role decision ledger",
        maximum_bytes=MAX_OPPORTUNITY_STATE_BYTES,
    )
    if not isinstance(payload, dict) or set(payload) != _DECISION_LOG_FIELDS:
        raise ValueError("Decision ledger must contain exactly the documented fields")
    schema_version = payload.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != 1
        or payload.get("opportunity_id") != opportunity_id
    ):
        raise ValueError("Decision ledger identity or schema is invalid")
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        raise ValueError("Decision ledger events must be a list")
    events: list[dict[str, object]] = []
    previous: str | None = None
    for sequence, raw_event in enumerate(raw_events, start=1):
        event = _validate_decision_event(
            raw_event,
            opportunity_id=opportunity_id,
            sequence=sequence,
            previous_decision_id=previous,
        )
        events.append(event)
        previous = cast(str, event["decision_id"])
    return path, events


def _same_decision(event: dict[str, object], decision: SuppliedDecisionInput) -> bool:
    return (
        all(
            event.get(field) == getattr(decision, field)
            for field in (
                "opportunity_id",
                "source_hash",
                "evaluation_fingerprint",
                "decision",
                "reason_code",
                "note",
            )
        )
        and event.get("confirmed_by") == "local_user"
    )


def _append_supplied_decision(
    instance_root: Path,
    decision: SuppliedDecisionInput,
    *,
    current_recommendation: str,
) -> tuple[dict[str, object], bool, int]:
    if current_recommendation not in _DECISIONS:
        raise ValueError("Current recommendation is invalid")
    if decision.reason_code == "accept_recommendation" and (
        decision.decision != current_recommendation
    ):
        raise ValueError("accept_recommendation must match the current recommendation")
    if decision.decision != current_recommendation and decision.note is None:
        raise ValueError("A decision that differs from the recommendation requires a note")
    path, events = _load_decision_events(instance_root, decision.opportunity_id)
    if events and _same_decision(events[-1], decision):
        return events[-1], False, len(events)
    previous = cast(str, events[-1]["decision_id"]) if events else None
    event: dict[str, object] = {
        "decision_id": "",
        "sequence": len(events) + 1,
        "previous_decision_id": previous,
        "opportunity_id": decision.opportunity_id,
        "source_hash": decision.source_hash,
        "evaluation_fingerprint": decision.evaluation_fingerprint,
        "decision": decision.decision,
        "reason_code": decision.reason_code,
        "note": decision.note,
        "confirmed_by": "local_user",
        "recorded_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "application_event_created": False,
        "external_event_confirmed": False,
        "external_actions": False,
    }
    event["decision_id"] = _decision_id(event)
    events.append(event)
    ledger = {
        "schema_version": 1,
        "opportunity_id": decision.opportunity_id,
        "events": events,
    }
    _atomic_write(
        path,
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        instance_root=instance_root.expanduser().resolve(strict=False),
    )
    return event, True, len(events)


def record_supplied_decision(
    instance_root: Path,
    decision: SuppliedDecisionInput,
    *,
    actor: str,
    expected_basis: str | None = None,
) -> tuple[dict[str, object], bool, int, str]:
    if actor != "local_user":
        raise PermissionError(
            "Supplied decision requires confirmation from the local-user boundary"
        )
    decision = SuppliedDecisionInput.from_dict(
        {"schema_version": 1, "confirmed_by": actor, **asdict(decision)}
    )
    vault = EvidenceVault(instance_root)
    if not vault.has_consent():
        raise PermissionError("Supplied role decisions require local-processing consent")
    # The same lock protects evidence revisions, dossier writers, and ledger appends.
    with vault.transaction():
        if not vault.has_consent():
            raise PermissionError("Supplied role decisions require local-processing consent")
        evaluation, dossier = _current_supplied_dossier(
            instance_root, decision.opportunity_id, vault
        )
        if decision.source_hash != dossier["source_hash"]:
            raise ValueError("Supplied decision source_hash is stale")
        if decision.evaluation_fingerprint != evaluation.evaluation_fingerprint:
            raise ValueError("Supplied decision evaluation_fingerprint is stale")
        if expected_basis is not None:
            _, events = _load_decision_events(instance_root, decision.opportunity_id)
            latest = events[-1] if events else None
            state = SuppliedOpportunityState(
                SuppliedRoleResult(
                    decision.opportunity_id,
                    decision.source_hash,
                    evaluation.evaluation_fingerprint,
                    dossier,
                ),
                latest,
                latest is not None
                and latest["source_hash"] == decision.source_hash
                and latest["evaluation_fingerprint"] == evaluation.evaluation_fingerprint,
            )
            if companion_digest(state.to_dict()) != expected_basis:
                raise PermissionError("Decision history changed after local review")
        event, created, count = _append_supplied_decision(
            instance_root, decision, current_recommendation=evaluation.recommendation
        )
        return event, created, count, evaluation.recommendation


def _persist_supplied_dossier(
    instance_root: Path,
    request: SuppliedRoleRequest,
    source_content: bytes,
    dossier: dict[str, object],
) -> str:
    root = instance_root.expanduser().resolve(strict=False)
    directory = root / "data" / "runtime" / "opportunities" / request.opportunity_id
    source_path = directory / "supplied-request.json"
    dossier_path = directory / "decision-dossier.json"
    if not _inside(source_path, root) or source_path.is_symlink():
        raise ValueError("Supplied opportunity source escaped the private instance")
    if source_path.exists() and source_path.read_bytes() != source_content:
        raise ValueError("Opportunity identity already belongs to a different supplied request")
    if not source_path.exists():
        _atomic_write(source_path, source_content, instance_root=root)
    rendered = json.dumps(dossier, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    _atomic_write(dossier_path, rendered + b"\n", instance_root=root)
    return dossier_path.relative_to(root).as_posix()


class LocalOpportunityRepository:
    """One instance's original evaluator, dossier and decision writer."""

    def __init__(
        self,
        instance_root: str | Path,
        *,
        vault: EvidenceVault | None = None,
        read_only_resolution: bool = False,
    ) -> None:
        self._vault = vault if vault is not None else EvidenceVault(instance_root)
        if self._vault.instance_root != Path(instance_root).expanduser().resolve(strict=False):
            raise ValueError("Opportunity reader vault belongs to a different instance")
        self.instance_root = self._vault.instance_root
        self._read_only_resolution = read_only_resolution

    def has_consent(self) -> bool:
        return self._vault.has_consent()

    def list_supplied(self, *, limit: int, cursor: str | None):
        # Same capacity as Focus. No discovery or projection writes from this API.
        from career_os.infrastructure.focus import _existing_directory

        namespace = self.instance_root / "data/runtime/opportunities"
        entries = []
        with self._vault.read_transaction():
            if not self._vault.has_consent():
                raise PermissionError("Local processing consent is required")
            directories = []
            if _existing_directory(namespace):
                for directory in namespace.iterdir():
                    directories.append(directory)
                    if len(directories) > 100:
                        raise ValueError("Supplied inventory exceeds 100 opportunities")
            reader = LocalOpportunityRepository(
                self.instance_root, vault=self._vault, read_only_resolution=True
            )
            for directory in sorted(directories):
                identity = directory.name
                try:
                    from career_os.application.opportunities import _identifier

                    _identifier(identity, "opportunity_id")
                    if not _existing_directory(directory):
                        raise ValueError("Opportunity directory disappeared")
                    state = reader.resolve_supplied(identity)
                    dossier = state.dossier.dossier
                    decision = state.latest_decision
                    card = SuppliedRoleCard(
                        str(dossier["company"]),
                        str(dossier["title"]),
                        str(dossier["location"]),
                        str(dossier["work_model"]),
                        str(dossier["role_evaluation"]["recommendation"]),
                        state.dossier.evaluation_fingerprint,
                        state.dossier.source_hash,
                        str(decision["decision"]) if decision else None,
                        state.decision_current,
                        str(decision["decision_id"]) if decision else None,
                    )
                    entries.append(InventoryEntry(identity, card))
                except (ValueError, OSError):
                    entries.append(InventoryEntry(identity, None, "invalid_or_stale"))
        return inventory_page(
            tuple(entries), scope=f"supplied:{self.instance_root}", limit=limit, cursor=cursor
        )

    def preflight_supplied(
        self, proposal: SuppliedRoleProposal, source_content: bytes
    ) -> SuppliedRolePreflight:
        with self._vault.read_transaction():
            if not self._vault.has_consent():
                raise PermissionError("Role preflight requires local-processing consent")
            digest = hashlib.sha256(source_content).hexdigest()
            evaluation, dossier = evaluate_supplied_role(
                proposal, source_hash=digest, vault=self._vault
            )
            return SuppliedRolePreflight(
                proposal, digest, evaluation.evaluation_fingerprint, dossier
            )

    def evaluate_supplied(
        self,
        request: SuppliedRoleRequest,
        source_content: bytes,
        *,
        confirm_fingerprint: str | None,
    ) -> SuppliedRoleResult:
        with self._vault.transaction():
            if not self._vault.has_consent():
                raise PermissionError("Supplied role evaluation requires local-processing consent")
            return self._evaluate_supplied_in_transaction(
                request, source_content, confirm_fingerprint=confirm_fingerprint
            )

    def _evaluate_supplied_in_transaction(
        self,
        request: SuppliedRoleRequest,
        source_content: bytes,
        *,
        confirm_fingerprint: str | None,
    ) -> SuppliedRoleResult:
        source_hash = hashlib.sha256(source_content).hexdigest()
        evaluation, dossier = evaluate_supplied_role(
            request, source_hash=source_hash, vault=self._vault
        )
        if confirm_fingerprint is not None:
            if confirm_fingerprint != evaluation.evaluation_fingerprint:
                raise PermissionError("Mapping confirmation is stale; review the current preview")
            dossier["mapping_confirmed_by"] = "local_user"
            dossier["dossier_relative_path"] = (
                f"data/runtime/opportunities/{request.opportunity_id}/decision-dossier.json"
            )
            persisted_path = _persist_supplied_dossier(
                self.instance_root, request, source_content, dossier
            )
            if persisted_path != dossier["dossier_relative_path"]:
                raise AssertionError("Persisted dossier path changed unexpectedly")
        return SuppliedRoleResult(
            request.opportunity_id, source_hash, evaluation.evaluation_fingerprint, dossier
        )

    def confirm_proposal(
        self,
        proposal: SuppliedRoleProposal,
        source_content: bytes,
        *,
        preflight_fingerprint: str,
    ) -> SuppliedRoleResult:
        # Hold the same vault write transaction from checking the reviewed
        # unconfirmed basis through conversion and the existing dossier writer.
        # A second fresh preview must never silently replace the approved basis.
        with self._vault.existing_transaction():
            if not self._vault.has_consent():
                raise PermissionError("Mapping confirmation requires local-processing consent")
            evaluation, _ = evaluate_supplied_role(
                proposal,
                source_hash=hashlib.sha256(source_content).hexdigest(),
                vault=self._vault,
            )
            if evaluation.evaluation_fingerprint != preflight_fingerprint:
                raise PermissionError("Mapping confirmation is stale; review the current preview")
            request = SuppliedRoleRequest(
                **{field.name: getattr(proposal, field.name) for field in fields(proposal)}
            )
            confirmed_content = json.dumps(
                request.to_dict(),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            confirmed_evaluation, _ = evaluate_supplied_role(
                request,
                source_hash=hashlib.sha256(confirmed_content).hexdigest(),
                vault=self._vault,
            )
            return self._evaluate_supplied_in_transaction(
                request,
                confirmed_content,
                confirm_fingerprint=confirmed_evaluation.evaluation_fingerprint,
            )

    def resolve_supplied(self, opportunity_id: str) -> SuppliedOpportunityState:
        # Existing callers retain migration behavior; Focus explicitly opts into pure reads.
        transaction = (
            self._vault.read_transaction if self._read_only_resolution else self._vault.transaction
        )
        with transaction():
            if not self._vault.has_consent():
                raise PermissionError("Supplied role resolution requires local-processing consent")
            evaluation, dossier = _current_supplied_dossier(
                self.instance_root, opportunity_id, self._vault
            )
            _, events = _load_decision_events(self.instance_root, opportunity_id)
            latest = events[-1] if events else None
            source_hash = cast(str, dossier["source_hash"])
            return SuppliedOpportunityState(
                SuppliedRoleResult(
                    opportunity_id, source_hash, evaluation.evaluation_fingerprint, dossier
                ),
                latest,
                latest is not None
                and latest["source_hash"] == source_hash
                and latest["evaluation_fingerprint"] == evaluation.evaluation_fingerprint,
            )

    def record_decision(
        self, decision: SuppliedDecisionInput, *, expected_basis: str | None = None
    ) -> SuppliedDecisionReceipt:
        event, created, count, recommendation = record_supplied_decision(
            self.instance_root, decision, actor="local_user", expected_basis=expected_basis
        )
        return SuppliedDecisionReceipt(
            opportunity_id=decision.opportunity_id,
            decision_id=cast(str, event["decision_id"]),
            sequence=cast(int, event["sequence"]),
            decision=cast(str, event["decision"]),
            current_recommendation=recommendation,
            record_created=created,
            decision_count=count,
        )


def create_opportunity_service(instance_root: str | Path) -> OpportunityService:
    return OpportunityService(LocalOpportunityRepository(instance_root))

"""Fictional-only companion over existing public authorities and their one vault."""

from __future__ import annotations

import hashlib
import secrets
import time
from copy import deepcopy
from dataclasses import asdict, fields
from pathlib import Path

from career_os.application.companion import (
    MAX_COMPANION_ARCHIVE_RECORDS,
    PURPOSES,
    CompanionError,
    CompanionRef,
    CompanionService,
    bounded_companion_json,
    companion_digest,
    companion_json,
    exact_fields,
    validate_companion_state,
)
from career_os.application.evidence import (
    MAX_PROPOSAL_BYTES,
    EvidenceProposalInput,
    EvidenceService,
    require_evidence_result,
)
from career_os.application.interviews import (
    MAX_INTERVIEW_REQUEST_BYTES,
    InterviewRequest,
    require_interview_result,
)
from career_os.application.offers import OfferComparisonRequest, require_offer_result
from career_os.application.opportunities import (
    OpportunityService,
    SuppliedDecisionInput,
    SuppliedRoleProposal,
    SuppliedRoleRequest,
    _identifier,
    require_opportunity_result,
)
from career_os.application.workspaces import WorkspaceRef, require_workspace_result
from career_os.document_intake import extract_candidate_document
from career_os.evidence_vault import EvidenceVault
from career_os.infrastructure.evidence import read_private_json
from career_os.infrastructure.interviews import create_interview_service
from career_os.infrastructure.offers import create_offer_service
from career_os.infrastructure.opportunities import (
    LocalOpportunityRepository,
    _load_decision_events,
    _same_decision,
    create_opportunity_service,
)
from career_os.infrastructure.recovery import _root
from career_os.infrastructure.workspaces import create_workspace_service

# Only the existing reviewed fictional persona's source is admissible. This is an
# instance boundary, not a classifier for arbitrary pasted text or a real-data switch.
FICTIONAL_SOURCE_SHA256 = "b5380844a7009f1051d18e617f2fc5cc2454a1157bcdcef538285420e359985d"
_FIELDS = {
    "evidence": {
        "evidence_id",
        "revision",
        "kind",
        "statement",
        "details",
        "source_id",
        "source_hash",
        "source_start_line",
        "source_end_line",
        "ownership",
        "confidence",
        "approximation",
        "publication_boundary",
        "status",
        "content_hash",
        "created_at",
        "supersedes_revision",
        "metadata",
        "extracted_hash",
    },
    "source_span": {
        "source_id",
        "source_hash",
        "extracted_hash",
        "start_line",
        "end_line",
        "lines",
        "locations",
    },
    "opportunity": {
        "opportunity_id",
        "source_hash",
        "evaluation_fingerprint",
        "company",
        "title",
        "location",
        "work_model",
        "source_url",
        "mapping_confirmed_by",
        "evidence_routes",
        "mapped_evidence",
        "constraints",
        "constraint_assessments",
        "role_evaluation",
        "latest_decision",
        "decision_current",
        "authority",
        "official_source_verified",
        "practical_fit",
    },
    "interview": {"summary", "plan", "documents"},
    "offer": {
        "request",
        "expected",
        "downside",
        "limitations",
        "confirmation_fingerprint",
        "authority",
        "downside_assumption",
    },
    "pending": {
        "operation",
        "digest",
        "request",
        "basis",
        "created_at",
        "expires_at",
        "state",
        "receipt",
        "restored",
        "retired_at",
    },
}


class LocalCompanionRepository:
    """Selected instance and recipient are trusted composition inputs, never tool arguments."""

    def __init__(self, instance_root: str | Path) -> None:
        self.root = _root(instance_root)
        self.vault = EvidenceVault(self.root)
        self.evidence = EvidenceService(self.vault, extract_candidate_document)
        self.instance_hash = hashlib.sha256(str(self.root).encode()).hexdigest()
        self.opportunities = OpportunityService(
            LocalOpportunityRepository(self.root, vault=self.vault, read_only_resolution=True)
        )

    def _fictional(self) -> None:
        if not self.vault.has_consent():
            raise CompanionError("consent_required")
        sources = self.vault.sources()
        if not sources or any(source.source_hash != FICTIONAL_SOURCE_SHA256 for source in sources):
            raise CompanionError("unsupported_profile")

    def _state(self, *, active: bool = True) -> dict:
        self._fictional()
        raw = self.vault.companion_metadata()
        if raw is None:
            raise CompanionError("not_found_or_denied")
        state = validate_companion_state(raw, self.instance_hash)
        if active and not state["enabled"]:
            raise CompanionError("not_found_or_denied")
        return state

    def _save(self, state: dict) -> None:
        self.vault.companion_metadata(content=companion_json(state).decode())

    def _object(
        self,
        kind: str,
        identity: str,
        *,
        historical: bool = False,
        revision: str | None = None,
        state: dict | None = None,
    ) -> dict:
        if kind == "evidence":
            records = (
                self.vault.revisions(identity) if historical else (self.vault.latest(identity),)
            )
            for record in records:
                payload = asdict(record)
                if revision is None or companion_digest(payload) == revision:
                    return payload
            raise CompanionError("stale_basis")
        if kind == "source_span":
            parts = identity.split(":")
            if len(parts) != 3:
                raise CompanionError("invalid_input")
            source, start, end = parts
            # The source-span service owns size, range, content and provenance validation.
            return require_evidence_result(
                self.evidence.read_source_span(source, int(start), int(end))
            ).to_dict()
        if kind == "opportunity":
            if historical:
                raise CompanionError("unsupported_source")
            current = require_opportunity_result(self.opportunities.resolve_supplied(identity))
            dossier = current.dossier.to_dict()
            return {
                **{key: value for key, value in dossier.items() if key in _FIELDS[kind]},
                "evaluation_fingerprint": current.dossier.evaluation_fingerprint,
                "latest_decision": current.latest_decision,
                "decision_current": current.decision_current,
            }
        if kind == "interview":
            parts = identity.split(":")
            if len(parts) != 3:
                raise CompanionError("invalid_input")
            ref = WorkspaceRef("interview", parts[0], parts[2], parts[1])
            inspected = require_workspace_result(create_workspace_service(self.root).inspect(ref))
            if not historical and inspected.summary.current is not True:
                raise CompanionError("stale_basis")
            payload = inspected.to_dict()
            # Explicitly remove local relative paths, including nested summary metadata.
            payload["summary"].pop("relative_path", None)
            return {key: payload[key] for key in _FIELDS[kind] if key in payload}
        if kind == "offer":
            parts = identity.split(":")
            if len(parts) != 2:
                raise CompanionError("invalid_input")
            identifier = _identifier(parts[0], "comparison_id")
            CompanionRef("offer", identifier, parts[1])
            path = self.root / "data/runtime/offer-comparisons" / identifier / f"{parts[1]}.json"
            saved, _ = read_private_json(self.root, path, label="Offer comparison")
            if not isinstance(saved, dict):
                raise CompanionError("integrity_error")
            checked = require_offer_result(
                create_offer_service(self.root).compare(
                    OfferComparisonRequest.from_dict(saved.get("request"))
                )
            ).to_dict()
            if checked["confirmation_fingerprint"] != parts[1] or companion_digest(
                checked
            ) != companion_digest(saved):
                raise CompanionError("integrity_error")
            return {key: checked[key] for key in _FIELDS[kind]}
        if kind == "pending" and state is not None:
            archived = identity in state.get("archive", {})
            if archived and not historical:
                raise CompanionError("not_found_or_denied")
            record = (
                state.get("archive", {}).get(identity)
                if archived
                else state["pending"].get(identity)
            )
            if record is None:
                raise CompanionError("not_found_or_denied")
            return {
                **{key: record[key] for key in _FIELDS[kind] if key in record},
                "state": self._effective_state(record, int(time.time())),
                **({"retired_at": record["retirement"]["at"]} if archived else {}),
            }
        raise CompanionError("not_found_or_denied")

    def _permission(
        self,
        state: dict,
        ref: CompanionRef,
        fields: tuple[str, ...],
        purpose: str,
        historical: bool = False,
    ) -> None:
        now = int(time.time())
        permitted = set()
        for grant in state["grants"].values():
            if (
                grant["ref"] == ref.to_dict()
                and grant["purpose"] == purpose
                and grant["recipient"] == "chatgpt"
                and grant["generation"] == state["generation"]
                and grant["historical"] == historical
                and not grant["revoked"]
                and grant["created_at"] <= now < grant["expires_at"]
            ):
                permitted.update(grant["fields"])
        if not fields or not set(fields) <= permitted or not set(fields) <= _FIELDS[ref.kind]:
            raise CompanionError("not_found_or_denied")

    def read_permitted(
        self, ref: CompanionRef, fields: tuple[str, ...], purpose: str, historical: bool
    ) -> dict:
        with self.vault.read_transaction():
            state = self._state()
            self._permission(state, ref, fields, purpose, historical)
            payload = self._object(
                ref.kind, ref.object_id, historical=historical, revision=ref.revision, state=state
            )
            if companion_digest(payload) != ref.revision:
                raise CompanionError("stale_basis")
            return {
                "ref": ref.to_dict(),
                "historical": historical,
                "data": {key: payload[key] for key in fields},
            }

    def list_permitted(self, kind: str, purpose: str, offset: int, limit: int) -> dict:
        with self.vault.read_transaction():
            state = self._state()
            unique = {}
            for grant in state["grants"].values():
                ref = CompanionRef.from_dict(grant["ref"])
                if ref.kind != kind or grant["purpose"] != purpose:
                    continue
                try:
                    self._permission(
                        state, ref, tuple(grant["fields"]), purpose, grant["historical"]
                    )
                    payload = self._object(
                        kind,
                        ref.object_id,
                        revision=ref.revision,
                        historical=grant["historical"],
                        state=state,
                    )
                    if companion_digest(payload) != ref.revision:
                        continue
                    item = {**ref.to_dict(), "historical": grant["historical"]}
                    unique[companion_digest(item)] = item
                except (CompanionError, PermissionError, ValueError, FileNotFoundError):
                    continue
            entries = sorted(unique.values(), key=lambda ref: (ref["object_id"], ref["revision"]))
            return {
                "items": entries[offset : offset + limit],
                "next_offset": offset + limit if len(entries) > offset + limit else None,
            }

    def _preview(self, operation: str, request: dict) -> tuple[str, dict]:
        if operation == "assessment":
            preview = require_opportunity_result(
                self.opportunities.preflight_supplied(companion_json(request))
            )
            return preview.evaluation_fingerprint, preview.to_dict()
        if operation == "decision":
            args = exact_fields(
                request,
                {
                    "opportunity_id",
                    "source_hash",
                    "evaluation_fingerprint",
                    "decision",
                    "reason_code",
                    "note",
                },
            )
            # This validates a local choice, not actor authority or a confirmation.
            decision = SuppliedDecisionInput.from_choice(args)
            current = require_opportunity_result(
                self.opportunities.resolve_supplied(decision.opportunity_id)
            )
            if (
                decision.source_hash != current.dossier.source_hash
                or decision.evaluation_fingerprint != current.dossier.evaluation_fingerprint
            ):
                raise CompanionError("stale_basis")
            return companion_digest(current.to_dict()), args
        if operation == "interview":
            bounded_companion_json(companion_json(request), maximum=MAX_INTERVIEW_REQUEST_BYTES)
            parsed = InterviewRequest.from_dict(request)
            preview = require_interview_result(create_interview_service(self.root).plan(parsed))
            return preview.confirmation_fingerprint, preview.to_dict()
        if operation == "offer":
            preview = require_offer_result(
                create_offer_service(self.root).preflight(request)
            ).to_dict()
            return preview.get("confirmation_fingerprint", companion_digest(preview)), preview
        if operation == "evidence":
            if "actor" in request:
                raise CompanionError("invalid_input")
            parsed = EvidenceProposalInput.from_dict(
                bounded_companion_json(
                    companion_json({**request, "actor": "model"}), maximum=MAX_PROPOSAL_BYTES
                )
            )
            source = self.vault.source(parsed.source_id)
            if (
                source.source_hash != parsed.source_hash
                or source.extracted_hash != parsed.extracted_hash
            ):
                raise CompanionError("stale_basis")
            self.vault.source_lines(
                parsed.source_id, parsed.source_start_line, parsed.source_end_line
            )
            return companion_digest(
                {
                    "source_hash": source.source_hash,
                    "extracted_hash": source.extracted_hash,
                    "context": dict(self.vault.context_fingerprints()),
                }
            ), request
        raise CompanionError("unavailable_operation")

    def prepare(self, operation: str, request: dict, key: str) -> dict:
        digest = companion_digest(request)
        now = int(time.time())
        with self.vault.existing_transaction():
            state = self._state()
            for identity, record in (*state["pending"].items(), *state.get("archive", {}).items()):
                if record["operation"] == operation and record["key"] == key:
                    if now >= record["created_at"] + 30 * 86400:
                        raise CompanionError("conflict")
                    if record["digest"] != digest:
                        raise CompanionError("conflict")
                    return {"pending_id": identity, "state": self._effective_state(record, now)}
            self._require_inputs(state, operation, request)
            basis, _ = self._preview(operation, request)
            if len(state["pending"]) >= 256:
                raise CompanionError("input_too_large")
            identity = "pending-" + secrets.token_hex(16)
            state["pending"][identity] = {
                "operation": operation,
                "key": key,
                "digest": digest,
                "request": request,
                "basis": basis,
                "created_at": now,
                "expires_at": now + 86400,
                "state": "prepared",
                "receipt": None,
                "confirmation": None,
                "restored": False,
            }
            self._save(state)
            return {"pending_id": identity, "state": "prepared", "human_required": True}

    def _require_inputs(self, state: dict, operation: str, request: dict) -> None:
        def allowed(kind: str, identity: str, purpose: str):
            for grant in state["grants"].values():
                ref = CompanionRef.from_dict(grant["ref"])
                if ref.kind != kind or ref.object_id != identity or grant["purpose"] != purpose:
                    continue
                try:
                    self._permission(state, ref, tuple(grant["fields"]), purpose)
                    actual = self._object(kind, identity, revision=ref.revision, state=state)
                    if companion_digest(actual) == ref.revision:
                        return
                except CompanionError:
                    continue
            raise CompanionError("not_found_or_denied")

        if operation == "assessment":
            parsed = SuppliedRoleProposal.from_dict(request)
            for routes in (
                parsed.direct_evidence,
                parsed.transferable_evidence,
                parsed.gap_evidence,
            ):
                for evidence_ids in routes.values():
                    for identity in evidence_ids:
                        allowed("evidence", identity, "assess_role")
        elif operation in {"decision", "interview"}:
            purpose = "resume_work" if operation == "decision" else "prepare_interview"
            allowed("opportunity", request.get("opportunity_id"), purpose)
            if operation == "interview":
                for identity in InterviewRequest.from_dict(request).evidence_ids:
                    allowed("evidence", identity, purpose)
        elif operation == "evidence":
            identity = f"{request.get('source_id')}:{request.get('source_start_line')}:{request.get('source_end_line')}"
            allowed("source_span", identity, "evidence_review")

    @staticmethod
    def _effective_state(record: dict, now: int) -> str:
        return (
            "expired"
            if record["state"] == "prepared" and now >= record["expires_at"]
            else record["state"]
        )


def create_companion_service(instance_root: str | Path) -> CompanionService:
    return CompanionService(LocalCompanionRepository(instance_root))


class LocalCompanionReview:
    """Independent local controller; never exposed as an MCP tool or adapter argument.

    Same-user shell access can invoke this controller. It provides no real-data
    human isolation, so only the sealed fictional instance profile is implemented.
    """

    def __init__(self, repository: LocalCompanionRepository) -> None:
        self.repository = repository
        self._session = secrets.token_hex(32)

    def setup(self) -> None:
        repo = self.repository
        with repo.vault.existing_transaction():
            repo._fictional()
            existing = repo.vault.companion_metadata()
            if existing is not None:
                validate_companion_state(existing, repo.instance_hash)
                raise CompanionError("conflict")
            repo._save(
                {
                    "version": 1,
                    "instance_hash": repo.instance_hash,
                    "generation": secrets.token_hex(32),
                    "enabled": True,
                    "grants": {},
                    "pending": {},
                }
            )

    def inspect(
        self, kind: str, identity: str, *, historical: bool = False, revision: str | None = None
    ) -> tuple[CompanionRef, dict]:
        repo = self.repository
        with repo.vault.read_transaction():
            state = repo._state()
            payload = repo._object(
                kind, identity, historical=historical, revision=revision, state=state
            )
            return CompanionRef(kind, identity, companion_digest(payload)), payload

    def grant(
        self,
        ref: CompanionRef,
        fields: tuple[str, ...],
        purpose: str,
        *,
        lifetime_seconds: int = 86400,
        historical: bool = False,
    ) -> str:
        repo = self.repository
        if (
            purpose not in PURPOSES
            or not fields
            or len(set(fields)) != len(fields)
            or not set(fields) <= _FIELDS[ref.kind]
            or type(lifetime_seconds) is not int
            or not 1 <= lifetime_seconds <= 30 * 86400
            or type(historical) is not bool
        ):
            raise CompanionError("invalid_input")
        with repo.vault.existing_transaction():
            state = repo._state()
            payload = repo._object(
                ref.kind, ref.object_id, revision=ref.revision, historical=historical, state=state
            )
            if companion_digest(payload) != ref.revision:
                raise CompanionError("stale_basis")
            now = int(time.time())
            grant = {
                "ref": ref.to_dict(),
                "fields": sorted(fields),
                "purpose": purpose,
                "recipient": "chatgpt",
                "generation": state["generation"],
                "created_at": now,
                "expires_at": now + lifetime_seconds,
                "revoked": False,
                "historical": historical,
            }
            identity = companion_digest(grant)
            state["grants"][identity] = grant
            repo._save(state)
            return identity

    def revoke(self, grant_id: str) -> None:
        repo = self.repository
        with repo.vault.existing_transaction():
            state = repo._state()
            if grant_id not in state["grants"]:
                raise CompanionError("not_found_or_denied")
            state["grants"][grant_id]["revoked"] = True
            repo._save(state)

    def disconnect(self) -> None:
        repo = self.repository
        with repo.vault.existing_transaction():
            state = repo._state(active=False)
            state["enabled"] = False
            state["generation"] = secrets.token_hex(32)
            state["grants"] = {}
            for record in state["pending"].values():
                record["confirmation"] = None
            repo._save(state)

    def reconnect(self) -> None:
        repo = self.repository
        with repo.vault.existing_transaction():
            state = repo._state(active=False)
            if state["enabled"]:
                raise CompanionError("conflict")
            state["enabled"] = True
            state["generation"] = secrets.token_hex(32)
            state["grants"] = {}
            for record in state["pending"].values():
                record["confirmation"] = None
            repo._save(state)
        self._session = secrets.token_hex(32)

    def pending_review(self, identity: str) -> dict:
        repo = self.repository
        with repo.vault.read_transaction():
            state = repo._state()
            record = state["pending"].get(identity)
            if record is None:
                raise CompanionError("not_found_or_denied")
            basis, preview = repo._preview(record["operation"], record["request"])
            if basis != record["basis"]:
                raise CompanionError("stale_basis")
            return {
                "pending_id": identity,
                "input_digest": record["digest"],
                "basis": basis,
                "state": repo._effective_state(record, int(time.time())),
                "preview": preview,
            }

    def lifecycle_preview(self, operation: str, pending_ids: tuple[str, ...] = ()) -> dict:
        """Read-only local-human selection; no MCP capability or implicit upgrade."""
        repo = self.repository
        with repo.vault.read_transaction():
            return self._lifecycle_plan(repo._state(active=False), operation, pending_ids)

    def _lifecycle_plan(self, state: dict, operation: str, pending_ids: tuple[str, ...]) -> dict:
        if (
            operation not in {"upgrade", "cancel", "retire"}
            or not isinstance(pending_ids, tuple)
            or any(not isinstance(identity, str) for identity in pending_ids)
            or len(set(pending_ids)) != len(pending_ids)
            or len(pending_ids) > 256
            or (operation == "upgrade" and pending_ids)
            or (operation == "cancel" and len(pending_ids) != 1)
            or (operation == "retire" and not pending_ids)
        ):
            raise CompanionError("invalid_input")
        if operation == "upgrade":
            if state["version"] != 1:
                raise CompanionError("conflict")
        elif state["version"] != 2:
            raise CompanionError("upgrade_required")
        records = {}
        now = int(time.time())
        for identity in sorted(pending_ids):
            record = state["pending"].get(identity)
            if record is None:
                raise CompanionError("not_found_or_denied")
            effective = self.repository._effective_state(record, now)
            if operation == "cancel" and effective != "prepared":
                raise CompanionError("human_required")
            if operation == "retire":
                if now < record["created_at"] + 30 * 86400 or effective not in {
                    "cancelled",
                    "expired",
                    "completed",
                }:
                    raise CompanionError("not_retirable")
                if effective == "completed":
                    self._verify_completed(record)
                elif record["receipt"] is not None:
                    raise CompanionError("execution_unknown")
            records[identity] = deepcopy(record)
        preview = {
            "operation": operation,
            "pending_ids": sorted(pending_ids),
            "instance_hash": state["instance_hash"],
            "namespace_fingerprint": companion_digest(state),
            "records": records,
        }
        preview["confirmation_fingerprint"] = companion_digest(preview)
        # Validate the complete proposed transition, including both finite bounds,
        # before displaying a confirmation. No changes are written here.
        self._lifecycle_transition(deepcopy(state), preview, now)
        return preview

    @staticmethod
    def _lifecycle_transition(state: dict, preview: dict, now: int) -> dict:
        stamp = {"at": now, "fingerprint": preview["confirmation_fingerprint"]}
        operation = preview["operation"]
        if operation == "upgrade":
            state.update(version=2, archive={}, upgrade=stamp)
        else:
            if (
                operation == "retire"
                and len(state["archive"]) + len(preview["pending_ids"])
                > MAX_COMPANION_ARCHIVE_RECORDS
            ):
                raise CompanionError("input_too_large")
            for identity in preview["pending_ids"]:
                record = state["pending"][identity]
                record["confirmation"] = None
                if operation == "cancel":
                    record.update(state="cancelled", cancellation=stamp)
                else:
                    record["retirement"] = stamp
                    state["archive"][identity] = record
                    del state["pending"][identity]
        validate_companion_state(companion_json(state).decode(), state["instance_hash"])
        return state

    def lifecycle_confirm(self, preview: dict) -> dict:
        """Separate local-human confirmation, with stored fingerprints for retries."""
        checked = exact_fields(
            preview,
            {
                "operation",
                "pending_ids",
                "instance_hash",
                "namespace_fingerprint",
                "records",
                "confirmation_fingerprint",
            },
        )
        fingerprint = checked["confirmation_fingerprint"]
        if (
            companion_digest({k: v for k, v in checked.items() if k != "confirmation_fingerprint"})
            != fingerprint
        ):
            raise CompanionError("invalid_input")
        repo = self.repository
        with repo.vault.existing_transaction():
            state = repo._state(active=False)
            if checked["instance_hash"] != state["instance_hash"]:
                raise CompanionError("not_found_or_denied")
            operation = checked["operation"]
            ids = checked["pending_ids"]
            if (
                not isinstance(ids, list)
                or any(not isinstance(identity, str) for identity in ids)
                or len(ids) > 256
                or len(set(ids)) != len(ids)
            ):
                raise CompanionError("invalid_input")
            # A lost response after commit recovers the exact prior action even if
            # later evidence/grant changes have made the old namespace stale.
            stamps = []
            if operation == "upgrade" and state["version"] == 2:
                stamps = [state["upgrade"]]
            elif operation in {"cancel", "retire"} and ids:
                namespace = state.get("archive", {}) if operation == "retire" else state["pending"]
                if operation == "cancel":
                    namespace = {**state.get("archive", {}), **namespace}
                field = "retirement" if operation == "retire" else "cancellation"
                stamps = [namespace.get(identity, {}).get(field) for identity in ids]
            if stamps and all(
                isinstance(stamp, dict) and stamp["fingerprint"] == fingerprint for stamp in stamps
            ):
                return {
                    "operation": operation,
                    "pending_ids": ids,
                    "version": 2,
                    "confirmation_fingerprint": fingerprint,
                    "completed_at": stamps[0]["at"],
                }
            if companion_digest(state) != checked["namespace_fingerprint"]:
                raise CompanionError("stale_basis")
            expected = self._lifecycle_plan(state, operation, tuple(ids))
            if companion_digest(expected) != companion_digest(checked):
                raise CompanionError("stale_basis")
            now = int(time.time())
            updated = self._lifecycle_transition(state, checked, now)
            repo._save(updated)
            readback = repo._state(active=False)
            if companion_digest(readback) != companion_digest(updated):
                raise CompanionError("integrity_error")
            return {
                "operation": operation,
                "pending_ids": ids,
                "version": 2,
                "confirmation_fingerprint": fingerprint,
                "completed_at": now,
            }

    def _verify_completed(self, record: dict) -> None:
        """Reconcile with the existing canonical writer's output, never re-execute."""
        repo = self.repository
        receipt, request = record["receipt"], record["request"]
        if not isinstance(receipt, dict):
            raise CompanionError("execution_unknown")
        try:
            operation = record["operation"]
            if operation == "assessment":
                current = repo._object("opportunity", request["opportunity_id"])
                expected = {
                    "kind": "opportunity",
                    "object_id": request["opportunity_id"],
                    "source_hash": current["source_hash"],
                    "evaluation_fingerprint": current["evaluation_fingerprint"],
                }
                valid = receipt == expected
            elif operation == "evidence":
                proposal = EvidenceProposalInput.from_dict({**request, "actor": "model"})
                valid = receipt.get("kind") == "evidence" and any(
                    card.revision == receipt["revision"]
                    and card.content_hash == receipt["content_hash"]
                    and all(
                        getattr(card, field) == getattr(proposal, field)
                        for field in (
                            "source_id",
                            "source_hash",
                            "extracted_hash",
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
                    )
                    for card in repo.vault.revisions(receipt["object_id"])
                )
            elif operation == "decision":
                _, events = _load_decision_events(repo.root, request["opportunity_id"])
                choice = SuppliedDecisionInput.from_choice(request)
                valid = any(
                    event["decision_id"] == receipt["decision_id"]
                    and _same_decision(event, choice)
                    and all(
                        event[field] == receipt[field]
                        for field in ("opportunity_id", "sequence", "decision")
                    )
                    for event in events
                )
            elif operation in {"offer", "interview"} and receipt.get("kind") == operation:
                repo._object(operation, receipt["object_id"], historical=True)
                expected_id = (
                    f"{request['comparison_id']}:{record['basis']}"
                    if operation == "offer"
                    else f"{request['opportunity_id']}:{request['round_id']}:{record['basis']}"
                )
                valid = (
                    receipt["confirmation_fingerprint"] == record["basis"]
                    and receipt["object_id"] == expected_id
                )
            elif operation == "offer" and receipt.get("incomplete") is True:
                current = require_offer_result(
                    create_offer_service(repo.root).preflight(request)
                ).to_dict()
                valid = receipt == {
                    "kind": "pending",
                    "incomplete": True,
                    "unknown_fields": current["unknown_fields"],
                    "comparison_saved": False,
                }
            else:
                valid = False
        except (KeyError, ValueError, OSError, PermissionError) as exc:
            raise CompanionError("execution_unknown") from exc
        if not valid:
            raise CompanionError("execution_unknown")

    def issue_confirmation(self, identity: str) -> str:
        repo = self.repository
        with repo.vault.existing_transaction():
            state = repo._state()
            record = state["pending"].get(identity)
            now = int(time.time())
            if (
                record is None
                or repo._effective_state(record, now) != "prepared"
                or record["restored"]
            ):
                raise CompanionError("human_required")
            basis, _ = repo._preview(record["operation"], record["request"])
            if basis != record["basis"]:
                raise CompanionError("stale_basis")
            token = secrets.token_hex(32)
            record["confirmation"] = {
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "session_hash": hashlib.sha256(self._session.encode()).hexdigest(),
                "generation": state["generation"],
                "basis": basis,
                "created_at": now,
                "expires_at": min(now + 600, record["expires_at"]),
            }
            repo._save(state)
            return token

    def confirm(self, identity: str, token: str) -> dict:
        """Consume one capability before invoking an existing public writer once."""
        repo = self.repository
        if not isinstance(token, str) or len(token) != 64:
            raise CompanionError("human_required")
        with repo.vault.existing_transaction():
            state = repo._state()
            record = state["pending"].get(identity)
            now = int(time.time())
            confirmation = record["confirmation"] if record else None
            if (
                record is None
                or repo._effective_state(record, now) != "prepared"
                or confirmation is None
                or record["restored"]
                or not confirmation["created_at"] <= now < confirmation["expires_at"]
                or confirmation["generation"] != state["generation"]
                or not secrets.compare_digest(
                    confirmation["session_hash"], hashlib.sha256(self._session.encode()).hexdigest()
                )
                or not secrets.compare_digest(
                    confirmation["token_hash"], hashlib.sha256(token.encode()).hexdigest()
                )
            ):
                raise CompanionError("human_required")
            basis, _ = repo._preview(record["operation"], record["request"])
            if basis != record["basis"] or basis != confirmation["basis"]:
                raise CompanionError("stale_basis")
            record["confirmation"] = None
            record["state"] = "executing"
            repo._save(state)
        # The admission above is durable. A crash/timeout below leaves executing,
        # never a new-key retry. Domain writers independently revalidate their basis.
        try:
            receipt = self._execute(record)
        except (PermissionError, ValueError, OSError) as exc:
            raise CompanionError("execution_unknown") from exc
        self._complete(identity, record["digest"], receipt)
        return receipt

    @staticmethod
    def _confirmed_role(request: dict) -> SuppliedRoleRequest:
        proposal = SuppliedRoleProposal.from_dict(request)
        # Called only after independent local capability consumption. No model
        # proposal is relabelled confirmed during parsing, staging or previews.
        return SuppliedRoleRequest(
            **{field.name: getattr(proposal, field.name) for field in fields(proposal)}
        )

    def _execute(self, record: dict) -> dict:
        repo = self.repository
        request = record["request"]
        operation = record["operation"]
        if operation == "assessment":
            result = require_opportunity_result(
                create_opportunity_service(repo.root).confirm_proposal(
                    companion_json(request),
                    preflight_fingerprint=record["basis"],
                    actor="local_user",
                )
            )
            return {
                "kind": "opportunity",
                "object_id": result.opportunity_id,
                "source_hash": result.source_hash,
                "evaluation_fingerprint": result.evaluation_fingerprint,
            }
        if operation == "decision":
            decision = SuppliedDecisionInput.from_choice(request)
            return require_opportunity_result(
                create_opportunity_service(repo.root).record_decision(
                    decision, actor="local_user", expected_basis=record["basis"]
                )
            ).to_dict()
        if operation == "interview":
            parsed = InterviewRequest.from_dict(request)
            receipt = require_interview_result(
                create_interview_service(repo.root).prepare(
                    parsed, confirmation_fingerprint=record["basis"], actor="local_user"
                )
            )
            return {
                "kind": "interview",
                "object_id": f"{parsed.opportunity_id}:{parsed.round_id}:{record['basis']}",
                "confirmation_fingerprint": receipt.confirmation_fingerprint,
            }
        if operation == "offer":
            preview = require_offer_result(
                create_offer_service(repo.root).preflight(request)
            ).to_dict()
            if preview.get("complete") is False:
                return {
                    "kind": "pending",
                    "incomplete": True,
                    "unknown_fields": preview["unknown_fields"],
                    "comparison_saved": False,
                }
            parsed = OfferComparisonRequest.from_dict(request)
            receipt = require_offer_result(
                create_offer_service(repo.root).save_comparison(
                    parsed, confirmation_fingerprint=record["basis"], actor="local_user"
                )
            )
            return {
                "kind": "offer",
                "object_id": f"{parsed.comparison_id}:{receipt.confirmation_fingerprint}",
                "confirmation_fingerprint": receipt.confirmation_fingerprint,
            }
        if operation == "evidence":
            parsed = EvidenceProposalInput.from_dict({**request, "actor": "model"})
            receipt = require_evidence_result(repo.evidence.propose(parsed))
            return {
                "kind": "evidence",
                "object_id": receipt.evidence_id,
                "revision": receipt.revision,
                "content_hash": receipt.content_hash,
                "status": receipt.status,
            }
        raise CompanionError("unavailable_operation")

    def _complete(self, identity: str, digest: str, receipt: dict) -> None:
        repo = self.repository
        with repo.vault.existing_transaction():
            state = repo._state(active=False)
            current = state["pending"].get(identity)
            if current is None or current["digest"] != digest or current["state"] != "executing":
                raise CompanionError("execution_unknown")
            current["state"] = "completed"
            current["receipt"] = receipt
            repo._save(state)

    def recover(self, identity: str) -> dict:
        """Read public results after interrupted execution; never invoke a writer."""
        try:
            return self._recover(identity)
        except CompanionError:
            raise
        except (PermissionError, ValueError, OSError) as exc:
            raise CompanionError("execution_unknown") from exc

    def _recover(self, identity: str) -> dict:
        repo = self.repository
        with repo.vault.read_transaction():
            state = repo._state(active=False)
            record = state["pending"].get(identity) or state.get("archive", {}).get(identity)
            if record is None:
                raise CompanionError("not_found_or_denied")
            if record["state"] == "completed":
                return record["receipt"]
            if record["state"] != "executing":
                raise CompanionError("execution_unknown")
            operation, request = record["operation"], record["request"]
            if operation == "assessment":
                reviewed = require_opportunity_result(
                    repo.opportunities.preflight_supplied(companion_json(request))
                )
                if reviewed.evaluation_fingerprint != record["basis"]:
                    raise CompanionError("execution_unknown")
                confirmed = self._confirmed_role(request)
                current = require_opportunity_result(
                    repo.opportunities.resolve_supplied(confirmed.opportunity_id)
                )
                expected_hash = hashlib.sha256(companion_json(confirmed.to_dict())).hexdigest()
                if current.dossier.source_hash != expected_hash:
                    raise CompanionError("execution_unknown")
                receipt = {
                    "kind": "opportunity",
                    "object_id": confirmed.opportunity_id,
                    "source_hash": expected_hash,
                    "evaluation_fingerprint": current.dossier.evaluation_fingerprint,
                }
            elif operation == "offer":
                parsed = OfferComparisonRequest.from_dict(request)
                identity_ref = f"{parsed.comparison_id}:{record['basis']}"
                saved = repo._object("offer", identity_ref)
                if saved["confirmation_fingerprint"] != record["basis"]:
                    raise CompanionError("execution_unknown")
                receipt = {
                    "kind": "offer",
                    "object_id": identity_ref,
                    "confirmation_fingerprint": record["basis"],
                }
            elif operation == "interview":
                parsed = InterviewRequest.from_dict(request)
                checked = require_interview_result(
                    create_interview_service(repo.root).validate(
                        parsed.opportunity_id, parsed.round_id, record["basis"]
                    )
                )
                if not checked.current:
                    raise CompanionError("execution_unknown")
                receipt = {
                    "kind": "interview",
                    "object_id": f"{parsed.opportunity_id}:{parsed.round_id}:{record['basis']}",
                    "confirmation_fingerprint": record["basis"],
                }
            else:
                # Evidence/decision ambiguity needs independent local recovery;
                # matching prose or a latest event alone cannot prove this write.
                raise CompanionError("execution_unknown")
        self._complete(identity, record["digest"], receipt)
        return receipt

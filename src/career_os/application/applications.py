"""Local application-content planning over confirmed opportunity and evidence authority."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Protocol, TypeVar

from career_os.application.opportunities import SuppliedOpportunityState, _identifier
from career_os.application_records import (
    ApplicationPlan,
    _hash_payload,
    route_application_artifacts,
)
from career_os.evidence_contract import PublicationReadiness, validate_evidence_identifier
from career_os.evidence_records import EvidenceRevision

MAX_APPLICATION_REQUEST_BYTES = 64 * 1024


def application_fingerprint(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Application fingerprint must be a lowercase SHA-256 value")
    return value


@dataclass(frozen=True, slots=True)
class ApplicationRequest:
    opportunity_id: str
    evidence_ids: tuple[str, ...]
    archetype: str | None = None

    def __post_init__(self) -> None:
        if _identifier(self.opportunity_id, "opportunity_id") != self.opportunity_id:
            raise ValueError("Application opportunity identity must be canonical")
        if not isinstance(self.evidence_ids, tuple) or not 1 <= len(self.evidence_ids) <= 40:
            raise ValueError("Select 1-40 evidence IDs")
        for item in self.evidence_ids:
            if validate_evidence_identifier(item, "evidence_id") != item:
                raise ValueError("Application evidence identity must be canonical")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Application evidence IDs must be unique")
        if self.archetype is not None and (
            not isinstance(self.archetype, str) or not 1 <= len(self.archetype) <= 120
        ):
            raise ValueError("Application archetype must be a bounded identifier")

    @classmethod
    def from_dict(cls, payload: object) -> ApplicationRequest:
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "opportunity_id",
            "evidence_ids",
            "archetype",
        }:
            raise ValueError("Application request requires exactly the documented fields")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValueError("Application request requires schema version 1")
        if not isinstance(payload["evidence_ids"], list):
            raise ValueError("Application evidence IDs must be a list")
        return cls(payload["opportunity_id"], tuple(payload["evidence_ids"]), payload["archetype"])

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 1, **asdict(self), "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True, slots=True)
class ApplicationContext:
    opportunity: SuppliedOpportunityState
    evidence: tuple[EvidenceRevision, ...]
    publication: tuple[PublicationReadiness, ...]
    context_fingerprints: Mapping[str, str]
    application_archetypes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApplicationContentPlan:
    payload: dict[str, object]

    @property
    def confirmation_fingerprint(self) -> str:
        return application_fingerprint(self.payload["confirmation_fingerprint"])

    def to_dict(self) -> dict[str, object]:
        return deepcopy(self.payload)


def build_application_plan(
    request: ApplicationRequest, context: ApplicationContext
) -> ApplicationContentPlan:
    state = context.opportunity
    decision = state.latest_decision
    if (
        not state.decision_current
        or decision is None
        or decision["decision"] not in {"pursue", "conditional"}
    ):
        raise ValueError("Preparation requires a current pursue or conditional decision")
    dossier = state.dossier.to_dict()
    if state.dossier.opportunity_id != request.opportunity_id:
        raise ValueError("Application opportunity identity does not match")
    if dossier["authority"] != "user_supplied" or dossier["official_source_verified"] is not False:
        raise ValueError("This content route requires the supplied-posting authority")
    evaluation = dossier["role_evaluation"]
    allowed_ids = set(evaluation["supported_claim_ids"]) | set(
        evaluation["transferable_evidence_ids"]
    )
    if not set(request.evidence_ids) <= allowed_ids:
        raise ValueError("Selected evidence must support this dossier's assessed requirements")
    if tuple(item.evidence_id for item in context.evidence) != request.evidence_ids:
        raise ValueError("Application evidence does not match the selected IDs")
    if len(context.publication) != len(context.evidence) or any(
        not result.ready or result.evidence_ids != (item.evidence_id,)
        for item, result in zip(context.evidence, context.publication, strict=True)
    ):
        raise ValueError("Selected statements do not pass current publication checks")
    archetype = request.archetype
    if archetype is not None and archetype not in context.application_archetypes:
        raise ValueError("Selected archetype is not supported by this role pack")
    if archetype is None and context.application_archetypes:
        archetype = context.application_archetypes[0]
    intended = ApplicationPlan(
        job_key=request.opportunity_id,
        opportunity_id=request.opportunity_id,
        job_content_hash=state.dossier.source_hash,
        candidate_context_hashes=dict(context.context_fingerprints),
        artifact_route=route_application_artifacts().value,
        renderer_version="content_brief_v1",
        language="source_evidence_unchanged",
        route_rationale="CV-only intended route; this operation produces a content brief, not a CV.",
    )
    claims = [
        {
            "evidence_id": item.evidence_id,
            "revision": item.revision,
            "content_hash": item.content_hash,
            "source_id": item.source_id,
            "source_hash": item.source_hash,
            "extracted_hash": item.extracted_hash,
            "source_start_line": item.source_start_line,
            "source_end_line": item.source_end_line,
            "statement": item.statement,
            "ownership": item.ownership,
            "confidence": item.confidence,
            "approximation": item.approximation,
            "publication_boundary": item.publication_boundary,
        }
        for item in context.evidence
    ]
    payload = {
        "schema_version": 1,
        "request": request.to_dict(),
        "intended_application_plan": intended.to_dict(),
        "opportunity": {
            key: dossier[key]
            for key in (
                "opportunity_id",
                "company",
                "title",
                "source_url",
                "source_hash",
                "authority",
                "official_source_verified",
            )
        },
        "evaluation_fingerprint": state.dossier.evaluation_fingerprint,
        "decision_id": decision["decision_id"],
        "decision": decision["decision"],
        "role_selection": evaluation["selection"],
        "archetype": archetype,
        "archetype_selection": (
            "user_selected"
            if request.archetype
            else "role_pack_default"
            if archetype
            else "unresolved"
        ),
        "claims": claims,
        "requirement_assessments": evaluation["assessments"],
        "generated_artifacts": ["application-brief.txt", "requirement-evidence-map.json"],
        "local_content_ready": True,
        "application_ready": False,
        "requires_user_confirmation": True,
        "model_calls": False,
        "external_actions": False,
        "limitations": [
            "Supplied posting is not official-source verified; application readiness remains false.",
            "Verbatim evidence selection only; no translation, finished CV, or cover letter.",
            "Human writing and final document review remain required before external use.",
            "Preparation does not record submission, outreach, or a pipeline event.",
        ],
    }
    payload["confirmation_fingerprint"] = _hash_payload(payload)
    return ApplicationContentPlan(payload)


def application_artifact_bytes(plan: ApplicationContentPlan) -> dict[str, bytes]:
    """Plain text keeps supplied posting/evidence text inert, without changing claims."""
    payload = plan.to_dict()
    opportunity = payload["opportunity"]
    lines = [
        "APPLICATION CONTENT BRIEF",
        "",
        f"Company: {opportunity['company']}",
        f"Role: {opportunity['title']}",
        f"Opportunity: {opportunity['opportunity_id']}",
        f"Decision: {payload['decision']}",
        f"Archetype: {payload['archetype'] or 'unresolved'}",
        "",
        "LIMITATIONS",
        *payload["limitations"],
        "",
        "SELECTED VERBATIM EVIDENCE",
    ]
    for claim in payload["claims"]:
        lines.extend(
            [
                "",
                f"[{claim['evidence_id']}, revision {claim['revision']}]",
                claim["statement"],
                f"Ownership: {claim['ownership']}; approximation: {claim['approximation']}",
            ]
        )
    lines.extend(["", "REQUIREMENT ASSESSMENTS"])
    for item in payload["requirement_assessments"]:
        lines.extend(
            [
                "",
                f"{item['requirement_id']}: {item['statement']}",
                f"Assessment: {item['outcome']}",
                item["rationale"],
            ]
        )
    mapping = {
        "schema_version": 1,
        "opportunity_id": opportunity["opportunity_id"],
        "confirmation_fingerprint": plan.confirmation_fingerprint,
        "claims": payload["claims"],
        "requirement_assessments": payload["requirement_assessments"],
    }
    return {
        "application-brief.txt": ("\n".join(lines) + "\n").encode("utf-8"),
        "application-plan.json": (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode(),
        "requirement-evidence-map.json": (
            json.dumps(mapping, sort_keys=True, indent=2) + "\n"
        ).encode(),
    }


@dataclass(frozen=True, slots=True)
class ApplicationReceipt:
    opportunity_id: str
    confirmation_fingerprint: str
    relative_path: str
    created: bool
    schema_version: int = 3
    application_ready: bool = False
    external_actions: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ApplicationValidation:
    opportunity_id: str
    confirmation_fingerprint: str
    artifacts_intact: bool
    current: bool
    local_content_ready: bool
    limitations: tuple[str, ...]
    application_ready: bool = False
    external_actions: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ApplicationRepository(Protocol):
    def has_consent(self) -> bool: ...
    def plan(self, request: ApplicationRequest) -> ApplicationContentPlan: ...
    def prepare(self, request: ApplicationRequest, fingerprint: str) -> ApplicationReceipt: ...
    def validate(self, opportunity_id: str, fingerprint: str) -> ApplicationValidation: ...


@dataclass(frozen=True, slots=True)
class ApplicationOperationError:
    code: str
    message: str


_T = TypeVar("_T")


def require_application_result(result: _T | ApplicationOperationError) -> _T:
    if isinstance(result, ApplicationOperationError):
        if result.code in {"consent_required", "human_required"}:
            raise PermissionError(result.message)
        if result.code == "io_error":
            raise OSError(result.message)
        raise ValueError(result.message)
    return result


class ApplicationService:
    def __init__(self, repository: ApplicationRepository) -> None:
        self._repository = repository

    def _execute(self, operation: Callable[[], _T]) -> _T | ApplicationOperationError:
        try:
            if not self._repository.has_consent():
                return ApplicationOperationError(
                    "consent_required", "Local-processing consent required"
                )
            return operation()
        except PermissionError as exc:
            if exc.errno is None:
                return ApplicationOperationError(
                    "human_required", "Current local-user confirmation required"
                )
            return ApplicationOperationError("io_error", "Local application storage is unavailable")
        except ValueError:
            return ApplicationOperationError(
                "invalid_application", "Application input or current authority is invalid"
            )
        except OSError:
            return ApplicationOperationError("io_error", "Local application storage is unavailable")

    def plan(
        self, request: ApplicationRequest
    ) -> ApplicationContentPlan | ApplicationOperationError:
        return self._execute(
            lambda: self._repository.plan(ApplicationRequest.from_dict(request.to_dict()))
        )

    def prepare(
        self,
        request: ApplicationRequest,
        *,
        confirmation_fingerprint: str,
        actor: str | None = None,
    ) -> ApplicationReceipt | ApplicationOperationError:
        def prepare() -> ApplicationReceipt:
            if actor != "local_user":
                raise PermissionError("Local-user confirmation required")
            return self._repository.prepare(
                ApplicationRequest.from_dict(request.to_dict()),
                application_fingerprint(confirmation_fingerprint),
            )

        return self._execute(prepare)

    def validate(
        self, opportunity_id: str, confirmation_fingerprint: str
    ) -> ApplicationValidation | ApplicationOperationError:
        return self._execute(
            lambda: self._repository.validate(
                _identifier(opportunity_id, "opportunity_id"),
                application_fingerprint(confirmation_fingerprint),
            )
        )

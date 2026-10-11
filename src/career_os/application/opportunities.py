"""Supplied-opportunity requests and use cases over one injected local authority."""

from __future__ import annotations

import json
import ntpath
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import PureWindowsPath
from typing import Literal, Protocol, TypeVar, cast
from urllib.parse import urlsplit

from career_os.application.companion import bounded_companion_json
from career_os.application.inventory import InventoryPage
from career_os.evidence_contract import validate_evidence_identifier

RequirementLevel = Literal["mandatory", "preferred", "optional"]

MAX_POSTING_TEXT_CHARS = 40_000
MAX_REQUIREMENTS = 40
MAX_EVIDENCE_PER_ROUTE = 40
MAX_ROLE_REQUEST_BYTES = 1024 * 1024
MAX_ROLE_PROPOSAL_BYTES = 256 * 1024
_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_windows_reserved = getattr(
    ntpath, "isreserved", lambda value: PureWindowsPath(value).is_reserved()
)


def is_windows_reserved_name(value: str) -> bool:
    """Return whether a path component is reserved by Windows on any platform."""
    return _windows_reserved(value)


_REQUEST_FIELDS = {
    "schema_version",
    "authority",
    "opportunity_id",
    "company",
    "title",
    "location",
    "work_model",
    "source_url",
    "posting_text",
    "role_signals",
    "requirements",
    "evidence_routes",
    "constraints",
    "mapping_confirmed_by",
}
_ROUTE_FIELDS = {"direct", "transferable", "gaps"}
_CONSTRAINT_FIELDS = {
    "preferred_location",
    "preferred_work_model",
    "maximum_travel_percent",
    "currency",
}
_WORK_MODELS = frozenset({"remote", "hybrid", "onsite", "flexible", "unknown"})
_DECISION_FIELDS = {
    "schema_version",
    "opportunity_id",
    "source_hash",
    "evaluation_fingerprint",
    "decision",
    "reason_code",
    "note",
    "confirmed_by",
}
_DECISIONS = frozenset({"pursue", "conditional", "hold", "skip"})
_DECISION_REASONS = frozenset(
    {
        "accept_recommendation",
        "mandate_fit",
        "evidence_gap",
        "practical_constraint",
        "company_risk",
        "other",
    }
)


def _require_utf8(value: str, field: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must contain valid Unicode text") from exc


def _text(value: object, field: str, *, maximum: int = 2_000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum or "\x00" in cleaned:
        raise ValueError(f"{field} must contain 1-{maximum} safe characters")
    _require_utf8(cleaned, field)
    return cleaned


def _identifier(value: object, field: str) -> str:
    cleaned = _text(value, field, maximum=120)
    if not _IDENTIFIER.fullmatch(cleaned):
        raise ValueError(f"{field} must be a stable snake-case identifier")
    if field == "opportunity_id" and is_windows_reserved_name(cleaned):
        raise ValueError(f"{field} must not be a Windows-reserved name")
    return cleaned


def _strings(
    value: object,
    field: str,
    *,
    maximum_items: int,
    identifiers: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ValueError(f"{field} must be a bounded list")
    normalizer = _identifier if identifiers else _text
    result = tuple(normalizer(item, field) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} contains duplicates")
    return result


@dataclass(frozen=True, slots=True)
class SuppliedRequirement:
    requirement_id: str
    level: RequirementLevel
    statement: str

    @classmethod
    def from_dict(cls, payload: object) -> SuppliedRequirement:
        if not isinstance(payload, dict) or set(payload) != {
            "requirement_id",
            "level",
            "statement",
        }:
            raise ValueError("Each supplied requirement must contain exactly documented fields")
        level = payload.get("level")
        if not isinstance(level, str) or level not in {"mandatory", "preferred", "optional"}:
            raise ValueError("Supplied requirement level is invalid")
        return cls(
            requirement_id=_identifier(payload.get("requirement_id"), "requirement_id"),
            level=cast(RequirementLevel, level),
            statement=_text(payload.get("statement"), "requirement statement", maximum=4_000),
        )


@dataclass(frozen=True, slots=True)
class SuppliedConstraints:
    preferred_location: str
    preferred_work_model: str
    maximum_travel_percent: int
    currency: str

    @classmethod
    def from_dict(cls, payload: object) -> SuppliedConstraints:
        if not isinstance(payload, dict) or set(payload) != _CONSTRAINT_FIELDS:
            raise ValueError("Supplied constraints must contain exactly documented fields")
        work_model = payload.get("preferred_work_model")
        if not isinstance(work_model, str) or work_model not in _WORK_MODELS:
            raise ValueError("Supplied preferred_work_model is invalid")
        travel = payload.get("maximum_travel_percent")
        if isinstance(travel, bool) or not isinstance(travel, int) or not 0 <= travel <= 100:
            raise ValueError("Supplied maximum_travel_percent must be an integer from 0 to 100")
        currency = payload.get("currency")
        if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
            raise ValueError("Supplied currency must be an uppercase three-letter code")
        return cls(
            preferred_location=_text(
                payload.get("preferred_location"),
                "preferred_location",
                maximum=200,
            ),
            preferred_work_model=work_model,
            maximum_travel_percent=travel,
            currency=currency,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


EvidenceRoutes = dict[str, tuple[str, ...]]


def _evidence_route(value: object, field: str) -> EvidenceRoutes:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must map requirement ids to evidence-id lists")
    result: EvidenceRoutes = {}
    for raw_requirement_id, raw_evidence_ids in value.items():
        requirement_id = _identifier(raw_requirement_id, f"{field} requirement_id")
        if requirement_id in result:
            raise ValueError(f"{field} contains duplicate normalized requirement ids")
        evidence_ids = _strings(
            raw_evidence_ids,
            f"{field}.{requirement_id}",
            maximum_items=MAX_EVIDENCE_PER_ROUTE,
        )
        result[requirement_id] = tuple(
            validate_evidence_identifier(item, f"{field} evidence_id") for item in evidence_ids
        )
    return result


@dataclass(frozen=True, slots=True)
class _SuppliedRoleFields:
    opportunity_id: str
    company: str
    title: str
    location: str
    work_model: str
    source_url: str | None
    posting_text: str
    role_signals: tuple[str, ...]
    requirements: tuple[SuppliedRequirement, ...]
    direct_evidence: EvidenceRoutes
    transferable_evidence: EvidenceRoutes
    gap_evidence: EvidenceRoutes
    constraints: SuppliedConstraints

    @classmethod
    def _from_fields(cls, payload: dict[str, object]) -> _SuppliedRoleFields:
        work_model = payload.get("work_model")
        if not isinstance(work_model, str) or work_model not in _WORK_MODELS:
            raise ValueError("Supplied work_model is invalid")
        source_url_value = payload.get("source_url")
        source_url: str | None
        if source_url_value is None:
            source_url = None
        else:
            source_url = _text(source_url_value, "source_url", maximum=2_048)
            parsed = urlsplit(source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Supplied source_url must be an absolute HTTP(S) URL")
        posting_text = payload.get("posting_text")
        if not isinstance(posting_text, str):
            raise ValueError("Supplied posting_text must be text")
        if (
            not posting_text.strip()
            or len(posting_text) > MAX_POSTING_TEXT_CHARS
            or "\x00" in posting_text
        ):
            raise ValueError("Supplied posting_text must contain bounded safe text")
        _require_utf8(posting_text, "Supplied posting_text")
        raw_requirements = payload.get("requirements")
        if (
            not isinstance(raw_requirements, list)
            or not raw_requirements
            or len(raw_requirements) > MAX_REQUIREMENTS
        ):
            raise ValueError("Supplied requirements must be a non-empty bounded list")
        requirements = tuple(SuppliedRequirement.from_dict(item) for item in raw_requirements)
        requirement_ids = {item.requirement_id for item in requirements}
        if len(requirement_ids) != len(requirements):
            raise ValueError("Supplied requirement ids must be unique")
        routes = payload.get("evidence_routes")
        if not isinstance(routes, dict) or set(routes) != _ROUTE_FIELDS:
            raise ValueError("Supplied evidence_routes must contain direct, transferable, and gaps")
        direct = _evidence_route(routes["direct"], "direct evidence")
        transferable = _evidence_route(routes["transferable"], "transferable evidence")
        gaps = _evidence_route(routes["gaps"], "gap evidence")
        routed_requirements = set(direct) | set(transferable) | set(gaps)
        if routed_requirements - requirement_ids:
            raise ValueError("Evidence routes cannot introduce posting requirements")
        return cls(
            opportunity_id=_identifier(payload.get("opportunity_id"), "opportunity_id"),
            company=_text(payload.get("company"), "company", maximum=300),
            title=_text(payload.get("title"), "title", maximum=300),
            location=_text(payload.get("location"), "location", maximum=300),
            work_model=work_model,
            source_url=source_url,
            posting_text=posting_text,
            role_signals=_strings(
                payload.get("role_signals"),
                "role_signals",
                maximum_items=40,
                identifiers=True,
            ),
            requirements=requirements,
            direct_evidence=direct,
            transferable_evidence=transferable,
            gap_evidence=gaps,
            constraints=SuppliedConstraints.from_dict(payload.get("constraints")),
        )

    def _fields_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["role_signals"] = list(self.role_signals)
        payload["requirements"] = [asdict(item) for item in self.requirements]
        payload["evidence_routes"] = {
            "direct": {key: list(value) for key, value in self.direct_evidence.items()},
            "transferable": {key: list(value) for key, value in self.transferable_evidence.items()},
            "gaps": {key: list(value) for key, value in self.gap_evidence.items()},
        }
        for field in ("direct_evidence", "transferable_evidence", "gap_evidence"):
            del payload[field]
        return payload


def _role_envelope(payload: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError("Supplied role input must contain exactly the documented fields")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("Supplied role input schema_version must be 1")
    if payload.get("authority") != "user_supplied":
        raise ValueError("Supplied role authority must remain user_supplied")
    return payload


@dataclass(frozen=True, slots=True)
class SuppliedRoleRequest(_SuppliedRoleFields):
    """Existing explicitly confirmed local request; wire format is preserved."""

    @classmethod
    def from_dict(cls, payload: object) -> SuppliedRoleRequest:
        checked = _role_envelope(payload, _REQUEST_FIELDS)
        if checked["mapping_confirmed_by"] != "local_user":
            raise PermissionError("Evidence routes require explicit local-user confirmation")
        return cast(SuppliedRoleRequest, cls._from_fields(checked))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "authority": "user_supplied",
            "mapping_confirmed_by": "local_user",
            **self._fields_dict(),
        }


@dataclass(frozen=True, slots=True)
class SuppliedRoleProposal(_SuppliedRoleFields):
    """Lower-authority input that cannot claim local mapping confirmation."""

    @classmethod
    def from_dict(cls, payload: object) -> SuppliedRoleProposal:
        checked = _role_envelope(
            payload, (_REQUEST_FIELDS - {"mapping_confirmed_by"}) | {"mapping_status"}
        )
        if checked["mapping_status"] != "unconfirmed":
            raise ValueError("Role proposals must keep mapping unconfirmed")
        return cast(SuppliedRoleProposal, cls._from_fields(checked))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "authority": "user_supplied",
            "mapping_status": "unconfirmed",
            **self._fields_dict(),
        }


@dataclass(frozen=True, slots=True)
class SuppliedRolePreflight:
    proposal: SuppliedRoleProposal
    input_digest: str
    evaluation_fingerprint: str
    dossier: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "mapping_status": "unconfirmed",
            "input_digest": self.input_digest,
            "evaluation_fingerprint": self.evaluation_fingerprint,
            "dossier": deepcopy(self.dossier),
            "persisted": False,
            "external_actions": False,
        }


@dataclass(frozen=True, slots=True)
class SuppliedDecisionInput:
    opportunity_id: str
    source_hash: str
    evaluation_fingerprint: str
    decision: str
    reason_code: str
    note: str | None

    @classmethod
    def from_dict(cls, payload: object) -> SuppliedDecisionInput:
        if not isinstance(payload, dict) or set(payload) != _DECISION_FIELDS:
            raise ValueError("Supplied decision must contain exactly the documented fields")
        schema_version = payload.get("schema_version")
        if type(schema_version) is not int or schema_version != 1:
            raise ValueError("Supplied decision schema_version must be 1")
        if payload.get("confirmed_by") != "local_user":
            raise PermissionError("Supplied decision requires explicit local-user confirmation")
        return cls.from_choice(
            {
                key: value
                for key, value in payload.items()
                if key not in {"schema_version", "confirmed_by"}
            }
        )

    @classmethod
    def from_choice(cls, payload: object) -> SuppliedDecisionInput:
        """Validate an unconfirmed choice without manufacturing actor authority."""
        if not isinstance(payload, dict) or set(payload) != _DECISION_FIELDS - {
            "schema_version",
            "confirmed_by",
        }:
            raise ValueError("Supplied choice fields are invalid")
        source_hash = payload.get("source_hash")
        evaluation_fingerprint = payload.get("evaluation_fingerprint")
        if (
            not isinstance(source_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", source_hash)
            or not isinstance(evaluation_fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", evaluation_fingerprint)
        ):
            raise ValueError("Supplied decision hashes must be lowercase SHA-256 values")
        decision = payload.get("decision")
        reason_code = payload.get("reason_code")
        if (
            not isinstance(decision, str)
            or decision not in _DECISIONS
            or not isinstance(reason_code, str)
            or reason_code not in _DECISION_REASONS
        ):
            raise ValueError("Supplied decision or reason_code is invalid")
        raw_note = payload.get("note")
        note = None if raw_note is None else _text(raw_note, "decision note", maximum=2_000)
        if reason_code == "other" and note is None:
            raise ValueError("Supplied decision reason 'other' requires a note")
        return cls(
            opportunity_id=_identifier(payload.get("opportunity_id"), "opportunity_id"),
            source_hash=source_hash,
            evaluation_fingerprint=evaluation_fingerprint,
            decision=decision,
            reason_code=reason_code,
            note=note,
        )


@dataclass(frozen=True, slots=True)
class SuppliedRoleResult:
    """Versioned dossier projection; returned data never authorizes a later write."""

    opportunity_id: str
    source_hash: str
    evaluation_fingerprint: str
    dossier: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(json.dumps(self.dossier)))


@dataclass(frozen=True, slots=True)
class SuppliedDecisionReceipt:
    opportunity_id: str
    decision_id: str
    sequence: int
    decision: str
    current_recommendation: str
    record_created: bool
    decision_count: int
    schema_version: int = 1
    decision_recorded: bool = True
    application_event_created: bool = False
    external_event_confirmed: bool = False
    external_actions: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SuppliedOpportunityState:
    dossier: SuppliedRoleResult
    latest_decision: dict[str, object] | None
    decision_current: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "dossier": self.dossier.to_dict(),
            "latest_decision": deepcopy(self.latest_decision),
            "decision_current": self.decision_current,
        }


@dataclass(frozen=True, slots=True)
class SuppliedRoleCard:
    company: str
    title: str
    location: str
    work_model: str
    recommendation: str
    evaluation_fingerprint: str
    source_hash: str
    decision: str | None
    decision_current: bool
    decision_id: str | None


class OpportunityRepository(Protocol):
    def has_consent(self) -> bool: ...

    def list_supplied(
        self, *, limit: int, cursor: str | None
    ) -> InventoryPage[SuppliedRoleCard]: ...

    def preflight_supplied(
        self, proposal: SuppliedRoleProposal, source_content: bytes
    ) -> SuppliedRolePreflight: ...

    def confirm_proposal(
        self,
        proposal: SuppliedRoleProposal,
        source_content: bytes,
        *,
        preflight_fingerprint: str,
    ) -> SuppliedRoleResult: ...

    def evaluate_supplied(
        self,
        request: SuppliedRoleRequest,
        source_content: bytes,
        *,
        confirm_fingerprint: str | None,
    ) -> SuppliedRoleResult: ...

    def resolve_supplied(self, opportunity_id: str) -> SuppliedOpportunityState: ...

    def record_decision(
        self, decision: SuppliedDecisionInput, *, expected_basis: str | None = None
    ) -> SuppliedDecisionReceipt: ...


@dataclass(frozen=True, slots=True)
class OpportunityOperationError:
    code: Literal["consent_required", "human_required", "invalid_opportunity", "io_error"]
    message: str


_T = TypeVar("_T")


def require_opportunity_result(result: _T | OpportunityOperationError) -> _T:
    if isinstance(result, OpportunityOperationError):
        if result.code in {"consent_required", "human_required"}:
            raise PermissionError(result.message)
        if result.code == "io_error":
            raise OSError(result.message)
        raise ValueError(result.message)
    return result


class OpportunityService:
    """No IO on construction; only the trusted caller supplies actor authority.

    Supplied content is lower-authority data. Confirmation binds a fresh evaluation
    inside the repository transaction; caller-edited previews are never persisted.
    Expected errors contain no request, source, path, or database exception payload.
    """

    def __init__(self, repository: OpportunityRepository) -> None:
        self._repository = repository

    def _execute(
        self, operation: Callable[[], _T], *, consent_required: bool = False
    ) -> _T | OpportunityOperationError:
        try:
            if consent_required and not self._repository.has_consent():
                return OpportunityOperationError(
                    "consent_required", "Operation requires local-processing consent"
                )
            return operation()
        except PermissionError as exc:
            # OS access failures carry errno; local confirmation refusals do not.
            if exc.errno is not None:
                return OpportunityOperationError(
                    "io_error", "Local opportunity storage is unavailable"
                )
            return OpportunityOperationError(
                "human_required", "Operation requires current local-user confirmation"
            )
        except ValueError:
            return OpportunityOperationError(
                "invalid_opportunity", "Opportunity input or current state is invalid"
            )
        except OSError:
            return OpportunityOperationError("io_error", "Local opportunity storage is unavailable")

    def has_consent(self) -> bool | OpportunityOperationError:
        return self._execute(self._repository.has_consent)

    def list_supplied(
        self, *, limit: int = 20, cursor: str | None = None
    ) -> InventoryPage[SuppliedRoleCard] | OpportunityOperationError:
        return self._execute(
            lambda: self._repository.list_supplied(limit=limit, cursor=cursor),
            consent_required=True,
        )

    def preflight_supplied(
        self, source_content: bytes
    ) -> SuppliedRolePreflight | OpportunityOperationError:
        def preflight() -> SuppliedRolePreflight:
            if not isinstance(source_content, bytes) or not (
                1 <= len(source_content) <= MAX_ROLE_PROPOSAL_BYTES
            ):
                raise ValueError("Role proposal must contain bounded UTF-8 JSON bytes")

            payload = bounded_companion_json(source_content, maximum=MAX_ROLE_PROPOSAL_BYTES)
            proposal = SuppliedRoleProposal.from_dict(payload)
            return self._repository.preflight_supplied(proposal, source_content)

        return self._execute(preflight, consent_required=True)

    def evaluate_supplied(
        self,
        source_content: bytes,
        *,
        confirm_fingerprint: str | None = None,
        actor: str | None = None,
    ) -> SuppliedRoleResult | OpportunityOperationError:
        def evaluate() -> SuppliedRoleResult:
            if not isinstance(source_content, bytes) or not (
                1 <= len(source_content) <= MAX_ROLE_REQUEST_BYTES
            ):
                raise ValueError("Supplied request must contain bounded UTF-8 JSON bytes")
            if confirm_fingerprint is not None and actor != "local_user":
                raise PermissionError("Only a local user can confirm the mapping")
            try:
                payload = json.loads(source_content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
                raise ValueError("Supplied request must contain valid UTF-8 JSON") from exc
            request = SuppliedRoleRequest.from_dict(payload)
            return self._repository.evaluate_supplied(
                request, source_content, confirm_fingerprint=confirm_fingerprint
            )

        return self._execute(evaluate, consent_required=True)

    def confirm_proposal(
        self,
        source_content: bytes,
        *,
        preflight_fingerprint: str,
        actor: str,
    ) -> SuppliedRoleResult | OpportunityOperationError:
        """Trusted local approval binds the unconfirmed preview inside the writer."""

        def confirm() -> SuppliedRoleResult:
            if actor != "local_user":
                raise PermissionError("Only a local user can confirm the mapping")
            if not isinstance(preflight_fingerprint, str) or not re.fullmatch(
                r"[0-9a-f]{64}", preflight_fingerprint
            ):
                raise ValueError("Mapping confirmation fingerprint is invalid")
            payload = bounded_companion_json(source_content, maximum=MAX_ROLE_PROPOSAL_BYTES)
            proposal = SuppliedRoleProposal.from_dict(payload)
            return self._repository.confirm_proposal(
                proposal, source_content, preflight_fingerprint=preflight_fingerprint
            )

        return self._execute(confirm, consent_required=True)

    def resolve_supplied(
        self, opportunity_id: str
    ) -> SuppliedOpportunityState | OpportunityOperationError:
        return self._execute(
            lambda: self._repository.resolve_supplied(
                _identifier(opportunity_id, "opportunity_id")
            ),
            consent_required=True,
        )

    def record_decision(
        self, decision: SuppliedDecisionInput, *, actor: str, expected_basis: str | None = None
    ) -> SuppliedDecisionReceipt | OpportunityOperationError:
        def record() -> SuppliedDecisionReceipt:
            if actor != "local_user":
                raise PermissionError("Only a local user can record a decision")
            if expected_basis is not None and (
                not isinstance(expected_basis, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_basis)
            ):
                raise ValueError("Decision basis fingerprint is invalid")
            validated = SuppliedDecisionInput.from_dict(
                {"schema_version": 1, "confirmed_by": actor, **asdict(decision)}
            )
            return self._repository.record_decision(validated, expected_basis=expected_basis)

        return self._execute(record, consent_required=True)

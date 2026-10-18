"""Versioned, candidate-neutral evidence contract and publication validators."""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, cast, get_args

EVIDENCE_CONTRACT_VERSION = 3

EvidenceKind = Literal[
    "candidate_profile",
    "role_timeline",
    "responsibility",
    "achievement",
    "interview_story",
    "metric",
    "stakeholder_seniority",
    "skill_domain",
    "leadership_team",
    "compensation_constraint",
    "career_objective",
    "development_priority",
    "education",
    "gap",
    "forbidden_claim",
    "correction_note",
]
Ownership = Literal["personal", "team", "company", "unknown"]
Confidence = Literal[
    "exact",
    "approximate",
    "directional",
    "unknown",
    "disputed",
    "superseded",
]
Approximation = Literal["exact", "approximate", "directional", "unknown"]
PublicationBoundary = Literal[
    "private",
    "application",
    "application_and_interview",
    "interview",
    "public",
]
PublicationUse = Literal["internal", "application", "interview", "public"]
EvidenceStatus = Literal[
    "approved",
    "unapproved",
    "rejected",
    "deferred",
    "revoked",
    "superseded",
]
RelationshipKind = Literal[
    "supports",
    "conflicts_with",
    "clarifies",
    "derived_from",
    "corrects",
]
RetentionPolicy = Literal[
    "until_user_deletion",
    "until_superseded_and_exported",
    "session_only",
]

EVIDENCE_KINDS = frozenset(get_args(EvidenceKind))
OWNERSHIP_VALUES = frozenset(get_args(Ownership))
CONFIDENCE_VALUES = frozenset(get_args(Confidence))
APPROXIMATION_VALUES = frozenset(get_args(Approximation))
PUBLICATION_BOUNDARIES = frozenset(get_args(PublicationBoundary))
PUBLICATION_USES = frozenset(get_args(PublicationUse))
RELATIONSHIP_KINDS = frozenset(get_args(RelationshipKind))
RETENTION_POLICIES = frozenset(get_args(RetentionPolicy))
EVIDENCE_STATUSES = frozenset(get_args(EvidenceStatus))

MAX_FORMULATIONS = 24
MAX_RELATIONSHIPS = 64
MAX_ROLE_FAMILIES = 32
MAX_NOTE_CHARS = 2_000
MAX_LIST_VALUE_CHARS = 500

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,199}\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_EXACTNESS_CLAIM = re.compile(r"\b(?:exactly|precisely|without approximation)\b", re.I)


def _clean_text(value: object, field_name: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum or "\x00" in cleaned:
        raise ValueError(f"{field_name} must contain 1-{maximum} safe characters")
    return cleaned


def validate_evidence_identifier(value: object, field_name: str = "evidence_id") -> str:
    cleaned = _clean_text(value, field_name, maximum=200)
    if not _IDENTIFIER.fullmatch(cleaned):
        raise ValueError(f"{field_name} is invalid")
    return cleaned


def _optional_text(value: object, field_name: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _clean_text(value, field_name, maximum=maximum)


def _text_tuple(
    values: object,
    field_name: str,
    *,
    maximum_items: int,
    maximum_chars: int = MAX_LIST_VALUE_CHARS,
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or len(values) > maximum_items:
        raise ValueError(f"{field_name} must be a bounded list")
    cleaned = tuple(
        _clean_text(value, f"{field_name} item", maximum=maximum_chars) for value in values
    )
    if len(cleaned) != len(set(cleaned)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return cleaned


@dataclass(frozen=True, slots=True)
class EvidenceRelationship:
    target_evidence_id: str
    kind: RelationshipKind

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_evidence_id",
            validate_evidence_identifier(
                self.target_evidence_id,
                "relationship target_evidence_id",
            ),
        )
        if not isinstance(self.kind, str) or self.kind not in RELATIONSHIP_KINDS:
            raise ValueError("relationship kind is invalid")

    def to_dict(self) -> dict[str, str]:
        return {"target_evidence_id": self.target_evidence_id, "kind": self.kind}

    @classmethod
    def from_dict(cls, payload: object) -> EvidenceRelationship:
        if not isinstance(payload, dict) or set(payload) != {"target_evidence_id", "kind"}:
            raise ValueError("relationship must contain target_evidence_id and kind")
        return cls(
            target_evidence_id=payload["target_evidence_id"],
            kind=cast(RelationshipKind, payload["kind"]),
        )


@dataclass(frozen=True, slots=True)
class EvidenceContractMetadata:
    """Immutable common metadata stored with one evidence revision."""

    origin_ownership: Ownership
    permitted_formulations: tuple[str, ...] = ()
    prohibited_formulations: tuple[str, ...] = ()
    role_family_relevance: tuple[str, ...] = ()
    intended_channels: tuple[PublicationUse, ...] = ()
    relationships: tuple[EvidenceRelationship, ...] = ()
    uncertainty_note: str | None = None
    correction_note: str | None = None
    retention_policy: RetentionPolicy = "until_user_deletion"
    timeline_group: str | None = None
    mutually_exclusive_timeline: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.origin_ownership, str)
            or self.origin_ownership not in OWNERSHIP_VALUES
        ):
            raise ValueError("origin_ownership is invalid")
        object.__setattr__(
            self,
            "permitted_formulations",
            _text_tuple(
                self.permitted_formulations,
                "permitted_formulations",
                maximum_items=MAX_FORMULATIONS,
                maximum_chars=2_000,
            ),
        )
        object.__setattr__(
            self,
            "prohibited_formulations",
            _text_tuple(
                self.prohibited_formulations,
                "prohibited_formulations",
                maximum_items=MAX_FORMULATIONS,
                maximum_chars=2_000,
            ),
        )
        object.__setattr__(
            self,
            "role_family_relevance",
            _text_tuple(
                self.role_family_relevance,
                "role_family_relevance",
                maximum_items=MAX_ROLE_FAMILIES,
            ),
        )
        channels = _text_tuple(
            self.intended_channels, "intended_channels", maximum_items=len(PUBLICATION_USES)
        )
        if (
            len(channels) > len(PUBLICATION_USES)
            or len(channels) != len(set(channels))
            or any(item not in PUBLICATION_USES for item in channels)
        ):
            raise ValueError("intended_channels is invalid")
        object.__setattr__(self, "intended_channels", channels)
        if not isinstance(self.relationships, (tuple, list)) or any(
            not isinstance(item, EvidenceRelationship) for item in self.relationships
        ):
            raise ValueError("relationships must contain relationship records")
        relationships = tuple(self.relationships)
        if len(relationships) > MAX_RELATIONSHIPS or len(
            {(item.target_evidence_id, item.kind) for item in relationships}
        ) != len(relationships):
            raise ValueError("relationships are invalid or duplicated")
        object.__setattr__(self, "relationships", relationships)
        object.__setattr__(
            self,
            "uncertainty_note",
            _optional_text(
                self.uncertainty_note,
                "uncertainty_note",
                maximum=MAX_NOTE_CHARS,
            ),
        )
        object.__setattr__(
            self,
            "correction_note",
            _optional_text(
                self.correction_note,
                "correction_note",
                maximum=MAX_NOTE_CHARS,
            ),
        )
        if (
            not isinstance(self.retention_policy, str)
            or self.retention_policy not in RETENTION_POLICIES
        ):
            raise ValueError("retention_policy is invalid")
        object.__setattr__(
            self,
            "timeline_group",
            _optional_text(self.timeline_group, "timeline_group", maximum=160),
        )
        if not isinstance(self.mutually_exclusive_timeline, bool):
            raise ValueError("mutually_exclusive_timeline must be a boolean")
        if self.mutually_exclusive_timeline and self.timeline_group is None:
            raise ValueError("mutually exclusive timelines require a timeline_group")

    def to_dict(self) -> dict[str, object]:
        return {
            "origin_ownership": self.origin_ownership,
            "permitted_formulations": list(self.permitted_formulations),
            "prohibited_formulations": list(self.prohibited_formulations),
            "role_family_relevance": list(self.role_family_relevance),
            "intended_channels": list(self.intended_channels),
            "relationships": [item.to_dict() for item in self.relationships],
            "uncertainty_note": self.uncertainty_note,
            "correction_note": self.correction_note,
            "retention_policy": self.retention_policy,
            "timeline_group": self.timeline_group,
            "mutually_exclusive_timeline": self.mutually_exclusive_timeline,
        }

    @classmethod
    def from_dict(
        cls,
        payload: object,
        *,
        fallback_ownership: Ownership,
    ) -> EvidenceContractMetadata:
        if payload in ({}, None):
            return cls(origin_ownership=fallback_ownership)
        if not isinstance(payload, dict):
            raise ValueError("contract metadata must be an object")
        allowed = {
            "origin_ownership",
            "permitted_formulations",
            "prohibited_formulations",
            "role_family_relevance",
            "intended_channels",
            "relationships",
            "uncertainty_note",
            "correction_note",
            "retention_policy",
            "timeline_group",
            "mutually_exclusive_timeline",
        }
        if set(payload) - allowed:
            raise ValueError("contract metadata contains unsupported fields")
        for field_name in (
            "permitted_formulations",
            "prohibited_formulations",
            "role_family_relevance",
            "intended_channels",
            "relationships",
        ):
            values = payload.get(field_name, [])
            if not isinstance(values, list):
                raise ValueError(f"{field_name} must be a list")
            if field_name != "relationships" and any(
                not isinstance(value, str) for value in values
            ):
                raise ValueError(f"{field_name} must contain text")
        relationships = payload.get("relationships", [])
        return cls(
            origin_ownership=cast(
                Ownership,
                payload.get("origin_ownership", fallback_ownership),
            ),
            permitted_formulations=cast(
                tuple[str, ...], tuple(payload.get("permitted_formulations", []))
            ),
            prohibited_formulations=cast(
                tuple[str, ...], tuple(payload.get("prohibited_formulations", []))
            ),
            role_family_relevance=cast(
                tuple[str, ...], tuple(payload.get("role_family_relevance", []))
            ),
            intended_channels=cast(
                tuple[PublicationUse, ...], tuple(payload.get("intended_channels", []))
            ),
            relationships=tuple(EvidenceRelationship.from_dict(item) for item in relationships),
            uncertainty_note=cast(str | None, payload.get("uncertainty_note")),
            correction_note=cast(str | None, payload.get("correction_note")),
            retention_policy=cast(
                RetentionPolicy,
                payload.get("retention_policy", "until_user_deletion"),
            ),
            timeline_group=cast(str | None, payload.get("timeline_group")),
            mutually_exclusive_timeline=payload.get("mutually_exclusive_timeline", False),
        )


@dataclass(frozen=True, slots=True)
class PublicationRequest:
    statement: str
    evidence_ids: tuple[str, ...]
    use: PublicationUse
    ownership: Ownership
    approximation: Approximation

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "statement", _clean_text(self.statement, "statement", maximum=8_000)
        )
        identifiers = tuple(self.evidence_ids)
        if not identifiers or len(identifiers) > 64:
            raise ValueError("publication request requires 1-64 unique evidence IDs")
        identifiers = tuple(
            validate_evidence_identifier(item, "publication evidence_id") for item in identifiers
        )
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("publication request requires 1-64 unique evidence IDs")
        object.__setattr__(self, "evidence_ids", identifiers)
        if self.use not in PUBLICATION_USES - {"internal"}:
            raise ValueError("publication request requires a publishable use")
        if self.ownership not in OWNERSHIP_VALUES:
            raise ValueError("publication ownership is invalid")
        if self.approximation not in APPROXIMATION_VALUES:
            raise ValueError("publication approximation is invalid")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    evidence_id: str | None = None


@dataclass(frozen=True, slots=True)
class PublicationReadiness:
    ready: bool
    evidence_ids: tuple[str, ...]
    source_references: tuple[str, ...]
    issues: tuple[ValidationIssue, ...]


@dataclass(frozen=True, slots=True)
class RoleEvidenceSummary:
    strong_verified: tuple[str, ...] = ()
    transferable: tuple[str, ...] = ()
    genuine_gaps: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TimelineItem:
    evidence_id: str
    start_date: str
    end_date: str
    timeline_group: str | None = None
    mutually_exclusive: bool = False


def publication_boundary_allows(boundary: str, use: PublicationUse) -> bool:
    if use == "internal":
        return True
    return (
        boundary
        in {
            "application": {"application", "application_and_interview", "public"},
            "interview": {"interview", "application_and_interview", "public"},
            "public": {"public"},
        }[use]
    )


def validate_metric_details(details: dict[str, str]) -> None:
    value = details.get("metric_value") or details.get("value")
    if value is None:
        raise ValueError("metric evidence requires a numeric value")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("metric value must be a finite decimal") from exc
    if not parsed.is_finite():
        raise ValueError("metric value must be a finite decimal")
    unit = details.get("metric_unit") or details.get("unit")
    period = details.get("metric_period") or details.get("period")
    scope = details.get("metric_scope") or details.get("scope")
    if not unit or not period or not scope:
        raise ValueError("metric evidence requires unit, period, and scope")
    currency = details.get("currency")
    if currency and not _CURRENCY.fullmatch(currency):
        raise ValueError("metric currency must be an uppercase ISO-style code")
    population = details.get("population")
    if population is not None:
        _clean_text(population, "metric population", maximum=500)


def validate_statement_precision(statement: str, approximation: Approximation) -> None:
    if approximation != "exact" and _EXACTNESS_CLAIM.search(statement):
        raise ValueError("Approximate evidence cannot use an exact formulation")


def _date_bound(value: str, *, end: bool = False) -> date:
    normalized = value.strip().casefold()
    if normalized in {"present", "current", "ongoing"}:
        return date.max
    try:
        if re.fullmatch(r"\d{4}", value):
            value += "-12-31" if end else "-01-01"
        elif re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = map(int, value.split("-"))
            day = monthrange(year, month)[1] if end else 1
            value += f"-{day:02d}"
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"timeline date is not ISO-compatible: {value}") from exc


def validate_timeline(items: tuple[TimelineItem, ...]) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    parsed: list[tuple[TimelineItem, date, date]] = []
    for item in items:
        try:
            start = _date_bound(item.start_date)
            end = _date_bound(item.end_date, end=True)
        except ValueError as exc:
            issues.append(
                ValidationIssue(
                    code="timeline_invalid",
                    message=str(exc),
                    evidence_id=item.evidence_id,
                )
            )
            continue
        if end < start:
            issues.append(
                ValidationIssue(
                    code="timeline_inverted",
                    message="Timeline end precedes its start",
                    evidence_id=item.evidence_id,
                )
            )
        parsed.append((item, start, end))
    for index, (left, left_start, left_end) in enumerate(parsed):
        for right, right_start, right_end in parsed[index + 1 :]:
            if not (
                left.mutually_exclusive
                and right.mutually_exclusive
                and left.timeline_group
                and left.timeline_group == right.timeline_group
            ):
                continue
            if max(left_start, right_start) <= min(left_end, right_end):
                partial = any(
                    re.fullmatch(r"\d{4}(?:-\d{2})?", value)
                    for value in (left.start_date, left.end_date, right.start_date, right.end_date)
                )
                issues.append(
                    ValidationIssue(
                        code="mutually_exclusive_overlap",
                        message=(
                            (
                                "Mutually exclusive timeline records may overlap; "
                                "partial dates require clarification: "
                                if partial
                                else "Mutually exclusive timeline records overlap: "
                            )
                            + f"{left.evidence_id}, {right.evidence_id}"
                        ),
                    )
                )
    return tuple(issues)


def validate_timestamp(value: str | None, field_name: str) -> None:
    if value is None:
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date-time") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")


def evidence_contract_schema() -> dict[str, object]:
    """Return the dependency-free JSON Schema 2020-12 contract snapshot."""

    string_list = {
        "type": "array",
        "maxItems": MAX_FORMULATIONS,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1, "maxLength": 2_000},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://career-os.local/schemas/evidence-vault.schema.json",
        "title": "Rolevane generic evidence vault contract",
        "x-career-os-contract-version": EVIDENCE_CONTRACT_VERSION,
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "boundary_version",
            "sources",
            "evidence",
            "audit_events",
            "deletion_inventory",
            "credentials_included",
            "remote_telemetry",
        ],
        "properties": {
            "schema_version": {"const": EVIDENCE_CONTRACT_VERSION},
            "boundary_version": {"type": "string", "minLength": 1},
            "sources": {"type": "array", "items": {"$ref": "#/$defs/source"}},
            "evidence": {"type": "array", "items": {"$ref": "#/$defs/evidence"}},
            "audit_events": {"type": "array", "items": {"$ref": "#/$defs/audit"}},
            "deletion_inventory": {"$ref": "#/$defs/deletion_inventory"},
            "credentials_included": {"const": False},
            "remote_telemetry": {"const": False},
        },
        "$defs": {
            "deletion_inventory": {
                "type": "object",
                "additionalProperties": False,
                "required": ["database", "exists", "tables"],
                "properties": {
                    "database": {"type": "string", "minLength": 1},
                    "exists": {"type": "boolean"},
                    "tables": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["count", "identifiers"],
                            "properties": {
                                "count": {"type": "integer", "minimum": 0},
                                "identifiers": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
            "source": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "source_id",
                    "display_name",
                    "media_type",
                    "source_hash",
                    "extracted_hash",
                    "size_bytes",
                    "line_count",
                    "input_format",
                    "locations",
                    "page_count",
                    "managed_copy",
                    "imported_at",
                ],
                "properties": {
                    "source_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$",
                    },
                    "display_name": {"type": "string", "minLength": 1, "maxLength": 160},
                    "media_type": {
                        "enum": [
                            "text/plain",
                            "text/markdown",
                            "application/pdf",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        ]
                    },
                    "source_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "extracted_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "size_bytes": {"type": "integer", "minimum": 1, "maximum": 15 * 1024 * 1024},
                    "line_count": {"type": "integer", "minimum": 1, "maximum": 50_000},
                    "input_format": {"enum": ["text", "markdown", "pdf", "docx"]},
                    "locations": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 50_000,
                        "items": {"$ref": "#/$defs/source_location"},
                    },
                    "page_count": {"type": ["integer", "null"], "minimum": 1, "maximum": 50},
                    "managed_copy": {"const": True},
                    "imported_at": {"type": "string", "format": "date-time"},
                },
            },
            "source_location": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "ordinal", "start_line", "end_line", "part"],
                "properties": {
                    "kind": {"enum": ["line", "page", "paragraph"]},
                    "ordinal": {"type": "integer", "minimum": 1},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "part": {"type": ["string", "null"], "minLength": 1},
                },
            },
            "relationship": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target_evidence_id", "kind"],
                "properties": {
                    "target_evidence_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$",
                    },
                    "kind": {"enum": sorted(RELATIONSHIP_KINDS)},
                },
            },
            "metadata": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "origin_ownership",
                    "permitted_formulations",
                    "prohibited_formulations",
                    "role_family_relevance",
                    "intended_channels",
                    "relationships",
                    "uncertainty_note",
                    "correction_note",
                    "retention_policy",
                    "timeline_group",
                    "mutually_exclusive_timeline",
                ],
                "properties": {
                    "origin_ownership": {"enum": sorted(OWNERSHIP_VALUES)},
                    "permitted_formulations": string_list,
                    "prohibited_formulations": string_list,
                    "role_family_relevance": {
                        **string_list,
                        "maxItems": MAX_ROLE_FAMILIES,
                        "items": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    "intended_channels": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"enum": sorted(PUBLICATION_USES)},
                    },
                    "relationships": {
                        "type": "array",
                        "maxItems": MAX_RELATIONSHIPS,
                        "items": {"$ref": "#/$defs/relationship"},
                    },
                    "uncertainty_note": {
                        "type": ["string", "null"],
                        "maxLength": MAX_NOTE_CHARS,
                    },
                    "correction_note": {
                        "type": ["string", "null"],
                        "maxLength": MAX_NOTE_CHARS,
                    },
                    "retention_policy": {"enum": sorted(RETENTION_POLICIES)},
                    "timeline_group": {"type": ["string", "null"], "maxLength": 160},
                    "mutually_exclusive_timeline": {"type": "boolean"},
                },
            },
            "evidence": {
                "type": "object",
                "additionalProperties": False,
                "required": [
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
                ],
                "properties": {
                    "evidence_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$",
                    },
                    "revision": {"type": "integer", "minimum": 1},
                    "kind": {"enum": sorted(EVIDENCE_KINDS)},
                    "statement": {"type": "string", "minLength": 1, "maxLength": 4_000},
                    "details": {
                        "type": "object",
                        "minProperties": 1,
                        "maxProperties": 32,
                        "additionalProperties": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 2_000,
                        },
                    },
                    "source_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$",
                    },
                    "source_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "source_start_line": {"type": "integer", "minimum": 1},
                    "source_end_line": {"type": "integer", "minimum": 1},
                    "ownership": {"enum": sorted(OWNERSHIP_VALUES)},
                    "confidence": {"enum": sorted(CONFIDENCE_VALUES)},
                    "approximation": {"enum": sorted(APPROXIMATION_VALUES)},
                    "publication_boundary": {"enum": sorted(PUBLICATION_BOUNDARIES)},
                    "status": {"enum": sorted(EVIDENCE_STATUSES)},
                    "content_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "created_at": {"type": "string", "format": "date-time"},
                    "supersedes_revision": {"type": ["integer", "null"], "minimum": 1},
                    "metadata": {"$ref": "#/$defs/metadata"},
                    "extracted_hash": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
                },
            },
            "audit": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "event_id",
                    "evidence_id",
                    "revision",
                    "action",
                    "actor",
                    "occurred_at",
                    "content_hash",
                    "reason",
                ],
                "properties": {
                    "event_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$",
                    },
                    "evidence_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,199}$",
                    },
                    "revision": {"type": "integer", "minimum": 1},
                    "action": {
                        "enum": [
                            "proposed",
                            "approved",
                            "verified",
                            "rejected",
                            "deferred",
                            "revoked",
                            "superseded",
                        ]
                    },
                    "actor": {"type": "string", "minLength": 1, "maxLength": 160},
                    "occurred_at": {"type": "string", "format": "date-time"},
                    "content_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "reason": {"type": ["string", "null"], "maxLength": 2_000},
                },
            },
        },
    }


__all__ = [
    "APPROXIMATION_VALUES",
    "CONFIDENCE_VALUES",
    "EVIDENCE_CONTRACT_VERSION",
    "EVIDENCE_KINDS",
    "EvidenceContractMetadata",
    "EvidenceRelationship",
    "PublicationReadiness",
    "PublicationRequest",
    "RoleEvidenceSummary",
    "TimelineItem",
    "ValidationIssue",
    "evidence_contract_schema",
    "publication_boundary_allows",
    "validate_metric_details",
    "validate_evidence_identifier",
    "validate_statement_precision",
    "validate_timeline",
    "validate_timestamp",
]

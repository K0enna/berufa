"""Versioned, candidate-neutral role-pack selection and evidence evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from importlib.resources import files
from typing import Literal, cast

RolePackId = Literal[
    "commercial_leadership",
    "customer_post_sales_leadership",
    "generic_fallback",
    "product_leadership",
    "senior_expert_ic",
    "services_solutions_leadership",
]
SUPPORTED_ROLE_PACK_IDS = frozenset(
    {
        "commercial_leadership",
        "customer_post_sales_leadership",
        "generic_fallback",
        "product_leadership",
        "senior_expert_ic",
        "services_solutions_leadership",
    }
)
RequirementLevel = Literal["mandatory", "preferred", "optional"]
RequirementImpact = Literal["hard_gate", "stretch", "evidence"]
EvidenceOutcome = Literal["proven", "disproven", "conflict", "unknown", "not_found"]
SelectionStatus = Literal["selected", "fallback", "manual_review"]
Recommendation = Literal["pursue", "conditional", "hold", "skip"]

ROLE_PACK_SCHEMA_VERSION = 1
ROLE_PACK_ENGINE_CONTRACT = 1
MAX_PACK_BYTES = 256 * 1024
MAX_ADJACENT_PACKS = 2
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_SEMVER = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
_PACK_KEYS = {
    "schema_version",
    "pack_id",
    "version",
    "label",
    "provenance",
    "compatibility",
    "fallback",
    "confidence_cap",
    "application_readiness_allowed",
    "structural_meaning",
    "taxonomy",
    "selection",
    "requirements",
    "misleading_patterns",
    "economics_questions",
    "diligence_prompts",
    "application_archetypes",
    "dimension_weights",
    "bounded_adjustments",
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"Role pack {field} must be an object")
    return cast(Mapping[str, object], value)


def _text(value: object, field: str, *, maximum: int = 2_000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Role pack {field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ValueError(f"Role pack {field} must contain safe text")
    return normalized


def _identifier(value: object, field: str) -> str:
    result = _text(value, field, maximum=100)
    if not _IDENTIFIER.fullmatch(result):
        raise ValueError(f"Role pack {field} must be a stable snake-case identifier")
    return result


def _strings(
    value: object,
    field: str,
    *,
    allow_empty: bool = True,
    identifiers: bool = False,
    maximum_items: int = 40,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value) or len(value) > maximum_items:
        raise ValueError(f"Role pack {field} must be a bounded list")
    normalizer = _identifier if identifiers else _text
    result = tuple(normalizer(item, field) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"Role pack {field} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class RequirementRule:
    requirement_id: str
    level: RequirementLevel
    impact: RequirementImpact
    statement: str
    evidence_question: str


@dataclass(frozen=True, slots=True)
class AdjacentArchetype:
    archetype_id: str
    label: str
    trigger_signals: tuple[str, ...]
    explanation: str


@dataclass(frozen=True, slots=True)
class RolePack:
    pack_id: RolePackId
    version: str
    label: str
    provenance: Mapping[str, str]
    compatibility_contract: int
    replaces: tuple[str, ...]
    fallback: bool
    confidence_cap: float
    application_readiness_allowed: bool
    structural_meaning: str
    role_family: str
    title_patterns: tuple[str, ...]
    mandate_archetypes: tuple[str, ...]
    adjacent_archetypes: tuple[AdjacentArchetype, ...]
    required_signals: tuple[str, ...]
    supporting_signals: tuple[str, ...]
    disqualifying_signals: tuple[str, ...]
    requirements: tuple[RequirementRule, ...]
    misleading_patterns: tuple[Mapping[str, str], ...]
    economics_questions: tuple[str, ...]
    diligence_prompts: tuple[str, ...]
    application_archetypes: tuple[str, ...]
    dimension_weights: Mapping[str, float]
    bounded_adjustments: tuple[Mapping[str, object], ...]
    fingerprint: str

    def requirement(self, requirement_id: str) -> RequirementRule | None:
        return next(
            (item for item in self.requirements if item.requirement_id == requirement_id),
            None,
        )

    def public_summary(self) -> dict[str, object]:
        # Return detached mappings while preserving dataclass serialization of
        # the stored pack used by participant snapshot export.
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "label": self.label,
            "structural_meaning": self.structural_meaning,
            "fallback": self.fallback,
            "confidence_cap": self.confidence_cap,
            "application_readiness_allowed": self.application_readiness_allowed,
            "fingerprint": self.fingerprint,
            "role_family": self.role_family,
            "title_patterns": self.title_patterns,
            "mandate_archetypes": self.mandate_archetypes,
            "selection": {
                "required_signals": self.required_signals,
                "supporting_signals": self.supporting_signals,
                "disqualifying_signals": self.disqualifying_signals,
            },
            "requirements": [asdict(item) for item in self.requirements],
            "adjacent_archetypes": [asdict(item) for item in self.adjacent_archetypes],
            "misleading_patterns": tuple(dict(item) for item in self.misleading_patterns),
            "economics_questions": self.economics_questions,
            "diligence_prompts": self.diligence_prompts,
            "application_archetypes": self.application_archetypes,
            "dimension_weights": dict(self.dimension_weights),
            "bounded_adjustments": tuple(dict(item) for item in self.bounded_adjustments),
        }


@dataclass(frozen=True, slots=True)
class PostingRequirement:
    requirement_id: str
    level: RequirementLevel
    statement: str
    posting_evidence_id: str


@dataclass(frozen=True, slots=True)
class PostingProfile:
    opportunity_id: str
    posting_hash: str
    source_id: str
    official_source_verified: bool
    signals: tuple[str, ...]
    requirements: tuple[PostingRequirement, ...]


@dataclass(frozen=True, slots=True)
class CandidateOverlay:
    positive_evidence: Mapping[str, tuple[str, ...]]
    negative_evidence: Mapping[str, tuple[str, ...]]
    transferable_evidence: Mapping[str, tuple[str, ...]]
    constraints: Mapping[str, object]
    context_fingerprints: Mapping[str, str]

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "positive_evidence": self.positive_evidence,
                "negative_evidence": self.negative_evidence,
                "transferable_evidence": self.transferable_evidence,
                "constraints": self.constraints,
                "context_fingerprints": self.context_fingerprints,
            }
        )


@dataclass(frozen=True, slots=True)
class RoleSelection:
    status: SelectionStatus
    pack_id: RolePackId
    pack_version: str
    pack_fingerprint: str
    matched_signals: tuple[str, ...]
    missing_required_signals: tuple[str, ...]
    adjacent_pack_ids: tuple[str, ...]
    adjacent_archetype_ids: tuple[str, ...]
    explanation: tuple[str, ...]


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
class RoleEvaluation:
    selection: RoleSelection
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


def validate_role_pack(payload: object) -> RolePack:
    raw = _mapping(payload, "root")
    if set(raw) != _PACK_KEYS or raw.get("schema_version") != ROLE_PACK_SCHEMA_VERSION:
        raise ValueError("Role pack must use schema version 1 and exactly the documented fields")
    pack_id_value = _identifier(raw.get("pack_id"), "pack_id")
    if pack_id_value not in SUPPORTED_ROLE_PACK_IDS:
        raise ValueError("Role pack id is not in the reviewed registry contract")
    pack_id = cast(RolePackId, pack_id_value)
    version = _text(raw.get("version"), "version", maximum=32)
    if not _SEMVER.fullmatch(version):
        raise ValueError("Role pack version must be semantic x.y.z")

    provenance_raw = _mapping(raw.get("provenance"), "provenance")
    if set(provenance_raw) != {"owner", "reviewed_on", "source"}:
        raise ValueError("Role pack provenance must contain owner, reviewed_on, and source")
    provenance = {key: _text(provenance_raw[key], f"provenance.{key}") for key in provenance_raw}

    compatibility = _mapping(raw.get("compatibility"), "compatibility")
    if set(compatibility) != {"engine_contract", "replaces"}:
        raise ValueError("Role pack compatibility fields are invalid")
    engine_contract = compatibility.get("engine_contract")
    if engine_contract != ROLE_PACK_ENGINE_CONTRACT:
        raise ValueError("Role pack targets an unsupported engine contract")
    replaces = _strings(compatibility.get("replaces"), "compatibility.replaces")

    fallback = raw.get("fallback")
    readiness_allowed = raw.get("application_readiness_allowed")
    if not isinstance(fallback, bool) or not isinstance(readiness_allowed, bool):
        raise ValueError("Role pack fallback/readiness flags must be booleans")
    confidence_cap = raw.get("confidence_cap")
    if (
        isinstance(confidence_cap, bool)
        or not isinstance(confidence_cap, (int, float))
        or not 0.0 <= float(confidence_cap) <= 1.0
    ):
        raise ValueError("Role pack confidence_cap must be between zero and one")
    if fallback and (float(confidence_cap) > 0.5 or readiness_allowed):
        raise ValueError(
            "Fallback packs must cap confidence at 0.5 and block application readiness"
        )

    taxonomy = _mapping(raw.get("taxonomy"), "taxonomy")
    if set(taxonomy) != {
        "role_family",
        "title_patterns",
        "mandate_archetypes",
        "adjacent_archetypes",
    }:
        raise ValueError("Role pack taxonomy fields are invalid")
    adjacent_raw = taxonomy.get("adjacent_archetypes")
    if not isinstance(adjacent_raw, list) or len(adjacent_raw) > MAX_ADJACENT_PACKS:
        raise ValueError("Role pack adjacent archetypes must be a bounded list")
    adjacent: list[AdjacentArchetype] = []
    for index, value in enumerate(adjacent_raw):
        item = _mapping(value, f"adjacent_archetypes[{index}]")
        if set(item) != {"id", "label", "trigger_signals", "explanation"}:
            raise ValueError("Role pack adjacent archetype fields are invalid")
        adjacent.append(
            AdjacentArchetype(
                archetype_id=_identifier(item.get("id"), "adjacent archetype id"),
                label=_text(item.get("label"), "adjacent archetype label"),
                trigger_signals=_strings(
                    item.get("trigger_signals"),
                    "adjacent archetype trigger_signals",
                    allow_empty=False,
                    identifiers=True,
                ),
                explanation=_text(item.get("explanation"), "adjacent archetype explanation"),
            )
        )
    if len({item.archetype_id for item in adjacent}) != len(adjacent):
        raise ValueError("Role pack adjacent archetype ids must be unique")

    selection = _mapping(raw.get("selection"), "selection")
    if set(selection) != {"required_signals", "supporting_signals", "disqualifying_signals"}:
        raise ValueError("Role pack selection fields are invalid")
    required_signals = _strings(
        selection.get("required_signals"), "required_signals", identifiers=True
    )
    supporting_signals = _strings(
        selection.get("supporting_signals"), "supporting_signals", identifiers=True
    )
    disqualifying_signals = _strings(
        selection.get("disqualifying_signals"), "disqualifying_signals", identifiers=True
    )
    signal_groups = (set(required_signals), set(supporting_signals), set(disqualifying_signals))
    if any(
        left & right
        for index, left in enumerate(signal_groups)
        for right in signal_groups[index + 1 :]
    ):
        raise ValueError("Required, supporting, and disqualifying signals must remain distinct")
    if fallback and (required_signals or supporting_signals or disqualifying_signals):
        raise ValueError("Fallback pack selection must not pretend to understand a role family")
    if not fallback and not required_signals:
        raise ValueError("Non-fallback role packs require structural selection signals")

    requirements_raw = raw.get("requirements")
    if not isinstance(requirements_raw, list) or len(requirements_raw) > 40:
        raise ValueError("Role pack requirements must be a bounded list")
    requirements: list[RequirementRule] = []
    for index, value in enumerate(requirements_raw):
        item = _mapping(value, f"requirements[{index}]")
        if set(item) != {"id", "level", "impact", "statement", "evidence_question"}:
            raise ValueError("Role pack requirement fields are invalid")
        level = item.get("level")
        impact = item.get("impact")
        if level not in {"mandatory", "preferred", "optional"}:
            raise ValueError("Role pack requirement level is invalid")
        if impact not in {"hard_gate", "stretch", "evidence"}:
            raise ValueError("Role pack requirement impact is invalid")
        requirements.append(
            RequirementRule(
                requirement_id=_identifier(item.get("id"), "requirement id"),
                level=cast(RequirementLevel, level),
                impact=cast(RequirementImpact, impact),
                statement=_text(item.get("statement"), "requirement statement"),
                evidence_question=_text(item.get("evidence_question"), "evidence question"),
            )
        )
    if len({item.requirement_id for item in requirements}) != len(requirements):
        raise ValueError("Role pack requirement ids must be unique")
    if fallback and requirements:
        raise ValueError("Fallback packs preserve posting requirements instead of defining them")
    if not fallback and not any(
        item.level == "mandatory" and item.impact == "hard_gate" for item in requirements
    ):
        raise ValueError("Non-fallback role packs require a mandatory structural hard gate")

    misleading_raw = raw.get("misleading_patterns")
    if not isinstance(misleading_raw, list) or len(misleading_raw) > 30:
        raise ValueError("Role pack misleading_patterns must be a bounded list")
    misleading: list[Mapping[str, str]] = []
    for index, value in enumerate(misleading_raw):
        item = _mapping(value, f"misleading_patterns[{index}]")
        if set(item) != {"pattern", "caution"}:
            raise ValueError("Role pack misleading pattern fields are invalid")
        misleading.append(
            {
                "pattern": _text(item.get("pattern"), "misleading pattern"),
                "caution": _text(item.get("caution"), "misleading caution"),
            }
        )

    weights_raw = _mapping(raw.get("dimension_weights"), "dimension_weights")
    weights: dict[str, float] = {}
    for key, value in weights_raw.items():
        name = _identifier(key, "dimension weight id")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            raise ValueError("Role pack dimension weights must be non-negative numbers")
        weights[name] = float(value)
    if not fallback and (not weights or abs(sum(weights.values()) - 1.0) > 0.000001):
        raise ValueError("Non-fallback role pack dimension weights must sum to one")
    if fallback and weights:
        raise ValueError("Fallback packs cannot imply unsupported dimension weights")

    adjustments_raw = raw.get("bounded_adjustments")
    if not isinstance(adjustments_raw, list) or len(adjustments_raw) > 20:
        raise ValueError("Role pack bounded_adjustments must be a bounded list")
    adjustments: list[Mapping[str, object]] = []
    for index, value in enumerate(adjustments_raw):
        item = _mapping(value, f"bounded_adjustments[{index}]")
        if set(item) != {"id", "minimum", "maximum", "reason"}:
            raise ValueError("Role pack bounded adjustment fields are invalid")
        minimum, maximum = item.get("minimum"), item.get("maximum")
        if any(
            isinstance(bound, bool)
            or not isinstance(bound, (int, float))
            or not math.isfinite(float(bound))
            for bound in (minimum, maximum)
        ):
            raise ValueError("Role pack adjustment bounds must be finite numbers")
        minimum_value = float(cast(float, minimum))
        maximum_value = float(cast(float, maximum))
        if minimum_value > maximum_value:
            raise ValueError("Role pack adjustment minimum cannot exceed maximum")
        adjustments.append(
            {
                "id": _identifier(item.get("id"), "adjustment id"),
                "minimum": minimum_value,
                "maximum": maximum_value,
                "reason": _text(item.get("reason"), "adjustment reason"),
            }
        )

    return RolePack(
        pack_id=pack_id,
        version=version,
        label=_text(raw.get("label"), "label"),
        provenance=provenance,
        compatibility_contract=cast(int, engine_contract),
        replaces=replaces,
        fallback=fallback,
        confidence_cap=float(confidence_cap),
        application_readiness_allowed=readiness_allowed,
        structural_meaning=_text(raw.get("structural_meaning"), "structural_meaning"),
        role_family=_identifier(taxonomy.get("role_family"), "role_family"),
        title_patterns=_strings(
            taxonomy.get("title_patterns"), "title_patterns", allow_empty=fallback
        ),
        mandate_archetypes=_strings(
            taxonomy.get("mandate_archetypes"), "mandate_archetypes", identifiers=True
        ),
        adjacent_archetypes=tuple(adjacent),
        required_signals=required_signals,
        supporting_signals=supporting_signals,
        disqualifying_signals=disqualifying_signals,
        requirements=tuple(requirements),
        misleading_patterns=tuple(misleading),
        economics_questions=_strings(raw.get("economics_questions"), "economics_questions"),
        diligence_prompts=_strings(raw.get("diligence_prompts"), "diligence_prompts"),
        application_archetypes=_strings(
            raw.get("application_archetypes"), "application_archetypes", identifiers=True
        ),
        dimension_weights=weights,
        bounded_adjustments=tuple(adjustments),
        fingerprint=_fingerprint(raw),
    )


def load_bundled_role_packs() -> tuple[RolePack, ...]:
    package = files("career_os.role_pack_data")
    loaded: list[RolePack] = []
    for resource in sorted(package.iterdir(), key=lambda item: item.name):
        if not resource.name.endswith(".json") or resource.name == "role-pack.schema.json":
            continue
        content = resource.read_bytes()
        if len(content) > MAX_PACK_BYTES:
            raise ValueError(f"Role pack exceeds {MAX_PACK_BYTES} bytes: {resource.name}")
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid role-pack JSON: {resource.name}") from exc
        loaded.append(validate_role_pack(payload))
    if not loaded:
        raise ValueError("No bundled role packs were found")
    return tuple(loaded)


class RolePackRegistry:
    """Deterministic registry over validated role-pack versions."""

    def __init__(self, packs: Sequence[RolePack], *, max_adjacent_routes: int = 1) -> None:
        if not 0 <= max_adjacent_routes <= MAX_ADJACENT_PACKS:
            raise ValueError("Adjacent route limit must be between zero and two")
        identities = [(pack.pack_id, pack.version) for pack in packs]
        if not packs or len(identities) != len(set(identities)):
            raise ValueError("Role-pack registry identities must be unique")
        current_ids = [pack.pack_id for pack in packs]
        if len(current_ids) != len(set(current_ids)):
            raise ValueError("The role-pack registry supports one current version per pack id")
        fallbacks = [pack for pack in packs if pack.fallback]
        if len(fallbacks) != 1:
            raise ValueError("Role-pack registry requires exactly one conservative fallback")
        self._packs = {pack.pack_id: pack for pack in packs}
        self._fallback = fallbacks[0]
        self.max_adjacent_routes = max_adjacent_routes

    @property
    def packs(self) -> Mapping[RolePackId, RolePack]:
        return dict(self._packs)

    def get(self, pack_id: str) -> RolePack | None:
        return self._packs.get(cast(RolePackId, pack_id))

    @staticmethod
    def _score(pack: RolePack, signals: frozenset[str]) -> int:
        return (10 * len(signals & set(pack.required_signals))) + (
            2 * len(signals & set(pack.supporting_signals))
        )

    def select(self, signals: Collection[str]) -> RoleSelection:
        normalized = frozenset(_identifier(item, "posting signal") for item in signals)
        eligible = [
            pack
            for pack in self._packs.values()
            if not pack.fallback
            and set(pack.required_signals).issubset(normalized)
            and not set(pack.disqualifying_signals) & normalized
        ]
        if not eligible:
            missing = tuple(
                sorted(
                    {
                        signal
                        for pack in self._packs.values()
                        if not pack.fallback
                        for signal in pack.required_signals
                        if signal not in normalized
                    }
                )
            )
            return RoleSelection(
                status="fallback",
                pack_id=self._fallback.pack_id,
                pack_version=self._fallback.version,
                pack_fingerprint=self._fallback.fingerprint,
                matched_signals=tuple(sorted(normalized)),
                missing_required_signals=missing,
                adjacent_pack_ids=(),
                adjacent_archetype_ids=(),
                explanation=(
                    "No supported role-family pack passed every structural selection gate.",
                    "The conservative fallback preserves wording, caps confidence, and blocks application readiness.",
                ),
            )
        ranked = sorted(
            ((self._score(pack, normalized), pack.pack_id, pack) for pack in eligible),
            key=lambda item: (-item[0], item[1]),
        )
        top_score = ranked[0][0]
        top = [item for item in ranked if item[0] == top_score]
        if len(top) != 1:
            return RoleSelection(
                status="manual_review",
                pack_id=self._fallback.pack_id,
                pack_version=self._fallback.version,
                pack_fingerprint=self._fallback.fingerprint,
                matched_signals=tuple(sorted(normalized)),
                missing_required_signals=(),
                adjacent_pack_ids=(),
                adjacent_archetype_ids=(),
                explanation=(
                    "Multiple supported packs tied for the closest primary mandate.",
                    "Manual review is required; the fallback cannot authorize readiness.",
                ),
            )
        primary = ranked[0][2]
        adjacent_packs = tuple(item[1] for item in ranked[1 : 1 + self.max_adjacent_routes])
        archetypes = tuple(
            item.archetype_id
            for item in primary.adjacent_archetypes
            if set(item.trigger_signals) & normalized
        )[: self.max_adjacent_routes]
        return RoleSelection(
            status="selected",
            pack_id=primary.pack_id,
            pack_version=primary.version,
            pack_fingerprint=primary.fingerprint,
            matched_signals=tuple(
                sorted(
                    normalized & (set(primary.required_signals) | set(primary.supporting_signals))
                )
            ),
            missing_required_signals=(),
            adjacent_pack_ids=adjacent_packs,
            adjacent_archetype_ids=archetypes,
            explanation=(
                f"Selected {primary.pack_id} as the unique closest mandate after structural gates passed.",
                f"Applied role pack {primary.pack_id} v{primary.version} ({primary.fingerprint[:12]}).",
                f"Adjacent proof routes are capped at {self.max_adjacent_routes} for the reviewed registry.",
            ),
        )

    def select_for(self, pack_id: RolePackId, signals: Collection[str]) -> RoleSelection:
        automatic = self.select(signals)
        requested = self.get(pack_id)
        if requested is None:
            raise ValueError("Unsupported role pack")
        if pack_id == automatic.pack_id:
            return automatic
        if requested.fallback:
            return RoleSelection(
                status="fallback",
                pack_id=requested.pack_id,
                pack_version=requested.version,
                pack_fingerprint=requested.fingerprint,
                matched_signals=automatic.matched_signals,
                missing_required_signals=automatic.missing_required_signals,
                adjacent_pack_ids=(),
                adjacent_archetype_ids=(),
                explanation=(
                    "The user explicitly chose the conservative fallback instead of the deterministic supported pack.",
                    "Fallback confidence remains capped and application readiness remains blocked.",
                ),
            )
        raise ValueError("Selected role pack is not the deterministic primary mandate")

    def evaluate(
        self,
        selection: RoleSelection,
        posting: PostingProfile,
        overlay: CandidateOverlay,
        *,
        approved_evidence_ids: Collection[str],
    ) -> RoleEvaluation:
        pack = self.get(selection.pack_id)
        if (
            pack is None
            or selection.pack_version != pack.version
            or selection.pack_fingerprint != pack.fingerprint
        ):
            raise ValueError("Role-pack selection is stale and requires deterministic rescoring")
        signals = tuple(_identifier(item, "posting signal") for item in posting.signals)
        expected = self.select_for(selection.pack_id, signals)
        if expected != selection:
            raise ValueError("Role-pack selection trace does not match current posting evidence")
        requirement_ids = [item.requirement_id for item in posting.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("Posting requirements must have unique ids")
        rules = {item.requirement_id: item for item in pack.requirements}
        if not pack.fallback:
            if any(item not in rules for item in requirement_ids):
                raise ValueError("Posting requirement is not defined by the selected role pack")
            missing_hard_gates = {
                rule.requirement_id
                for rule in pack.requirements
                if rule.level == "mandatory" and rule.impact == "hard_gate"
            } - set(requirement_ids)
            if missing_hard_gates:
                raise ValueError("Posting profile omits a mandatory structural hard gate")
            if any(
                requirement.level != rules[requirement.requirement_id].level
                for requirement in posting.requirements
            ):
                raise ValueError("Posting profile cannot relabel a role-pack requirement")
        overlay_ids = (
            set(overlay.positive_evidence)
            | set(overlay.negative_evidence)
            | set(overlay.transferable_evidence)
        )
        if overlay_ids - set(requirement_ids):
            raise ValueError(
                "Candidate overlay cannot introduce or redefine structural requirements"
            )

        approved = set(approved_evidence_ids)
        assessments: list[RequirementAssessment] = []
        for requirement in posting.requirements:
            rule = pack.requirement(requirement.requirement_id)
            impact: RequirementImpact = rule.impact if rule is not None else "evidence"
            level = rule.level if rule is not None else requirement.level
            positive_configured = overlay.positive_evidence.get(requirement.requirement_id, ())
            negative_configured = overlay.negative_evidence.get(requirement.requirement_id, ())
            transferable_configured = overlay.transferable_evidence.get(
                requirement.requirement_id, ()
            )
            positive = tuple(item for item in positive_configured if item in approved)
            negative = tuple(item for item in negative_configured if item in approved)
            transferable = tuple(item for item in transferable_configured if item in approved)
            if positive and negative:
                outcome: EvidenceOutcome = "conflict"
                rationale = "Approved evidence both supports and contradicts this requirement."
            elif positive:
                outcome = "proven"
                rationale = "Approved direct candidate evidence supports this requirement."
            elif negative:
                outcome = "disproven"
                rationale = "Approved evidence establishes a genuine gap or boundary."
            elif transferable:
                outcome = "unknown"
                rationale = "Transferable evidence exists, but it is not promoted to direct proof."
            elif positive_configured or negative_configured or transferable_configured:
                outcome = "not_found"
                rationale = (
                    "Configured evidence was not approved; absence is not treated as disproof."
                )
            else:
                outcome = "unknown"
                rationale = "The posting asks for this, but the candidate overlay has no answer."
            assessments.append(
                RequirementAssessment(
                    requirement_id=requirement.requirement_id,
                    level=level,
                    impact=impact,
                    statement=requirement.statement,
                    outcome=outcome,
                    candidate_evidence_ids=(*positive, *negative),
                    transferable_evidence_ids=transferable,
                    posting_evidence_id=requirement.posting_evidence_id,
                    rationale=rationale,
                )
            )

        mandatory = [item for item in assessments if item.level == "mandatory"]
        preferred = [item for item in assessments if item.level == "preferred"]
        if pack.fallback or selection.status != "selected":
            recommendation: Recommendation = "hold"
        elif any(item.outcome in {"disproven", "conflict"} for item in mandatory):
            recommendation = "hold"
        elif any(item.outcome in {"unknown", "not_found"} for item in mandatory) or any(
            item.outcome in {"disproven", "conflict", "unknown", "not_found"} for item in preferred
        ):
            recommendation = "conditional"
        else:
            recommendation = "pursue"
        unresolved = tuple(
            item.requirement_id
            for item in assessments
            if item.outcome in {"conflict", "unknown", "not_found"}
        )
        gaps = tuple(
            dict.fromkeys(
                evidence_id
                for item in assessments
                if item.outcome == "disproven"
                for evidence_id in item.candidate_evidence_ids
            )
        )
        supported = tuple(
            dict.fromkeys(
                evidence_id
                for item in assessments
                if item.outcome == "proven"
                for evidence_id in item.candidate_evidence_ids
            )
        )
        transferable = tuple(
            dict.fromkeys(
                evidence_id
                for item in assessments
                for evidence_id in item.transferable_evidence_ids
            )
        )
        confidence = max(
            0.0,
            min(
                pack.confidence_cap,
                round(
                    0.9
                    - (0.08 * len(unresolved))
                    - (0.05 * len(gaps))
                    - (0.03 * len(transferable)),
                    2,
                ),
            ),
        )
        hard_gates_proven = all(
            item.outcome == "proven"
            for item in assessments
            if item.impact == "hard_gate" and item.level == "mandatory"
        )
        evaluation_fingerprint = _fingerprint(
            {
                "engine_contract": ROLE_PACK_ENGINE_CONTRACT,
                "pack_id": pack.pack_id,
                "pack_version": pack.version,
                "pack_fingerprint": pack.fingerprint,
                "posting_hash": posting.posting_hash,
                "overlay_fingerprint": overlay.fingerprint,
                "approved_evidence_ids": sorted(approved),
            }
        )
        return RoleEvaluation(
            selection=selection,
            recommendation=recommendation,
            confidence=confidence,
            assessments=tuple(assessments),
            supported_claim_ids=supported,
            transferable_evidence_ids=transferable,
            genuine_gap_ids=gaps,
            unknown_requirement_ids=unresolved,
            application_ready=(
                pack.application_readiness_allowed
                and selection.status == "selected"
                and posting.official_source_verified
                and recommendation in {"pursue", "conditional"}
                and hard_gates_proven
            ),
            evaluation_fingerprint=evaluation_fingerprint,
            explanation=(
                *selection.explanation,
                f"Evaluated {len(assessments)} posting requirements against an immutable candidate overlay.",
                "Direct proof, transferable evidence, genuine gaps, and missing evidence remain separate.",
            ),
        )


def bundled_role_pack_registry() -> RolePackRegistry:
    return RolePackRegistry(load_bundled_role_packs(), max_adjacent_routes=2)


def requires_rescore(previous_fingerprint: str, current: RoleEvaluation) -> bool:
    return previous_fingerprint != current.evaluation_fingerprint


def explain_bundled_role_packs(signals: Collection[str] = ()) -> dict[str, object]:
    registry = bundled_role_pack_registry()
    selection = registry.select(signals)
    return {
        "schema_version": ROLE_PACK_SCHEMA_VERSION,
        "engine_contract": ROLE_PACK_ENGINE_CONTRACT,
        "selection": asdict(selection),
        "packs": [pack.public_summary() for pack in registry.packs.values()],
        "candidate_data_included": False,
        "application_ready": False,
        "external_actions": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explain bundled Rolevane role-pack rules")
    parser.add_argument(
        "--signals",
        default="",
        help="Comma-separated canonical posting signals used for deterministic selection",
    )
    args = parser.parse_args(argv)
    signals = tuple(item.strip() for item in args.signals.split(",") if item.strip())
    print(json.dumps(explain_bundled_role_packs(signals), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CandidateOverlay",
    "EvidenceOutcome",
    "PostingProfile",
    "PostingRequirement",
    "Recommendation",
    "RequirementAssessment",
    "RequirementImpact",
    "RequirementLevel",
    "RoleEvaluation",
    "RolePack",
    "RolePackId",
    "RolePackRegistry",
    "RoleSelection",
    "SUPPORTED_ROLE_PACK_IDS",
    "bundled_role_pack_registry",
    "explain_bundled_role_packs",
    "load_bundled_role_packs",
    "requires_rescore",
    "validate_role_pack",
]

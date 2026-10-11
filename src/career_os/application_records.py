"""Pure application plan values and routes shared with private compatibility callers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ApplicationInvestment(StrEnum):
    STANDARD = "standard"
    TARGETED = "targeted"
    FLAGSHIP = "flagship"


class ArtifactRoute(StrEnum):
    CV_ONLY = "cv_only"
    CV_PLUS_SHORT_LETTER = "cv_plus_short_letter"
    CV_PLUS_POV = "cv_plus_pov"
    CV_PLUS_THREE_CASES = "cv_plus_three_cases"


class CultureCalibration(StrEnum):
    GERMAN_CONSERVATIVE = "german_conservative"
    INTERNATIONAL = "international"
    UNKNOWN = "unknown"


ROUTE_ARTIFACTS: dict[str, tuple[str, ...]] = {
    ArtifactRoute.CV_ONLY.value: ("cv",),
    ArtifactRoute.CV_PLUS_SHORT_LETTER.value: ("cv", "short_letter"),
    ArtifactRoute.CV_PLUS_POV.value: ("cv", "pov"),
    ArtifactRoute.CV_PLUS_THREE_CASES.value: ("cv", "case_portfolio"),
}


def _as_value(value: str | StrEnum) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)


def _strict_bool(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"Application field {field_name!r} must be a boolean")
    return value


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ApplicationEvidenceItem:
    need_id: str
    employer_need: str
    importance: str
    need_status: str
    evidence_class: str
    evidence_ids: tuple[str, ...]
    result: str
    candidate_owned: str
    collaborators: str
    boundary: str
    publication: str
    artifact_use: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "need_id": self.need_id,
            "employer_need": self.employer_need,
            "importance": self.importance,
            "need_status": self.need_status,
            "evidence_class": self.evidence_class,
            "evidence_ids": list(self.evidence_ids),
            "result": self.result,
            "candidate_owned": self.candidate_owned,
            "collaborators": self.collaborators,
            "boundary": self.boundary,
            "publication": self.publication,
            "artifact_use": list(self.artifact_use),
        }


@dataclass(frozen=True, slots=True)
class ApplicationPlan:
    job_key: str
    job_content_hash: str
    candidate_context_hashes: dict[str, str]
    language: str = "en"
    culture_calibration: str = CultureCalibration.UNKNOWN.value
    investment_tier: str = ApplicationInvestment.STANDARD.value
    artifact_route: str = ArtifactRoute.CV_ONLY.value
    route_rationale: str = "CV-only is the default until a supplement earns its place."
    opportunity_id: str | None = None
    renderer_version: str = "silver_rennsport_v2"
    problem_signals: tuple[str, ...] = ()
    evidence_items: tuple[ApplicationEvidenceItem, ...] = ()
    approved: bool = False
    claims_reviewed: bool = False
    visual_inspection_confirmed: bool = False
    application_submitted: bool = False
    outreach_sent: bool = False
    pipeline_changed: bool = False
    generated_at: str | None = None
    schema_version: int = 2

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ValueError("ApplicationPlan requires schema_version 2")
        if not isinstance(self.job_key, str) or not self.job_key.strip():
            raise ValueError("ApplicationPlan job_key must be a non-empty string")
        if self.opportunity_id is not None and (
            not isinstance(self.opportunity_id, str) or not self.opportunity_id.strip()
        ):
            raise ValueError("ApplicationPlan opportunity_id must be a non-empty string")
        if self.artifact_route not in ROUTE_ARTIFACTS:
            raise ValueError(f"Unsupported application artifact route: {self.artifact_route}")
        if self.investment_tier not in {item.value for item in ApplicationInvestment}:
            raise ValueError(f"Unsupported application investment tier: {self.investment_tier}")
        if self.culture_calibration not in {item.value for item in CultureCalibration}:
            raise ValueError(f"Unsupported culture calibration: {self.culture_calibration}")
        for field_name in (
            "approved",
            "claims_reviewed",
            "visual_inspection_confirmed",
            "application_submitted",
            "outreach_sent",
            "pipeline_changed",
        ):
            _strict_bool(getattr(self, field_name), f"application_plan.{field_name}")
        if self.application_submitted or self.outreach_sent or self.pipeline_changed:
            raise ValueError("Local application planning cannot assert an external event")

    @property
    def artifact_kinds(self) -> tuple[str, ...]:
        return ROUTE_ARTIFACTS[self.artifact_route]

    @property
    def plan_hash(self) -> str:
        return _hash_payload(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "opportunity_id": self.opportunity_id or self.job_key,
            "job_key": self.job_key,
            "job_content_hash": self.job_content_hash,
            "candidate_context_hashes": dict(self.candidate_context_hashes),
            "renderer_version": self.renderer_version,
            "language": self.language,
            "culture_calibration": self.culture_calibration,
            "investment_tier": self.investment_tier,
            "artifact_route": self.artifact_route,
            "route_rationale": self.route_rationale,
            "problem_signals": list(self.problem_signals),
            "artifacts": [{"kind": kind} for kind in self.artifact_kinds],
            "approved": self.approved,
            "claims_reviewed": self.claims_reviewed,
            "visual_inspection_confirmed": self.visual_inspection_confirmed,
            "application_submitted": self.application_submitted,
            "outreach_sent": self.outreach_sent,
            "pipeline_changed": self.pipeline_changed,
            "generated_at": self.generated_at,
        }
        if self.evidence_items:
            result["evidence_items"] = [item.to_dict() for item in self.evidence_items]
        if include_hash:
            result["application_plan_hash"] = self.plan_hash
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ApplicationPlan:
        if not isinstance(payload, dict) or payload.get("schema_version") != 2:
            raise ValueError("Application plan requires schema_version 2")
        raw_items = payload.get("evidence_items") or ()
        items = tuple(
            ApplicationEvidenceItem(
                need_id=str(item.get("need_id") or ""),
                employer_need=str(item.get("employer_need") or ""),
                importance=str(item.get("importance") or ""),
                need_status=str(item.get("need_status") or ""),
                evidence_class=str(item.get("evidence_class") or ""),
                evidence_ids=tuple(str(value) for value in item.get("evidence_ids") or ()),
                result=str(item.get("result") or ""),
                candidate_owned=str(item.get("candidate_owned") or ""),
                collaborators=str(item.get("collaborators") or ""),
                boundary=str(item.get("boundary") or ""),
                publication=str(item.get("publication") or ""),
                artifact_use=tuple(str(value) for value in item.get("artifact_use") or ()),
            )
            for item in raw_items
            if isinstance(item, dict)
        )
        if "candidate_context_hashes" not in payload:
            raw_context_hashes: Any = {}
        else:
            raw_context_hashes = payload["candidate_context_hashes"]
            if not isinstance(raw_context_hashes, dict):
                raise ValueError("application_plan.candidate_context_hashes must be an object")
        raw_job_key = payload.get("job_key")
        if not isinstance(raw_job_key, str) or not raw_job_key.strip():
            raise ValueError("application_plan.job_key must be a non-empty string")
        raw_opportunity_id = payload.get("opportunity_id")
        if raw_opportunity_id in (None, ""):
            raw_opportunity_id = raw_job_key
        if not isinstance(raw_opportunity_id, str) or not raw_opportunity_id.strip():
            raise ValueError("application_plan.opportunity_id must be a non-empty string")
        plan = cls(
            schema_version=2,
            opportunity_id=raw_opportunity_id.strip(),
            job_key=raw_job_key.strip(),
            job_content_hash=str(payload.get("job_content_hash") or ""),
            candidate_context_hashes={
                str(key): str(value) for key, value in raw_context_hashes.items()
            },
            renderer_version=str(payload.get("renderer_version") or "silver_rennsport_v2"),
            language=str(payload.get("language") or "en"),
            culture_calibration=str(
                payload.get("culture_calibration") or CultureCalibration.UNKNOWN.value
            ),
            investment_tier=str(
                payload.get("investment_tier") or ApplicationInvestment.STANDARD.value
            ),
            artifact_route=str(payload.get("artifact_route") or ArtifactRoute.CV_ONLY.value),
            route_rationale=str(payload.get("route_rationale") or ""),
            problem_signals=tuple(str(value) for value in payload.get("problem_signals") or ()),
            evidence_items=items,
            approved=_strict_bool(payload.get("approved", False), "application_plan.approved"),
            claims_reviewed=_strict_bool(
                payload.get("claims_reviewed", False), "application_plan.claims_reviewed"
            ),
            visual_inspection_confirmed=_strict_bool(
                payload.get("visual_inspection_confirmed", False),
                "application_plan.visual_inspection_confirmed",
            ),
            application_submitted=_strict_bool(
                payload.get("application_submitted", False),
                "application_plan.application_submitted",
            ),
            outreach_sent=_strict_bool(
                payload.get("outreach_sent", False), "application_plan.outreach_sent"
            ),
            pipeline_changed=_strict_bool(
                payload.get("pipeline_changed", False), "application_plan.pipeline_changed"
            ),
            generated_at=payload.get("generated_at"),
        )
        expected_hash = payload.get("application_plan_hash")
        if expected_hash and expected_hash != plan.plan_hash:
            raise ValueError("Application plan hash does not match its content")
        return plan


def route_application_artifacts(
    *,
    investment_tier: str | ApplicationInvestment = ApplicationInvestment.STANDARD,
    culture_calibration: str | CultureCalibration = CultureCalibration.UNKNOWN,
    route: str = "auto",
    letter_requested: bool = False,
    application_form_supports_letter: bool = False,
    transition_or_gap: bool = False,
    flagship_builder_problem: bool = False,
    directly_relevant_evidence: int = 0,
    supports_supplement: bool = False,
    distinct_case_needs: int = 0,
    all_cases_anonymizable: bool = False,
    hard_gates_clear: bool = True,
    approved: bool = False,
) -> ArtifactRoute:
    """Choose a route; explicit supplements require their documented gates."""

    for field_name, value in (
        ("letter_requested", letter_requested),
        ("application_form_supports_letter", application_form_supports_letter),
        ("transition_or_gap", transition_or_gap),
        ("flagship_builder_problem", flagship_builder_problem),
        ("supports_supplement", supports_supplement),
        ("all_cases_anonymizable", all_cases_anonymizable),
        ("hard_gates_clear", hard_gates_clear),
        ("approved", approved),
    ):
        _strict_bool(value, f"route_application_artifacts.{field_name}")
    tier = _as_value(investment_tier)
    culture = _as_value(culture_calibration)
    if tier not in {item.value for item in ApplicationInvestment}:
        raise ValueError(f"Unsupported application investment tier: {tier}")
    if culture not in {item.value for item in CultureCalibration}:
        raise ValueError(f"Unsupported culture calibration: {culture}")
    normalized = route.replace("-", "_").casefold()
    aliases = {
        "cv": ArtifactRoute.CV_ONLY.value,
        "cv_only": ArtifactRoute.CV_ONLY.value,
        "cv_letter": ArtifactRoute.CV_PLUS_SHORT_LETTER.value,
        "cv_plus_short_letter": ArtifactRoute.CV_PLUS_SHORT_LETTER.value,
        "cv_pov": ArtifactRoute.CV_PLUS_POV.value,
        "cv_plus_pov": ArtifactRoute.CV_PLUS_POV.value,
        "cv_cases": ArtifactRoute.CV_PLUS_THREE_CASES.value,
        "cv_plus_three_cases": ArtifactRoute.CV_PLUS_THREE_CASES.value,
    }
    if normalized not in {"auto", *aliases}:
        raise ValueError(f"Unsupported application route: {route}")
    if not hard_gates_clear:
        if normalized not in {"auto", "cv", "cv_only"}:
            raise ValueError("Supplement routes require clear opportunity gates")
        return ArtifactRoute.CV_ONLY
    if normalized in aliases:
        selected = aliases[normalized]
        if selected != ArtifactRoute.CV_ONLY.value and not approved:
            raise ValueError("Supplement route requires explicit approval")
        if selected == ArtifactRoute.CV_PLUS_SHORT_LETTER.value and not (
            letter_requested or application_form_supports_letter or transition_or_gap
        ):
            raise ValueError("A short letter requires a channel, motivation, or transition reason")
        if selected == ArtifactRoute.CV_PLUS_POV.value and not (
            tier == ApplicationInvestment.FLAGSHIP
            and flagship_builder_problem
            and directly_relevant_evidence >= 2
            and supports_supplement
        ):
            raise ValueError(
                "POV route does not meet flagship, problem, evidence, and channel gates"
            )
        if selected == ArtifactRoute.CV_PLUS_THREE_CASES.value and not (
            tier in {ApplicationInvestment.TARGETED.value, ApplicationInvestment.FLAGSHIP.value}
            and distinct_case_needs >= 3
            and all_cases_anonymizable
            and supports_supplement
        ):
            raise ValueError(
                "Case route requires three distinct anonymizable needs and a supported channel"
            )
        return ArtifactRoute(selected)
    if letter_requested or application_form_supports_letter or transition_or_gap:
        return (
            ArtifactRoute.CV_PLUS_SHORT_LETTER
            if approved and tier != ApplicationInvestment.STANDARD.value
            else ArtifactRoute.CV_ONLY
        )
    # Auto never silently adds a proof supplement.  This is the core default.
    return ArtifactRoute.CV_ONLY


def plan_from_mapping(payload: dict[str, Any]) -> ApplicationPlan:
    """Small public alias used by CLI/dashboard adapters."""

    return ApplicationPlan.from_dict(payload)

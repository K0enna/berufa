"""Fully fictional posting and overlay bindings for role-pack regression proof."""

from __future__ import annotations

import hashlib
from typing import Literal

from career_os.models import JobPosting
from career_os.role_packs import (
    CandidateOverlay,
    PostingProfile,
    PostingRequirement,
    RolePackId,
    bundled_role_pack_registry,
)

SYNTHETIC_POSTING_FIXTURE_VERSION = "2.0.1"

SYNTHETIC_POSTING = JobPosting(
    provider="synthetic_official",
    source_id="northstar-atlas-official-role-1",
    external_id="customer-operations-director-2030",
    company="Northstar Atlas Software",
    title="Director of Customer Operations",
    url="https://jobs.example.invalid/northstar-atlas/customer-operations-director-2030",
    location="Berlin or remote within Germany",
    description=(
        "SYNTHETIC OFFICIAL POSTING. Lead an eight-person post-sales team, own service "
        "operating reviews, improve onboarding, partner with Sales without directly leading "
        "new-logo Account Executives, and bring a completed degree or equivalent experience. "
        "Prior direct leadership experience with quota-carrying new-logo Account Executives is "
        "preferred; departmental budget ownership is additional useful experience."
    ),
    observed_at="2030-01-02T09:30:00+00:00",
)

SYNTHETIC_POSTING_PROFILE = PostingProfile(
    opportunity_id=SYNTHETIC_POSTING.job_key,
    posting_hash=SYNTHETIC_POSTING.content_hash,
    source_id=SYNTHETIC_POSTING.source_id,
    official_source_verified=True,
    signals=(
        "post_sales_scope",
        "people_leadership_current",
        "customer_delivery_ownership",
        "customer_operations_ownership",
    ),
    requirements=(
        PostingRequirement(
            "post_sales_team_leadership",
            "mandatory",
            "Lead a post-sales team and its operating cadence.",
            "posting:post_sales_team_leadership",
        ),
        PostingRequirement(
            "service_operating_reviews",
            "mandatory",
            "Own service operating reviews and customer-delivery performance.",
            "posting:service_operating_reviews",
        ),
        PostingRequirement(
            "onboarding_improvement",
            "preferred",
            "Show measurable customer-onboarding improvement.",
            "posting:onboarding_improvement",
        ),
        PostingRequirement(
            "new_logo_ae_leadership",
            "preferred",
            "Bring prior direct leadership experience with quota-carrying new-logo Account Executives.",
            "posting:new_logo_ae_leadership",
        ),
        PostingRequirement(
            "completed_degree",
            "optional",
            "Hold a completed bachelor's degree or equivalent experience.",
            "posting:completed_degree",
        ),
        PostingRequirement(
            "budget_ownership",
            "optional",
            "Own a departmental budget.",
            "posting:budget_ownership",
        ),
    ),
)

SYNTHETIC_POSITIVE_EVIDENCE = {
    "post_sales_team_leadership": ("synthetic-responsibility",),
    "service_operating_reviews": ("synthetic-responsibility",),
    "onboarding_improvement": ("synthetic-achievement",),
}
SYNTHETIC_NEGATIVE_EVIDENCE = {
    "new_logo_ae_leadership": ("synthetic-gap",),
    "completed_degree": ("synthetic-education", "synthetic-forbidden-degree"),
}

SyntheticEvidenceMode = Literal["positive", "negative", "ambiguous", "conflicting"]

SYNTHETIC_GATE_D_SIGNAL_CASES: dict[RolePackId, dict[str, tuple[str, ...]]] = {
    "commercial_leadership": {
        "positive": (
            "commercial_leadership_scope",
            "people_leadership_current",
            "existing_customer_growth_ownership",
            "new_logo_sales_ownership",
            "revenue_operating_ownership",
        ),
        "negative": ("commercial_leadership_scope", "no_current_people_leadership"),
        "ambiguous": (
            "commercial_leadership_scope",
            "people_leadership_current",
            "product_leadership_scope",
        ),
        "adversarial": ("people_leadership_current",),
    },
    "product_leadership": {
        "positive": (
            "people_leadership_current",
            "product_discovery_ownership",
            "product_leadership_scope",
            "product_operations_ownership",
            "product_strategy_ownership",
        ),
        "negative": ("no_current_people_leadership", "product_leadership_scope"),
        "ambiguous": (
            "people_leadership_current",
            "product_leadership_scope",
            "services_solutions_leadership_scope",
        ),
        "adversarial": ("people_leadership_current", "product_strategy_ownership"),
    },
    "services_solutions_leadership": {
        "positive": (
            "implementation_delivery_ownership",
            "people_leadership_current",
            "services_solutions_leadership_scope",
            "solution_consulting_ownership",
            "technical_customer_leadership",
        ),
        "negative": (
            "no_current_people_leadership",
            "services_solutions_leadership_scope",
        ),
        "ambiguous": (
            "commercial_leadership_scope",
            "people_leadership_current",
            "services_solutions_leadership_scope",
        ),
        "adversarial": ("people_leadership_current", "technical_customer_leadership"),
    },
    "senior_expert_ic": {
        "positive": (
            "cross_functional_program_leadership",
            "functional_authority",
            "individual_contributor_current",
            "principal_advisory_authority",
            "senior_expert_scope",
        ),
        "negative": (
            "individual_contributor_current",
            "people_management_required",
            "senior_expert_scope",
        ),
        "ambiguous": ("senior_expert_scope",),
        "adversarial": (
            "people_leadership_current",
            "people_leadership_primary_mandate",
            "senior_expert_scope",
        ),
    },
}


def synthetic_role_pack_profile(
    pack_id: RolePackId,
    *,
    case: str = "positive",
    official_source_verified: bool = True,
) -> PostingProfile:
    """Build a fictional, source-bound profile for one reviewed pack and signal case."""
    pack = bundled_role_pack_registry().get(pack_id)
    if pack is None or pack.fallback or pack_id not in SYNTHETIC_GATE_D_SIGNAL_CASES:
        raise ValueError("Synthetic Gate D profiles require a reviewed founding-cohort pack")
    cases = SYNTHETIC_GATE_D_SIGNAL_CASES[pack_id]
    if case not in cases:
        raise ValueError("Unsupported synthetic role-pack case")
    identity = f"synthetic:{pack_id}:{case}:{SYNTHETIC_POSTING_FIXTURE_VERSION}"
    return PostingProfile(
        opportunity_id=identity,
        posting_hash=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        source_id=f"synthetic-{pack_id}-{case}",
        official_source_verified=official_source_verified,
        signals=cases[case],
        requirements=tuple(
            PostingRequirement(
                requirement_id=rule.requirement_id,
                level=rule.level,
                statement=rule.statement,
                posting_evidence_id=f"posting:{pack_id}:{rule.requirement_id}",
            )
            for rule in pack.requirements
        ),
    )


def synthetic_role_pack_overlay(
    pack_id: RolePackId,
    *,
    mode: SyntheticEvidenceMode,
) -> CandidateOverlay:
    """Create fictional evidence bindings without changing any structural pack rule."""
    pack = bundled_role_pack_registry().get(pack_id)
    if pack is None or pack.fallback or pack_id not in SYNTHETIC_GATE_D_SIGNAL_CASES:
        raise ValueError("Synthetic Gate D overlays require a reviewed founding-cohort pack")
    evidence = {
        rule.requirement_id: (f"synthetic:{pack_id}:{rule.requirement_id}",)
        for rule in pack.requirements
    }
    first_hard_gate = next(
        rule.requirement_id
        for rule in pack.requirements
        if rule.level == "mandatory" and rule.impact == "hard_gate"
    )
    positive: dict[str, tuple[str, ...]] = {}
    negative: dict[str, tuple[str, ...]] = {}
    transferable: dict[str, tuple[str, ...]] = {}
    if mode == "positive":
        positive = evidence
    elif mode == "negative":
        positive = {key: value for key, value in evidence.items() if key != first_hard_gate}
        negative = {first_hard_gate: evidence[first_hard_gate]}
    elif mode == "ambiguous":
        positive = {key: value for key, value in evidence.items() if key != first_hard_gate}
        transferable = {first_hard_gate: evidence[first_hard_gate]}
    elif mode == "conflicting":
        positive = evidence
        negative = {first_hard_gate: evidence[first_hard_gate]}
    else:  # pragma: no cover - Literal plus runtime guard for untyped callers
        raise ValueError("Unsupported synthetic evidence mode")
    return CandidateOverlay(
        positive_evidence=positive,
        negative_evidence=negative,
        transferable_evidence=transferable,
        constraints={"currency": "SYNTHETIC"},
        context_fingerprints={"synthetic": SYNTHETIC_POSTING_FIXTURE_VERSION},
    )


def synthetic_candidate_overlay(
    *,
    constraints: dict[str, object],
    context_fingerprints: dict[str, str],
) -> CandidateOverlay:
    """Bind fictional candidate evidence without changing structural pack semantics."""
    return CandidateOverlay(
        positive_evidence=SYNTHETIC_POSITIVE_EVIDENCE,
        negative_evidence=SYNTHETIC_NEGATIVE_EVIDENCE,
        transferable_evidence={},
        constraints=constraints,
        context_fingerprints=context_fingerprints,
    )


__all__ = [
    "SYNTHETIC_NEGATIVE_EVIDENCE",
    "SYNTHETIC_POSITIVE_EVIDENCE",
    "SYNTHETIC_GATE_D_SIGNAL_CASES",
    "SYNTHETIC_POSTING",
    "SYNTHETIC_POSTING_FIXTURE_VERSION",
    "SYNTHETIC_POSTING_PROFILE",
    "synthetic_candidate_overlay",
    "synthetic_role_pack_overlay",
    "synthetic_role_pack_profile",
]

"""Versioned fictional source and deterministic proposals for the product journey."""

from __future__ import annotations

from dataclasses import dataclass

from career_os.evidence_contract import EvidenceContractMetadata
from career_os.evidence_vault import EvidenceRevision, EvidenceVault, SourceDocument

SYNTHETIC_PERSONA_VERSION = "1.0.0"
SYNTHETIC_SOURCE_NAME = "mara-voss-synthetic-career-note.txt"
SYNTHETIC_SOURCE_LINES = (
    "SYNTHETIC DEMO DOCUMENT — all people, employers, and metrics are fictional.",
    "Name: Mara Voss (fictional persona).",
    "Role: Director of Client Operations at Northstar Atlas Software from 2021 to present.",
    "Northstar Atlas Software is a fictional B2B workflow company created only for testing.",
    "Responsibility: Mara leads an eight-person post-sales team and owns service operating reviews.",
    "Decision rights: pricing and new-logo sales targets remain outside Mara's authority.",
    "Outcome: the team reduced median onboarding time by approximately 18 percent during 2024.",
    "Measurement: team outcome, percent, calendar year 2024, customer onboarding cohort.",
    (
        "Education: coursework toward a B.Sc. in Information Systems at Fictional Metropolitan "
        "Institute; no degree was awarded."
    ),
    "Publication: operational metrics are suitable for direct applications and interviews only.",
    "Gap: Mara has not directly led quota-carrying Account Executives.",
    "Forbidden claim: do not state that Mara completed a bachelor's degree.",
)
SYNTHETIC_SOURCE_BYTES = ("\n".join(SYNTHETIC_SOURCE_LINES) + "\n").encode("utf-8")


@dataclass(frozen=True, slots=True)
class SyntheticProposal:
    evidence_id: str
    kind: str
    statement: str
    details: dict[str, str]
    start_line: int
    end_line: int
    ownership: str
    confidence: str
    approximation: str
    publication_boundary: str
    metadata: EvidenceContractMetadata | None = None


@dataclass(frozen=True, slots=True)
class SyntheticCareerPattern:
    pattern_id: str
    source_name: str
    source_bytes: bytes
    proposals: tuple[SyntheticProposal, ...]


SYNTHETIC_PROPOSALS = (
    SyntheticProposal(
        evidence_id="synthetic-role-timeline",
        kind="role_timeline",
        statement=(
            "Mara Voss has served as Director of Client Operations at Northstar Atlas "
            "Software since 2021."
        ),
        details={
            "employer": "Northstar Atlas Software",
            "title": "Director of Client Operations",
            "start_date": "2021",
            "end_date": "present",
        },
        start_line=3,
        end_line=4,
        ownership="personal",
        confidence="exact",
        approximation="exact",
        publication_boundary="application_and_interview",
    ),
    SyntheticProposal(
        evidence_id="synthetic-responsibility",
        kind="responsibility",
        statement=(
            "Mara leads an eight-person post-sales team and owns service operating reviews; "
            "pricing and new-logo sales targets are outside her authority."
        ),
        details={
            "responsibility": "Lead an eight-person post-sales team",
            "decision_right": "Own service operating reviews, excluding pricing and new-logo sales",
        },
        start_line=5,
        end_line=6,
        ownership="personal",
        confidence="exact",
        approximation="exact",
        publication_boundary="application_and_interview",
    ),
    SyntheticProposal(
        evidence_id="synthetic-achievement",
        kind="achievement",
        statement=(
            "Mara's team reduced median customer onboarding time by approximately 18 percent "
            "during 2024."
        ),
        details={
            "outcome": "Reduced median customer onboarding time",
            "metric_value": "18",
            "metric_unit": "percent",
            "metric_period": "calendar year 2024",
            "metric_scope": "team outcome for the customer onboarding cohort",
        },
        start_line=7,
        end_line=8,
        ownership="team",
        confidence="approximate",
        approximation="approximate",
        publication_boundary="application_and_interview",
    ),
    SyntheticProposal(
        evidence_id="synthetic-education",
        kind="education",
        statement=(
            "Mara completed coursework toward a B.Sc. in Information Systems; no degree was awarded."
        ),
        details={
            "institution": "Fictional Metropolitan Institute",
            "program": "B.Sc. in Information Systems",
            "completion_status": "coursework",
        },
        start_line=9,
        end_line=9,
        ownership="personal",
        confidence="exact",
        approximation="exact",
        publication_boundary="application_and_interview",
    ),
    SyntheticProposal(
        evidence_id="synthetic-gap",
        kind="gap",
        statement="Mara has not directly led quota-carrying Account Executives.",
        details={"gap": "No direct leadership of quota-carrying Account Executives"},
        start_line=11,
        end_line=11,
        ownership="personal",
        confidence="exact",
        approximation="exact",
        publication_boundary="private",
    ),
    SyntheticProposal(
        evidence_id="synthetic-forbidden-degree",
        kind="forbidden_claim",
        statement="Do not claim that Mara completed a bachelor's degree.",
        details={"claim": "Completed a bachelor's degree"},
        start_line=12,
        end_line=12,
        ownership="personal",
        confidence="exact",
        approximation="exact",
        publication_boundary="private",
    ),
)

PORTFOLIO_SOURCE_BYTES = (
    "SYNTHETIC DEMO DOCUMENT — all people and organizations are fictional.\n"
    "Name: Arun Mehta (fictional persona).\n"
    "Role: independent service designer at Juniper Harbour from 2022 to present.\n"
    "Concurrent role: part-time lecturer at Eastlake Studio School from 2023 to present.\n"
    "Metric: portfolio revenue was approximately EUR 84000 in 2025 across all clients.\n"
).encode()
PORTFOLIO_PROPOSALS = (
    SyntheticProposal(
        evidence_id="portfolio-consulting-role",
        kind="role_timeline",
        statement="Arun has worked as an independent service designer since 2022.",
        details={
            "employer": "Juniper Harbour",
            "title": "Independent service designer",
            "start_date": "2022",
            "end_date": "present",
        },
        start_line=3,
        end_line=3,
        ownership="personal",
        confidence="exact",
        approximation="exact",
        publication_boundary="public",
        metadata=EvidenceContractMetadata(
            origin_ownership="personal",
            role_family_relevance=("service design",),
            intended_channels=("application", "interview", "public"),
        ),
    ),
    SyntheticProposal(
        evidence_id="portfolio-teaching-role",
        kind="role_timeline",
        statement="Arun has concurrently taught part-time since 2023.",
        details={
            "employer": "Eastlake Studio School",
            "title": "Part-time lecturer",
            "start_date": "2023",
            "end_date": "present",
        },
        start_line=4,
        end_line=4,
        ownership="personal",
        confidence="exact",
        approximation="exact",
        publication_boundary="public",
    ),
    SyntheticProposal(
        evidence_id="portfolio-revenue-metric",
        kind="metric",
        statement="Arun's fictional portfolio revenue was approximately EUR 84,000 in 2025.",
        details={
            "value": "84000",
            "unit": "currency",
            "period": "2025",
            "scope": "all portfolio clients",
            "currency": "EUR",
        },
        start_line=5,
        end_line=5,
        ownership="personal",
        confidence="approximate",
        approximation="approximate",
        publication_boundary="application_and_interview",
    ),
)

TRANSITION_SOURCE_BYTES = (
    "SYNTHETIC DEMO DOCUMENT — all people and organizations are fictional.\n"
    "Name: Lucia Romero (fictional persona).\n"
    "Skill: customer research in Spanish and English for public-sector services.\n"
    "Objective: move from public-sector research into climate-software product operations.\n"
    "Gap: no direct climate-software industry experience.\n"
).encode()
TRANSITION_PROPOSALS = (
    SyntheticProposal(
        evidence_id="transition-research-skill",
        kind="skill_domain",
        statement="Lucia conducts customer research in Spanish and English.",
        details={
            "skill": "Customer research",
            "domain": "Public-sector services",
            "evidence_level": "direct",
            "customer_environment": "Spanish- and English-language users",
        },
        start_line=3,
        end_line=3,
        ownership="personal",
        confidence="exact",
        approximation="exact",
        publication_boundary="public",
        metadata=EvidenceContractMetadata(
            origin_ownership="personal",
            role_family_relevance=("product operations",),
        ),
    ),
    SyntheticProposal(
        evidence_id="transition-objective",
        kind="career_objective",
        statement="Lucia is targeting climate-software product operations roles.",
        details={"objective": "Climate-software product operations", "horizon": "next role"},
        start_line=4,
        end_line=4,
        ownership="personal",
        confidence="exact",
        approximation="exact",
        publication_boundary="application_and_interview",
    ),
    SyntheticProposal(
        evidence_id="transition-industry-gap",
        kind="gap",
        statement="Lucia has no direct climate-software industry experience.",
        details={"gap": "No direct climate-software industry experience"},
        start_line=5,
        end_line=5,
        ownership="personal",
        confidence="exact",
        approximation="exact",
        publication_boundary="private",
    ),
)

SYNTHETIC_CAREER_PATTERNS = {
    "operator_leader": SyntheticCareerPattern(
        "operator_leader", SYNTHETIC_SOURCE_NAME, SYNTHETIC_SOURCE_BYTES, SYNTHETIC_PROPOSALS
    ),
    "portfolio_career": SyntheticCareerPattern(
        "portfolio_career",
        "arun-mehta-synthetic-note.txt",
        PORTFOLIO_SOURCE_BYTES,
        PORTFOLIO_PROPOSALS,
    ),
    "career_transition": SyntheticCareerPattern(
        "career_transition",
        "lucia-romero-synthetic-note.txt",
        TRANSITION_SOURCE_BYTES,
        TRANSITION_PROPOSALS,
    ),
}


def seed_synthetic_evidence(
    vault: EvidenceVault,
) -> tuple[SourceDocument, tuple[EvidenceRevision, ...]]:
    """Import the fictional document and idempotently create six review cards."""
    return seed_synthetic_pattern(vault, "operator_leader")


def seed_synthetic_pattern(
    vault: EvidenceVault,
    pattern_id: str,
) -> tuple[SourceDocument, tuple[EvidenceRevision, ...]]:
    """Import one candidate-neutral fictional pattern and create its review cards."""
    try:
        pattern = SYNTHETIC_CAREER_PATTERNS[pattern_id]
    except KeyError as exc:
        raise ValueError(f"Unknown synthetic career pattern: {pattern_id}") from exc
    source = vault.import_document(
        display_name=pattern.source_name,
        media_type="text/plain",
        content=pattern.source_bytes,
    )
    existing = {record.evidence_id for record in vault.enumerate()}
    for proposal in pattern.proposals:
        if proposal.evidence_id in existing:
            continue
        vault.propose(
            evidence_id=proposal.evidence_id,
            source_id=source.source_id,
            kind=proposal.kind,  # type: ignore[arg-type]
            statement=proposal.statement,
            details=proposal.details,
            source_start_line=proposal.start_line,
            source_end_line=proposal.end_line,
            ownership=proposal.ownership,  # type: ignore[arg-type]
            confidence=proposal.confidence,  # type: ignore[arg-type]
            approximation=proposal.approximation,  # type: ignore[arg-type]
            publication_boundary=proposal.publication_boundary,  # type: ignore[arg-type]
            metadata=proposal.metadata,
        )
    return source, tuple(vault.latest(item.evidence_id) for item in pattern.proposals)


__all__ = [
    "SYNTHETIC_PERSONA_VERSION",
    "SYNTHETIC_PROPOSALS",
    "SYNTHETIC_CAREER_PATTERNS",
    "SYNTHETIC_SOURCE_BYTES",
    "SYNTHETIC_SOURCE_NAME",
    "seed_synthetic_evidence",
    "seed_synthetic_pattern",
]

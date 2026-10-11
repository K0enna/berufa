"""Candidate-neutral authority and policy ports for one Rolevane runtime."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from career_os.evidence_contract import (
    PublicationReadiness,
    PublicationRequest,
    RoleEvidenceSummary,
)
from career_os.models import JobPosting

EvidenceStatus = Literal[
    "approved",
    "unapproved",
    "rejected",
    "deferred",
    "revoked",
    "superseded",
]
PublicationUse = Literal["internal", "application", "interview", "public"]
ProviderPurpose = Literal["lead", "official"]
WorkspaceKind = Literal["application", "interview"]


@dataclass(frozen=True, slots=True)
class CandidateEvidenceRecord:
    evidence_id: str
    statement: str
    provenance: str
    source_hash: str
    publication_boundary: str
    status: EvidenceStatus
    source_heading: str | None = None
    supersedes: str | None = None
    revision: int = 1
    content_hash: str = ""
    ownership: str = "unknown"
    confidence: str = "unknown"
    approximation: str = "unknown"
    source_id: str | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None


@dataclass(frozen=True, slots=True)
class CareerEventProposal:
    proposal_id: str
    opportunity_id: str
    kind: str
    occurred_at: str
    summary: str
    next_action: str | None = None
    due_date: str | None = None
    requires_user_confirmation: bool = True
    external_event_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class CareerEventRecord:
    event_id: str
    opportunity_id: str
    kind: str
    occurred_at: str
    summary: str
    confirmed_by: str
    confirmed_at: str


@dataclass(frozen=True, slots=True)
class OpportunityRecord:
    opportunity_id: str
    company: str
    role: str
    stage: str | None = None
    status: str | None = None
    job_url: str | None = None
    job_key: str | None = None
    workspace_path: str | None = None
    source_hash: str | None = None
    official_source_verified: bool = False
    posting: JobPosting | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    workspace_id: str
    opportunity_id: str
    kind: WorkspaceKind
    path: Path
    manifest_hash: str
    validated: bool


@dataclass(frozen=True, slots=True)
class WorkspaceDocument:
    document_id: str
    workspace_id: str
    relative_path: str
    content_hash: str
    media_type: str
    content: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class WorkspacePlan:
    plan_id: str
    opportunity_id: str
    kind: WorkspaceKind
    target: Path
    requires_user_confirmation: bool = True
    filesystem_changed: bool = False


@dataclass(frozen=True, slots=True)
class SourceSpec:
    source_id: str
    provider: str
    authority: ProviderPurpose
    company: str | None = None
    endpoint: str | None = None
    enabled: bool = False
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderAccess:
    allowed: bool
    purpose: ProviderPurpose
    network_allowed: bool
    credential_ref: str | None = None
    budget_remaining: int | None = None


@dataclass(frozen=True, slots=True)
class ModelAccess:
    allowed: bool
    provider: str | None
    model: str | None
    credential_ref: str | None = None


@dataclass(frozen=True, slots=True)
class OpportunityEvaluation:
    opportunity_id: str
    posting: JobPosting
    recommendation: str
    score: float
    confidence: float
    reasons: tuple[str, ...]
    risks: tuple[str, ...] = ()
    requires_user_confirmation: bool = True


@dataclass(frozen=True, slots=True)
class DecisionItem:
    opportunity_id: str
    company: str
    role: str
    recommendation: str
    reason: str
    action_route: str
    requires_user_confirmation: bool = True


@dataclass(frozen=True, slots=True)
class DecisionProjection:
    generated_at: str
    read_only: bool
    external_actions: bool
    pipeline_changed: bool
    scoring_changed: bool
    items: tuple[DecisionItem, ...]


class CandidateEvidenceAuthority(Protocol):
    instance_root: Path

    def resolve(self, evidence_id: str) -> CandidateEvidenceRecord: ...

    def enumerate(
        self,
        *,
        statuses: Collection[EvidenceStatus] | None = None,
        publication_use: PublicationUse | None = None,
    ) -> tuple[CandidateEvidenceRecord, ...]: ...

    def context_fingerprints(self) -> Mapping[str, str]: ...

    def export_records(self) -> tuple[CandidateEvidenceRecord, ...]: ...

    def validate_publication(self, request: PublicationRequest) -> PublicationReadiness: ...

    def classify_for_role(
        self,
        role_family: str,
        *,
        required_evidence_ids: Collection[str] = (),
    ) -> RoleEvidenceSummary: ...

    def deletion_inventory(self) -> dict[str, object]: ...


class CareerEventAuthority(Protocol):
    instance_root: Path

    def propose(
        self,
        opportunity_id: str,
        *,
        kind: str,
        occurred_at: str,
        summary: str,
        next_action: str | None = None,
        due_date: str | None = None,
    ) -> CareerEventProposal: ...

    def confirm(
        self,
        proposal: CareerEventProposal,
        *,
        confirmed: bool,
        confirmed_by: str,
    ) -> CareerEventRecord: ...

    def list(
        self,
        *,
        opportunity_id: str | None = None,
        as_of: str | None = None,
    ) -> tuple[CareerEventRecord, ...]: ...


class OpportunityAuthority(Protocol):
    instance_root: Path

    def resolve(self, opportunity_id: str, *, as_of: str | None = None) -> OpportunityRecord: ...

    def canonical_id(self, opportunity_id: str) -> str: ...

    def aliases(self) -> Mapping[str, str]: ...

    def list(self) -> tuple[OpportunityRecord, ...]: ...


class WorkspaceAuthority(Protocol):
    instance_root: Path

    def resolve(
        self,
        opportunity_id: str,
        *,
        kind: WorkspaceKind,
    ) -> WorkspaceRecord | None: ...

    def list(self, *, opportunity_id: str | None = None) -> tuple[WorkspaceRecord, ...]: ...

    def read_document(self, workspace_id: str, document_id: str) -> WorkspaceDocument: ...

    def plan_application(self, opportunity_id: str) -> WorkspacePlan: ...

    def plan_interview(self, opportunity_id: str, *, round_name: str) -> WorkspacePlan: ...


class PostingProvider(Protocol):
    name: str

    def fetch(self, source: SourceSpec, access: ProviderAccess) -> tuple[JobPosting, ...]: ...


class ProviderRegistry(Protocol):
    instance_root: Path

    def source(self, source_id: str) -> SourceSpec: ...

    def provider(self, provider_name: str) -> PostingProvider: ...


class ProviderPolicy(Protocol):
    instance_root: Path

    def authorize(self, source: SourceSpec, *, purpose: ProviderPurpose) -> ProviderAccess: ...


class ModelProviderPolicy(Protocol):
    instance_root: Path

    def authorize(
        self,
        *,
        purpose: str,
        data_categories: Collection[str],
    ) -> ModelAccess: ...


class OpportunityEvaluator(Protocol):
    instance_root: Path

    def evaluate(
        self,
        posting: JobPosting,
        opportunity: OpportunityRecord,
    ) -> OpportunityEvaluation: ...


class DecisionProjectionService(Protocol):
    instance_root: Path

    def project(self, evaluation: OpportunityEvaluation) -> DecisionProjection: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


__all__ = [
    "CandidateEvidenceAuthority",
    "CandidateEvidenceRecord",
    "CareerEventAuthority",
    "CareerEventProposal",
    "CareerEventRecord",
    "Clock",
    "DecisionItem",
    "DecisionProjection",
    "DecisionProjectionService",
    "EvidenceStatus",
    "ModelAccess",
    "ModelProviderPolicy",
    "OpportunityAuthority",
    "OpportunityEvaluation",
    "OpportunityEvaluator",
    "OpportunityRecord",
    "PostingProvider",
    "ProviderAccess",
    "ProviderPolicy",
    "ProviderPurpose",
    "ProviderRegistry",
    "PublicationUse",
    "SourceSpec",
    "WorkspaceAuthority",
    "WorkspaceDocument",
    "WorkspaceKind",
    "WorkspacePlan",
    "WorkspaceRecord",
]

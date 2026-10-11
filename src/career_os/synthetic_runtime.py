"""Fully fictional, offline adapters and smoke proof for the participant runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory

from career_os.authority_ports import (
    CandidateEvidenceRecord,
    CareerEventProposal,
    CareerEventRecord,
    DecisionItem,
    DecisionProjection,
    EvidenceStatus,
    ModelAccess,
    OpportunityEvaluation,
    OpportunityRecord,
    ProviderAccess,
    ProviderPurpose,
    PublicationUse,
    SourceSpec,
    WorkspaceDocument,
    WorkspaceKind,
    WorkspacePlan,
    WorkspaceRecord,
)
from career_os.models import JobPosting
from career_os.product_runtime import ProductRuntime

FIXED_NOW = datetime(2030, 1, 2, 9, 30, tzinfo=UTC)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Synthetic authority timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Synthetic authority timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _identifier(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _safe_path_component(value: str, *, label: str) -> str:
    component = value.strip()
    windows = PureWindowsPath(component)
    if (
        not component
        or component != value
        or "/" in component
        or "\\" in component
        or component in {".", ".."}
        or windows.is_absolute()
        or bool(windows.drive)
    ):
        raise ValueError(f"{label} must be one safe path component")
    return component


@dataclass(frozen=True, slots=True)
class SyntheticClock:
    instant: datetime = FIXED_NOW

    def now(self) -> datetime:
        return self.instant


@dataclass(slots=True)
class SyntheticCandidateEvidenceAuthority:
    instance_root: Path
    records: tuple[CandidateEvidenceRecord, ...]

    def resolve(self, evidence_id: str) -> CandidateEvidenceRecord:
        matches = [record for record in self.records if record.evidence_id == evidence_id]
        if len(matches) != 1:
            raise ValueError("Candidate evidence identity is not exact")
        return matches[0]

    def enumerate(
        self,
        *,
        statuses: Collection[EvidenceStatus] | None = None,
        publication_use: PublicationUse | None = None,
    ) -> tuple[CandidateEvidenceRecord, ...]:
        allowed_statuses = set(statuses) if statuses is not None else None
        allowed_boundaries = {
            "internal": {"internal", "application", "interview", "public"},
            "application": {"application", "public"},
            "interview": {"interview", "public"},
            "public": {"public"},
        }
        return tuple(
            record
            for record in self.records
            if (allowed_statuses is None or record.status in allowed_statuses)
            and (
                publication_use is None
                or record.publication_boundary in allowed_boundaries[publication_use]
            )
        )

    def context_fingerprints(self) -> Mapping[str, str]:
        return {
            record.provenance: record.source_hash
            for record in self.records
            if record.status == "approved"
        }

    def export_records(self) -> tuple[CandidateEvidenceRecord, ...]:
        return self.enumerate(statuses=("approved",), publication_use="public")


@dataclass(slots=True)
class SyntheticCareerEventAuthority:
    instance_root: Path
    clock: SyntheticClock
    opportunity_ids: frozenset[str]
    _proposals: dict[str, CareerEventProposal] = field(default_factory=dict)
    _events: list[CareerEventRecord] = field(default_factory=list)
    _confirmations: dict[str, CareerEventRecord] = field(default_factory=dict)

    def propose(
        self,
        opportunity_id: str,
        *,
        kind: str,
        occurred_at: str,
        summary: str,
        next_action: str | None = None,
        due_date: str | None = None,
    ) -> CareerEventProposal:
        if opportunity_id not in self.opportunity_ids:
            raise ValueError("Career event requires an exact opportunity identity")
        _timestamp(occurred_at)
        normalized_kind = kind.strip()
        normalized_summary = " ".join(summary.split())
        if not normalized_kind or not normalized_summary:
            raise ValueError("Career event proposal requires kind and summary")
        proposal_payload = json.dumps(
            [
                opportunity_id,
                normalized_kind,
                occurred_at,
                normalized_summary,
                next_action,
                due_date,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        proposal = CareerEventProposal(
            proposal_id=hashlib.sha256(proposal_payload.encode("utf-8")).hexdigest(),
            opportunity_id=opportunity_id,
            kind=normalized_kind,
            occurred_at=occurred_at,
            summary=normalized_summary,
            next_action=next_action,
            due_date=due_date,
        )
        self._proposals[proposal.proposal_id] = proposal
        return proposal

    def confirm(
        self,
        proposal: CareerEventProposal,
        *,
        confirmed: bool,
        confirmed_by: str,
    ) -> CareerEventRecord:
        if not confirmed:
            raise PermissionError("Career events require explicit human confirmation")
        if self._proposals.get(proposal.proposal_id) != proposal:
            raise ValueError("Career event proposal is not authority-bound")
        actor = confirmed_by.strip()
        if not actor:
            raise ValueError("Career event confirmation requires an actor")
        existing = self._confirmations.get(proposal.proposal_id)
        if existing is not None:
            if existing.confirmed_by != actor:
                raise ValueError("Career event proposal was confirmed by a different actor")
            return existing
        record = CareerEventRecord(
            event_id=_identifier(proposal.proposal_id, self.clock.now().isoformat()),
            opportunity_id=proposal.opportunity_id,
            kind=proposal.kind,
            occurred_at=proposal.occurred_at,
            summary=proposal.summary,
            confirmed_by=actor,
            confirmed_at=self.clock.now().isoformat(),
        )
        self._events.append(record)
        self._confirmations[proposal.proposal_id] = record
        return record

    def list(
        self,
        *,
        opportunity_id: str | None = None,
        as_of: str | None = None,
    ) -> tuple[CareerEventRecord, ...]:
        cutoff = _timestamp(as_of) if as_of is not None else None
        return tuple(
            record
            for record in self._events
            if (opportunity_id is None or record.opportunity_id == opportunity_id)
            and (cutoff is None or _timestamp(record.confirmed_at) <= cutoff)
        )


@dataclass(frozen=True, slots=True)
class SyntheticOpportunityAuthority:
    instance_root: Path
    records: Mapping[str, OpportunityRecord]
    alias_bindings: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        canonical = set(self.records)
        if canonical & set(self.alias_bindings):
            raise ValueError("Opportunity aliases cannot replace canonical identities")
        if set(self.alias_bindings.values()) - canonical:
            raise ValueError("Opportunity aliases must target exact canonical identities")
        if set(self.alias_bindings) & set(self.alias_bindings.values()):
            raise ValueError("Opportunity aliases must not contain chains or cycles")

    def canonical_id(self, opportunity_id: str) -> str:
        identity = opportunity_id.strip()
        canonical = self.alias_bindings.get(identity, identity)
        if canonical not in self.records:
            raise ValueError("Unknown exact opportunity identity")
        return canonical

    def resolve(self, opportunity_id: str, *, as_of: str | None = None) -> OpportunityRecord:
        if as_of is not None:
            _timestamp(as_of)
        return self.records[self.canonical_id(opportunity_id)]

    def aliases(self) -> Mapping[str, str]:
        return dict(self.alias_bindings)

    def list(self) -> tuple[OpportunityRecord, ...]:
        return tuple(self.records[key] for key in sorted(self.records))


@dataclass(slots=True)
class SyntheticWorkspaceAuthority:
    instance_root: Path
    records: tuple[WorkspaceRecord, ...]
    documents: Mapping[tuple[str, str], WorkspaceDocument] = field(default_factory=dict)

    def __post_init__(self) -> None:
        root = self.instance_root.resolve(strict=False)
        for record in self.records:
            path = record.path.resolve(strict=False)
            if root not in path.parents or not record.validated:
                raise ValueError("Synthetic workspace is outside its instance or unvalidated")

    def resolve(
        self,
        opportunity_id: str,
        *,
        kind: WorkspaceKind,
    ) -> WorkspaceRecord | None:
        matches = [
            record
            for record in self.records
            if record.opportunity_id == opportunity_id and record.kind == kind
        ]
        if len(matches) > 1:
            raise ValueError("Workspace authority is ambiguous")
        return matches[0] if matches else None

    def list(self, *, opportunity_id: str | None = None) -> tuple[WorkspaceRecord, ...]:
        return tuple(
            record
            for record in self.records
            if opportunity_id is None or record.opportunity_id == opportunity_id
        )

    def read_document(self, workspace_id: str, document_id: str) -> WorkspaceDocument:
        try:
            return self.documents[(workspace_id, document_id)]
        except KeyError as exc:
            raise ValueError("Unknown registered workspace document") from exc

    def plan_application(self, opportunity_id: str) -> WorkspacePlan:
        return self._plan(opportunity_id, kind="application", suffix="application")

    def plan_interview(self, opportunity_id: str, *, round_name: str) -> WorkspacePlan:
        normalized = "-".join(round_name.casefold().split())
        _safe_path_component(normalized, label="Interview round name")
        return self._plan(opportunity_id, kind="interview", suffix=f"interview-{normalized}")

    def _plan(self, opportunity_id: str, *, kind: WorkspaceKind, suffix: str) -> WorkspacePlan:
        identity = _safe_path_component(
            opportunity_id,
            label="Workspace opportunity identity",
        )
        root = self.instance_root.resolve(strict=False)
        target = (root / "applications" / identity / suffix).resolve(strict=False)
        if root not in target.parents:
            raise ValueError("Workspace plan target crossed the participant instance boundary")
        return WorkspacePlan(
            plan_id=_identifier(identity, kind, suffix),
            opportunity_id=identity,
            kind=kind,
            target=target,
        )


@dataclass(slots=True)
class SyntheticPostingProvider:
    postings: Mapping[str, JobPosting]
    name: str = "synthetic"
    fetch_count: int = 0

    def fetch(self, source: SourceSpec, access: ProviderAccess) -> tuple[JobPosting, ...]:
        if access.network_allowed:
            raise ValueError("Synthetic smoke provider must remain offline")
        if not access.allowed or source.provider != self.name:
            raise PermissionError("Synthetic provider access was not authorized")
        self.fetch_count += 1
        try:
            return (self.postings[source.source_id],)
        except KeyError as exc:
            raise ValueError("Synthetic source does not identify an exact posting") from exc


@dataclass(frozen=True, slots=True)
class SyntheticProviderRegistry:
    instance_root: Path
    sources: Mapping[str, SourceSpec]
    providers: Mapping[str, SyntheticPostingProvider]

    def source(self, source_id: str) -> SourceSpec:
        try:
            return self.sources[source_id]
        except KeyError as exc:
            raise ValueError("Unknown provider source") from exc

    def provider(self, provider_name: str) -> SyntheticPostingProvider:
        try:
            return self.providers[provider_name]
        except KeyError as exc:
            raise ValueError("Unknown posting provider") from exc


@dataclass(frozen=True, slots=True)
class SyntheticProviderPolicy:
    instance_root: Path
    allowed_sources: frozenset[str]

    def authorize(self, source: SourceSpec, *, purpose: ProviderPurpose) -> ProviderAccess:
        allowed = source.source_id in self.allowed_sources and source.authority == purpose
        return ProviderAccess(
            allowed=allowed,
            purpose=purpose,
            network_allowed=False,
            budget_remaining=1 if allowed else 0,
        )


@dataclass(frozen=True, slots=True)
class SyntheticModelPolicy:
    instance_root: Path

    def authorize(
        self,
        *,
        purpose: str,
        data_categories: Collection[str],
    ) -> ModelAccess:
        del purpose, data_categories
        return ModelAccess(allowed=False, provider=None, model=None)


@dataclass(frozen=True, slots=True)
class SyntheticEvaluator:
    instance_root: Path

    def evaluate(
        self,
        posting: JobPosting,
        opportunity: OpportunityRecord,
    ) -> OpportunityEvaluation:
        if posting.job_key != opportunity.opportunity_id:
            raise ValueError("Synthetic evaluator requires exact posting identity")
        return OpportunityEvaluation(
            opportunity_id=opportunity.opportunity_id,
            posting=posting,
            recommendation="review_queue",
            score=82.0,
            confidence=0.9,
            reasons=("Synthetic evidence supports human review.",),
        )


@dataclass(frozen=True, slots=True)
class SyntheticDecisionProjectionService:
    instance_root: Path
    clock: SyntheticClock

    def project(self, evaluation: OpportunityEvaluation) -> DecisionProjection:
        return DecisionProjection(
            generated_at=self.clock.now().isoformat(),
            read_only=True,
            external_actions=False,
            pipeline_changed=False,
            scoring_changed=False,
            items=(
                DecisionItem(
                    opportunity_id=evaluation.opportunity_id,
                    company=evaluation.posting.company,
                    role=evaluation.posting.title,
                    recommendation=evaluation.recommendation,
                    reason=evaluation.reasons[0],
                    action_route=f"/opportunities/{evaluation.opportunity_id}",
                ),
            ),
        )


def build_synthetic_runtime(instance_root: str | Path, *, instance_tag: str) -> ProductRuntime:
    """Construct one entirely in-memory adapter graph without creating the instance root."""
    root = Path(instance_root).expanduser().resolve(strict=False)
    tag = instance_tag.strip().casefold()
    if not tag or not tag.replace("-", "").isalnum():
        raise ValueError("Synthetic instance tag must be a bounded slug")
    source_id = f"{tag}-official"
    posting = JobPosting(
        provider="synthetic",
        source_id=source_id,
        external_id="role-1",
        company=f"Fictional {tag.title()} Systems",
        title="Customer Operations Lead",
        url=f"https://example.invalid/{tag}/role-1",
        location="Remote",
        description="Lead a fictional customer operations team in an offline product test.",
        observed_at=FIXED_NOW.isoformat(),
    )
    opportunity_id = posting.job_key
    opportunity = OpportunityRecord(
        opportunity_id=opportunity_id,
        company=posting.company,
        role=posting.title,
        stage="review",
        status="saved",
        job_url=posting.url,
        job_key=posting.job_key,
        workspace_path=f"applications/{tag}/application",
        source_hash=posting.content_hash,
        official_source_verified=True,
        posting=posting,
    )
    workspace = WorkspaceRecord(
        workspace_id=f"workspace-{tag}",
        opportunity_id=opportunity_id,
        kind="application",
        path=root / "applications" / tag / "application",
        manifest_hash=hashlib.sha256(opportunity_id.encode()).hexdigest(),
        validated=True,
    )
    evidence = CandidateEvidenceRecord(
        evidence_id=f"synthetic.{tag}.leadership",
        statement="The fictional candidate led a synthetic customer operations team.",
        provenance=f"synthetic://{tag}/candidate-evidence",
        source_hash=hashlib.sha256(tag.encode()).hexdigest(),
        publication_boundary="public",
        status="approved",
    )
    clock = SyntheticClock()
    provider = SyntheticPostingProvider({source_id: posting})
    return ProductRuntime.compose(
        root,
        candidate_evidence=SyntheticCandidateEvidenceAuthority(root, (evidence,)),
        career_events=SyntheticCareerEventAuthority(root, clock, frozenset({opportunity_id})),
        opportunities=SyntheticOpportunityAuthority(root, {opportunity_id: opportunity}),
        workspaces=SyntheticWorkspaceAuthority(root, (workspace,)),
        providers=SyntheticProviderRegistry(
            root,
            {
                source_id: SourceSpec(
                    source_id=source_id,
                    provider="synthetic",
                    authority="official",
                    company=posting.company,
                    enabled=True,
                )
            },
            {"synthetic": provider},
        ),
        provider_policy=SyntheticProviderPolicy(root, frozenset({source_id})),
        model_policy=SyntheticModelPolicy(root),
        evaluator=SyntheticEvaluator(root),
        projections=SyntheticDecisionProjectionService(root, clock),
        clock=clock,
    )


def run_smoke() -> dict[str, object]:
    """Run two isolated, offline participant instances and return bounded proof metadata."""
    with TemporaryDirectory(prefix="career-os-product-") as temporary:
        outer = Path(temporary).resolve()
        roots = (outer / "instance-a", outer / "instance-b")
        runtimes = tuple(
            build_synthetic_runtime(root, instance_tag=f"participant-{index}")
            for index, root in enumerate(roots, start=1)
        )
        if any(root.exists() for root in roots):
            raise RuntimeError("Runtime construction created participant files")

        evaluations: list[OpportunityEvaluation] = []
        projections: list[DecisionProjection] = []
        for runtime in runtimes:
            posting = runtime.opportunities.list()[0].posting
            if posting is None:
                raise RuntimeError("Synthetic opportunity lost its official posting")
            evaluation = runtime.evaluate(posting.source_id)
            projection = runtime.project_decision(evaluation)
            with runtime.open_production_store() as store:
                store.record_source_success(
                    posting.source_id,
                    posting.provider,
                    posting.company,
                    jobs_seen=1,
                )
                store.upsert_job(evaluation.posting)
                if store.get_job(evaluation.posting.job_key) is None:
                    raise RuntimeError("Synthetic production store did not retain the posting")
            evaluations.append(evaluation)
            projections.append(projection)

        if evaluations[0].opportunity_id == evaluations[1].opportunity_id:
            raise RuntimeError("Synthetic instances shared an opportunity identity")
        if runtimes[0].paths.production_database == runtimes[1].paths.production_database:
            raise RuntimeError("Synthetic instances shared a production database")
        if any(
            root not in path.resolve().parents
            for root in roots
            for path in root.rglob("*")
            if path.is_file()
        ):
            raise RuntimeError("Synthetic smoke wrote outside an instance root")
        unexpected = {path.name for path in outer.iterdir()} - {root.name for root in roots}
        if unexpected:
            raise RuntimeError("Synthetic smoke left files outside participant roots")
        for runtime in runtimes:
            runtime.shutdown()
        return {
            "schema_version": 1,
            "instances": len(runtimes),
            "offline": True,
            "read_only_projection": all(projection.read_only for projection in projections),
            "external_actions": False,
            "database_roles": ["production", "production"],
            "opportunity_ids": [evaluation.opportunity_id for evaluation in evaluations],
        }


def main() -> int:
    print(json.dumps(run_smoke(), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())


__all__ = ["build_synthetic_runtime", "main", "run_smoke"]

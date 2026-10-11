"""Side-effect-free composition root for one participant Rolevane instance."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from career_os.authority_ports import (
    CandidateEvidenceAuthority,
    CareerEventAuthority,
    CareerEventProposal,
    CareerEventRecord,
    Clock,
    DecisionProjection,
    DecisionProjectionService,
    ModelAccess,
    ModelProviderPolicy,
    OpportunityAuthority,
    OpportunityEvaluation,
    OpportunityEvaluator,
    ProviderPolicy,
    ProviderPurpose,
    ProviderRegistry,
    WorkspaceAuthority,
    WorkspaceKind,
    WorkspacePlan,
    WorkspaceRecord,
)
from career_os.evidence_contract import (
    PublicationReadiness,
    PublicationRequest,
    RoleEvidenceSummary,
)
from career_os.instance_paths import InstancePaths
from career_os.store import CareerStore

_CREDENTIAL_REFERENCE = re.compile(r"(?:env|keyring|vault):[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _assert_component_root(name: str, component: object, expected: Path) -> None:
    raw = getattr(component, "instance_root", None)
    if raw is None or _resolved(raw) != expected:
        raise ValueError(f"Product runtime {name} belongs to another instance")


@dataclass(frozen=True, slots=True)
class ProductRuntime:
    """Validated references and factories; construction performs no external action."""

    paths: InstancePaths
    candidate_evidence: CandidateEvidenceAuthority
    career_events: CareerEventAuthority
    opportunities: OpportunityAuthority
    workspaces: WorkspaceAuthority
    providers: ProviderRegistry
    provider_policy: ProviderPolicy
    model_policy: ModelProviderPolicy
    evaluator: OpportunityEvaluator
    projections: DecisionProjectionService
    clock: Clock

    def __post_init__(self) -> None:
        root = self.paths.root.resolve(strict=False)
        if self.paths.production_database == self.paths.shadow_database:
            raise ValueError("Production and shadow databases must be separate")
        for name in (
            "candidate_evidence",
            "career_events",
            "opportunities",
            "workspaces",
            "providers",
            "provider_policy",
            "model_policy",
            "evaluator",
            "projections",
        ):
            _assert_component_root(name, getattr(self, name), root)

    @classmethod
    def compose(
        cls,
        instance_root: str | Path,
        *,
        candidate_evidence: CandidateEvidenceAuthority,
        career_events: CareerEventAuthority,
        opportunities: OpportunityAuthority,
        workspaces: WorkspaceAuthority,
        providers: ProviderRegistry,
        provider_policy: ProviderPolicy,
        model_policy: ModelProviderPolicy,
        evaluator: OpportunityEvaluator,
        projections: DecisionProjectionService,
        clock: Clock,
    ) -> ProductRuntime:
        return cls(
            paths=InstancePaths.from_root(instance_root),
            candidate_evidence=candidate_evidence,
            career_events=career_events,
            opportunities=opportunities,
            workspaces=workspaces,
            providers=providers,
            provider_policy=provider_policy,
            model_policy=model_policy,
            evaluator=evaluator,
            projections=projections,
            clock=clock,
        )

    def open_production_store(self, *, read_only: bool = False) -> CareerStore:
        """Explicitly open the instance's production-role store."""
        return CareerStore(
            self.paths.production_database,
            read_only=read_only,
            expected_role="production",
            default_production_path=self.paths.production_database,
        )

    def open_shadow_store(self, *, read_only: bool = False) -> CareerStore:
        """Explicitly open the instance's shadow-role store."""
        return CareerStore(
            self.paths.shadow_database,
            read_only=read_only,
            expected_role="shadow",
            default_production_path=self.paths.production_database,
        )

    def output_path(self, relative_path: str | Path) -> Path:
        """Resolve a generated output beneath this instance's export root without creating it."""
        relative = Path(relative_path)
        if (
            relative.is_absolute()
            or relative.drive
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("Generated output path must be a safe export-relative path")
        output = (self.paths.exports / relative).resolve(strict=False)
        if self.paths.exports.resolve(strict=False) not in output.parents:
            raise ValueError("Generated output path escapes the instance export root")
        return output

    def validate_candidate_publication(
        self,
        request: PublicationRequest,
    ) -> PublicationReadiness:
        """Fail closed through the instance-bound evidence authority before publication."""
        return self.candidate_evidence.validate_publication(request)

    def classify_candidate_evidence(
        self,
        role_family: str,
        *,
        required_evidence_ids: tuple[str, ...] = (),
    ) -> RoleEvidenceSummary:
        return self.candidate_evidence.classify_for_role(
            role_family,
            required_evidence_ids=required_evidence_ids,
        )

    def resolve_workspace(
        self,
        opportunity_id: str,
        *,
        kind: WorkspaceKind,
    ) -> WorkspaceRecord | None:
        canonical = self.opportunities.canonical_id(opportunity_id)
        workspace = self.workspaces.resolve(canonical, kind=kind)
        if workspace is None:
            return None
        applications = self.paths.applications.resolve(strict=False)
        path = workspace.path.resolve(strict=False)
        if (
            workspace.opportunity_id != canonical
            or not workspace.validated
            or applications not in path.parents
        ):
            raise ValueError("Workspace authority crossed the participant instance boundary")
        return workspace

    def plan_workspace(
        self,
        opportunity_id: str,
        *,
        kind: WorkspaceKind,
        round_name: str | None = None,
    ) -> WorkspacePlan:
        canonical = self.opportunities.canonical_id(opportunity_id)
        if kind == "application":
            plan = self.workspaces.plan_application(canonical)
        else:
            if round_name is None or not round_name.strip():
                raise ValueError("Interview workspace plan requires a round name")
            plan = self.workspaces.plan_interview(canonical, round_name=round_name)
        target = plan.target.resolve(strict=False)
        if (
            plan.opportunity_id != canonical
            or plan.kind != kind
            or not plan.requires_user_confirmation
            or plan.filesystem_changed
            or self.paths.applications.resolve(strict=False) not in target.parents
        ):
            raise ValueError("Workspace plan violated the explicit effect boundary")
        return plan

    def propose_career_event(
        self,
        opportunity_id: str,
        *,
        kind: str,
        occurred_at: str,
        summary: str,
        next_action: str | None = None,
        due_date: str | None = None,
    ) -> CareerEventProposal:
        canonical = self.opportunities.canonical_id(opportunity_id)
        proposal = self.career_events.propose(
            canonical,
            kind=kind,
            occurred_at=occurred_at,
            summary=summary,
            next_action=next_action,
            due_date=due_date,
        )
        if (
            proposal.opportunity_id != canonical
            or not proposal.requires_user_confirmation
            or proposal.external_event_confirmed
        ):
            raise ValueError("Career event proposal crossed the human-confirmation boundary")
        return proposal

    def confirm_career_event(
        self,
        proposal: CareerEventProposal,
        *,
        confirmed: bool,
        confirmed_by: str,
    ) -> CareerEventRecord:
        if not confirmed:
            raise PermissionError("Career events require explicit human confirmation")
        event = self.career_events.confirm(
            proposal,
            confirmed=True,
            confirmed_by=confirmed_by,
        )
        if event.opportunity_id != self.opportunities.canonical_id(proposal.opportunity_id):
            raise ValueError("Confirmed career event changed opportunity identity")
        return event

    def authorize_model(
        self,
        *,
        purpose: str,
        data_categories: tuple[str, ...],
    ) -> ModelAccess:
        access = self.model_policy.authorize(
            purpose=purpose,
            data_categories=data_categories,
        )
        if access.credential_ref is not None and not _CREDENTIAL_REFERENCE.fullmatch(
            access.credential_ref
        ):
            raise ValueError("Model credentials must be supplied by reference only")
        if access.allowed and (not access.provider or not access.model):
            raise ValueError("Allowed model access requires an explicit provider and model")
        return access

    def evaluate(
        self,
        source_id: str,
        *,
        purpose: ProviderPurpose = "official",
    ) -> OpportunityEvaluation:
        """Explicitly evaluate one exact posting through injected provider policy."""
        source = self.providers.source(source_id)
        if not source.enabled:
            raise ValueError("Provider source is not explicitly enabled")
        if purpose == "official" and source.authority != "official":
            raise ValueError("An unverified lead cannot satisfy official-posting authority")
        access = self.provider_policy.authorize(source, purpose=purpose)
        if not access.allowed or access.purpose != purpose:
            raise PermissionError("Provider policy denied this source operation")
        if access.credential_ref is not None and not _CREDENTIAL_REFERENCE.fullmatch(
            access.credential_ref
        ):
            raise ValueError("Provider credentials must be supplied by reference only")
        postings = self.providers.provider(source.provider).fetch(source, access)
        if len(postings) != 1:
            raise ValueError("Exact source evaluation requires exactly one posting")
        posting = postings[0]
        if posting.provider != source.provider or posting.source_id != source.source_id:
            raise ValueError("Provider returned a posting for a different exact source")
        opportunity_id = self.opportunities.canonical_id(posting.job_key)
        opportunity = self.opportunities.resolve(opportunity_id)
        if opportunity.opportunity_id != opportunity_id:
            raise ValueError("Opportunity authority returned a mismatched exact identity")
        if purpose == "official" and not opportunity.official_source_verified:
            raise ValueError("Opportunity authority has not verified the official posting")
        evaluation = self.evaluator.evaluate(posting, opportunity)
        if evaluation.opportunity_id != opportunity_id:
            raise ValueError("Evaluation changed the authoritative opportunity identity")
        return evaluation

    def project_decision(self, evaluation: OpportunityEvaluation) -> DecisionProjection:
        """Create and validate a read-only human decision projection."""
        projection = self.projections.project(evaluation)
        if (
            not projection.read_only
            or projection.external_actions
            or projection.pipeline_changed
            or projection.scoring_changed
            or not projection.items
        ):
            raise ValueError("Decision projection violated the read-only authority contract")
        for item in projection.items:
            route = item.action_route
            if (
                item.opportunity_id != evaluation.opportunity_id
                or not route.startswith("/")
                or route.startswith("//")
                or "\\" in route
                or any(
                    ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in route
                )
                or not item.requires_user_confirmation
            ):
                raise ValueError("Decision item violated the human-confirmation contract")
        return projection

    def shutdown(self) -> None:
        """Release composition-owned resources; the current runtime owns no background effects."""


__all__ = ["ProductRuntime"]

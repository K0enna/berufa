"""Evidence-bound local interview preparation, without scheduling or event authority."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Protocol, TypeVar

from career_os.application.applications import application_fingerprint
from career_os.application.opportunities import (
    SuppliedOpportunityState,
    _identifier,
    _require_utf8,
    _text,
)
from career_os.application_records import _hash_payload
from career_os.evidence_contract import PublicationReadiness, validate_evidence_identifier
from career_os.evidence_records import EvidenceRevision
from career_os.interview_records import (
    REQUIRED_LIVE_HEADINGS,
    REQUIRED_PREP_HEADINGS,
    ROUND_QUESTIONS,
)

MAX_INTERVIEW_REQUEST_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class InterviewRequest:
    opportunity_id: str
    round_id: str
    round_name: str
    round_type: str
    evidence_ids: tuple[str, ...]
    panel: tuple[str, ...] = ()
    notes: str = ""
    practice_prompts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field in ("opportunity_id", "round_id"):
            value = getattr(self, field)
            if _identifier(value, field) != value:
                raise ValueError("Interview identities must be canonical")
        if _text(self.round_name, "round_name", maximum=120) != self.round_name:
            raise ValueError("Round name must be canonical text")
        if not isinstance(self.round_type, str) or self.round_type not in ROUND_QUESTIONS:
            raise ValueError("Unknown interview round type")
        if not isinstance(self.evidence_ids, tuple) or not 1 <= len(self.evidence_ids) <= 40:
            raise ValueError("Select 1-40 evidence IDs")
        for identifier in self.evidence_ids:
            if validate_evidence_identifier(identifier, "evidence_id") != identifier:
                raise ValueError("Evidence IDs must be canonical")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Evidence IDs must be unique")
        for field, maximum in (("panel", 200), ("practice_prompts", 500)):
            values = getattr(self, field)
            if not isinstance(values, tuple) or len(values) > 8:
                raise ValueError("Panel and prompts must be bounded tuples")
            for value in values:
                if _text(value, field, maximum=maximum) != value:
                    raise ValueError("Panel and prompts must contain canonical text")
            if len(set(values)) != len(values):
                raise ValueError("Panel and prompts must be unique")
        if not isinstance(self.notes, str) or len(self.notes) > 4000 or "\x00" in self.notes:
            raise ValueError("Notes must contain at most 4000 safe characters")
        _require_utf8(self.notes, "notes")

    @classmethod
    def from_dict(cls, payload: object) -> InterviewRequest:
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "opportunity_id",
            "round_id",
            "round_name",
            "round_type",
            "evidence_ids",
            "panel",
            "notes",
            "practice_prompts",
        }:
            raise ValueError("Interview request requires exactly the documented fields")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValueError("Interview request requires schema version 1")
        if any(
            not isinstance(payload[field], list)
            for field in ("evidence_ids", "panel", "practice_prompts")
        ):
            raise ValueError("Evidence, panel and prompts must be lists")
        return cls(
            **{
                key: tuple(value) if key in {"evidence_ids", "panel", "practice_prompts"} else value
                for key, value in payload.items()
                if key != "schema_version"
            }
        )

    def to_dict(self) -> dict[str, object]:
        payload = {"schema_version": 1, **asdict(self)}
        for field in ("evidence_ids", "panel", "practice_prompts"):
            payload[field] = list(payload[field])
        return payload


def interview_evidence_text(item: EvidenceRevision) -> dict[str, str]:
    """The exact display surface that must pass publication policy field by field."""
    return {
        "statement": item.statement,
        **(dict(item.details) if item.kind == "interview_story" else {}),
    }


@dataclass(frozen=True, slots=True)
class InterviewContext:
    opportunity: SuppliedOpportunityState
    evidence: tuple[EvidenceRevision, ...]
    publication: tuple[Mapping[str, PublicationReadiness], ...]
    context_fingerprints: Mapping[str, str]
    role_questions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InterviewContentPlan:
    payload: dict[str, object]

    @property
    def confirmation_fingerprint(self) -> str:
        return application_fingerprint(self.payload["confirmation_fingerprint"])

    def to_dict(self) -> dict[str, object]:
        return deepcopy(self.payload)


def build_interview_plan(
    request: InterviewRequest, context: InterviewContext
) -> InterviewContentPlan:
    state = context.opportunity
    decision = state.latest_decision
    if (
        not state.decision_current
        or decision is None
        or decision["decision"] not in {"pursue", "conditional"}
    ):
        raise ValueError("Preparation requires a current pursue or conditional decision")
    dossier = state.dossier.to_dict()
    if (
        state.dossier.opportunity_id != request.opportunity_id
        or dossier["authority"] != "user_supplied"
        or dossier["official_source_verified"] is not False
    ):
        raise ValueError("Interview preparation requires this supplied-role dossier")
    evaluation = dossier["role_evaluation"]
    allowed = set(evaluation["supported_claim_ids"]) | set(evaluation["transferable_evidence_ids"])
    if tuple(item.evidence_id for item in context.evidence) != request.evidence_ids or len(
        context.publication
    ) != len(context.evidence):
        raise ValueError("Interview evidence must match the selection")
    claims = []
    for item, checks in zip(context.evidence, context.publication, strict=True):
        if item.evidence_id not in allowed and item.kind != "interview_story":
            raise ValueError("Ordinary evidence must be mapped to this dossier")
        displayed = interview_evidence_text(item)
        if set(checks) != set(displayed) or any(
            not check.ready or check.evidence_ids != (item.evidence_id,)
            for check in checks.values()
        ):
            raise ValueError(
                "Every displayed statement and story field must pass publication checks"
            )
        claims.append(
            {
                "evidence_id": item.evidence_id,
                "revision": item.revision,
                "content_hash": item.content_hash,
                "kind": item.kind,
                "source_id": item.source_id,
                "source_hash": item.source_hash,
                "extracted_hash": item.extracted_hash,
                "source_start_line": item.source_start_line,
                "source_end_line": item.source_end_line,
                "text": displayed,
                "ownership": item.ownership,
                "confidence": item.confidence,
                "approximation": item.approximation,
                "publication_boundary": item.publication_boundary,
                "relevance": "dossier_assessed"
                if item.evidence_id in allowed
                else "user_selected_not_assessed",
            }
        )
    assessments = []
    for item in evaluation["assessments"]:
        outcome = item["outcome"]
        if outcome == "proven":
            frame = "Use only the selected approved evidence; retain its ownership and precision."
        elif outcome == "disproven":
            frame = "The assessment records a genuine gap. Do not present adjacent experience as direct ownership."
        elif outcome == "conflict":
            frame = (
                "The assessment contains conflicting evidence. Resolve it before making a claim."
            )
        else:
            frame = "The reviewed evidence does not establish this requirement. Clarify it; absence of evidence is not a proven personal gap."
        assessments.append(
            {
                **item,
                "selected_direct_ids": [
                    identifier
                    for identifier in item["candidate_evidence_ids"]
                    if identifier in request.evidence_ids
                ],
                "selected_transferable_ids": [
                    identifier
                    for identifier in item["transferable_evidence_ids"]
                    if identifier in request.evidence_ids
                ],
                "answer_frame": frame,
            }
        )
    payload = {
        "schema_version": 2,
        "request": request.to_dict(),
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
        "context_fingerprints": dict(context.context_fingerprints),
        "role_selection": evaluation["selection"],
        "claims": claims,
        "requirement_assessments": assessments,
        "suggested_questions": {
            "round": list(ROUND_QUESTIONS[request.round_type]),
            "role_pack": list(context.role_questions),
        },
        "local_content_ready": True,
        "schedule_status": "not_asserted",
        "external_event_recorded": False,
        "model_calls": False,
        "external_actions": False,
        "limitations": [
            "Supplied posting is not official-source verified. Human review remains required.",
            "Round, panel, notes and practice prompts are user-authored, not confirmed employer facts or approved evidence.",
            "Questions and answer frames are suggested preparation, not a verified employer agenda or factual candidate answers.",
            "Unassessed stories are user-selected examples, not assessed requirement proof.",
            "Preparation does not record scheduling, attendance, a debrief, or any external event.",
        ],
    }
    payload["confirmation_fingerprint"] = _hash_payload(payload)
    return InterviewContentPlan(payload)


def interview_artifact_bytes(plan: InterviewContentPlan) -> dict[str, bytes]:
    payload = plan.to_dict()
    request, opportunity = payload["request"], payload["opportunity"]
    header = [
        f"{opportunity['company']} | {opportunity['title']}",
        f"Round: {request['round_name']} ({request['round_type']}); user-authored",
        "",
        *payload["limitations"],
    ]
    proof = []
    for claim in payload["claims"]:
        proof.extend(
            [
                "",
                f"[{claim['evidence_id']}, revision {claim['revision']}; {claim['relevance']}]",
                f"Ownership: {claim['ownership']}; approximation: {claim['approximation']}",
            ]
        )
        proof.extend(f"{field}: {value}" for field, value in claim["text"].items())
    requirements, gaps = [], []
    for item in payload["requirement_assessments"]:
        requirements.extend(
            [
                "",
                f"{item['requirement_id']}: {item['statement']}",
                f"Assessment: {item['outcome']}",
                item["rationale"],
                "Selected direct evidence: " + (", ".join(item["selected_direct_ids"]) or "none"),
                "Selected transferable evidence: "
                + (", ".join(item["selected_transferable_ids"]) or "none"),
            ]
        )
        gaps.extend(["", item["statement"], item["answer_frame"]])
    questions = ["Suggested questions, not a confirmed employer agenda:"]
    for source, values in payload["suggested_questions"].items():
        questions.extend(f"[{source}] {value}" for value in values)
    panel = [
        "User-authored panel labels:",
        *(request["panel"] or ["Not supplied; confirm the panel and format."]),
    ]
    notes = [
        "",
        "USER-AUTHORED NOTES AND PRACTICE (NOT APPROVED EVIDENCE)",
        request["notes"] or "No notes supplied.",
        *request["practice_prompts"],
    ]
    logistics = [
        "Confirm time, timezone, meeting link or location, duration and next steps with the employer.",
        "Record a scheduled/completed stage or debrief separately through the confirmed event workflow.",
    ]
    prep_sections = [
        [
            f"Prepare for the user-named {request['round_type']} round; confirm its actual purpose and format."
        ],
        requirements,
        proof,
        gaps,
        [*panel, *questions],
        logistics,
    ]
    live_proof = [
        "First five selected statements in request order; full evidence and STAR details are in the interview brief."
    ]
    for claim in payload["claims"][:5]:
        live_proof.extend(
            [
                "",
                f"[{claim['evidence_id']}, revision {claim['revision']}; {claim['relevance']}]",
                f"Ownership: {claim['ownership']}; approximation: {claim['approximation']}",
                claim["text"]["statement"],
            ]
        )
    live_sections = [
        [f"Discuss the {opportunity['title']} scope and clarify unresolved requirements."],
        ["Build your opening from the approved statements below; do not expand their scope."],
        live_proof,
        panel,
        questions,
        logistics,
    ]

    def render(title: str, headings: tuple[str, ...], sections: list[list[str]]) -> bytes:
        lines = [title, "", *header]
        for heading, section in zip(headings, sections, strict=True):
            lines.extend(["", heading, *section])
        return ("\n".join([*lines, *notes]) + "\n").encode("utf-8")

    mapping = {
        "schema_version": 2,
        "confirmation_fingerprint": plan.confirmation_fingerprint,
        "claims": payload["claims"],
        "requirement_assessments": payload["requirement_assessments"],
    }
    return {
        "interview-brief.txt": render("INTERVIEW BRIEF", REQUIRED_PREP_HEADINGS, prep_sections),
        "live-call-sheet.txt": render("LIVE CALL SHEET", REQUIRED_LIVE_HEADINGS, live_sections),
        "interview-content-plan.json": (
            json.dumps(payload, sort_keys=True, indent=2) + "\n"
        ).encode(),
        "requirement-evidence-map.json": (
            json.dumps(mapping, sort_keys=True, indent=2) + "\n"
        ).encode(),
    }


@dataclass(frozen=True, slots=True)
class InterviewReceipt:
    opportunity_id: str
    round_id: str
    confirmation_fingerprint: str
    relative_path: str
    created: bool
    schema_version: int = 2
    external_actions: bool = False
    external_event_recorded: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class InterviewValidation:
    opportunity_id: str
    round_id: str
    confirmation_fingerprint: str
    artifacts_intact: bool
    current: bool
    local_content_ready: bool
    limitations: tuple[str, ...]
    external_actions: bool = False
    external_event_recorded: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class InterviewRepository(Protocol):
    def has_consent(self) -> bool: ...
    def plan(self, request: InterviewRequest) -> InterviewContentPlan: ...
    def prepare(self, request: InterviewRequest, fingerprint: str) -> InterviewReceipt: ...
    def validate(
        self, opportunity_id: str, round_id: str, fingerprint: str
    ) -> InterviewValidation: ...


@dataclass(frozen=True, slots=True)
class InterviewOperationError:
    code: str
    message: str


_T = TypeVar("_T")


def require_interview_result(result: _T | InterviewOperationError) -> _T:
    if isinstance(result, InterviewOperationError):
        if result.code in {"consent_required", "human_required"}:
            raise PermissionError(result.message)
        if result.code == "io_error":
            raise OSError(result.message)
        raise ValueError(result.message)
    return result


class InterviewService:
    def __init__(self, repository: InterviewRepository) -> None:
        self._repository = repository

    def _execute(self, operation: Callable[[], _T]) -> _T | InterviewOperationError:
        try:
            if not self._repository.has_consent():
                return InterviewOperationError(
                    "consent_required", "Local-processing consent required"
                )
            return operation()
        except PermissionError as exc:
            if exc.errno is None:
                return InterviewOperationError(
                    "human_required", "Current local-user confirmation required"
                )
            return InterviewOperationError("io_error", "Local interview storage is unavailable")
        except ValueError:
            return InterviewOperationError(
                "invalid_interview", "Interview input or current authority is invalid"
            )
        except OSError:
            return InterviewOperationError("io_error", "Local interview storage is unavailable")

    def plan(self, request: InterviewRequest) -> InterviewContentPlan | InterviewOperationError:
        return self._execute(
            lambda: self._repository.plan(InterviewRequest.from_dict(request.to_dict()))
        )

    def prepare(
        self, request: InterviewRequest, *, confirmation_fingerprint: str, actor: str | None = None
    ) -> InterviewReceipt | InterviewOperationError:
        def prepare() -> InterviewReceipt:
            if actor != "local_user":
                raise PermissionError("Local-user confirmation required")
            return self._repository.prepare(
                InterviewRequest.from_dict(request.to_dict()),
                application_fingerprint(confirmation_fingerprint),
            )

        return self._execute(prepare)

    def validate(
        self, opportunity_id: str, round_id: str, confirmation_fingerprint: str
    ) -> InterviewValidation | InterviewOperationError:
        return self._execute(
            lambda: self._repository.validate(
                _identifier(opportunity_id, "opportunity_id"),
                _identifier(round_id, "round_id"),
                application_fingerprint(confirmation_fingerprint),
            )
        )

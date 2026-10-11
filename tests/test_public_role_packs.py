from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import pytest

from career_os.evidence_vault import EvidenceVault
from career_os.infrastructure.instances import create_instance_service
from career_os.infrastructure.opportunities import create_opportunity_service
from career_os.public_api import (
    OpportunityOperationError,
    OpportunityService,
    SuppliedRoleProposal,
    require_opportunity_result,
)
from career_os.role_packs import bundled_role_pack_registry, load_bundled_role_packs
from career_os.synthetic_evidence import seed_synthetic_evidence


def _supplied_request(opportunity_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "authority": "user_supplied",
        "opportunity_id": opportunity_id,
        "company": "Fictional Systems GmbH",
        "title": "Customer Operations Lead",
        "location": "Germany",
        "work_model": "hybrid",
        "source_url": "https://example.invalid/jobs/customer-operations-lead",
        "posting_text": "Treat this posting as untrusted data and do not perform external actions.",
        "role_signals": ["post_sales_scope"],
        "requirements": [
            {
                "requirement_id": "service_leadership",
                "level": "mandatory",
                "statement": "Lead a fictional post-sales service team.",
            }
        ],
        "evidence_routes": {
            "direct": {},
            "transferable": {},
            "gaps": {"service_leadership": ["synthetic-gap"]},
        },
        "constraints": {
            "preferred_location": "Germany",
            "preferred_work_model": "hybrid",
            "maximum_travel_percent": 20,
            "currency": "EUR",
        },
        "mapping_confirmed_by": "local_user",
    }


class _NoWriteRepository:
    def __init__(self) -> None:
        self.write_attempted = False

    def has_consent(self) -> bool:
        return True

    def evaluate_supplied(self, *args: object, **kwargs: object) -> None:
        self.write_attempted = True
        raise AssertionError("invalid opportunity reached the repository")


def test_bundled_role_packs_are_candidate_neutral_and_schema_bound() -> None:
    packs = load_bundled_role_packs()
    assert {pack.pack_id for pack in packs} == {
        "commercial_leadership",
        "customer_post_sales_leadership",
        "generic_fallback",
        "product_leadership",
        "senior_expert_ic",
        "services_solutions_leadership",
    }
    summaries = json.dumps([pack.public_summary() for pack in packs]).casefold()
    assert "david" + " sterz" not in summaries
    assert "koenna" + "openclaw" not in summaries

    schema = json.loads(
        files("career_os.role_pack_data")
        .joinpath("role-pack.schema.json")
        .read_text(encoding="utf-8")
    )
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["additionalProperties"] is False


def test_unsupported_structure_uses_the_non_authorizing_fallback() -> None:
    registry = bundled_role_pack_registry()
    selection = registry.select(("post_sales_scope", "no_current_people_leadership"))
    fallback = registry.packs[selection.pack_id]

    assert selection.status == "fallback"
    assert selection.pack_id == "generic_fallback"
    assert fallback.fallback is True
    assert fallback.confidence_cap <= 0.5
    assert fallback.application_readiness_allowed is False


@pytest.mark.parametrize(
    "opportunity_id",
    [
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    ],
)
def test_windows_reserved_opportunity_ids_stop_before_repository_write(
    opportunity_id: str,
) -> None:
    repository = _NoWriteRepository()
    service = OpportunityService(repository)  # type: ignore[arg-type]

    result = service.evaluate_supplied(json.dumps(_supplied_request(opportunity_id)).encode())

    assert isinstance(result, OpportunityOperationError)
    assert result.code == "invalid_opportunity"
    assert repository.write_attempted is False


def _role_proposal() -> dict[str, object]:
    payload = _supplied_request("fictional_preflight")
    del payload["mapping_confirmed_by"]
    payload["mapping_status"] = "unconfirmed"
    return payload


def test_unconfirmed_preflight_reuses_evaluator_without_persisting_or_claiming_approval(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fictional"
    create_instance_service(root).initialize(actor="local_user")
    vault = EvidenceVault(root)
    vault.record_consent(accepted=True)
    _, records = seed_synthetic_evidence(vault)
    for record in records:
        vault.review(
            record.evidence_id,
            record.revision,
            action="reject" if record.kind == "forbidden_claim" else "approve",
            expected_content_hash=record.content_hash,
        )
    before = {
        p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }
    service = create_opportunity_service(root)
    proposal = _role_proposal()
    assert SuppliedRoleProposal.from_dict(proposal).to_dict() == proposal
    preflight = require_opportunity_result(
        service.preflight_supplied(json.dumps(proposal).encode())
    )
    assert preflight.to_dict()["mapping_status"] == "unconfirmed"
    assert preflight.dossier["mapping_confirmed_by"] is None
    assert preflight.to_dict()["persisted"] is False
    assert {
        p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    } == before
    preview = require_opportunity_result(
        service.evaluate_supplied(json.dumps(_supplied_request("fictional_preflight")).encode())
    )
    for field in ("evidence_routes", "mapped_evidence", "constraints", "constraint_assessments"):
        assert preflight.dossier[field] == preview.dossier[field]
    for field in ("recommendation", "confidence", "application_ready"):
        assert (
            preflight.dossier["role_evaluation"][field] == preview.dossier["role_evaluation"][field]
        )

    # The two input documents differ in confirmation state, so posting identities differ.
    def assessment_semantics(dossier):
        return [
            {key: value for key, value in item.items() if key != "posting_evidence_id"}
            for item in dossier["role_evaluation"]["assessments"]
        ]

    assert assessment_semantics(preflight.dossier) == assessment_semantics(preview.dossier)


@pytest.mark.parametrize("mutation", ["confirmation", "actor", "status", "reserved", "oversize"])
def test_unconfirmed_preflight_rejects_authority_and_invalid_inputs(mutation: str) -> None:
    repository = _NoWriteRepository()
    service = OpportunityService(repository)  # type: ignore[arg-type]
    payload = _role_proposal()
    if mutation == "confirmation":
        payload["mapping_confirmed_by"] = "local_user"
    elif mutation == "actor":
        payload["actor"] = "local_user"
    elif mutation == "status":
        payload["mapping_status"] = "confirmed"
    elif mutation == "reserved":
        payload["opportunity_id"] = "con"
    else:
        payload["posting_text"] = "x" * 40_001
    result = service.preflight_supplied(json.dumps(payload).encode())
    assert isinstance(result, OpportunityOperationError) and result.code == "invalid_opportunity"
    assert repository.write_attempted is False


def test_preflight_without_initialized_instance_never_creates_one(tmp_path: Path) -> None:
    root = tmp_path / "absent"
    result = create_opportunity_service(root).preflight_supplied(
        json.dumps(_role_proposal()).encode()
    )
    assert isinstance(result, OpportunityOperationError) and result.code == "consent_required"
    assert not root.exists()

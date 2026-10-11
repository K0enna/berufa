"""Public inventory contract: current state, bounded pages and stale/invalid reads."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from career_os.infrastructure.evidence import create_evidence_service
from career_os.infrastructure.instances import create_instance_service
from career_os.infrastructure.opportunities import create_opportunity_service
from career_os.public_api import (
    EvidenceContractMetadata,
    EvidenceOperationError,
    EvidenceProposalInput,
    OpportunityOperationError,
    SuppliedDecisionInput,
    require_evidence_result,
    require_opportunity_result,
)


def test_current_inventory_pages_bind_review_state_and_instance(tmp_path: Path) -> None:
    root = tmp_path / "Workspace with spaces"
    create_instance_service(root).initialize(actor="local_user")
    service = create_evidence_service(root)
    assert isinstance(service.list_current(), EvidenceOperationError)
    require_evidence_result(service.record_consent(accepted=True))
    document = tmp_path / "fictional.txt"
    document.write_text("Led fictional service reviews.\n", encoding="utf-8")
    source = require_evidence_result(service.import_document(document))
    proposal = EvidenceProposalInput(
        source.source_id,
        source.source_hash,
        source.extracted_hash,
        "responsibility",
        "Led fictional service reviews.",
        {"responsibility": "Service reviews", "decision_right": "Team cadence"},
        1,
        1,
        "team",
        "exact",
        "exact",
        "private",
        "local_user",
        "Fictional proof",
        EvidenceContractMetadata(origin_ownership="team"),
    )
    first = require_evidence_result(service.propose(proposal))
    second = require_evidence_result(
        service.propose(replace(proposal, statement="Coordinated fictional reviews."))
    )
    page = require_evidence_result(service.list_current(limit=1))
    next_page = require_evidence_result(service.list_current(limit=1, cursor=page.next_cursor))
    assert [page.entries[0].identity, next_page.entries[0].identity] == sorted(
        [first.evidence_id, second.evidence_id]
    )
    assert page.total == 2 and next_page.next_cursor is None
    require_evidence_result(
        service.review(
            first.evidence_id,
            first.revision,
            action="defer",
            expected_content_hash=first.content_hash,
            actor="local_user",
        )
    )
    assert isinstance(service.list_current(cursor=page.next_cursor), EvidenceOperationError)
    current = require_evidence_result(service.list_current())
    assert {entry.value.status for entry in current.entries} == {"unapproved", "deferred"}
    assert isinstance(service.list_current(limit=True), EvidenceOperationError)
    assert isinstance(service.list_current(limit=101), EvidenceOperationError)
    other = tmp_path / "Second workspace"
    create_instance_service(other).initialize(actor="local_user")
    other_service = create_evidence_service(other)
    require_evidence_result(other_service.record_consent(accepted=True))
    assert require_evidence_result(other_service.list_current()).total == 0
    assert isinstance(other_service.list_current(cursor=page.next_cursor), EvidenceOperationError)
    # Corrupt the managed original, not the fixture: an unreadable source must
    # remain an explicit card diagnostic, never disappear into an empty inventory.
    with sqlite3.connect(root / "data/evidence-vault.db") as connection:
        connection.execute(
            "UPDATE source_documents SET original_content=? WHERE source_id=?",
            (b"Corrupted", source.source_id),
        )
    broken = require_evidence_result(service.list_current())
    assert broken.total == 2 and broken.partial
    assert all(entry.value is None for entry in broken.entries)


def test_supplied_inventory_is_read_only_and_binds_saved_decisions(tmp_path: Path) -> None:
    from career_os.evidence_vault import EvidenceVault
    from career_os.synthetic_evidence import seed_synthetic_evidence

    root = tmp_path / "Fictional workspace"
    create_instance_service(root).initialize(actor="local_user")
    vault = EvidenceVault(root)
    vault.record_consent(accepted=True)
    seed_synthetic_evidence(vault)
    for item in vault.review_queue():
        vault.review(
            item.evidence_id,
            item.revision,
            action="reject" if item.kind == "forbidden_claim" else "approve",
            expected_content_hash=item.content_hash,
            actor="local_user",
        )
    service = create_opportunity_service(root)
    for identity in ("role_one", "role_two"):
        content = json.dumps(
            {
                "schema_version": 1,
                "authority": "user_supplied",
                "mapping_status": "unconfirmed",
                "opportunity_id": identity,
                "company": "Fictional Company",
                "title": "Service lead",
                "location": "Berlin",
                "work_model": "unknown",
                "source_url": None,
                "posting_text": "Lead service reviews.",
                "role_signals": [],
                "requirements": [
                    {
                        "requirement_id": "service_reviews",
                        "level": "mandatory",
                        "statement": "Lead service reviews",
                    }
                ],
                "evidence_routes": {
                    "direct": {"service_reviews": ["synthetic-responsibility"]},
                    "transferable": {},
                    "gaps": {},
                },
                "constraints": {
                    "preferred_location": "Berlin",
                    "preferred_work_model": "unknown",
                    "maximum_travel_percent": 0,
                    "currency": "EUR",
                },
            }
        ).encode()
        preview = require_opportunity_result(service.preflight_supplied(content))
        require_opportunity_result(
            service.confirm_proposal(
                content, preflight_fingerprint=preview.evaluation_fingerprint, actor="local_user"
            )
        )
    before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    page = require_opportunity_result(service.list_supplied(limit=1))
    tail = require_opportunity_result(service.list_supplied(limit=1, cursor=page.next_cursor))
    assert page.entries[0].identity == "role_one" and tail.entries[0].identity == "role_two"
    assert tail.next_cursor is None and page.total == 2
    assert before == {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    card = page.entries[0].value
    require_opportunity_result(
        service.record_decision(
            SuppliedDecisionInput(
                "role_one",
                card.source_hash,
                card.evaluation_fingerprint,
                "hold",
                "practical_constraint",
                "Confirm travel",
            ),
            actor="local_user",
        )
    )
    assert isinstance(service.list_supplied(cursor=page.next_cursor), OpportunityOperationError)
    current = require_opportunity_result(service.list_supplied())
    assert current.entries[0].value.decision == "hold" and current.entries[0].value.decision_current
    dossier = root / "data/runtime/opportunities/role_two/decision-dossier.json"
    dossier.write_text("malformed", encoding="utf-8")
    partial = require_opportunity_result(service.list_supplied())
    assert partial.total == 2 and partial.partial
    assert partial.entries[1].status == "invalid_or_stale"
    assert dossier.read_text() == "malformed"
    for index in range(99):
        (root / f"data/runtime/opportunities/orphan_{index}").mkdir()
    assert isinstance(service.list_supplied(), OpportunityOperationError)

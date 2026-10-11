from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from career_os.evidence_contract import (
    EVIDENCE_CONTRACT_VERSION,
    EVIDENCE_KINDS,
    EvidenceContractMetadata,
    EvidenceRelationship,
    PublicationRequest,
    evidence_contract_schema,
)
from career_os.evidence_vault import EvidenceVault
from career_os.infrastructure.evidence import create_evidence_service
from career_os.infrastructure.evidence_storage import EVIDENCE_STORAGE_VERSION
from career_os.infrastructure.instances import create_instance_service
from career_os.synthetic_evidence import (
    SYNTHETIC_CAREER_PATTERNS,
    SYNTHETIC_PROPOSALS,
    SYNTHETIC_SOURCE_BYTES,
    seed_synthetic_evidence,
    seed_synthetic_pattern,
)


def _vault(tmp_path: Path) -> EvidenceVault:
    vault = EvidenceVault(tmp_path / "participant")
    vault.record_consent(accepted=True)
    seed_synthetic_evidence(vault)
    return vault


def _approve(vault: EvidenceVault, evidence_id: str) -> None:
    item = vault.latest(evidence_id)
    vault.review(
        item.evidence_id,
        item.revision,
        action="approve",
        expected_content_hash=item.content_hash,
    )


def _relate(vault: EvidenceVault, source_id: str, target_id: str):
    source = vault.latest(source_id)
    return vault.edit(
        source.evidence_id,
        expected_revision=source.revision,
        metadata=replace(
            source.metadata,
            relationships=(EvidenceRelationship(target_id, "conflicts_with"),),
        ),
    )


def _publication_request(vault: EvidenceVault, *evidence_ids: str) -> PublicationRequest:
    revisions = tuple(vault.latest(evidence_id) for evidence_id in evidence_ids)
    return PublicationRequest(
        statement=" ".join(revision.statement for revision in revisions),
        evidence_ids=evidence_ids,
        use="application",
        ownership="team" if any(item.ownership == "team" for item in revisions) else "personal",
        approximation=(
            "approximate" if any(item.approximation != "exact" for item in revisions) else "exact"
        ),
    )


def test_contract_schema_is_candidate_neutral_and_matches_snapshot(tmp_path: Path) -> None:
    schema = evidence_contract_schema()
    snapshot = json.loads(Path("schemas/evidence-vault.schema.json").read_text(encoding="utf-8"))

    assert schema == snapshot
    assert schema["x-career-os-contract-version"] == EVIDENCE_CONTRACT_VERSION
    assert len(EVIDENCE_KINDS) == 16
    assert "candidate_profile" in EVIDENCE_KINDS
    assert "David" not in json.dumps(schema)
    source_schema = schema["$defs"]["source"]
    location_schema = schema["$defs"]["source_location"]
    for source in _vault(tmp_path).export_snapshot()["sources"]:
        assert set(source) == set(source_schema["required"]) == set(source_schema["properties"])
        assert source["media_type"] in source_schema["properties"]["media_type"]["enum"]
        assert source["input_format"] in source_schema["properties"]["input_format"]["enum"]
        for location in source["locations"]:
            assert set(location) == set(location_schema["required"])
    assert source_schema["properties"]["size_bytes"]["maximum"] == 15 * 1024 * 1024


def test_three_fictional_patterns_cover_parallel_and_transition_careers(
    tmp_path: Path,
) -> None:
    vault = EvidenceVault(tmp_path / "participant")
    vault.record_consent(accepted=True)
    for pattern_id in SYNTHETIC_CAREER_PATTERNS:
        seed_synthetic_pattern(vault, pattern_id)

    assert set(SYNTHETIC_CAREER_PATTERNS) == {
        "operator_leader",
        "portfolio_career",
        "career_transition",
    }
    assert len(vault.sources()) == 3
    assert vault.timeline_issues() == ()
    assert "David" not in json.dumps(vault.export_snapshot())


def test_publication_request_rejects_duplicates_after_identifier_normalization() -> None:
    with pytest.raises(ValueError, match="unique evidence IDs"):
        PublicationRequest(
            statement="Synthetic statement.",
            evidence_ids=("synthetic-achievement", " synthetic-achievement "),
            use="application",
            ownership="personal",
            approximation="exact",
        )


def test_publication_readiness_fails_closed_on_status_boundary_and_elevation(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    _approve(vault, "synthetic-role-timeline")
    _approve(vault, "synthetic-achievement")

    ready = vault.validate_publication(
        PublicationRequest(
            statement="Mara has served as Director of Client Operations since 2021.",
            evidence_ids=("synthetic-role-timeline",),
            use="application",
            ownership="personal",
            approximation="exact",
        )
    )
    assert ready.ready is True
    role = vault.latest("synthetic-role-timeline")
    vault.review(
        role.evidence_id,
        role.revision,
        action="revoke",
        expected_content_hash=role.content_hash,
        reason="The formulation needs a new source review.",
    )
    revoked = vault.validate_publication(
        PublicationRequest(
            statement="Mara has served as Director of Client Operations since 2021.",
            evidence_ids=("synthetic-role-timeline",),
            use="application",
            ownership="personal",
            approximation="exact",
        )
    )
    assert revoked.ready is False
    assert "not_approved" in {issue.code for issue in revoked.issues}
    assert vault.audit_events()[-1].reason == "The formulation needs a new source review."
    approximate_team = vault.validate_publication(
        PublicationRequest(
            statement="Mara exactly reduced onboarding time by 18 percent.",
            evidence_ids=("synthetic-achievement",),
            use="application",
            ownership="personal",
            approximation="exact",
        )
    )
    assert approximate_team.ready is False
    assert {issue.code for issue in approximate_team.issues} >= {
        "ownership_elevation",
        "approximation_elevation",
    }
    private_gap = vault.validate_publication(
        PublicationRequest(
            statement="Mara has a leadership gap.",
            evidence_ids=("synthetic-gap",),
            use="public",
            ownership="personal",
            approximation="exact",
        )
    )
    assert private_gap.ready is False
    assert {issue.code for issue in private_gap.issues} >= {
        "not_approved",
        "private_evidence_kind",
        "publication_boundary",
    }


def test_approved_forbidden_claim_blocks_unrelated_publication_request(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _approve(vault, "synthetic-role-timeline")
    _approve(vault, "synthetic-forbidden-degree")

    result = vault.validate_publication(
        PublicationRequest(
            statement="Mara completed a bachelor's degree and leads operations.",
            evidence_ids=("synthetic-role-timeline",),
            use="application",
            ownership="personal",
            approximation="exact",
        )
    )

    assert result.ready is False
    assert "vault_forbidden_claim" in {issue.code for issue in result.issues}


def test_verification_is_immutable_and_does_not_change_approval_status(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _approve(vault, "synthetic-role-timeline")
    item = vault.latest("synthetic-role-timeline")

    verified = vault.verify(
        item.evidence_id,
        item.revision,
        expected_content_hash=item.content_hash,
    )

    assert verified.status == "approved"
    assert vault.latest(item.evidence_id).status == "approved"
    assert vault.audit_events()[-1].action == "verified"


@pytest.mark.parametrize(
    ("source_id", "target_id"),
    [
        ("synthetic-role-timeline", "synthetic-achievement"),
        ("synthetic-achievement", "synthetic-role-timeline"),
    ],
)
def test_relationship_conflicts_and_origin_ownership_cannot_be_bypassed(
    tmp_path: Path,
    source_id: str,
    target_id: str,
) -> None:
    vault = _vault(tmp_path)
    edited = _relate(vault, source_id, target_id)
    _approve(vault, target_id)
    vault.review(
        edited.evidence_id,
        edited.revision,
        action="approve",
        expected_content_hash=edited.content_hash,
    )
    for selected_ids in (
        ("synthetic-role-timeline",),
        ("synthetic-achievement",),
        ("synthetic-role-timeline", "synthetic-achievement"),
    ):
        request = _publication_request(vault, *selected_ids)
        result = vault.validate_publication(request)
        conflicts = tuple(
            issue.evidence_id for issue in result.issues if issue.code == "unresolved_conflict"
        )
        assert conflicts == selected_ids
        assert create_evidence_service(vault.instance_root).validate_publication(request) == result
    assert vault.conflicting_evidence_ids() == (
        "synthetic-achievement",
        "synthetic-role-timeline",
    )
    assert vault.classify_for_role("operations").conflicts == vault.conflicting_evidence_ids()

    _approve(vault, "synthetic-education")
    assert vault.validate_publication(_publication_request(vault, "synthetic-education")).ready

    achievement = vault.latest("synthetic-achievement")
    try:
        vault.edit(
            achievement.evidence_id,
            expected_revision=achievement.revision,
            details={**achievement.details, "metric_scope": "one customer cohort"},
            ownership="personal",
        )
    except ValueError as exc:
        assert "elevated to personal" in str(exc)
    else:
        raise AssertionError("team-origin evidence was elevated to personal ownership")


@pytest.mark.parametrize("inactive_side", ["source", "target"])
@pytest.mark.parametrize("status", ["unapproved", "rejected", "deferred", "revoked"])
def test_inactive_conflict_endpoint_does_not_block_publication(
    tmp_path: Path,
    inactive_side: str,
    status: str,
) -> None:
    vault = _vault(tmp_path)
    source_id = "synthetic-role-timeline"
    target_id = "synthetic-achievement"
    source = _relate(vault, source_id, target_id)
    active_id = target_id if inactive_side == "source" else source_id
    inactive_id = source_id if inactive_side == "source" else target_id

    _approve(vault, active_id)
    inactive = source if inactive_side == "source" else vault.latest(target_id)
    actions = {
        "unapproved": (),
        "rejected": ("reject",),
        "deferred": ("defer",),
        "revoked": ("approve", "revoke"),
    }
    for action in actions[status]:
        inactive = vault.review(
            inactive.evidence_id,
            inactive.revision,
            action=action,  # type: ignore[arg-type]
            expected_content_hash=inactive.content_hash,
        )

    result = vault.validate_publication(_publication_request(vault, active_id))
    assert inactive.evidence_id == inactive_id
    assert vault.conflicting_evidence_ids() == ()
    assert "unresolved_conflict" not in {issue.code for issue in result.issues}


def test_reversed_duplicate_superseded_and_missing_conflicts_use_current_records(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    first = _relate(vault, "synthetic-role-timeline", "synthetic-achievement")
    second = _relate(vault, "synthetic-achievement", "synthetic-role-timeline")
    for revision in (first, second):
        vault.review(
            revision.evidence_id,
            revision.revision,
            action="approve",
            expected_content_hash=revision.content_hash,
        )
    assert vault.conflicting_evidence_ids() == (
        "synthetic-achievement",
        "synthetic-role-timeline",
    )

    for evidence_id in ("synthetic-role-timeline", "synthetic-achievement"):
        current = vault.latest(evidence_id)
        cleared = vault.edit(
            evidence_id,
            expected_revision=current.revision,
            metadata=replace(current.metadata, relationships=()),
        )
        vault.review(
            cleared.evidence_id,
            cleared.revision,
            action="approve",
            expected_content_hash=cleared.content_hash,
        )
    assert vault.conflicting_evidence_ids() == ()
    assert vault.validate_publication(
        _publication_request(vault, "synthetic-role-timeline", "synthetic-achievement")
    ).ready

    with pytest.raises(ValueError, match="unknown evidence identity"):
        _relate(vault, "synthetic-role-timeline", "missing-evidence")


def test_only_explicitly_mutually_exclusive_timeline_overlap_is_blocked(
    tmp_path: Path,
) -> None:
    vault = EvidenceVault(tmp_path / "participant")
    vault.record_consent(accepted=True)
    seed_synthetic_pattern(vault, "portfolio_career")
    assert vault.timeline_issues() == ()

    for evidence_id in ("portfolio-consulting-role", "portfolio-teaching-role"):
        current = vault.latest(evidence_id)
        vault.edit(
            evidence_id,
            expected_revision=current.revision,
            metadata=EvidenceContractMetadata(
                origin_ownership="personal",
                timeline_group="primary-employment",
                mutually_exclusive_timeline=True,
            ),
        )
    assert {issue.code for issue in vault.timeline_issues()} == {"mutually_exclusive_overlap"}


def test_unknown_origin_cannot_be_elevated_to_a_personal_claim(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    source = vault.sources()[0]
    unknown = vault.propose(
        evidence_id="unknown-origin-responsibility",
        source_id=source.source_id,
        kind="responsibility",
        statement="The source records an operating responsibility with unresolved ownership.",
        details={"responsibility": "Operating review", "decision_right": "Unresolved"},
        source_start_line=5,
        source_end_line=6,
        ownership="unknown",
        confidence="exact",
        approximation="exact",
        publication_boundary="application",
    )
    vault.review(
        unknown.evidence_id,
        unknown.revision,
        action="approve",
        expected_content_hash=unknown.content_hash,
    )
    result = vault.validate_publication(
        PublicationRequest(
            statement=unknown.statement,
            evidence_ids=(unknown.evidence_id,),
            use="application",
            ownership="personal",
            approximation="exact",
        )
    )
    assert "ownership_elevation" in {issue.code for issue in result.issues}
    try:
        vault.edit(unknown.evidence_id, expected_revision=unknown.revision, ownership="personal")
    except ValueError as exc:
        assert "elevated to personal" in str(exc)
    else:
        raise AssertionError("unknown-origin evidence was elevated to personal ownership")


def test_invalid_timeline_and_explicit_identity_fail_before_storage(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    source = vault.sources()[0]
    common = {
        "source_id": source.source_id,
        "kind": "role_timeline",
        "statement": "A fictional inverted role timeline.",
        "source_start_line": 3,
        "source_end_line": 3,
        "ownership": "personal",
        "confidence": "exact",
        "approximation": "exact",
        "publication_boundary": "application",
    }
    try:
        vault.propose(
            evidence_id="valid-but-inverted",
            details={
                "employer": "Fictional Employer",
                "title": "Operator",
                "start_date": "2025",
                "end_date": "2020",
            },
            **common,  # type: ignore[arg-type]
        )
    except ValueError as exc:
        assert "precedes" in str(exc)
    else:
        raise AssertionError("inverted timeline was stored")
    try:
        vault.propose(
            evidence_id="x",
            details={
                "employer": "Fictional Employer",
                "title": "Operator",
                "start_date": "2020",
                "end_date": "present",
            },
            **common,  # type: ignore[arg-type]
        )
    except ValueError as exc:
        assert "evidence_id is invalid" in str(exc)
    else:
        raise AssertionError("invalid explicit evidence ID was stored")


def test_v1_database_initialize_adds_contract_column_without_data_rewrite(
    tmp_path: Path,
) -> None:
    vault = EvidenceVault(tmp_path / "participant")
    vault.path.parent.mkdir(parents=True)
    with sqlite3.connect(vault.path) as connection:
        connection.execute("CREATE TABLE vault_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO vault_meta VALUES(?, ?)",
            [
                ("schema_version", "1"),
                (
                    "instance_root_hash",
                    hashlib.sha256(str(vault.instance_root).encode()).hexdigest(),
                ),
            ],
        )
        connection.execute(
            "CREATE TABLE evidence_revisions ("
            "evidence_id TEXT NOT NULL, revision INTEGER NOT NULL, kind TEXT NOT NULL, "
            "statement TEXT NOT NULL, details_json TEXT NOT NULL, "
            "source_id TEXT NOT NULL REFERENCES source_documents(source_id), "
            "source_hash TEXT NOT NULL, source_start_line INTEGER NOT NULL, "
            "source_end_line INTEGER NOT NULL, ownership TEXT NOT NULL, confidence TEXT NOT NULL, "
            "approximation TEXT NOT NULL, publication_boundary TEXT NOT NULL, "
            "content_hash TEXT NOT NULL, created_at TEXT NOT NULL, supersedes_revision INTEGER, "
            "PRIMARY KEY (evidence_id, revision))"
        )
        connection.execute(
            "CREATE TABLE source_documents ("
            "source_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, "
            "media_type TEXT NOT NULL, source_hash TEXT NOT NULL, "
            "size_bytes INTEGER NOT NULL, line_count INTEGER NOT NULL, "
            "imported_at TEXT NOT NULL, content BLOB NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE evidence_events ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, "
            "evidence_id TEXT NOT NULL, revision INTEGER NOT NULL, action TEXT NOT NULL, "
            "actor TEXT NOT NULL, occurred_at TEXT NOT NULL, content_hash TEXT NOT NULL, "
            "FOREIGN KEY (evidence_id, revision) "
            "REFERENCES evidence_revisions(evidence_id, revision))"
        )
        source_id = "source-synthetic-legacy"
        source_hash = hashlib.sha256(SYNTHETIC_SOURCE_BYTES).hexdigest()
        imported_at = "2025-01-02T03:04:05+00:00"
        connection.execute(
            "INSERT INTO source_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_id,
                "fictional-career-note.txt",
                "text/plain",
                source_hash,
                len(SYNTHETIC_SOURCE_BYTES),
                len(SYNTHETIC_SOURCE_BYTES.decode().splitlines()),
                imported_at,
                SYNTHETIC_SOURCE_BYTES,
            ),
        )
        proposal = SYNTHETIC_PROPOSALS[0]
        evidence_id = proposal.evidence_id
        statements = (
            "Mara Voss has served as Director of Client Operations since 2021.",
            proposal.statement,
        )
        for revision, statement in enumerate(statements, start=1):
            created_at = f"2025-01-0{revision + 2}T03:04:05+00:00"
            hash_payload = {
                "kind": proposal.kind,
                "statement": statement,
                "details": proposal.details,
                "source_id": source_id,
                "source_hash": source_hash,
                "source_start_line": proposal.start_line,
                "source_end_line": proposal.end_line,
                "ownership": proposal.ownership,
                "confidence": proposal.confidence,
                "approximation": proposal.approximation,
                "publication_boundary": proposal.publication_boundary,
            }
            content_hash = hashlib.sha256(
                json.dumps(
                    hash_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            connection.execute(
                "INSERT INTO evidence_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    evidence_id,
                    revision,
                    proposal.kind,
                    statement,
                    json.dumps(proposal.details, sort_keys=True, separators=(",", ":")),
                    source_id,
                    source_hash,
                    proposal.start_line,
                    proposal.end_line,
                    proposal.ownership,
                    proposal.confidence,
                    proposal.approximation,
                    proposal.publication_boundary,
                    content_hash,
                    created_at,
                    revision - 1 if revision > 1 else None,
                ),
            )
            action = "approved" if revision == 2 else "proposed"
            occurred_at = f"2025-01-0{revision + 3}T04:05:06+00:00"
            actor = "local_user"
            event_id = (
                "evidence-event-"
                + hashlib.sha256(
                    f"{evidence_id}\0{revision}\0{action}\0{actor}\0{occurred_at}".encode()
                ).hexdigest()[:24]
            )
            connection.execute(
                "INSERT INTO evidence_events(event_id, evidence_id, revision, action, "
                "actor, occurred_at, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, evidence_id, revision, action, actor, occurred_at, content_hash),
            )
        original_source = connection.execute("SELECT * FROM source_documents").fetchone()
        original_revisions = connection.execute(
            "SELECT * FROM evidence_revisions ORDER BY revision"
        ).fetchall()
        original_events = connection.execute(
            "SELECT event_id, evidence_id, revision, action, actor, occurred_at, content_hash "
            "FROM evidence_events ORDER BY sequence"
        ).fetchall()
    service = create_instance_service(vault.instance_root)
    backup = tmp_path / "pre-upgrade"
    preview = service.upgrade(str(backup))
    service.upgrade(
        str(backup),
        confirmation_fingerprint=preview.confirmation_fingerprint,
        offline=True,
        actor="local_user",
    )
    with sqlite3.connect(vault.path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(evidence_revisions)")}
        version = connection.execute(
            "SELECT value FROM vault_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        migrated_source = connection.execute(
            "SELECT source_id, display_name, media_type, source_hash, size_bytes, "
            "line_count, imported_at, content FROM source_documents"
        ).fetchone()
        migrated_revisions = connection.execute(
            "SELECT evidence_id, revision, kind, statement, details_json, source_id, "
            "source_hash, source_start_line, source_end_line, ownership, confidence, "
            "approximation, publication_boundary, content_hash, created_at, "
            "supersedes_revision FROM evidence_revisions ORDER BY revision"
        ).fetchall()
        migrated_events = connection.execute(
            "SELECT event_id, evidence_id, revision, action, actor, occurred_at, content_hash "
            "FROM evidence_events ORDER BY sequence"
        ).fetchall()
    assert "contract_json" in columns
    assert version == str(EVIDENCE_STORAGE_VERSION)
    assert migrated_source == original_source
    assert migrated_revisions == original_revisions
    assert migrated_events == original_events
    history = vault.revisions(evidence_id)
    assert [
        (item.revision, item.content_hash, item.created_at, item.status) for item in history
    ] == [
        (1, original_revisions[0][13], original_revisions[0][14], "unapproved"),
        (2, original_revisions[1][13], original_revisions[1][14], "approved"),
    ]
    assert history[-1].source_hash == source_hash
    assert history[-1].supersedes_revision == 1


def test_export_and_deletion_inventory_enumerate_all_owned_tables(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    inventory = vault.deletion_inventory()
    tables = inventory["tables"]

    assert inventory["exists"] is True
    assert set(tables) == {
        "vault_meta",
        "consent_receipts",
        "source_documents",
        "evidence_revisions",
        "evidence_events",
    }
    assert vault.export_snapshot()["deletion_inventory"] == inventory


def test_role_classification_requires_approval_and_verification(tmp_path: Path) -> None:
    vault = EvidenceVault(tmp_path / "participant")
    vault.record_consent(accepted=True)
    seed_synthetic_pattern(vault, "career_transition")

    assert vault.classify_for_role("product operations").genuine_gaps == ()
    _approve(vault, "transition-research-skill")
    before = vault.classify_for_role("product operations")
    assert before.strong_verified == ()
    assert before.transferable == ("transition-research-skill",)

    skill = vault.latest("transition-research-skill")
    vault.verify(skill.evidence_id, skill.revision, expected_content_hash=skill.content_hash)
    _approve(vault, "transition-industry-gap")
    after = vault.classify_for_role("product operations")
    assert after.strong_verified == ("transition-research-skill",)
    assert after.genuine_gaps == ("transition-industry-gap",)


def test_revision_row_tampering_is_detected_on_every_read(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with sqlite3.connect(vault.path) as connection:
        connection.execute(
            "UPDATE evidence_revisions SET statement = ? WHERE evidence_id = ?",
            ("tampered statement", "synthetic-role-timeline"),
        )

    try:
        vault.latest("synthetic-role-timeline")
    except ValueError as exc:
        assert "content hash" in str(exc)
    else:
        raise AssertionError("tampered revision was accepted")


def test_audit_action_tampering_cannot_invent_approval(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with sqlite3.connect(vault.path) as connection:
        connection.execute(
            "UPDATE evidence_events SET action = 'approved' "
            "WHERE evidence_id = ? AND action = 'proposed'",
            ("synthetic-role-timeline",),
        )
    try:
        vault.latest("synthetic-role-timeline")
    except ValueError as exc:
        assert "integrity identity" in str(exc)
    else:
        raise AssertionError("tampered audit action invented approval")


def test_legacy_approximate_metric_text_remains_readable_after_v2_initialize(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    with sqlite3.connect(vault.path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM evidence_revisions WHERE evidence_id = 'synthetic-achievement'"
        ).fetchone()
        assert row is not None
        details = json.loads(row["details_json"])
        details["metric_value"] = "~18"
        payload = {
            "kind": row["kind"],
            "statement": row["statement"],
            "details": details,
            "source_id": row["source_id"],
            "source_hash": row["source_hash"],
            "source_start_line": row["source_start_line"],
            "source_end_line": row["source_end_line"],
            "ownership": row["ownership"],
            "confidence": row["confidence"],
            "approximation": row["approximation"],
            "publication_boundary": row["publication_boundary"],
        }
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        event = connection.execute(
            "SELECT * FROM evidence_events WHERE evidence_id = 'synthetic-achievement'"
        ).fetchone()
        assert event is not None
        legacy_event_id = (
            "evidence-event-"
            + hashlib.sha256(
                (
                    f"{event['evidence_id']}\0{event['revision']}\0{event['action']}\0"
                    f"{event['actor']}\0{event['occurred_at']}"
                ).encode()
            ).hexdigest()[:24]
        )
        connection.execute(
            "UPDATE evidence_revisions SET details_json = ?, contract_json = '{}', extracted_hash = NULL, "
            "content_hash = ? WHERE evidence_id = 'synthetic-achievement'",
            (json.dumps(details, sort_keys=True, separators=(",", ":")), content_hash),
        )
        connection.execute(
            "UPDATE evidence_events SET event_id = ?, content_hash = ? "
            "WHERE evidence_id = 'synthetic-achievement'",
            (legacy_event_id, content_hash),
        )

    vault.initialize()
    assert vault.latest("synthetic-achievement").details["metric_value"] == "~18"

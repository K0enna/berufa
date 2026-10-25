"""Service-boundary proof of retained-document authority and artifact safety."""

import sqlite3
import tracemalloc
from dataclasses import replace
from pathlib import Path

import pytest
from test_document_intake import _write_docx, _write_text_pdf
from test_evidence_vault import _link_directory

from career_os.application.documents import (
    DocumentBasis,
    DocumentExportRequest,
    DocumentImportRequest,
    DocumentReviewRequest,
)
from career_os.evidence_vault import EvidenceVault
from career_os.infrastructure.documents import (
    NAMESPACE,
    REVIEW_PATH,
    create_document_service,
    inspect_document_archive,
)
from career_os.infrastructure.instances import create_instance_service
from career_os.synthetic_evidence import seed_synthetic_evidence


@pytest.fixture
def instance(tmp_path):
    root = tmp_path / "fictional"
    create_instance_service(root).initialize(actor="local_user")
    vault = EvidenceVault(root)
    vault.record_consent(accepted=True)
    return root, vault, create_document_service(root)


def request(request_id="fictional-draft", **kw):
    return DocumentImportRequest(
        request_id,
        "Fictional draft",
        "other",
        "Supplied fictional local file",
        {"md": b"Fictional supplied wording\n"},
        **kw,
    )


def accepted(service, ref, **kw):
    req = DocumentReviewRequest(ref, "accepted", basis_unknown_ack=True, **kw)
    proposal = service.review(req)
    return service.review(
        req, confirmation_fingerprint=proposal.confirmation_fingerprint, actor="local_user"
    )


def test_standalone_immutable_versions_restart_retry_and_unknown_basis(instance):
    root, vault, service = instance
    before = vault.enumerate()
    receipt = service.import_version(request(), actor="local_user")
    assert receipt.changed and service.inspect(receipt.ref).basis_current is None
    assert service.inspect(receipt.ref).opportunity_id is None
    service = create_document_service(root)
    assert service.import_version(request(), actor="local_user") == replace(receipt, changed=False)
    with pytest.raises(ValueError, match="different content"):
        service.import_version(
            replace(request(), artifacts={"txt": b"Changed"}), actor="local_user"
        )
    accepted(service, receipt.ref)
    second = service.import_version(
        request("fictional-revision", document_id=receipt.ref.document_id), actor="local_user"
    )
    assert second.ref.document_id == receipt.ref.document_id and second.ref != receipt.ref
    assert service.inspect(second.ref).review_state == "unreviewed"
    assert service.inspect(receipt.ref).review_state == "accepted"
    assert len(service.list().versions) == 2 and vault.enumerate() == before


def test_targeted_edit_preview_conflict_retry_and_selected_history(instance):
    from career_os.application.documents import DocumentEditRequest

    root, _, service = instance
    original = "# Fictional CV\n\n## Leadership\nLed six specialists with colleagues.\n\n## Education\nB.Sc. coursework; degree not completed.\n"
    base = service.import_version(
        replace(request(), artifacts={"md": original.encode()}), actor="local_user"
    ).ref
    accepted(service, base)
    edit = DocumentEditRequest(
        "section-edit",
        base,
        "md",
        "Led six specialists with colleagues.",
        "Led a team of six specialists, working with colleagues.",
    )
    before = inspect_document_archive(root)
    preview = service.edit(edit)
    assert inspect_document_archive(root) == before  # preview/review-only/cancel writes nothing
    assert "Education" not in preview.change_summary
    assert preview.text == original.replace(edit.selected_text, edit.replacement_text)
    competitor = service.edit(replace(edit, request_id="competing-edit"))
    saved = service.edit(
        edit, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
    )
    assert saved.changed and service.inspect(saved.ref).review_state == "unreviewed"
    assert service.inspect(base).review_state == "accepted"
    with pytest.raises(ValueError, match="conflict"):
        service.edit(
            competitor.request,
            confirmation_fingerprint=competitor.confirmation_fingerprint,
            actor="local_user",
        )
    service = create_document_service(root)
    assert service.edit(
        edit, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
    ) == replace(saved, changed=False)
    with pytest.raises(ValueError, match="different content"):
        service.edit(
            replace(edit, replacement_text="Changed assertion"),
            confirmation_fingerprint=preview.confirmation_fingerprint,
            actor="local_user",
        )
    old_export = service.export(
        DocumentExportRequest(base, ("md",), service.inspect(base).review["review_id"]),
        actor="local_user",
    )
    assert (Path(old_export.path) / "draft.md").read_bytes() == original.encode()
    assert service.inspect(saved.ref).artifacts["md"]["text"] == preview.text
    for selected in ("missing text", "", "\n"):
        with pytest.raises(ValueError):
            service.edit(replace(edit, request_id="bad-edit", selected_text=selected))


def test_repetitive_ambiguous_selection_rejects_with_bounded_memory(instance):
    from career_os.application.documents import DocumentEditRequest

    root, _, service = instance
    base = service.import_version(
        replace(request(), artifacts={"md": b"a" * 65536}), actor="local_user"
    ).ref
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="ambiguous"):
            service.edit(DocumentEditRequest("ambiguous-edit", base, "md", "a" * 1024, "new"))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 32 * 1024 * 1024
    assert len(inspect_document_archive(root).versions) == 1
    assert service.inspect(base).artifacts["md"]["text"] == "a" * 65536


def test_targeted_windows_multiline_preserves_line_endings_and_rejects_mixed_ambiguity(instance):
    from career_os.application.documents import DocumentEditRequest

    _, _, service = instance
    raw = b"# Original\r\n\r\nFirst line.\r\nSecond line.\r\n\r\nEducation unchanged.\r\n"
    base = service.import_version(replace(request(), artifacts={"md": raw}), actor="local_user").ref
    edit = DocumentEditRequest(
        "windows-edit", base, "md", "First line.\nSecond line.", "New first.\nNew second."
    )
    preview = service.edit(edit)
    assert preview.text.encode() == raw.replace(
        b"First line.\r\nSecond line.", b"New first.\r\nNew second."
    )
    service.edit(
        edit, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
    )
    leading = service.edit(
        DocumentEditRequest("leading-newline", base, "md", "\nSecond line.", "\nChanged second.")
    )
    assert leading.text.encode() == raw.replace(b"\r\nSecond line.", b"\r\nChanged second.")
    for name, breaks in (("bare-cr", (b"\r", b"\r")), ("mixed-breaks", (b"\n", b"\r\n"))):
        passage = b"First" + breaks[0] + b"Second" + breaks[1] + b"Third"
        source = b"Unchanged prefix\r\n" + passage + b"\rUnchanged suffix\n"
        selected = service.import_version(
            replace(request(name), artifacts={"md": source}), actor="local_user"
        ).ref
        proposed = service.edit(
            DocumentEditRequest(name + "-edit", selected, "md", "First\nSecond\nThird", "Revised")
        )
        assert proposed.text.encode() == source.replace(passage, b"Revised")
    ambiguous = service.import_version(
        replace(request("mixed"), artifacts={"md": b"First\r\nSecond\nFirst\nSecond"}),
        actor="local_user",
    ).ref
    with pytest.raises(ValueError, match="ambiguous"):
        service.edit(
            DocumentEditRequest("mixed-edit", ambiguous, "md", "First\nSecond", "Replacement")
        )
    overlapping = service.import_version(
        replace(request("overlapping"), artifacts={"md": b"aaa"}), actor="local_user"
    ).ref
    with pytest.raises(ValueError, match="ambiguous"):
        service.edit(DocumentEditRequest("overlapping-edit", overlapping, "md", "aa", "New"))


def test_targeted_currentness_and_instance_binding_preserve_committed_retry(instance, tmp_path):
    from career_os.application.documents import DocumentEditRequest

    root, vault, service = instance
    _, revisions = seed_synthetic_evidence(vault)
    fact = revisions[0]
    vault.review(
        fact.evidence_id, fact.revision, action="approve", expected_content_hash=fact.content_hash
    )
    imported = service.import_version(
        request(basis=(DocumentBasis(fact.evidence_id, fact.revision, fact.content_hash),)),
        actor="local_user",
    )
    edit = DocumentEditRequest(
        "basis-edit", imported.ref, "md", "Fictional supplied wording", "Revised supplied wording"
    )
    preview = service.edit(edit)
    with pytest.raises(PermissionError):
        service.edit(edit, confirmation_fingerprint=preview.confirmation_fingerprint, actor="model")
    saved = service.edit(
        edit, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
    )
    restored_wording = DocumentEditRequest(
        "restore-wording", saved.ref, "md", edit.replacement_text, edit.selected_text
    )
    restore_preview = service.edit(restored_wording)
    restored = service.edit(
        restored_wording,
        confirmation_fingerprint=restore_preview.confirmation_fingerprint,
        actor="local_user",
    )
    assert (
        restored.ref != imported.ref and service.inspect(restored.ref).review_state == "unreviewed"
    )
    vault.edit(
        fact.evidence_id,
        expected_revision=fact.revision,
        statement=fact.statement + " (correction)",
    )
    with pytest.raises(ValueError, match="stale"):
        service.edit(replace(edit, request_id="new-stale"))
    assert service.edit(
        edit, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
    ) == replace(saved, changed=False)
    assert len(service.list().versions) == 3

    other = tmp_path / "another-instance"
    create_instance_service(other).initialize(actor="local_user")
    EvidenceVault(other).record_consent(accepted=True)
    foreign = create_document_service(other)
    shared = replace(request("identical-source"), artifacts={"md": b"Same text"})
    left = service.import_version(shared, actor="local_user").ref
    right = foreign.import_version(shared, actor="local_user").ref
    assert left == right  # identical supplied refs do not grant cross-instance confirmation
    cross = DocumentEditRequest("cross-instance", left, "md", "Same text", "New text")
    cross_preview = service.edit(cross)
    before = inspect_document_archive(other)
    with pytest.raises(ValueError, match="conflict"):
        foreign.edit(
            cross,
            confirmation_fingerprint=cross_preview.confirmation_fingerprint,
            actor="local_user",
        )
    assert inspect_document_archive(other) == before and root.exists()


def test_review_preview_is_read_only_and_export_requires_exact_page_ack(instance, tmp_path):
    root, _, service = instance
    pdf = tmp_path / "draft.pdf"
    _write_text_pdf(pdf, "Fictional retained output")
    req = replace(request(), artifacts={"md": b"Fictional wording", "pdf": pdf.read_bytes()})
    receipt = service.import_version(req, actor="local_user")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(PermissionError, match="unknown-basis"):
        service.review(DocumentReviewRequest(receipt.ref, "accepted"))
    proposal = service.review(
        DocumentReviewRequest(receipt.ref, "accepted", basis_unknown_ack=True)
    )
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before
    review = service.review(
        proposal.request,
        confirmation_fingerprint=proposal.confirmation_fingerprint,
        actor="local_user",
    )
    with pytest.raises(PermissionError, match="page inspection"):
        service.export(
            DocumentExportRequest(receipt.ref, ("pdf",), review.review_id), actor="local_user"
        )
    inspection = service.inspect(receipt.ref)
    review = accepted(
        service, receipt.ref, page_inspected_hashes=(inspection.artifacts["pdf"]["sha256"],)
    )
    assert not service.review(
        DocumentReviewRequest(
            receipt.ref,
            "accepted",
            basis_unknown_ack=True,
            page_inspected_hashes=(inspection.artifacts["pdf"]["sha256"],),
        ),
        confirmation_fingerprint=review.confirmation_fingerprint,
        actor="local_user",
    ).changed
    exported = service.export(
        DocumentExportRequest(receipt.ref, ("pdf",), review.review_id), actor="local_user"
    )
    assert {p.name for p in Path(exported.path).iterdir()} == {"draft.pdf"}
    assert (Path(exported.path) / "draft.pdf").read_bytes() == req.artifacts["pdf"]
    archive = inspect_document_archive(root)
    assert len(archive.reviews) == 2 and ".document-reviews.json.lock" in {
        Path(p).name for p in archive.managed_paths
    }
    stale = proposal.confirmation_fingerprint
    with pytest.raises(PermissionError, match="stale"):
        service.review(
            DocumentReviewRequest(receipt.ref, "changes_requested", "Recheck"),
            confirmation_fingerprint=stale,
            actor="local_user",
        )


@pytest.mark.parametrize("mutation", ["corrupt", "extra", "linked"])
def test_retained_corruption_extra_and_linked_versions_refuse(instance, tmp_path, mutation):
    root, _, service = instance
    receipt = service.import_version(request(), actor="local_user")
    version = root / NAMESPACE / receipt.ref.document_id / receipt.ref.fingerprint
    if mutation == "corrupt":
        (version / "draft.md").write_bytes(b"Altered retained wording")
    elif mutation == "extra":
        (version / "private-note.txt").write_text("must not export")
    else:
        outside = tmp_path / "outside"
        version.rename(outside)
        _link_directory(version, outside)
    with pytest.raises((ValueError, OSError)):
        service.inspect(receipt.ref)
    with pytest.raises((ValueError, OSError)):
        inspect_document_archive(root)
    with pytest.raises((ValueError, OSError)):
        service.import_version(request(), actor="local_user")


def test_parser_rejects_oversized_and_active_content_and_keeps_rejected_binary(instance, tmp_path):
    root, _, service = instance
    with pytest.raises(ValueError, match="size"):
        replace(request(), artifacts={"md": b"x" * (2 * 1024 * 1024 + 1)})
    with pytest.raises(ValueError):
        service.import_version(
            replace(request(), artifacts={"pdf": b"not a PDF"}), actor="local_user"
        )
    docx = tmp_path / "draft.docx"
    _write_docx(docx, extra={"word/vbaProject.bin": b"active"})
    with pytest.raises(ValueError):
        service.import_version(
            replace(request(), artifacts={"docx": docx.read_bytes()}), actor="local_user"
        )
    _write_docx(docx)
    receipt = service.import_version(
        replace(request(), artifacts={"docx": docx.read_bytes()}), actor="local_user"
    )
    req = DocumentReviewRequest(
        receipt.ref, "changes_requested", "Fictional wording needs correction"
    )
    preview = service.review(req)
    review = service.review(
        req, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
    )
    revised = service.import_version(
        request("fictional-text-revision", document_id=receipt.ref.document_id),
        actor="local_user",
    )
    current = service.inspect(revised.ref)
    assert set(current.artifacts) == {"md"} and current.review is None
    archive = inspect_document_archive(root)
    retained = next(v for v in archive.versions if v.ref == receipt.ref)
    assert len(archive.versions) == 2
    assert retained.artifacts["docx"] == docx.read_bytes()
    assert archive.reviews[0]["request"]["note"] == req.note
    with pytest.raises(PermissionError):
        service.export(
            DocumentExportRequest(receipt.ref, ("docx",), review.review_id), actor="local_user"
        )


@pytest.mark.parametrize("mutation", ["correction", "source_corruption"])
def test_approved_basis_stales_on_correction_and_source_corruption_retry_remains_stable(
    instance, mutation
):
    root, vault, service = instance
    _, revisions = seed_synthetic_evidence(vault)
    revision = revisions[0]
    vault.review(
        revision.evidence_id,
        revision.revision,
        action="approve",
        expected_content_hash=revision.content_hash,
    )
    req = request(
        basis=(DocumentBasis(revision.evidence_id, revision.revision, revision.content_hash),)
    )
    receipt = service.import_version(req, actor="local_user")
    assert service.inspect(receipt.ref).basis_current is True
    reviewed = accepted(service, receipt.ref)
    if mutation == "correction":
        vault.edit(
            revision.evidence_id,
            expected_revision=revision.revision,
            statement=revision.statement + " (corrected)",
        )
    else:
        with sqlite3.connect(vault.path) as connection:
            connection.execute(
                "UPDATE source_documents SET content = ? WHERE source_id = ?",
                (b"Corrupted fictional source", revision.source_id),
            )
    assert service.inspect(receipt.ref).basis_current is False
    assert service.inspect(receipt.ref).review_state == "accepted"
    if mutation == "correction":
        assert vault.revisions(revision.evidence_id)[0].statement == revision.statement
        latest = vault.latest(revision.evidence_id)
        assert latest.revision == revision.revision + 1 and latest.status == "unapproved"
    assert service.import_version(req, actor="local_user") == replace(receipt, changed=False)
    with pytest.raises(ValueError, match="stale"):
        service.import_version(replace(req, request_id="new-stale"), actor="local_user")
    with pytest.raises(PermissionError):
        service.export(
            DocumentExportRequest(receipt.ref, ("md",), reviewed.review_id), actor="local_user"
        )
    assert inspect_document_archive(root).versions[0].ref == receipt.ref


def test_live_requires_initialized_consented_complete_root_archive_does_not(instance):
    root, vault, service = instance
    receipt = service.import_version(request(), actor="local_user")
    vault.record_consent(accepted=False)
    with pytest.raises(PermissionError, match="consent"):
        service.list()
    for artifacts in ({"md": b"Fictional text"}, {"pdf": b"Malformed fictional PDF"}):
        with pytest.raises(PermissionError, match="consent"):
            service.import_version(
                replace(request("without-consent"), artifacts=artifacts), actor="local_user"
            )
    assert inspect_document_archive(root).versions[0].ref == receipt.ref
    vault.record_consent(accepted=True)
    (root / ".career-os-recovery-incomplete").write_text("incomplete")
    with pytest.raises(ValueError, match="incomplete"):
        service.inspect(receipt.ref)
    assert not (root / REVIEW_PATH).exists()


def test_archive_refuses_disconnected_review_and_accepts_valid_lock_only_state(instance):
    root, _, service = instance
    lock = root / "data/runtime/.document-reviews.json.lock"
    lock.write_bytes(b"12345")
    assert inspect_document_archive(root).managed_paths == (
        "data/runtime/.document-reviews.json.lock",
    )
    lock.write_bytes(b"not a process lock")
    with pytest.raises(ValueError, match="process lock"):
        inspect_document_archive(root)
    lock.write_bytes(b"12345")
    receipt = service.import_version(request(), actor="local_user")
    accepted(service, receipt.ref)
    path = root / REVIEW_PATH
    import json

    history = json.loads(path.read_bytes())
    history["reviews"][0]["previous_review_id"] = "a" * 64
    path.write_text(json.dumps(history))
    with pytest.raises(ValueError, match="Disconnected"):
        inspect_document_archive(root)


@pytest.mark.parametrize(
    "limit_name,error",
    [
        ("MAX_CATALOGUE_ARTIFACT_BYTES", "retained artifact byte limit"),
        ("MAX_CATALOGUE_EXTRACTED_BYTES", "extracted text byte limit"),
    ],
)
def test_aggregate_catalogue_capacity_retry_and_archival_refusal(
    instance, monkeypatch, limit_name, error
):
    from career_os.infrastructure import documents as document_module

    root, _, service = instance
    # Each format is individually safe; the catalogue counts all retained formats.
    req = replace(request(), artifacts={"md": b"abc", "txt": b"def"})
    monkeypatch.setattr(document_module, limit_name, 5)
    with pytest.raises(ValueError, match=error):
        service.import_version(req, actor="local_user")
    assert not (root / NAMESPACE).exists()
    monkeypatch.setattr(document_module, limit_name, 6)
    receipt = service.import_version(req, actor="local_user")
    assert not service.import_version(req, actor="local_user").changed
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match=error):
        service.import_version(
            replace(req, request_id="another-version", document_id=receipt.ref.document_id),
            actor="local_user",
        )
    assert len(service.list().versions) == 1
    monkeypatch.setattr(document_module, limit_name, 5)
    with pytest.raises(ValueError, match=error):
        inspect_document_archive(root)
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before


def test_extracted_catalogue_budget_counts_utf8_bytes(instance, monkeypatch):
    from career_os.infrastructure import documents as document_module

    root, _, service = instance
    monkeypatch.setattr(document_module, "MAX_CATALOGUE_EXTRACTED_BYTES", 4)
    req = replace(request(), artifacts={"txt": "Caf\u00e9".encode("utf-8")})
    with pytest.raises(ValueError, match="extracted text byte limit"):
        service.import_version(req, actor="local_user")
    assert not (root / NAMESPACE).exists()
    monkeypatch.setattr(document_module, "MAX_CATALOGUE_EXTRACTED_BYTES", 5)
    receipt = service.import_version(req, actor="local_user")
    assert service.inspect(receipt.ref).artifacts["txt"]["text"] == "Caf\u00e9"
    assert inspect_document_archive(root).versions[0].extracted_size == 5


@pytest.mark.parametrize("bad_value", [[], {}])
@pytest.mark.parametrize("field", ["purpose", "disposition", "page_inspected_hashes", "review_id"])
def test_malformed_retained_document_fields_produce_controlled_refusal(instance, bad_value, field):
    import json

    root, _, service = instance
    imported = service.import_version(request(), actor="local_user")
    reviewed = accepted(service, imported.ref)
    if field == "purpose":
        path = (
            root
            / NAMESPACE
            / imported.ref.document_id
            / imported.ref.fingerprint
            / "document-manifest.json"
        )
        payload = json.loads(path.read_bytes())
        payload[field] = bad_value
    else:
        path = root / REVIEW_PATH
        payload = json.loads(path.read_bytes())
        record = payload["reviews"][0]
        if field == "review_id":
            record[field] = bad_value
        elif field == "page_inspected_hashes":
            record["request"][field] = [bad_value]
        else:
            record["request"][field] = bad_value
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        inspect_document_archive(root)
    with pytest.raises(ValueError):
        service.inspect(imported.ref)
    with pytest.raises(ValueError):
        service.review(
            DocumentReviewRequest(imported.ref, "accepted", basis_unknown_ack=True),
            confirmation_fingerprint=reviewed.confirmation_fingerprint,
            actor="local_user",
        )
    report = create_instance_service(root).doctor()
    assert any(
        check.name == "content_documents" and check.status == "invalid_or_unavailable"
        for check in report.checks
    )
    assert path.read_bytes() == before


@pytest.mark.parametrize("location", ["manifest", "review_history"])
def test_excessively_nested_retained_json_has_controlled_refusal(instance, location):
    root, _, service = instance
    imported = service.import_version(request(), actor="local_user")
    reviewed = accepted(service, imported.ref)
    path = (
        root
        / NAMESPACE
        / imported.ref.document_id
        / imported.ref.fingerprint
        / "document-manifest.json"
        if location == "manifest"
        else root / REVIEW_PATH
    )
    malformed = b"[" * 4000 + b"0" + b"]" * 4000
    path.write_bytes(malformed)
    review_request = DocumentReviewRequest(imported.ref, "accepted", basis_unknown_ack=True)
    for operation in (
        lambda: inspect_document_archive(root),
        lambda: service.inspect(imported.ref),
        lambda: service.list(),
        lambda: service.import_version(request(), actor="local_user"),
        lambda: service.review(review_request),
        lambda: service.review(
            review_request,
            confirmation_fingerprint=reviewed.confirmation_fingerprint,
            actor="local_user",
        ),
        lambda: service.export(
            DocumentExportRequest(imported.ref, ("md",), reviewed.review_id), actor="local_user"
        ),
    ):
        with pytest.raises(ValueError):
            operation()
    report = create_instance_service(root).doctor()
    assert any(
        check.name == "content_documents" and check.status == "invalid_or_unavailable"
        for check in report.checks
    )
    assert path.read_bytes() == malformed


def test_committed_review_replay_survives_stale_basis_and_later_review(instance):
    root, vault, service = instance
    _, revisions = seed_synthetic_evidence(vault)
    revision = revisions[0]
    vault.review(
        revision.evidence_id,
        revision.revision,
        action="approve",
        expected_content_hash=revision.content_hash,
    )
    imported = service.import_version(
        request(
            basis=(DocumentBasis(revision.evidence_id, revision.revision, revision.content_hash),)
        ),
        actor="local_user",
    )
    req = DocumentReviewRequest(imported.ref, "accepted")
    preview = service.review(req)
    committed = service.review(
        req, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
    )
    vault.edit(
        revision.evidence_id,
        expected_revision=revision.revision,
        statement=revision.statement + " (corrected)",
    )
    before = (root / REVIEW_PATH).read_bytes()
    retried = service.review(
        req, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
    )
    assert retried.review_id == committed.review_id and not retried.changed
    assert (root / REVIEW_PATH).read_bytes() == before
    assert service.inspect(imported.ref).review_state == "accepted"
    assert service.inspect(imported.ref).basis_current is False
    with pytest.raises(PermissionError):
        service.export(
            DocumentExportRequest(imported.ref, ("md",), committed.review_id), actor="local_user"
        )
    later_request = DocumentReviewRequest(
        imported.ref, "changes_requested", "Correct supplied wording"
    )
    later_preview = service.review(later_request)
    later = service.review(
        later_request,
        confirmation_fingerprint=later_preview.confirmation_fingerprint,
        actor="local_user",
    )
    before = (root / REVIEW_PATH).read_bytes()
    retried = service.review(
        req, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
    )
    assert retried.review_id == committed.review_id and not retried.changed
    assert service.inspect(imported.ref).review["review_id"] == later.review_id
    for changed_req, fingerprint in (
        (replace(req, note="Changed request"), preview.confirmation_fingerprint),
        (req, "a" * 64),
    ):
        with pytest.raises(PermissionError):
            service.review(changed_req, confirmation_fingerprint=fingerprint, actor="local_user")
    assert (root / REVIEW_PATH).read_bytes() == before


def test_explicit_legacy_review_lock_recovery_and_repeated_reviews(instance):
    import os

    from career_os.application.documents import DocumentEditRequest

    root, _, service = instance
    original = "# Fictional — café 日本語\n\nOriginal wording.\n"
    ref = service.import_version(
        replace(request(), artifacts={"md": original.encode()}), actor="local_user"
    ).ref
    accepted(service, ref)
    lock = root / "data/runtime/.document-reviews.json.lock"
    lock.write_bytes(b"123456" * 6)
    identity = (lock.stat().st_dev, lock.stat().st_ino)
    preserved = {
        p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file() and p != lock
    }
    with pytest.raises(ValueError, match="lock exceeds"):
        inspect_document_archive(root)
    with pytest.raises(PermissionError):
        service.recover_review_lock(actor="agent")
    assert lock.read_bytes() == b"123456" * 6
    assert service.recover_review_lock(actor="local_user") is True
    assert lock.read_bytes() == str(os.getpid()).encode("ascii")
    assert (lock.stat().st_dev, lock.stat().st_ino) == identity
    assert {
        p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file() and p != lock
    } == preserved
    assert service.recover_review_lock(actor="local_user") is False
    for index in range(24):
        accepted(service, ref, note=f"Exact fictional review {index}")
        assert service.inspect(ref).review_state == "accepted"
        assert len(lock.read_bytes()) <= 32
    edit = DocumentEditRequest(
        "lock-repair-edit", ref, "md", "Original wording.", "Revised wording — 日本語."
    )
    preview = service.edit(edit)
    edited = service.edit(
        edit, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
    ).ref
    review = accepted(service, edited)
    service = create_document_service(root)
    exported = service.export(
        DocumentExportRequest(edited, ("md",), review.review_id), actor="local_user"
    )
    assert (Path(exported.path) / "draft.md").read_bytes() == original.replace(
        edit.selected_text, edit.replacement_text
    ).encode()
    assert service.inspect(ref).artifacts["md"]["text"] == original
    assert len(inspect_document_archive(root).reviews) == 26


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"x" * 36,
        b"1" * 33 + b"\n",
        b"+" + b"1" * 35,
        b"1" * 33 + b"\0",
        "１".encode() * 12,
        b"1" * 4097,
    ],
)
def test_review_lock_recovery_refuses_malformed_without_writes(instance, payload):
    root, _, service = instance
    ref = service.import_version(request(), actor="local_user").ref
    req = DocumentReviewRequest(ref, "accepted", basis_unknown_ack=True)
    preview = service.review(req)
    lock = root / "data/runtime/.document-reviews.json.lock"
    lock.write_bytes(payload)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        service.recover_review_lock(actor="local_user")
    with pytest.raises(ValueError):
        service.review(
            req, confirmation_fingerprint=preview.confirmation_fingerprint, actor="local_user"
        )
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_review_lock_recovery_requires_consent_and_valid_archive(instance):
    root, vault, service = instance
    ref = service.import_version(request(), actor="local_user").ref
    lock = root / "data/runtime/.document-reviews.json.lock"
    lock.write_bytes(b"1" * 36)
    vault.record_consent(accepted=False)
    with pytest.raises(PermissionError, match="consent"):
        service.recover_review_lock(actor="local_user")
    vault.record_consent(accepted=True)
    artifact = root / NAMESPACE / ref.document_id / ref.fingerprint / "draft.md"
    artifact.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        service.recover_review_lock(actor="local_user")
    assert lock.read_bytes() == b"1" * 36


@pytest.mark.parametrize("size", [1, 32, 33, 4096])
def test_review_lock_recovery_exact_numeric_boundaries_and_missing(instance, size):
    root, _, service = instance
    lock = root / "data/runtime/.document-reviews.json.lock"
    assert service.recover_review_lock(actor="local_user") is False
    assert not lock.exists()
    lock.write_bytes(b"2" * size)
    assert service.recover_review_lock(actor="local_user") is (size > 32)
    assert 1 <= len(lock.read_bytes()) <= 32
    inspect_document_archive(root)


def test_review_lock_recovery_refuses_busy_identity_redirect_and_incomplete(instance, monkeypatch):
    import subprocess
    import sys

    import career_os.infrastructure.documents as documents

    root, _, service = instance
    lock = root / "data/runtime/.document-reviews.json.lock"
    lock.write_bytes(b"3" * 36)
    code = "from career_os.process_control import locked_process_handle; import sys; f=open(sys.argv[1], 'r+b'); c=locked_process_handle(f,label='fictional'); c.__enter__(); print('ready',flush=True); sys.stdin.read(1)"
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises((OSError, ValueError)):
            service.recover_review_lock(actor="local_user")
    finally:
        process.communicate("x", timeout=10)
    assert process.returncode == 0 and lock.read_bytes() == b"3" * 36
    identity = documents.file_identity
    with monkeypatch.context() as patch:
        patch.setattr(documents, "file_identity", lambda p: (-1, -1) if p == lock else identity(p))
        with pytest.raises(ValueError, match="changed"):
            service.recover_review_lock(actor="local_user")
    assert lock.read_bytes() == b"3" * 36
    incomplete = root / ".career-os-recovery-incomplete"
    incomplete.write_text("pending")
    with pytest.raises(ValueError, match="incomplete"):
        service.recover_review_lock(actor="local_user")
    incomplete.unlink()
    # Existing house helper creates a real junction on Windows without symlink privilege.
    runtime = root / "data/runtime"
    retained = root / "data/retained-runtime"
    runtime.rename(retained)
    _link_directory(runtime, retained)
    with pytest.raises(ValueError):
        service.recover_review_lock(actor="local_user")
    assert (retained / lock.name).read_bytes() == b"3" * 36

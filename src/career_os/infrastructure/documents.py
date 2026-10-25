"""Immutable local document versions and separately confirmed exact review history."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import Path

from career_os.application.documents import (
    FORMATS,
    DocumentBasis,
    DocumentEditProposal,
    DocumentEditRequest,
    DocumentExportReceipt,
    DocumentImportRequest,
    DocumentInspection,
    DocumentInventory,
    DocumentReceipt,
    DocumentRef,
    DocumentReviewProposal,
    DocumentReviewReceipt,
    DocumentReviewRequest,
    DocumentService,
    digest,
    identifier,
)
from career_os.document_intake import DocumentIntakeLimits, extract_document_bytes
from career_os.evidence_vault import EvidenceVault
from career_os.file_transactions import file_identity, locked_compare_and_swap, open_anchored_binary
from career_os.infrastructure.content_artifacts import (
    MAX_ARTIFACT_BYTES,
    _directory,
    publish_content_version,
)
from career_os.infrastructure.evidence_storage import (
    INSTANCE_LAYOUT_IDENTITY,
    validate_current_storage,
)
from career_os.outcome_records import _timestamp_instant
from career_os.process_control import locked_process_handle

NAMESPACE = "data/runtime/content-documents"
REVIEW_PATH = "data/runtime/document-reviews.json"
MANIFEST = "document-manifest.json"
MAX_REVIEWS = 5000
MAX_REVIEW_BYTES = 2 * 1024 * 1024
# Aggregate bounds include every format of every immutable retained version.
# Per-artifact/cardinality bounds alone could otherwise retain several GiB in one catalogue.
MAX_CATALOGUE_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_CATALOGUE_EXTRACTED_BYTES = 32 * 1024 * 1024


def _json(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _read(root, path, maximum=MAX_ARTIFACT_BYTES):
    with open_anchored_binary(
        root, file_identity(root), path.relative_to(root), label="Document artifact"
    ) as stream:
        raw = stream.read(maximum + 1)
    if not 1 <= len(raw) <= maximum:
        raise ValueError("Document artifact size is invalid")
    return raw


def _metadata(request):
    return {
        "request_id": request.request_id,
        "label": request.label,
        "purpose": request.purpose,
        "provenance": request.provenance,
        "opportunity_id": request.opportunity_id,
        "basis": [b.to_dict() for b in request.basis],
        "artifacts": {
            f: {"filename": FORMATS[f], "sha256": _hash(raw), "size": len(raw)}
            for f, raw in sorted(request.artifacts.items())
        },
    }


@dataclass(frozen=True, slots=True)
class DocumentArchivedVersion:
    ref: DocumentRef
    manifest: dict
    manifest_bytes: bytes
    artifacts: dict[str, bytes]
    extracted_size: int


@dataclass(frozen=True, slots=True)
class DocumentArchive:
    versions: tuple[DocumentArchivedVersion, ...]
    reviews: tuple[dict, ...]
    managed_paths: tuple[str, ...]


def _parse_reviews(raw):
    if not raw:
        return ()
    if len(raw) > MAX_REVIEW_BYTES:
        raise ValueError("Document review size bound exceeded")
    try:
        payload = json.loads(raw)
    except RecursionError as exc:
        raise ValueError("Document review JSON exceeds safe nesting") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "reviews"}
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or not isinstance(payload["reviews"], list)
        or len(payload["reviews"]) > MAX_REVIEWS
    ):
        raise ValueError("Invalid document review history")
    latest = {}
    seen = set()
    for record in payload["reviews"]:
        if not isinstance(record, dict) or set(record) != {
            "request",
            "manifest_sha256",
            "basis_current",
            "previous_review_id",
            "reviewed_at",
            "confirmation_fingerprint",
            "review_id",
            "confirmed_by",
        }:
            raise ValueError("Invalid document review record")
        request = DocumentReviewRequest.from_dict(record["request"])
        digest(record["manifest_sha256"])
        digest(record["review_id"])
        digest(record["confirmation_fingerprint"])
        if record["previous_review_id"] is not None:
            digest(record["previous_review_id"])
        _timestamp_instant(record["reviewed_at"], "reviewed_at")
        if record["confirmed_by"] != "local_user" or not (
            record["basis_current"] is None or type(record["basis_current"]) is bool
        ):
            raise ValueError("Invalid document review authority")
        key = (request.ref.document_id, request.ref.fingerprint)
        if record["previous_review_id"] != latest.get(key) or record["review_id"] in seen:
            raise ValueError("Disconnected document review history")
        binding = {
            k: record[k]
            for k in ("request", "manifest_sha256", "basis_current", "previous_review_id")
        }
        if record["confirmation_fingerprint"] != _hash(_json(binding)) or record[
            "review_id"
        ] != _hash(_json({k: v for k, v in record.items() if k != "review_id"})):
            raise ValueError("Document review identity changed")
        latest[key] = record["review_id"]
        seen.add(record["review_id"])
    return tuple(payload["reviews"])


def _version(root, ref, *, artifact_remaining, extracted_remaining):
    target = _directory(root / NAMESPACE / ref.document_id / ref.fingerprint)
    raw = _read(root, target / MANIFEST)
    try:
        manifest = json.loads(raw)
    except RecursionError as exc:
        raise ValueError("Document manifest JSON exceeds safe nesting") from exc
    expected = {
        "schema_version",
        "document_id",
        "fingerprint",
        "created_at",
        "request_id",
        "label",
        "purpose",
        "provenance",
        "opportunity_id",
        "basis",
        "artifacts",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != expected
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or manifest["document_id"] != ref.document_id
        or manifest["fingerprint"] != ref.fingerprint
    ):
        raise ValueError("Invalid document manifest")
    if not isinstance(manifest["basis"], list) or any(
        not isinstance(b, dict) or set(b) != {"evidence_id", "revision", "content_hash"}
        for b in manifest["basis"]
    ):
        raise ValueError("Invalid document basis")
    artifacts = manifest["artifacts"]
    if (
        not isinstance(artifacts, dict)
        or not 1 <= len(artifacts) <= 4
        or set(artifacts) - FORMATS.keys()
    ):
        raise ValueError("Invalid document artifacts")
    # Refuse declared aggregate overflow before opening any of this version's artifacts.
    declared_size = 0
    for value in artifacts.values():
        if (
            not isinstance(value, dict)
            or type(value.get("size")) is not int
            or not 1 <= value["size"] <= MAX_ARTIFACT_BYTES
        ):
            raise ValueError("Invalid document artifact metadata size")
        declared_size += value["size"]
    if declared_size > artifact_remaining:
        raise ValueError("Document catalogue retained artifact byte limit exceeded")
    names = {MANIFEST, *(FORMATS[f] for f in artifacts)}
    if {p.name for p in target.iterdir()} != names:
        raise ValueError("Document inventory is incomplete or contains extra files")
    contents = {}
    extracted_size = 0
    for f, value in artifacts.items():
        content = _read(root, target / FORMATS[f])
        if value != {"filename": FORMATS[f], "sha256": _hash(content), "size": len(content)}:
            raise ValueError("Document artifact hash changed")
        extracted = extract_document_bytes(
            content,
            filename=FORMATS[f],
            limits=DocumentIntakeLimits(max_document_bytes=MAX_ARTIFACT_BYTES),
            allow_safe_hyperlinks=True,
        )
        extracted_size += len(extracted.text.encode("utf-8"))
        if extracted_size > extracted_remaining:
            raise ValueError("Document catalogue extracted text byte limit exceeded")
        contents[f] = content
    request = DocumentImportRequest(
        manifest["request_id"],
        manifest["label"],
        manifest["purpose"],
        manifest["provenance"],
        contents,
        manifest["opportunity_id"],
        tuple(DocumentBasis(**b) for b in manifest["basis"]),
        ref.document_id,
    )
    metadata = _metadata(request)
    if ref.fingerprint != _hash(_json({"document_id": ref.document_id, **metadata})):
        raise ValueError("Document version fingerprint changed")
    _timestamp_instant(manifest["created_at"], "created_at")
    return DocumentArchivedVersion(ref, manifest, raw, contents, extracted_size)


def _inspect_document_archive(
    root: str | Path,
    *,
    review_lock_held: bool = False,
    legacy_lock_identity: tuple[int, int] | None = None,
) -> DocumentArchive:
    """Pure bounded retained-data validation; no consent, DB, locks or currentness checks.

    managed_paths includes directories, exact files and the valid bounded process review lock.
    Recovery must separately account for managed exports and the private root layout.
    """
    root = _directory(Path(root).absolute())
    namespace = _directory(root / NAMESPACE)
    paths = []
    versions = []
    request_ids = set()
    artifact_total = 0
    extracted_total = 0
    if namespace.exists():
        paths.append(NAMESPACE)
        documents = sorted(namespace.iterdir())
        if len(documents) > 100:
            raise ValueError("Document catalogue limit exceeded")
        for directory in documents:
            identifier(directory.name, "document_id")
            _directory(directory)
            paths.append(directory.relative_to(root).as_posix())
            for version in sorted(directory.iterdir()):
                if len(versions) >= 500:
                    raise ValueError("Document version limit exceeded")
                item = _version(
                    root,
                    DocumentRef(directory.name, version.name),
                    artifact_remaining=MAX_CATALOGUE_ARTIFACT_BYTES - artifact_total,
                    extracted_remaining=MAX_CATALOGUE_EXTRACTED_BYTES - extracted_total,
                )
                artifact_total += sum(len(raw) for raw in item.artifacts.values())
                extracted_total += item.extracted_size
                if item.manifest["request_id"] in request_ids:
                    raise ValueError("Document import request identity duplicated")
                request_ids.add(item.manifest["request_id"])
                versions.append(item)
                paths.extend(
                    [
                        version.relative_to(root).as_posix(),
                        *(
                            (version / name).relative_to(root).as_posix()
                            for name in [MANIFEST, *(FORMATS[f] for f in item.artifacts)]
                        ),
                    ]
                )
    review_path = root / REVIEW_PATH
    try:
        reviews = _parse_reviews(_read(root, review_path, MAX_REVIEW_BYTES))
        paths.append(REVIEW_PATH)
    except FileNotFoundError:
        reviews = ()
    lock = review_path.with_name("." + review_path.name + ".lock")
    if lock.exists():
        with open_anchored_binary(
            root, file_identity(root), lock.relative_to(root), label="Document review lock"
        ) as stream:
            metadata = os.fstat(stream.fileno())
            maximum = 4096 if legacy_lock_identity is not None else 32
            if (
                legacy_lock_identity is not None
                and (metadata.st_dev, metadata.st_ino) != legacy_lock_identity
            ):
                raise ValueError("Document review lock identity changed")
            if not 1 <= metadata.st_size <= maximum:
                raise ValueError("Document review lock exceeds its bound")
            # The Windows byte-range lock prohibits reading its first byte while held.
            # The CAS owns that lock; archival callers validate its retained PID bytes.
            if not review_lock_held and not stream.read(33).isdigit():
                raise ValueError("Document review lock is not a process lock")
        paths.append(lock.relative_to(root).as_posix())
    by_ref = {(v.ref.document_id, v.ref.fingerprint): v for v in versions}
    for record in reviews:
        request = DocumentReviewRequest.from_dict(record["request"])
        item = by_ref.get((request.ref.document_id, request.ref.fingerprint))
        if item is None or record["manifest_sha256"] != _hash(item.manifest_bytes):
            raise ValueError("Document review refers to missing or changed version")
        binary = {
            item.manifest["artifacts"][f]["sha256"] for f in item.artifacts if f in {"pdf", "docx"}
        }
        if (
            set(request.page_inspected_hashes) - binary
            or record["basis_current"] is None
            and bool(item.manifest["basis"])
            or record["basis_current"] is not None
            and not item.manifest["basis"]
        ):
            raise ValueError("Document review basis or page acknowledgements are invalid")
        if request.disposition == "accepted" and (
            record["basis_current"] is False
            or record["basis_current"] is None
            and not request.basis_unknown_ack
        ):
            raise ValueError("Retained accepted review lacks its required basis acknowledgement")
    return DocumentArchive(tuple(versions), reviews, tuple(sorted(paths)))


def inspect_document_archive(root: str | Path) -> DocumentArchive:
    """Validate every retained version/review and account for managed paths without IO writes."""
    return _inspect_document_archive(root)


class LocalDocumentRepository:
    def __init__(self, instance_root, *, vault=None):
        lexical = _directory(Path(instance_root).absolute())
        self._vault = vault or EvidenceVault(lexical)
        self.root = self._vault.instance_root
        if self.root != lexical.resolve(strict=False):
            raise ValueError("Document vault belongs to another private root")

    def _guard(self):
        _directory(self.root)
        with self._vault._connect(create=False) as connection:
            if validate_current_storage(connection, self.root) != INSTANCE_LAYOUT_IDENTITY:
                raise ValueError("Documents require an initialized current private workspace")
        for name in (
            "inbox",
            "applications",
            "exports",
            "tmp",
            "data",
            "data/runtime",
            "data/logs",
        ):
            if not _directory(self.root / name).is_dir():
                raise ValueError("Private workspace layout is incomplete")
        incomplete = self.root / ".career-os-recovery-incomplete"
        from career_os.file_transactions import _safe_regular_file_path

        if _safe_regular_file_path(incomplete).exists():
            raise ValueError("Private workspace recovery is incomplete")
        if not self._vault.has_consent():
            raise PermissionError("Local-processing consent required")

    def recover_review_lock(self):
        # This is deliberately separate from pure reads and normal PID-writing locks.
        with self._vault.read_transaction():
            self._guard()
            root_identity = file_identity(self.root)
            lock = (self.root / REVIEW_PATH).with_name(".document-reviews.json.lock")
            from career_os.file_transactions import _safe_regular_file_path

            if not _safe_regular_file_path(lock).exists():
                return False
            opened = open_anchored_binary(
                self.root,
                root_identity,
                lock.relative_to(self.root),
                label="Document review lock",
                writable=True,
            )
            with opened as stream:
                observed = stream.read(4097)
                if not 1 <= len(observed) <= 4096 or not observed.isdigit():
                    raise ValueError("Document review lock is not bounded ASCII digits")
                metadata = os.fstat(stream.fileno())
                identity = (metadata.st_dev, metadata.st_ino)
                with locked_process_handle(stream, label=str(lock)):
                    stream.seek(0)
                    if stream.read(4097) != observed or file_identity(lock) != identity:
                        raise ValueError("Document review lock changed before recovery")
                    if len(observed) <= 32:
                        return False
                    _inspect_document_archive(
                        self.root, review_lock_held=True, legacy_lock_identity=identity
                    )
                    self._guard()
                    stream.seek(0)
                    if (
                        stream.read(4097) != observed
                        or file_identity(lock) != identity
                        or file_identity(self.root) != root_identity
                    ):
                        raise ValueError("Document review lock changed during recovery")
                    stream.seek(0)
                    stream.write(str(os.getpid()).encode("ascii"))
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
                    return True

    def _current(self, basis):
        if not basis:
            return None
        try:
            for b in basis:
                current = self._vault.resolve(b.evidence_id)
                if (
                    current.status != "approved"
                    or current.revision != b.revision
                    or current.content_hash != b.content_hash
                ):
                    return False
        except (ValueError, OSError):
            return False
        return True

    def _inspection(self, item, reviews):
        m = item.manifest
        basis = tuple(DocumentBasis(**b) for b in m["basis"])
        current = self._current(basis)
        review = next(
            (r for r in reversed(reviews) if r["request"]["ref"] == item.ref.to_dict()), None
        )
        state = review["request"]["disposition"] if review else "unreviewed"
        artifacts = {}
        for f, raw in item.artifacts.items():
            extracted = extract_document_bytes(
                raw,
                filename=FORMATS[f],
                limits=DocumentIntakeLimits(max_document_bytes=MAX_ARTIFACT_BYTES),
                allow_safe_hyperlinks=True,
            )
            artifacts[f] = {
                **m["artifacts"][f],
                "text": extracted.text,
                "page_count": extracted.page_count,
            }
        return DocumentInspection(
            item.ref,
            m["label"],
            m["purpose"],
            m["opportunity_id"],
            True,
            current,
            artifacts,
            state,
            review,
            m["created_at"],
            m["provenance"],
            basis,
        )

    def inspect(self, ref):
        with self._vault.read_transaction():
            self._guard()
            archive = inspect_document_archive(self.root)
            item = next((v for v in archive.versions if v.ref == ref), None)
            if item is None:
                raise ValueError("Document version is missing")
            return self._inspection(item, archive.reviews)

    def list(self, *, opportunity_id=None):
        with self._vault.read_transaction():
            self._guard()
            archive = inspect_document_archive(self.root)
            return DocumentInventory(
                tuple(
                    self._inspection(v, archive.reviews)
                    for v in archive.versions
                    if opportunity_id is None or v.manifest["opportunity_id"] == opportunity_id
                )
            )

    def import_version(self, request):
        # Revalidate a possibly mutated caller dictionary before retaining it.
        request = DocumentImportRequest(
            request.request_id,
            request.label,
            request.purpose,
            request.provenance,
            dict(request.artifacts),
            request.opportunity_id,
            request.basis,
            request.document_id,
        )
        # Admission precedes format parsing. The write transaction rechecks it
        # before retaining anything, including if consent changes during parsing.
        with self._vault.read_transaction():
            self._guard()
        incoming_extracted_size = 0
        for f, raw in request.artifacts.items():
            extracted = extract_document_bytes(
                raw,
                filename=FORMATS[f],
                limits=DocumentIntakeLimits(max_document_bytes=MAX_ARTIFACT_BYTES),
                allow_safe_hyperlinks=True,
            )
            incoming_extracted_size += len(extracted.text.encode("utf-8"))
        with self._vault.existing_transaction():
            return self._import_locked(request, incoming_extracted_size)

    def _import_locked(self, request, incoming_extracted_size):
        self._guard()
        archive = inspect_document_archive(self.root)
        metadata = _metadata(request)
        prior = next(
            (v for v in archive.versions if v.manifest["request_id"] == request.request_id),
            None,
        )
        if prior:
            if (
                {k: prior.manifest[k] for k in metadata} != metadata
                or request.document_id is not None
                and request.document_id != prior.ref.document_id
            ):
                raise ValueError("Import request identity already binds different content")
            return DocumentReceipt(prior.ref, False, prior.manifest["created_at"])
        artifact_total = sum(
            len(raw) for version in archive.versions for raw in version.artifacts.values()
        )
        extracted_total = sum(version.extracted_size for version in archive.versions)
        if (
            artifact_total + sum(len(raw) for raw in request.artifacts.values())
            > MAX_CATALOGUE_ARTIFACT_BYTES
        ):
            raise ValueError("Document catalogue retained artifact byte limit exceeded")
        if extracted_total + incoming_extracted_size > MAX_CATALOGUE_EXTRACTED_BYTES:
            raise ValueError("Document catalogue extracted text byte limit exceeded")
        document_id = request.document_id or "document-" + _hash(request.request_id.encode())[:32]
        if request.document_id is not None and not any(
            v.ref.document_id == document_id for v in archive.versions
        ):
            raise ValueError("Revised document identity does not exist")
        if (
            len(archive.versions) >= 500
            or len({v.ref.document_id for v in archive.versions} | {document_id}) > 100
        ):
            raise ValueError("Document catalogue limit exceeded")
        if self._current(request.basis) is False:
            raise ValueError("Document evidence basis is stale or unresolved")
        fingerprint = _hash(_json({"document_id": document_id, **metadata}))
        ref = DocumentRef(document_id, fingerprint)
        created_at = datetime.now(UTC).isoformat()
        manifest = {
            "schema_version": 1,
            "document_id": document_id,
            "fingerprint": fingerprint,
            "created_at": created_at,
            **metadata,
        }
        publish_content_version(
            self.root / NAMESPACE / document_id / fingerprint,
            {
                MANIFEST: _json(manifest),
                **{FORMATS[f]: raw for f, raw in request.artifacts.items()},
            },
        )
        return DocumentReceipt(ref, True, created_at)

    def edit(self, request, fingerprint):
        request = DocumentEditRequest(**request.to_dict() | {"ref": request.ref})
        transaction = (
            self._vault.existing_transaction
            if fingerprint is not None
            else self._vault.read_transaction
        )
        with transaction():
            self._guard()
            archive = inspect_document_archive(self.root)
            item = next((v for v in archive.versions if v.ref == request.ref), None)
            if item is None or request.format not in item.artifacts:
                raise ValueError("Selected document text version is unavailable")
            text = item.artifacts[request.format].decode("utf-8")
            # Intake/Qt display all source line endings as LF. Map the unique
            # displayed span back to its original boundaries, including mixed
            # and standalone CR breaks; retain every source byte outside it.
            displayed = []
            boundaries = [0]
            offset = 0
            while offset < len(text):
                char = text[offset]
                offset += 1
                if char == "\r":
                    if offset < len(text) and text[offset] == "\n":
                        offset += 1
                    char = "\n"
                displayed.append(char)
                boundaries.append(offset)
            displayed = "".join(displayed)
            selection = request.selected_text.replace("\r\n", "\n").replace("\r", "\n")
            position = displayed.find(selection)
            if position == -1 or displayed.find(selection, position + 1) != -1:
                raise ValueError(
                    "Selected passage is missing or ambiguous; select exact unique text"
                )
            start, end = boundaries[position], boundaries[position + len(selection)]
            position, passage = start, text[start:end]
            replacement = request.replacement_text
            if "\r" not in replacement:
                # New lines inside the edited span use its first source break.
                first_break = next((i for i, ch in enumerate(passage) if ch in "\r\n"), None)
                if first_break is not None and passage[first_break] == "\r":
                    newline = "\r\n" if passage.startswith("\r\n", first_break) else "\r"
                    replacement = replacement.replace("\n", newline)
            proposed = text[:position] + replacement + text[position + len(passage) :]
            raw = proposed.encode("utf-8")
            # Existing intake owns active content, unsafe links and size validation.
            extracted = extract_document_bytes(
                raw,
                filename=FORMATS[request.format],
                limits=DocumentIntakeLimits(max_document_bytes=MAX_ARTIFACT_BYTES),
                allow_safe_hyperlinks=True,
            )
            m = item.manifest
            basis = tuple(DocumentBasis(**b) for b in m["basis"])
            incoming = DocumentImportRequest(
                request.request_id,
                m["label"],
                m["purpose"],
                "Targeted edit of "
                + request.ref.fingerprint
                + "; request "
                + _hash(_json(request.to_dict())),
                {request.format: raw},
                m["opportunity_id"],
                basis,
                request.ref.document_id,
            )
            # Recover a committed identical request before checking a now-stale basis.
            prior = next(
                (v for v in archive.versions if v.manifest["request_id"] == request.request_id),
                None,
            )
            if fingerprint is not None and prior is not None:
                return self._import_locked(incoming, len(extracted.text.encode("utf-8")))
            if self._current(basis) is False:
                raise ValueError("Document evidence basis is stale or unresolved")
            binding = {
                "instance": str(self.root),
                "request": request.to_dict(),
                "versions": [
                    v.ref.to_dict()
                    for v in archive.versions
                    if v.ref.document_id == request.ref.document_id
                ],
                "reviews": [
                    r["review_id"]
                    for r in archive.reviews
                    if r["request"]["ref"]["document_id"] == request.ref.document_id
                ],
                "basis_current": self._current(basis),
            }
            expected = _hash(_json(binding))
            if fingerprint is not None:
                if fingerprint != expected:
                    raise ValueError("Document edit conflict; preview the selected version again")
                return self._import_locked(incoming, len(extracted.text.encode("utf-8")))
            summary = "\n".join(
                unified_diff(
                    request.selected_text.splitlines(),
                    request.replacement_text.splitlines(),
                    fromfile="Selected passage",
                    tofile="Proposed passage",
                    lineterm="",
                )
            )
            return DocumentEditProposal(request, proposed, summary, expected)

    def _proposal(self, request, archive):
        item = next((v for v in archive.versions if v.ref == request.ref), None)
        if item is None:
            raise ValueError("Document version is missing")
        inspection = self._inspection(item, archive.reviews)
        binary = {m["sha256"] for f, m in inspection.artifacts.items() if f in {"pdf", "docx"}}
        if set(request.page_inspected_hashes) - binary:
            raise ValueError("Page acknowledgement does not identify a retained binary")
        if request.disposition == "accepted" and (
            inspection.basis_current is False
            or inspection.basis_current is None
            and not request.basis_unknown_ack
        ):
            raise PermissionError(
                "Acceptance requires current basis or explicit unknown-basis acknowledgement"
            )
        previous = inspection.review["review_id"] if inspection.review else None
        binding = {
            "request": request.to_dict(),
            "manifest_sha256": _hash(item.manifest_bytes),
            "basis_current": inspection.basis_current,
            "previous_review_id": previous,
        }
        return DocumentReviewProposal(request, inspection, _hash(_json(binding)), previous), binding

    def review(self, request, fingerprint):
        if fingerprint is None:
            with self._vault.read_transaction():
                self._guard()
                proposal, _ = self._proposal(request, inspect_document_archive(self.root))
                return proposal
        receipt = None
        with self._vault.existing_transaction():
            self._guard()

            # A saved preview must not normalize malformed retained lock bytes as a
            # side effect of the normal PID-writing CAS lock. Recovery is explicit.
            inspect_document_archive(self.root)

            def append(raw):
                nonlocal receipt
                records = _parse_reviews(raw)
                archive = _inspect_document_archive(self.root, review_lock_held=True)
                if archive.reviews != records:
                    raise ValueError("Document review history changed")
                # Archive validation binds all retained bytes, manifests and the review
                # chain before replay. A committed reply may be retried after its basis
                # changes or a later review; replay never creates or replaces a review.
                prior = next(
                    (
                        record
                        for record in records
                        if record["confirmation_fingerprint"] == fingerprint
                        and record["request"] == request.to_dict()
                    ),
                    None,
                )
                if prior is not None:
                    receipt = DocumentReviewReceipt(prior, False)
                    return raw
                proposal, binding = self._proposal(request, archive)
                if proposal.confirmation_fingerprint != fingerprint:
                    raise PermissionError("Document review confirmation is stale")
                if len(records) >= MAX_REVIEWS:
                    raise ValueError("Document review history limit exceeded")
                record = {
                    **binding,
                    "reviewed_at": datetime.now(UTC).isoformat(),
                    "confirmation_fingerprint": fingerprint,
                    "confirmed_by": "local_user",
                }
                record["review_id"] = _hash(_json(record))
                updated = _json({"schema_version": 1, "reviews": [*records, record]})
                if len(updated) > MAX_REVIEW_BYTES:
                    raise ValueError("Document review size bound exceeded")
                receipt = DocumentReviewReceipt(record, True)
                return updated

            locked_compare_and_swap(self.root / REVIEW_PATH, append, maximum_bytes=MAX_REVIEW_BYTES)
        return receipt

    def export(self, request):
        with self._vault.existing_transaction():
            self._guard()
            archive = inspect_document_archive(self.root)
            item = next((v for v in archive.versions if v.ref == request.ref), None)
            if item is None:
                raise ValueError("Document version is missing")
            inspection = self._inspection(item, archive.reviews)
            review = inspection.review
            if (
                not review
                or review["review_id"] != request.review_id
                or inspection.review_state != "accepted"
                or inspection.basis_current is False
            ):
                raise PermissionError("Export requires the exact current accepted review")
            if set(request.formats) - item.artifacts.keys():
                raise ValueError("Selected export format is unavailable")
            if inspection.basis_current is None and not review["request"]["basis_unknown_ack"]:
                raise PermissionError("Unknown basis must be acknowledged")
            required = {
                inspection.artifacts[f]["sha256"] for f in request.formats if f in {"pdf", "docx"}
            }
            if required - set(review["request"]["page_inspected_hashes"]):
                raise PermissionError("Each selected binary requires explicit page inspection")
            target = (
                self.root
                / "exports"
                / ("document-" + request.ref.fingerprint[:16] + "-" + secrets.token_hex(8))
            )
            files = {FORMATS[f]: item.artifacts[f] for f in request.formats}
            publish_content_version(target, files)
            return DocumentExportReceipt(
                item.ref, request.review_id, str(target), tuple(sorted(files))
            )


def create_document_service(instance_root: str | Path) -> DocumentService:
    return DocumentService(LocalDocumentRepository(instance_root))

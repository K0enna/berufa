"""Consent-bound local evidence onboarding with untrusted content treated only as data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from career_os.infrastructure.evidence import (
    create_evidence_service,
)
from career_os.infrastructure.evidence import (
    read_private_json as read_private_json,
)
from career_os.public_api import (
    EvidenceProposalInput as EvidenceProposalInput,
)
from career_os.public_api import (
    InterviewAnswerInput as InterviewAnswerInput,
)
from career_os.public_api import (
    SourceDocument,
    require_evidence_result,
)


def _read_private_proposal(instance_root: Path, path: Path) -> EvidenceProposalInput:
    payload, _ = read_private_json(instance_root, path, label="Proposal")
    return EvidenceProposalInput.from_dict(payload)


def _source_receipt(source: SourceDocument) -> dict[str, object]:
    return {
        "source_id": source.source_id,
        "source_hash": source.source_hash,
        "extracted_hash": source.extracted_hash,
        "media_type": source.media_type,
        "input_format": source.input_format,
        "size_bytes": source.size_bytes,
        "line_count": source.line_count,
        "page_count": source.page_count,
        "location_count": len(source.locations),
        "location_kinds": sorted({location.kind for location in source.locations}),
        "managed_copy": source.managed_copy,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument(
        "--accept-local-processing",
        action="store_true",
        help=("Record consent for a managed local copy when this workspace has no prior consent"),
    )
    args = parser.parse_args(argv)
    try:
        service = create_evidence_service(args.instance_root)
        if not require_evidence_result(service.has_consent()):
            if not args.accept_local_processing:
                print(
                    "Document import requires prior local consent or --accept-local-processing. "
                    "The workspace stores a managed copy of the original bytes and extracted text; "
                    "it makes no model call or external action.",
                    file=sys.stderr,
                )
                return 2
            require_evidence_result(service.record_consent(accepted=True))
        source = require_evidence_result(service.import_document(args.document))
    except (OSError, PermissionError, ValueError) as exc:
        print(f"Rolevane document import failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema_version": 1,
                "source": _source_receipt(source),
                "proposals_created": 0,
                "model_calls": False,
                "external_actions": False,
            },
            sort_keys=True,
        )
    )
    return 0


def proposal_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create one explicitly unapproved evidence proposal from a private JSON file."
    )
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        proposal = _read_private_proposal(args.instance_root, args.proposal)
        service = create_evidence_service(args.instance_root)
        revision = require_evidence_result(service.propose(proposal))
        source_locations = require_evidence_result(
            service.source_locations(
                revision.source_id, revision.source_start_line, revision.source_end_line
            )
        )
    except (OSError, PermissionError, ValueError) as exc:
        print(f"Rolevane evidence proposal failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_id": revision.evidence_id,
                "revision": revision.revision,
                "content_hash": revision.content_hash,
                "source_id": revision.source_id,
                "source_hash": revision.source_hash,
                "source_start_line": revision.source_start_line,
                "source_end_line": revision.source_end_line,
                "source_locations": [location.to_dict() for location in source_locations],
                "kind": revision.kind,
                "status": revision.status,
                "proposal_origin": proposal.actor,
                "requires_user_review": revision.status in {"unapproved", "deferred"},
                "model_calls": False,
                "external_actions": False,
            },
            sort_keys=True,
        )
    )
    return 0


def source_list_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List content-free identities for imported local sources."
    )
    parser.add_argument("--instance-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        service = create_evidence_service(args.instance_root)
        sources = require_evidence_result(service.list_sources())
    except (OSError, PermissionError, ValueError) as exc:
        print(f"Rolevane evidence source list failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [source.to_dict() for source in sources],
                "content_included": False,
                "filenames_included": False,
                "model_calls": False,
                "external_actions": False,
            },
            sort_keys=True,
        )
    )
    return 0


def source_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read one bounded exact span from an imported local source."
    )
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--start-line", type=int, required=True)
    parser.add_argument("--end-line", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        service = create_evidence_service(args.instance_root)
        span = require_evidence_result(
            service.read_source_span(args.source_id, args.start_line, args.end_line)
        )
    except (OSError, PermissionError, ValueError) as exc:
        print(f"Rolevane evidence source read failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                **span.to_dict(),
                "model_calls": False,
                "external_actions": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def interview_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the resumable, model-disabled evidence interview or import one private answer."
        )
    )
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--answer", type=Path)
    args = parser.parse_args(argv)
    try:
        service = create_evidence_service(args.instance_root)
        if not require_evidence_result(service.has_consent()):
            print(
                "Evidence interview requires prior local-processing consent. It stores each answer "
                "as a managed local source and creates only an unapproved proposal.",
                file=sys.stderr,
            )
            return 2
        if args.answer is None:
            status = require_evidence_result(service.interview_status())
            print(json.dumps({"schema_version": 1, **status}, sort_keys=True))
            return 0
        payload, content = read_private_json(
            args.instance_root,
            args.answer,
            label="Interview answer",
        )
        answer = InterviewAnswerInput.from_dict(payload)
        result = require_evidence_result(
            service.submit_interview_answer(display_name=args.answer.name, content=content)
        )
        created, revision, source = result.created, result.revision, result.source
        source_locations, status = result.source_locations, result.status
    except (OSError, PermissionError, ValueError) as exc:
        print(f"Rolevane evidence interview failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema_version": 1,
                "question_id": answer.question.question_id,
                "created": created,
                "evidence_id": revision.evidence_id,
                "revision": revision.revision,
                "content_hash": revision.content_hash,
                "source_id": source.source_id,
                "source_hash": source.source_hash,
                "extracted_hash": source.extracted_hash,
                "source_start_line": revision.source_start_line,
                "source_end_line": revision.source_end_line,
                "source_locations": [location.to_dict() for location in source_locations],
                "kind": revision.kind,
                "status": revision.status,
                "completed_questions": status["completed_questions"],
                "total_questions": status["total_questions"],
                "next_question": status["next_question"],
                "proposal_origin": "deterministic_rule",
                "requires_user_review": revision.status in {"unapproved", "deferred"},
                "model_calls": False,
                "external_actions": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

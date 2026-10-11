"""Free local document management over the same service used by the desktop."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from career_os.document_intake import DocumentIntakeLimits, _read_bounded_regular_file
from career_os.infrastructure.documents import create_document_service
from career_os.public_api import (
    DocumentEditRequest,
    DocumentExportRequest,
    DocumentImportRequest,
    DocumentRef,
    DocumentReviewRequest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--opportunity-id")
    importing = commands.add_parser("import")
    importing.add_argument("--input", type=Path, action="append", required=True)
    importing.add_argument("--request-id", required=True)
    importing.add_argument("--document-id")
    importing.add_argument("--label", required=True)
    importing.add_argument(
        "--purpose",
        choices=("cv", "letter", "answer", "interview", "follow_up", "other"),
        required=True,
    )
    importing.add_argument("--provenance", required=True)
    importing.add_argument("--opportunity-id")
    importing.add_argument("--confirm-local-import", action="store_true", required=True)
    for name in ("inspect", "review", "export", "edit"):
        action = commands.add_parser(name)
        action.add_argument("--document-id", required=True)
        action.add_argument("--fingerprint", required=True)
        if name == "edit":
            action.add_argument("--request-id", required=True)
            action.add_argument("--format", choices=("md", "txt"), required=True)
            action.add_argument("--selected-text", required=True)
            action.add_argument("--replacement-input", type=Path, required=True)
            action.add_argument("--confirm-edit")
        elif name == "review":
            action.add_argument(
                "--disposition", choices=("accepted", "changes_requested"), required=True
            )
            action.add_argument("--note", default="")
            action.add_argument(
                "--page-inspected", action="append", choices=("pdf", "docx"), default=[]
            )
            action.add_argument("--acknowledge-unknown-basis", action="store_true")
            action.add_argument("--confirm-review")
        elif name == "export":
            action.add_argument(
                "--format", action="append", choices=("md", "txt", "pdf", "docx"), required=True
            )
            action.add_argument("--review-id", required=True)
            action.add_argument("--confirm-local-export", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        service = create_document_service(args.instance_root)
        if args.command == "list":
            result = service.list(opportunity_id=args.opportunity_id)
        elif args.command == "import":
            if not 1 <= len(args.input) <= 4:
                raise ValueError("Select one to four distinct formats")
            artifacts = {}
            for path in args.input:
                format = path.suffix.lower().lstrip(".")
                if format not in {"md", "txt", "pdf", "docx"} or format in artifacts:
                    raise ValueError("Select one file per supported format")
                artifacts[format] = _read_bounded_regular_file(
                    path, DocumentIntakeLimits(max_document_bytes=2 * 1024 * 1024)
                )
            result = service.import_version(
                DocumentImportRequest(
                    args.request_id,
                    args.label,
                    args.purpose,
                    args.provenance,
                    artifacts,
                    args.opportunity_id,
                    document_id=args.document_id,
                ),
                actor="local_user",
            )
        else:
            ref = DocumentRef(args.document_id, args.fingerprint)
            if args.command == "edit":
                replacement = _read_bounded_regular_file(
                    args.replacement_input, DocumentIntakeLimits(max_document_bytes=128 * 1024)
                ).decode("utf-8")
                result = service.edit(
                    DocumentEditRequest(
                        args.request_id, ref, args.format, args.selected_text, replacement
                    ),
                    confirmation_fingerprint=args.confirm_edit,
                    actor="local_user" if args.confirm_edit else None,
                )
            elif args.command == "inspect":
                result = service.inspect(ref)
            elif args.command == "review":
                inspection = service.inspect(ref)
                hashes = tuple(
                    inspection.artifacts[format]["sha256"] for format in args.page_inspected
                )
                result = service.review(
                    DocumentReviewRequest(
                        ref, args.disposition, args.note, hashes, args.acknowledge_unknown_basis
                    ),
                    confirmation_fingerprint=args.confirm_review,
                    actor="local_user" if args.confirm_review else None,
                )
            else:
                result = service.export(
                    DocumentExportRequest(ref, tuple(args.format), args.review_id),
                    actor="local_user",
                )
    except (ValueError, OSError, KeyError, RuntimeError):
        print(
            "Local document operation refused: check consent, input, exact review and retained state.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Inspect and review real local preparation versions without inferring career events."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from career_os.infrastructure.evidence import read_private_json
from career_os.infrastructure.workspaces import create_workspace_service
from career_os.public_api import (
    MAX_WORKSPACE_REQUEST_BYTES,
    WorkspaceRef,
    WorkspaceReviewRequest,
    require_workspace_result,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover, inspect and explicitly review prepared content."
    )
    parser.add_argument("--instance-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--kind", required=True, choices=("application", "interview"))
    inspect.add_argument("--opportunity-id", required=True)
    inspect.add_argument("--round-id")
    inspect.add_argument("--fingerprint", required=True)
    review = commands.add_parser("review")
    review.add_argument("--request", type=Path, required=True)
    review.add_argument("--confirm-review", metavar="PREVIEW_SHA256")
    args = parser.parse_args(argv)
    try:
        service = create_workspace_service(args.instance_root)
        if args.command == "list":
            result = require_workspace_result(service.list())
        elif args.command == "inspect":
            result = require_workspace_result(
                service.inspect(
                    WorkspaceRef(args.kind, args.opportunity_id, args.fingerprint, args.round_id)
                )
            )
        else:
            payload, _ = read_private_json(
                args.instance_root,
                args.request,
                label="Workspace review request",
                maximum_bytes=MAX_WORKSPACE_REQUEST_BYTES,
            )
            result = require_workspace_result(
                service.review(
                    WorkspaceReviewRequest.from_dict(payload),
                    confirmation_fingerprint=args.confirm_review,
                    actor="local_user" if args.confirm_review else None,
                )
            )
    except (ValueError, OSError):
        print(
            "Rolevane workspace operation failed: check input, consent, exact confirmation and local state.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result.to_dict(), sort_keys=True))
    if args.command == "list" and result.diagnostics:
        return 1
    return 1 if args.command == "inspect" and not result.summary.current else 0


if __name__ == "__main__":
    raise SystemExit(main())

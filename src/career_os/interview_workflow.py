"""Plan, confirm and validate real local interview content from approved evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from career_os.infrastructure.evidence import read_private_json
from career_os.infrastructure.interviews import create_interview_service
from career_os.public_api import (
    MAX_INTERVIEW_REQUEST_BYTES,
    InterviewRequest,
    require_interview_result,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a local evidence-bound interview brief.")
    parser.add_argument("--instance-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "prepare"):
        sub = commands.add_parser(command)
        sub.add_argument("--request", type=Path, required=True)
        if command == "prepare":
            sub.add_argument("--confirm-plan", required=True, metavar="PREVIEW_SHA256")
    validate = commands.add_parser("validate")
    validate.add_argument("--opportunity-id", required=True)
    validate.add_argument("--round-id", required=True)
    validate.add_argument("--fingerprint", required=True)
    args = parser.parse_args(argv)
    try:
        service = create_interview_service(args.instance_root)
        if args.command == "validate":
            result = require_interview_result(
                service.validate(args.opportunity_id, args.round_id, args.fingerprint)
            )
        else:
            payload, _ = read_private_json(
                args.instance_root,
                args.request,
                label="Interview request",
                maximum_bytes=MAX_INTERVIEW_REQUEST_BYTES,
            )
            request = InterviewRequest.from_dict(payload)
            if args.command == "plan":
                result = require_interview_result(service.plan(request))
            else:
                result = require_interview_result(
                    service.prepare(
                        request, confirmation_fingerprint=args.confirm_plan, actor="local_user"
                    )
                )
    except (ValueError, OSError):
        print(
            "Rolevane interview operation failed: check input, consent, confirmation and local state.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 1 if args.command == "validate" and not result.current else 0


if __name__ == "__main__":
    raise SystemExit(main())

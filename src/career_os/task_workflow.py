"""Explicit local plans; completing a task never records an employer event."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from career_os.infrastructure.tasks import create_task_service
from career_os.public_api import TaskDue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--opportunity-id")
    for name in ("create", "update"):
        action = commands.add_parser(name)
        if name == "create":
            action.add_argument("--opportunity-id", required=True)
        else:
            action.add_argument("--task-id", required=True)
            action.add_argument("--expected-revision", required=True, type=int)
        action.add_argument("--description", required=True)
        action.add_argument("--due")
        action.add_argument("--due-kind", choices=("date", "timed"))
        action.add_argument("--timezone")
        action.add_argument("--request-id", required=True)
        action.add_argument("--confirm-local-plan", action="store_true", required=True)
    terminal = commands.add_parser("status")
    terminal.add_argument("--task-id", required=True)
    terminal.add_argument("--expected-revision", required=True, type=int)
    terminal.add_argument("--status", required=True, choices=("completed", "cancelled"))
    terminal.add_argument("--request-id", required=True)
    terminal.add_argument("--confirm-local-plan", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        service = create_task_service(args.instance_root)
        if args.command == "list":
            result = service.list(opportunity_id=args.opportunity_id)
        elif args.command == "status":
            result = service.set_status(
                args.task_id,
                status=args.status,
                expected_revision=args.expected_revision,
                request_id=args.request_id,
                actor="local_user",
            )
        else:
            has_due = any((args.due, args.due_kind, args.timezone))
            if has_due and not all((args.due, args.due_kind, args.timezone)):
                raise ValueError("A due date requires kind, value and timezone")
            values = dict(
                description=args.description,
                due=TaskDue(args.due_kind, args.due, args.timezone) if has_due else None,
                request_id=args.request_id,
                actor="local_user",
            )
            if args.command == "create":
                result = service.create(opportunity_id=args.opportunity_id, **values)
            else:
                result = service.update(
                    args.task_id, expected_revision=args.expected_revision, **values
                )
    except (ValueError, OSError, PermissionError, RuntimeError):
        print(
            "Local task operation refused: check consent, input, revision and retained state.",
            file=sys.stderr,
        )
        return 1
    output = (
        {
            "current": [record.to_dict() for record in result.current],
            "history": [record.to_dict() for record in result.history],
        }
        if args.command == "list"
        else {"record": result.record.to_dict(), "changed": result.changed}
    )
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

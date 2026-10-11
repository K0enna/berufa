"""Independent human terminal review for the sealed fictional companion profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from career_os.infrastructure.companion import LocalCompanionRepository, LocalCompanionReview
from career_os.public_api import CompanionError, CompanionRef


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", type=Path, help="Explicit initialized fictional instance")
    args = parser.parse_args(argv)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(json.dumps({"error": "independent_human_terminal_required"}))
        return 2
    try:
        human = LocalCompanionReview(LocalCompanionRepository(args.instance))
        print(
            "Fictional profile only. Same-user agent access is not isolated. No real-data enablement."
        )
        print(
            "Commands: setup, inspect, grant, revoke, review, confirm, recover, "
            "upgrade, cancel, retire, disconnect, reconnect, exit."
        )
        while True:
            command = input("Local review command: ").strip()
            if command == "exit":
                return 0
            try:
                if command in {"setup", "disconnect", "reconnect"}:
                    if input(f"Type {command} to confirm this connection change: ") != command:
                        raise CompanionError("human_required")
                    getattr(human, command)()
                    result = {"operation": command, "completed": True}
                elif command == "inspect":
                    kind = input("Object kind: ")
                    identity = input("Exact object ID: ")
                    historical = input("Historical read? Type yes or no: ")
                    if historical not in {"yes", "no"}:
                        raise CompanionError("invalid_input")
                    revision = input("Exact revision for history, or blank for current: ") or None
                    ref, data = human.inspect(
                        kind, identity, historical=historical == "yes", revision=revision
                    )
                    result = {"ref": ref.to_dict(), "historical": historical == "yes", "data": data}
                elif command == "grant":
                    ref = CompanionRef.from_dict(
                        json.loads(input("Exact reference JSON from inspect: "))
                    )
                    fields = tuple(input("Reviewed fields, comma separated: ").split(","))
                    purpose = input("Purpose: ")
                    historical = input("Historical read? Type yes or no: ")
                    if historical not in {"yes", "no"}:
                        raise CompanionError("invalid_input")
                    ref_checked, data = human.inspect(
                        ref.kind,
                        ref.object_id,
                        historical=historical == "yes",
                        revision=ref.revision,
                    )
                    if ref_checked != ref:
                        raise CompanionError("stale_basis")
                    print(
                        json.dumps(
                            {
                                "recipient": "chatgpt",
                                "ref": ref.to_dict(),
                                "purpose": purpose,
                                "historical": historical == "yes",
                                "data": {field: data[field] for field in fields},
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    if input("Type grant to permit these values for 24 hours: ") != "grant":
                        raise CompanionError("human_required")
                    result = {
                        "grant_id": human.grant(
                            ref, fields, purpose, historical=historical == "yes"
                        )
                    }
                elif command == "revoke":
                    human.revoke(input("Exact grant ID: "))
                    result = {"revoked": True}
                elif command in {"upgrade", "cancel", "retire"}:
                    identities = ()
                    if command == "cancel":
                        identities = (input("Exact still-prepared pending ID: ").strip(),)
                    elif command == "retire":
                        identities = tuple(
                            input("Exact eligible pending IDs, comma separated: ")
                            .strip()
                            .split(",")
                        )
                    preview = human.lifecycle_preview(command, identities)
                    print(json.dumps(preview, ensure_ascii=False, indent=2))
                    if (
                        input(f"Type {command} to confirm this exact lifecycle preview: ")
                        != command
                    ):
                        raise CompanionError("human_required")
                    result = human.lifecycle_confirm(preview)
                elif command in {"review", "confirm", "recover"}:
                    identity = input("Exact pending ID: ")
                    if command == "recover":
                        result = human.recover(identity)
                    else:
                        result = human.pending_review(identity)
                        if command == "confirm":
                            print(json.dumps(result, ensure_ascii=False, indent=2))
                            if (
                                input(f"Type confirm {identity} after reviewing the exact inputs: ")
                                != f"confirm {identity}"
                            ):
                                raise CompanionError("human_required")
                            capability = human.issue_confirmation(identity)
                            # Capability remains in process memory; never print or put it in argv.
                            result = human.confirm(identity, capability)
                else:
                    raise CompanionError("unavailable_operation")
                print(json.dumps(result, ensure_ascii=False, indent=2))
            except CompanionError as exc:
                print(json.dumps({"error": exc.code}))
            except (OSError, ValueError, KeyError, TypeError):
                print(json.dumps({"error": "local_review_unavailable"}))
    except (EOFError, KeyboardInterrupt):
        return 0
    except (OSError, ValueError):
        print(json.dumps({"error": "local_review_unavailable"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

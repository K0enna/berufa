"""Thin local supplied-role commands over the public opportunity service."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from career_os.infrastructure.opportunities import create_opportunity_service, read_private_json
from career_os.infrastructure.opportunities import evaluate_supplied_role as evaluate_supplied_role
from career_os.infrastructure.opportunities import (
    record_supplied_decision as record_supplied_decision,
)
from career_os.public_api import MAX_ROLE_REQUEST_BYTES, require_opportunity_result
from career_os.public_api import SuppliedConstraints as SuppliedConstraints
from career_os.public_api import SuppliedDecisionInput as SuppliedDecisionInput
from career_os.public_api import SuppliedRequirement as SuppliedRequirement
from career_os.public_api import SuppliedRoleRequest as SuppliedRoleRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate one lower-authority supplied posting without network or external action."
    )
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument(
        "--confirm-mapping",
        metavar="EVALUATION_SHA256",
        help="Persist only after the local user confirms the exact preview fingerprint",
    )
    args = parser.parse_args(argv)
    try:
        service = create_opportunity_service(args.instance_root)
        if not require_opportunity_result(service.has_consent()):
            print(
                "Supplied role evaluation requires an initialized evidence workspace.",
                file=sys.stderr,
            )
            return 2
        _, content = read_private_json(
            args.instance_root,
            args.request,
            label="Supplied role request",
            maximum_bytes=MAX_ROLE_REQUEST_BYTES,
        )
        result = require_opportunity_result(
            service.evaluate_supplied(
                content,
                confirm_fingerprint=args.confirm_mapping,
                actor="local_user" if args.confirm_mapping is not None else None,
            )
        )
    except (OSError, PermissionError, ValueError) as exc:
        print(f"Rolevane supplied role evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0


def decision_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record one explicit local decision without application or external action."
    )
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument(
        "--confirm-decision",
        metavar="INPUT_SHA256",
        help="Record only after the local user confirms the exact decision file SHA-256",
    )
    args = parser.parse_args(argv)
    try:
        service = create_opportunity_service(args.instance_root)
        if not require_opportunity_result(service.has_consent()):
            print(
                "Supplied role decisions require an initialized evidence workspace.",
                file=sys.stderr,
            )
            return 2
        payload, content = read_private_json(
            args.instance_root,
            args.decision,
            label="Supplied role decision",
        )
        decision = SuppliedDecisionInput.from_dict(payload)
        input_hash = hashlib.sha256(content).hexdigest()
        if args.confirm_decision != input_hash:
            print(
                "Supplied decision requires local-user confirmation of this exact file: "
                f"--confirm-decision {input_hash}",
                file=sys.stderr,
            )
            return 2
        receipt = require_opportunity_result(service.record_decision(decision, actor="local_user"))
    except (OSError, PermissionError, ValueError) as exc:
        print(f"Rolevane supplied role decision failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SuppliedConstraints",
    "SuppliedDecisionInput",
    "SuppliedRequirement",
    "SuppliedRoleRequest",
    "decision_main",
    "evaluate_supplied_role",
    "main",
    "record_supplied_decision",
]

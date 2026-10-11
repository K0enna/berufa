"""Preview or confirm one supplied local offer comparison."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from career_os.infrastructure.offers import create_offer_service, read_private_json
from career_os.public_api import (
    MAX_OFFER_REQUEST_BYTES,
    OfferComparisonRequest,
    require_offer_result,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare supplied annual offer economics locally.")
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument(
        "--confirm-comparison",
        metavar="PREVIEW_SHA256",
        help="Save only after the local user confirms this exact preview",
    )
    args = parser.parse_args(argv)
    try:
        payload, _ = read_private_json(
            args.instance_root,
            args.request,
            label="Offer request",
            maximum_bytes=MAX_OFFER_REQUEST_BYTES,
        )
        request = OfferComparisonRequest.from_dict(payload)
        service = create_offer_service(args.instance_root)
        result = require_offer_result(service.compare(request))
        output = {"comparison": result.to_dict(), "saved": None}
        if args.confirm_comparison is not None:
            receipt = require_offer_result(
                service.save_comparison(
                    request,
                    confirmation_fingerprint=args.confirm_comparison,
                    actor="local_user",
                )
            )
            output["saved"] = receipt.to_dict()
    except (ValueError, OSError):
        print(
            "Rolevane offer comparison failed: check the request, confirmation and local storage.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Preview-first console edge for one explicitly authorized official lookup."""

from __future__ import annotations

import argparse
import json

from career_os.infrastructure.greenhouse import create_official_posting_service
from career_os.public_api import OfficialSourceError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview or retrieve one exact public Greenhouse posting"
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--confirm-network", metavar="PREVIEW_SHA256")
    args = parser.parse_args(argv)
    service = create_official_posting_service()
    result = (
        service.preview(args.url)
        if args.confirm_network is None
        else service.fetch(args.url, confirmation_hash=args.confirm_network, actor="local_user")
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    if isinstance(result, OfficialSourceError):
        return 2 if result.code == "confirmation_required" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

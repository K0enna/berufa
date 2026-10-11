"""Installed read-only Focus command for real local records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from career_os.infrastructure.focus import create_focus_service
from career_os.public_api import require_focus_result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project bounded local next-action reviews without writes."
    )
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args(argv)
    try:
        result = require_focus_result(
            create_focus_service(args.instance_root).project(limit=args.limit)
        )
    except (ValueError, OSError):
        print("Rolevane Focus failed: check the limit and local input state.", file=sys.stderr)
        return 1
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

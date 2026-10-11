"""Explicit private-instance composition and immutable comparison projections."""

from __future__ import annotations

import json
from pathlib import Path

from career_os.application.offers import OfferComparison, OfferComparisonReceipt, OfferService
from career_os.file_transactions import FileConflictError, locked_compare_and_swap
from career_os.infrastructure.evidence import read_private_json as read_private_json
from career_os.instance_paths import InstancePaths


class LocalOfferRepository:
    def __init__(self, instance_root: str | Path) -> None:
        self.paths = InstancePaths.from_root(instance_root)

    def save(self, comparison: OfferComparison) -> OfferComparisonReceipt:
        payload = comparison.to_dict()
        fingerprint = payload["confirmation_fingerprint"]
        identifier = comparison.request.comparison_id
        target = self.paths.runtime / "offer-comparisons" / identifier / f"{fingerprint}.json"
        content = (
            json.dumps(payload, sort_keys=True, indent=2, allow_nan=False, ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        existed = target.exists()

        def retain(previous: bytes) -> bytes:
            if (existed or previous) and previous != content:
                raise ValueError("Retained comparison differs; explicit recovery is required")
            return content

        try:
            locked_compare_and_swap(
                target, retain, expected_exists=existed, maximum_bytes=128 * 1024
            )
        except FileConflictError as exc:
            raise OSError(
                "Another writer changed this comparison; retry the confirmed request"
            ) from exc
        return OfferComparisonReceipt(
            identifier, fingerprint, target.relative_to(self.paths.root).as_posix()
        )


def create_offer_service(instance_root: str | Path) -> OfferService:
    return OfferService(LocalOfferRepository(instance_root))

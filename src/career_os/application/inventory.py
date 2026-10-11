"""Bounded read inventories shared by evidence and supplied opportunities."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Generic, TypeVar

T = TypeVar("T")
MAX_INVENTORY_PAGE = 100


@dataclass(frozen=True, slots=True)
class InventoryEntry(Generic[T]):
    identity: str
    value: T | None
    status: str = "ready"


@dataclass(frozen=True, slots=True)
class InventoryPage(Generic[T]):
    entries: tuple[InventoryEntry[T], ...]
    next_cursor: str | None
    snapshot: str
    total: int
    partial: bool


def inventory_page(
    entries: tuple[InventoryEntry[T], ...], *, scope: str, limit: int, cursor: str | None
) -> InventoryPage[T]:
    """A cursor binds this content-minimal projection and its selected instance."""
    if type(limit) is not int or not 1 <= limit <= MAX_INVENTORY_PAGE:
        raise ValueError("Inventory limit must be 1-100")
    snapshot = hashlib.sha256(
        json.dumps([scope, [asdict(entry) for entry in entries]], sort_keys=True).encode("utf-8")
    ).hexdigest()
    offset = 0
    if cursor is not None:
        if not isinstance(cursor, str) or not re.fullmatch(r"[0-9a-f]{64}:[0-9]{1,6}", cursor):
            raise ValueError("Invalid inventory cursor")
        digest, position = cursor.split(":")
        offset = int(position)
        if digest != snapshot or not 0 < offset < len(entries):
            raise ValueError("Inventory changed; restart the listing")
    end = min(offset + limit, len(entries))
    return InventoryPage(
        entries[offset:end],
        f"{snapshot}:{end}" if end < len(entries) else None,
        snapshot,
        len(entries),
        any(entry.status != "ready" for entry in entries),
    )

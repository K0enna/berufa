"""Opportunity identity syntax shared by events and private pipeline adapters."""

import re

OPPORTUNITY_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,99}")


def validate_opportunity_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Opportunity ID must be a string")
    normalized = value.strip()
    if not OPPORTUNITY_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Opportunity ID must be 3-100 characters using letters, numbers, '.', '_', ':', or '-'"
        )
    return normalized

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _normalize_bullet_spacing(value: str) -> str:
    """Replace supported bullets and adjacent whitespace in one linear pass."""

    output: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character not in "•▪●":
            output.append(character)
            index += 1
            continue
        while output and output[-1].isspace():
            output.pop()
        output.append("\n")
        index += 1
        while index < len(value) and value[index].isspace():
            index += 1
    return "".join(output)


def clean_description(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _normalize_bullet_spacing(normalized)
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line)


@dataclass(slots=True)
class Compensation:
    currency: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    interval: str | None = None
    raw: str | None = None

    def __post_init__(self) -> None:
        def amount(value: Any, name: str) -> float | None:
            if value is None or value == "":
                return None
            if isinstance(value, bool):
                raise ValueError(f"Compensation {name} must be numeric")
            try:
                normalized = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Compensation {name} must be numeric") from exc
            if not math.isfinite(normalized) or normalized < 0 or normalized > 1_000_000_000:
                raise ValueError(
                    f"Compensation {name} must be finite and between 0 and 1,000,000,000"
                )
            return normalized

        self.minimum = amount(self.minimum, "minimum")
        self.maximum = amount(self.maximum, "maximum")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Compensation minimum cannot exceed maximum")
        currency = clean_text(self.currency).upper()
        if currency and not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("Compensation currency must be a three-letter code")
        self.currency = currency or None
        self.interval = clean_text(self.interval) or None
        self.raw = clean_text(self.raw) or None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class JobPosting:
    provider: str
    source_id: str
    external_id: str
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    apply_url: str | None = None
    department: str | None = None
    employment_type: str | None = None
    workplace_type: str | None = None
    is_remote: bool | None = None
    posted_at: str | None = None
    updated_at: str | None = None
    compensation: Compensation = field(default_factory=Compensation)
    raw: dict[str, Any] = field(default_factory=dict)
    observed_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        self.provider = clean_text(self.provider).lower()
        self.source_id = clean_text(self.source_id)
        self.external_id = clean_text(str(self.external_id))
        self.company = clean_text(self.company)
        self.title = clean_text(self.title)
        self.url = clean_text(self.url)
        self.location = clean_text(self.location)
        self.description = clean_description(self.description)

    @property
    def job_key(self) -> str:
        return f"{self.provider}:{self.source_id}:{self.external_id}"

    @property
    def content_hash(self) -> str:
        stable = {
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "apply_url": clean_text(self.apply_url),
            "location": self.location,
            "description": self.description,
            "department": clean_text(self.department),
            "workplace_type": self.workplace_type,
            "employment_type": self.employment_type,
            "is_remote": self.is_remote,
            "posted_at": self.posted_at,
            "compensation": self.compensation.to_dict(),
        }
        payload = json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return sha256(payload).hexdigest()

    @property
    def match_fingerprint(self) -> str:
        """Identify likely cross-listings without using provider identity or URLs."""

        stable = {
            "company": self.company.casefold(),
            "title": self.title.casefold(),
            "location": self.location.casefold(),
            "description": self.description,
            "department": clean_text(self.department).casefold(),
            "employment_type": clean_text(self.employment_type).casefold(),
        }
        payload = json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return sha256(payload).hexdigest()

    def to_dict(self, *, include_raw: bool = True) -> dict[str, Any]:
        result = asdict(self)
        result["job_key"] = self.job_key
        result["content_hash"] = self.content_hash
        if not include_raw:
            result.pop("raw", None)
        return result


@dataclass(slots=True)
class ScoreResult:
    job_key: str
    total: float
    confidence: float
    recommendation: str
    dimensions: dict[str, float]
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    hard_gates: list[str] = field(default_factory=list)
    scorecard_version: str = "2.0"
    job_content_hash: str | None = None
    assessment_stage: str = "machine_triage"
    scorecard: dict[str, float] = field(default_factory=dict)
    progression: dict[str, Any] = field(default_factory=dict)
    gate_results: list[dict[str, Any]] = field(default_factory=list)
    requirement_evidence: list[dict[str, Any]] = field(default_factory=list)
    rule_trace: list[dict[str, Any]] = field(default_factory=list)
    search_track: str = "vendor_leadership"
    market_lane: str = "core_endpoint_itops"
    lead_kind: str = "official_posting"
    scorecard_kind: str = "vendor_post_sales"
    scored_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

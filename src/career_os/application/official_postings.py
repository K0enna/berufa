"""Typed exact-posting acquisition; no workspace, evidence or career-event effects."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class OfficialPostingTarget:
    provider: str
    tenant: str
    external_id: str
    canonical_url: str
    endpoint: str

    @property
    def confirmation_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            **asdict(self),
            "confirmation_hash": self.confirmation_hash,
            "requires_network_confirmation": True,
            "official_source_verified": False,
            "network_requests": 0,
            "candidate_data_sent": False,
            "filesystem_changed": False,
            "career_event_created": False,
        }


@dataclass(frozen=True, slots=True)
class OfficialPostingSnapshot:
    target: OfficialPostingTarget
    company: str
    title: str
    location: str
    description: str
    source_hash: str
    content_hash: str
    retrieved_at: str
    updated_at: str | None
    application_deadline: str | None
    liveness: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            **asdict(self),
            "authority": "official_api_at_retrieval",
            "official_source_verified": True,
            "network_requests": 1,
            "candidate_data_sent": False,
            "filesystem_changed": False,
            "career_event_created": False,
            "application_ready": False,
            "requires_evidence_mapping_and_decision": True,
        }


@dataclass(frozen=True, slots=True)
class OfficialSourceError:
    code: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 1, "error": asdict(self), "official_source_verified": False}


class OfficialSourceFailure(ValueError):
    """Expected adapter failure with no raw response, credentials or private context."""


class OfficialPostingAdapter(Protocol):
    def target(self, url: str) -> OfficialPostingTarget: ...

    def fetch(self, target: OfficialPostingTarget) -> OfficialPostingSnapshot: ...


class OfficialPostingService:
    def __init__(self, adapter: OfficialPostingAdapter) -> None:
        self._adapter = adapter

    def preview(self, url: str) -> OfficialPostingTarget | OfficialSourceError:
        try:
            return self._adapter.target(url)
        except OfficialSourceFailure:
            return OfficialSourceError(
                "unsupported_url", "Provide one supported exact public job URL"
            )

    def fetch(
        self, url: str, *, confirmation_hash: str, actor: str
    ) -> OfficialPostingSnapshot | OfficialSourceError:
        target = self.preview(url)
        if isinstance(target, OfficialSourceError):
            return target
        if actor != "local_user" or confirmation_hash != target.confirmation_hash:
            return OfficialSourceError(
                "confirmation_required",
                "Confirm the exact preview before requesting the official source",
            )
        try:
            return self._adapter.fetch(target)
        except OfficialSourceFailure:
            return OfficialSourceError(
                "source_unavailable",
                "The exact official posting could not be verified; no career state changed",
            )

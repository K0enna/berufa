"""Greenhouse normalization shared by private collection and exact public lookup."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from career_os.application.official_postings import (
    OfficialPostingService,
    OfficialPostingSnapshot,
    OfficialPostingTarget,
    OfficialSourceFailure,
)
from career_os.ats_urls import AtsBoardIdentity, canonical_board_url, parse_supported_job_url
from career_os.infrastructure.provider_http import (
    MAX_PUBLIC_RESPONSE_BYTES,
    ProviderError,
    SourceConfig,
    first_text,
    public_json_get,
    shadow_request_attempts,
    strip_html,
    validate_http_url,
)
from career_os.models import JobPosting


class GreenhouseProvider:
    name = "greenhouse"

    def __init__(self, fetch_json: Callable[..., Any]) -> None:
        self._fetch_json = fetch_json

    @staticmethod
    def endpoint(source: SourceConfig) -> str:
        if not source.slug:
            raise ProviderError(f"greenhouse source {source.id!r} requires a slug")
        return f"https://boards-api.greenhouse.io/v1/boards/{quote(source.slug, safe='-_~')}/jobs?content=true"

    def fetch(self, source: SourceConfig) -> list[JobPosting]:
        default_endpoint = self.endpoint(source)
        endpoint = source.endpoint or default_endpoint
        request_options: dict[str, Any] = {"allowed_hosts": {"boards-api.greenhouse.io"}}
        if (attempts := shadow_request_attempts(source)) is not None:
            request_options["attempts"] = attempts
        payload = self._fetch_json(endpoint, **request_options)
        if not isinstance(payload, dict) or "jobs" not in payload:
            raise ProviderError("Greenhouse response is missing expected field 'jobs'")
        jobs = payload["jobs"]
        if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
            raise ProviderError("Greenhouse response contains a malformed jobs collection")
        return [
            self.normalize(source, job, allow_custom_url=endpoint == default_endpoint)
            for job in jobs
        ]

    @staticmethod
    def normalize(
        source: SourceConfig, job: dict[str, Any], *, allow_custom_url: bool
    ) -> JobPosting:
        external_id = str(job.get("id", ""))
        title = first_text(job.get("title"))
        absolute_url = first_text(job.get("absolute_url"))
        if not external_id or not title or not absolute_url:
            raise ProviderError("Greenhouse response contains one or more malformed jobs")
        slug = quote(source.slug or "", safe="-_~").casefold()
        try:
            identity = parse_supported_job_url(absolute_url)
        except ProviderError as exc:
            validate_http_url(absolute_url)
            parsed = urlparse(absolute_url)
            host = (parsed.hostname or "").casefold().rstrip(".")
            job_ids = parse_qs(parsed.query, keep_blank_values=True).get("gh_jid", [])
            if (
                not allow_custom_url
                or host == "greenhouse.io"
                or host.endswith(".greenhouse.io")
                or not job_ids
                or any(value != external_id for value in job_ids)
            ):
                raise ProviderError(
                    "Greenhouse job URL does not match its tenant or posting id"
                ) from exc
            identity = parse_supported_job_url(
                f"{canonical_board_url(AtsBoardIdentity('greenhouse', slug))}/jobs/{external_id}"
            )
        if (
            identity.provider != "greenhouse"
            or identity.slug.casefold() != slug
            or identity.external_id != external_id
        ):
            raise ProviderError("Greenhouse job URL does not match its tenant or posting id")
        location = {} if job.get("location") is None else job["location"]
        departments = [] if job.get("departments") is None else job["departments"]
        if (
            not isinstance(location, dict)
            or not isinstance(departments, list)
            or any(not isinstance(item, dict) for item in departments)
        ):
            raise ProviderError("Greenhouse response contains malformed location or departments")
        return JobPosting(
            provider="greenhouse",
            source_id=source.id,
            external_id=external_id,
            company=source.company,
            title=title,
            location=first_text(location.get("name")),
            description=strip_html(job.get("content")),
            url=identity.canonical_url,
            posted_at=job.get("first_published"),
            updated_at=job.get("updated_at"),
            department=first_text(*[item.get("name") for item in departments]),
            raw=job,
        )


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Invalid posting timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Posting timestamp requires a timezone")
    return parsed.astimezone(UTC)


class GreenhousePostingAdapter:
    def __init__(
        self,
        fetch_text: Callable[..., str] = public_json_get,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._fetch_text = fetch_text
        self._now = now

    def target(self, url: str) -> OfficialPostingTarget:
        try:
            validate_http_url(url, allow_fragment=False)
            parsed = urlparse(url)
            if (
                parsed.query
                or parsed.params
                or parsed.netloc not in {"boards.greenhouse.io", "job-boards.greenhouse.io"}
            ):
                raise ProviderError("Unsupported exact Greenhouse URL")
            if not re.fullmatch(
                r"/[A-Za-z0-9][A-Za-z0-9_-]{0,255}/jobs/[1-9][0-9]{0,19}/?", parsed.path
            ):
                raise ProviderError("Unsupported exact Greenhouse URL")
            identity = parse_supported_job_url(url)
            if identity.slug.casefold() == "internal":
                raise ProviderError("Internal job boards are unsupported")
            return OfficialPostingTarget(
                provider="greenhouse",
                tenant=identity.slug,
                external_id=identity.external_id,
                canonical_url=identity.canonical_url,
                endpoint=f"https://boards-api.greenhouse.io/v1/boards/{identity.slug}/jobs/{identity.external_id}",
            )
        except (ProviderError, ValueError) as exc:
            raise OfficialSourceFailure("Unsupported exact Greenhouse URL") from exc

    def fetch(self, target: OfficialPostingTarget) -> OfficialPostingSnapshot:
        try:
            if target != self.target(target.canonical_url):
                raise ValueError("Source target does not match its canonical URL")
            raw = self._fetch_text(target.endpoint, allowed_host="boards-api.greenhouse.io")
            if not 1 <= len(raw.encode("utf-8")) <= MAX_PUBLIC_RESPONSE_BYTES:
                raise ValueError("Public source response exceeds its limit")
            payload = json.loads(raw)
            if (
                not isinstance(payload, dict)
                or type(payload.get("id")) is not int
                or str(payload["id"]) != target.external_id
            ):
                raise ValueError("Posting identity mismatch")
            if type(payload.get("internal_job_id")) is not int or payload["internal_job_id"] < 1:
                raise ValueError("Prospect posts do not establish a current role")
            # The exact job-detail endpoint, unlike the jobs list, supplies company_name.
            for field in ("company_name", "title", "content", "absolute_url"):
                value = payload.get(field)
                if not isinstance(value, str) or not value.strip() or "\x00" in value:
                    raise ValueError("Posting text is missing or malformed")
            source = SourceConfig(
                id=f"greenhouse-{target.tenant}",
                provider="greenhouse",
                company=payload["company_name"],
                slug=target.tenant,
            )
            posting = GreenhouseProvider.normalize(source, payload, allow_custom_url=True)
            if (
                not posting.description
                or len(posting.description) > 80_000
                or len(posting.title) > 500
                or len(posting.company) > 500
            ):
                raise ValueError("Posting text exceeds the supported contract")
            now = self._now().astimezone(UTC)
            updated = _timestamp(payload.get("updated_at"))
            deadline = _timestamp(payload.get("application_deadline"))
            return OfficialPostingSnapshot(
                target=target,
                company=posting.company,
                title=posting.title,
                location=posting.location,
                description=posting.description,
                source_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                content_hash=hashlib.sha256(posting.description.encode("utf-8")).hexdigest(),
                retrieved_at=now.isoformat(),
                updated_at=updated.isoformat() if updated else None,
                application_deadline=deadline.isoformat() if deadline else None,
                liveness="inactive"
                if deadline is not None and deadline <= now
                else "live_at_retrieval",
            )
        except (
            ProviderError,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            RecursionError,
            UnicodeError,
        ) as exc:
            raise OfficialSourceFailure("The exact official posting could not be verified") from exc


def create_official_posting_service() -> OfficialPostingService:
    return OfficialPostingService(GreenhousePostingAdapter())

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from career_os.infrastructure.provider_http import ProviderError, validate_http_url

SUPPORTED_ATS_PROVIDERS = {
    "ashby",
    "greenhouse",
    "jobvite",
    "jobylon",
    "lever",
    "personio",
    "pinpoint",
    "recruitee",
    "smartrecruiters",
    "teamtailor",
    "workable",
    "workday",
}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")
_WORKDAY_HOST = re.compile(
    r"^(?P<tenant>[a-z0-9][a-z0-9_-]{0,255})\."
    r"(?P<instance>wd[0-9]{1,4})\.myworkdayjobs\.com$"
)
_LOCALE = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.IGNORECASE)
_CAREER_HOST = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?"
)
_GREENHOUSE_BOARD_HOSTS = frozenset(
    {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}
)


@dataclass(frozen=True, slots=True)
class AtsBoardIdentity:
    provider: str
    slug: str
    region: str | None = None
    domain: str | None = None
    instance: str | None = None
    site: str | None = None


@dataclass(frozen=True, slots=True)
class AtsJobIdentity:
    provider: str
    slug: str
    external_id: str
    canonical_url: str
    region: str | None = None
    domain: str | None = None
    instance: str | None = None
    site: str | None = None
    job_path: str | None = None


def board_identity_from_job(identity: AtsJobIdentity) -> AtsBoardIdentity:
    return AtsBoardIdentity(
        provider=identity.provider,
        slug=identity.slug,
        region=identity.region,
        domain=identity.domain,
        instance=identity.instance,
        site=identity.site,
    )


def _safe_component(value: str, label: str) -> str:
    if not _SAFE_COMPONENT.fullmatch(value):
        raise ProviderError(f"Unsupported {label} in ATS URL")
    return value


def _parts(url: str) -> tuple[str, list[str]]:
    # Parsed posting links may carry a fragment for browser navigation; the
    # canonical identity below deliberately removes it before persistence.
    validate_http_url(url, allow_fragment=True)
    parsed = urlparse(url)
    if "%" in parsed.path or "\\" in parsed.path:
        raise ProviderError("Encoded or backslash ATS URL paths are not supported")
    host = (parsed.hostname or "").casefold().rstrip(".")
    parts = [part for part in parsed.path.split("/") if part]
    return host, parts


def normalize_lever_region(value: str | None) -> str:
    region = (value or "global").casefold()
    return "global" if region in {"global", "us"} else region


def normalize_teamtailor_domain(slug: str, value: str | None) -> str:
    tenant = _safe_component(slug, "Teamtailor tenant")
    host = (value or f"{tenant}.teamtailor.com").casefold().rstrip(".")
    validate_http_url(f"https://{host}")
    parsed = urlparse(f"https://{host}")
    if (
        parsed.hostname != host
        or "." not in host
        or not re.fullmatch(
            r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
            r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?",
            host,
        )
    ):
        raise ProviderError("Unsupported Teamtailor career-site domain")
    native_suffix = ".teamtailor.com"
    if host.endswith(native_suffix):
        native_tenant = host.removesuffix(native_suffix)
        if "." in native_tenant or native_tenant != tenant.casefold():
            raise ProviderError("Teamtailor native domain tenant does not match source tenant")
    return host


def normalize_recruitee_domain(slug: str, value: str | None) -> str:
    tenant = _safe_component(slug, "Recruitee tenant")
    host = (value or f"{tenant}.recruitee.com").casefold().rstrip(".")
    validate_http_url(f"https://{host}")
    parsed = urlparse(f"https://{host}")
    if (
        parsed.hostname != host
        or "." not in host
        or not re.fullmatch(
            r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
            r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?",
            host,
        )
    ):
        raise ProviderError("Unsupported Recruitee career-site domain")
    native_suffix = ".recruitee.com"
    if host.endswith(native_suffix):
        native_tenant = host.removesuffix(native_suffix)
        if "." in native_tenant or native_tenant != tenant.casefold():
            raise ProviderError("Recruitee native domain tenant does not match source tenant")
    return host


def normalize_workday_domain(value: str | None) -> str | None:
    if value is None:
        return None
    host = value.casefold().rstrip(".")
    validate_http_url(f"https://{host}")
    parsed = urlparse(f"https://{host}")
    if parsed.hostname != host or not _CAREER_HOST.fullmatch(host):
        raise ProviderError("Unsupported Workday career-site domain")
    return host


def canonical_board_url(identity: AtsBoardIdentity) -> str:
    slug = _safe_component(identity.slug, "ATS tenant")
    if identity.provider == "ashby":
        return f"https://jobs.ashbyhq.com/{slug}"
    if identity.provider == "greenhouse":
        return f"https://job-boards.greenhouse.io/{slug}"
    if identity.provider == "jobvite":
        return f"https://jobs.jobvite.com/{slug}/jobs"
    if identity.provider == "jobylon":
        company_id = identity.instance or ""
        if not company_id.isdecimal() or len(company_id) > 20:
            raise ProviderError("Unsupported Jobylon company id")
        return f"https://emp.jobylon.com/companies/{company_id}-{slug}/"
    if identity.provider == "lever":
        region = normalize_lever_region(identity.region)
        if region not in {"eu", "global"}:
            raise ProviderError(f"Unsupported Lever region: {region}")
        host = "jobs.eu.lever.co" if region == "eu" else "jobs.lever.co"
        return f"https://{host}/{slug}"
    if identity.provider == "personio":
        domain = (identity.domain or "de").casefold()
        if domain not in {"com", "de"}:
            raise ProviderError(f"Unsupported Personio domain: {domain}")
        return f"https://{slug}.jobs.personio.{domain}"
    if identity.provider == "pinpoint":
        return f"https://{slug}.pinpointhq.com"
    if identity.provider == "recruitee":
        return f"https://{normalize_recruitee_domain(slug, identity.domain)}"
    if identity.provider == "smartrecruiters":
        return f"https://careers.smartrecruiters.com/{slug}"
    if identity.provider == "teamtailor":
        host = normalize_teamtailor_domain(slug, identity.domain)
        return f"https://{host}/jobs"
    if identity.provider == "workable":
        return f"https://apply.workable.com/{slug}/"
    if identity.provider == "workday":
        instance = (identity.instance or "").casefold()
        if not re.fullmatch(r"wd[0-9]{1,4}", instance):
            raise ProviderError("Unsupported Workday instance")
        site = _safe_component(identity.site or "", "Workday site")
        host = normalize_workday_domain(identity.domain) or (f"{slug}.{instance}.myworkdayjobs.com")
        return f"https://{host}/{site}"
    raise ProviderError(f"Unsupported ATS provider: {identity.provider}")


def parse_supported_board_url(url: str) -> AtsBoardIdentity:
    host, parts = _parts(url)
    if host == "jobs.ashbyhq.com" and len(parts) == 1:
        return AtsBoardIdentity("ashby", _safe_component(parts[0], "Ashby tenant"))
    if host in _GREENHOUSE_BOARD_HOSTS and len(parts) == 1:
        return AtsBoardIdentity("greenhouse", _safe_component(parts[0], "Greenhouse tenant"))
    if host == "jobs.jobvite.com" and len(parts) == 2 and parts[1] == "jobs":
        return AtsBoardIdentity("jobvite", _safe_component(parts[0], "Jobvite tenant"))
    if host == "emp.jobylon.com" and len(parts) == 2 and parts[0] == "companies":
        match = re.fullmatch(
            r"(?P<company_id>[0-9]{1,20})-(?P<slug>[a-z0-9][a-z0-9_-]{0,255})",
            parts[1],
            re.IGNORECASE,
        )
        if match:
            return AtsBoardIdentity(
                "jobylon",
                _safe_component(match.group("slug"), "Jobylon company slug"),
                instance=match.group("company_id"),
            )
    if host in {"jobs.lever.co", "jobs.eu.lever.co"} and len(parts) == 1:
        region = "eu" if host == "jobs.eu.lever.co" else None
        return AtsBoardIdentity("lever", _safe_component(parts[0], "Lever tenant"), region)
    for domain in ("de", "com"):
        suffix = f".jobs.personio.{domain}"
        if host.endswith(suffix) and host != suffix.removeprefix(".") and not parts:
            slug = host.removesuffix(suffix)
            return AtsBoardIdentity(
                "personio", _safe_component(slug, "Personio tenant"), domain=domain
            )
    if host.endswith(".recruitee.com") and not parts:
        slug = host.removesuffix(".recruitee.com")
        if "." not in slug:
            return AtsBoardIdentity("recruitee", _safe_component(slug, "Recruitee tenant"))
    if host.endswith(".pinpointhq.com") and (not parts or parts == ["jobs"]):
        slug = host.removesuffix(".pinpointhq.com")
        if "." not in slug:
            return AtsBoardIdentity("pinpoint", _safe_component(slug, "Pinpoint tenant"))
    if host in {"careers.smartrecruiters.com", "jobs.smartrecruiters.com"} and len(parts) == 1:
        return AtsBoardIdentity(
            "smartrecruiters",
            _safe_component(parts[0], "SmartRecruiters company identifier"),
        )
    if host.endswith(".teamtailor.com") and len(parts) == 1 and parts[0] == "jobs":
        slug = host.removesuffix(".teamtailor.com")
        if "." not in slug:
            return AtsBoardIdentity(
                "teamtailor",
                _safe_component(slug, "Teamtailor tenant"),
                domain=host,
            )
    if host == "apply.workable.com" and (
        len(parts) == 1 or (len(parts) == 2 and parts[1] == "jobs")
    ):
        return AtsBoardIdentity("workable", _safe_component(parts[0], "Workable account slug"))
    if match := _WORKDAY_HOST.fullmatch(host):
        if len(parts) == 2 and _LOCALE.fullmatch(parts[0]):
            parts = parts[1:]
        if len(parts) == 1:
            return AtsBoardIdentity(
                "workday",
                _safe_component(match.group("tenant"), "Workday tenant"),
                instance=match.group("instance"),
                site=_safe_component(parts[0], "Workday site"),
            )
    raise ProviderError("URL is not a supported ATS board root")


def parse_supported_job_url(url: str) -> AtsJobIdentity:
    host, parts = _parts(url)
    board: AtsBoardIdentity
    external_id: str
    teamtailor_job_path: str | None = None
    pinpoint_job_path: str | None = None
    if host == "jobs.ashbyhq.com" and (
        len(parts) == 2 or (len(parts) == 3 and parts[2] in {"apply", "application"})
    ):
        board = AtsBoardIdentity("ashby", _safe_component(parts[0], "Ashby tenant"))
        external_id = _safe_component(parts[1], "Ashby job id")
    elif host in _GREENHOUSE_BOARD_HOSTS and len(parts) == 3 and parts[1] == "jobs":
        board = AtsBoardIdentity("greenhouse", _safe_component(parts[0], "Greenhouse tenant"))
        external_id = _safe_component(parts[2], "Greenhouse job id")
    elif host == "jobs.jobvite.com" and (
        len(parts) == 3 or (len(parts) == 4 and parts[3] == "apply")
    ):
        raw_path = urlparse(url).path
        if "//" in raw_path or raw_path.endswith("/"):
            raise ProviderError("Jobvite job URLs must use an exact, non-empty path")
        if parts[1] != "job":
            raise ProviderError("URL is not a supported individual ATS job URL")
        board = AtsBoardIdentity(
            "jobvite",
            _safe_component(parts[0], "Jobvite tenant"),
        )
        external_id = _safe_component(parts[2], "Jobvite job id")
    elif host == "emp.jobylon.com" and len(parts) == 2 and parts[0] == "jobs":
        raw_path = urlparse(url).path
        if "//" in raw_path or not raw_path.endswith("/"):
            raise ProviderError("Jobylon job URLs must use an exact canonical path")
        match = re.fullmatch(
            r"(?P<job_id>[0-9]{1,20})-(?P<company_slug>[a-z0-9_]+)-"
            r"(?P<role_slug>[a-z0-9][a-z0-9_-]{0,511})",
            parts[1],
            re.IGNORECASE,
        )
        if match is None:
            raise ProviderError("URL is not a supported individual ATS job URL")
        board = AtsBoardIdentity(
            "jobylon",
            _safe_component(match.group("company_slug"), "Jobylon company slug"),
        )
        external_id = match.group("job_id")
        jobylon_job_path = f"/jobs/{parts[1]}/"
    elif host in {"jobs.lever.co", "jobs.eu.lever.co"} and (
        len(parts) == 2 or (len(parts) == 3 and parts[2] in {"apply", "application"})
    ):
        region = "eu" if host == "jobs.eu.lever.co" else None
        board = AtsBoardIdentity("lever", _safe_component(parts[0], "Lever tenant"), region)
        external_id = _safe_component(parts[1], "Lever job id")
    elif host == "jobs.smartrecruiters.com" and (
        len(parts) == 2 or (len(parts) == 3 and parts[2] == "apply")
    ):
        raw_path = urlparse(url).path
        if "//" in raw_path or raw_path.endswith("/"):
            raise ProviderError("SmartRecruiters job URLs must use an exact, non-empty path")
        posting_segment = _safe_component(parts[1], "SmartRecruiters posting path")
        match = re.match(r"^(?P<posting_id>[0-9]{1,32})(?:-|$)", posting_segment)
        if match is None:
            raise ProviderError("URL is not a supported individual ATS job URL")
        board = AtsBoardIdentity(
            "smartrecruiters",
            _safe_component(parts[0], "SmartRecruiters company identifier"),
        )
        external_id = match.group("posting_id")
    elif host.endswith(".teamtailor.com"):
        slug = host.removesuffix(".teamtailor.com")
        if "." in slug:
            raise ProviderError("URL is not a supported individual ATS job URL")
        return parse_configured_teamtailor_job_url(
            url,
            slug=_safe_component(slug, "Teamtailor tenant"),
            domain=host,
        )
    elif host.endswith(".recruitee.com"):
        slug = host.removesuffix(".recruitee.com")
        if "." in slug:
            raise ProviderError("URL is not a supported individual ATS job URL")
        return parse_configured_recruitee_job_url(
            url,
            slug=_safe_component(slug, "Recruitee tenant"),
            domain=host,
        )
    elif host.endswith(".pinpointhq.com"):
        slug = host.removesuffix(".pinpointhq.com")
        if "." in slug:
            raise ProviderError("URL is not a supported individual ATS job URL")
        if len(parts) == 2 and parts[0] == "postings":
            posting_id = parts[1]
            pinpoint_job_path = f"/postings/{posting_id}"
        elif len(parts) == 3 and len(parts[0]) == 2 and parts[1] == "postings":
            posting_id = parts[2]
            pinpoint_job_path = f"/{parts[0].casefold()}/postings/{posting_id}"
        else:
            raise ProviderError("URL is not a supported individual ATS job URL")
        board = AtsBoardIdentity("pinpoint", _safe_component(slug, "Pinpoint tenant"))
        external_id = _safe_component(posting_id, "Pinpoint posting id")
    elif host == "apply.workable.com" and (
        len(parts) == 3 or (len(parts) == 4 and parts[3] == "apply")
    ):
        raw_path = urlparse(url).path
        if "//" in raw_path or raw_path.endswith("/") or parts[1] != "j":
            raise ProviderError("Workable job URLs must use an exact, non-empty path")
        board = AtsBoardIdentity("workable", _safe_component(parts[0], "Workable account slug"))
        external_id = _safe_component(parts[2], "Workable job shortcode")
    elif match := _WORKDAY_HOST.fullmatch(host):
        raw_path = urlparse(url).path
        if "//" in raw_path or raw_path.endswith("/"):
            raise ProviderError("Workday job URLs must use an exact, non-empty path")
        if parts and _LOCALE.fullmatch(parts[0]):
            parts = parts[1:]
        if len(parts) not in {3, 4}:
            raise ProviderError("URL is not a supported individual ATS job URL")
        site, marker, *job_components = parts
        if marker != "job":
            raise ProviderError("URL is not a supported individual ATS job URL")
        board = AtsBoardIdentity(
            "workday",
            _safe_component(match.group("tenant"), "Workday tenant"),
            instance=match.group("instance"),
            site=_safe_component(site, "Workday site"),
        )
        job_components = [_safe_component(item, "Workday job path") for item in job_components]
        external_id = job_components[-1]
        workday_job_path = f"/job/{'/'.join(job_components)}"
    else:
        domain = next(
            (
                value
                for value in ("de", "com")
                if host.endswith(f".jobs.personio.{value}") and host != f"jobs.personio.{value}"
            ),
            None,
        )
        if domain is None or len(parts) != 2 or parts[0] != "job":
            raise ProviderError("URL is not a supported individual ATS job URL")
        suffix = f".jobs.personio.{domain}"
        board = AtsBoardIdentity(
            "personio",
            _safe_component(host.removesuffix(suffix), "Personio tenant"),
            domain=domain,
        )
        external_id = _safe_component(parts[1], "Personio job id")
    board_url = canonical_board_url(board) if board.provider != "jobylon" else ""
    if board.provider == "jobylon":
        canonical_url = f"https://emp.jobylon.com{jobylon_job_path}"
    elif board.provider == "greenhouse":
        canonical_url = f"{board_url}/jobs/{external_id}"
    elif board.provider == "jobvite":
        canonical_url = f"https://jobs.jobvite.com/{board.slug}/job/{external_id}"
    elif board.provider == "personio":
        canonical_url = f"{board_url}/job/{external_id}"
    elif board.provider == "pinpoint":
        canonical_url = f"{board_url}{pinpoint_job_path}"
    elif board.provider == "smartrecruiters":
        canonical_url = f"https://jobs.smartrecruiters.com/{board.slug}/{external_id}"
    elif board.provider == "teamtailor":
        canonical_url = f"{board_url}{teamtailor_job_path}"
    elif board.provider == "workable":
        canonical_url = f"https://apply.workable.com/{board.slug}/j/{external_id}"
    elif board.provider == "workday":
        canonical_url = f"{board_url}{workday_job_path}"
    else:
        canonical_url = f"{board_url}/{external_id}"
    return AtsJobIdentity(
        provider=board.provider,
        slug=board.slug,
        external_id=external_id,
        canonical_url=canonical_url,
        region=board.region,
        domain=board.domain,
        instance=board.instance,
        site=board.site,
        job_path=(
            workday_job_path
            if board.provider == "workday"
            else teamtailor_job_path
            if board.provider == "teamtailor"
            else jobylon_job_path
            if board.provider == "jobylon"
            else pinpoint_job_path
            if board.provider == "pinpoint"
            else None
        ),
    )


def parse_configured_teamtailor_board_url(
    url: str,
    *,
    slug: str,
    domain: str | None,
) -> AtsBoardIdentity:
    expected_host = normalize_teamtailor_domain(slug, domain)
    host, parts = _parts(url)
    if host != expected_host or parts != ["jobs"]:
        raise ProviderError("URL does not match the configured Teamtailor board identity")
    return AtsBoardIdentity("teamtailor", slug, domain=expected_host)


def parse_configured_teamtailor_job_url(
    url: str,
    *,
    slug: str,
    domain: str | None,
) -> AtsJobIdentity:
    board = parse_configured_teamtailor_board_url(
        f"https://{normalize_teamtailor_domain(slug, domain)}/jobs",
        slug=slug,
        domain=domain,
    )
    host, parts = _parts(url)
    raw_path = urlparse(url).path
    if host != board.domain or "//" in raw_path or raw_path.endswith("/"):
        raise ProviderError("Teamtailor job URLs must use the configured exact domain and path")
    if len(parts) == 4 and parts[2:] == ["applications", "new"]:
        parts = parts[:2]
    if len(parts) != 2 or parts[0] != "jobs":
        raise ProviderError("URL is not a supported individual ATS job URL")
    match = re.fullmatch(r"(?P<job_id>[0-9]{1,20})(?:-[A-Za-z0-9_-]+)?", parts[1])
    if match is None:
        raise ProviderError("URL is not a supported individual ATS job URL")
    job_path = f"/jobs/{parts[1]}"
    canonical_url = f"https://{board.domain}{job_path}"
    return AtsJobIdentity(
        provider="teamtailor",
        slug=board.slug,
        external_id=match.group("job_id"),
        canonical_url=canonical_url,
        domain=board.domain,
        job_path=job_path,
    )


def parse_configured_recruitee_job_url(
    url: str,
    *,
    slug: str,
    domain: str | None,
) -> AtsJobIdentity:
    tenant = _safe_component(slug, "Recruitee tenant")
    expected_host = normalize_recruitee_domain(tenant, domain)
    host, parts = _parts(url)
    raw_path = urlparse(url).path
    if host != expected_host or "//" in raw_path or raw_path.endswith("/"):
        raise ProviderError("Recruitee job URLs must use the configured exact domain and path")
    if len(parts) == 4 and parts[2:] == ["c", "new"]:
        parts = parts[:2]
    if len(parts) != 2 or parts[0] != "o":
        raise ProviderError("URL is not a supported individual ATS job URL")
    external_id = _safe_component(parts[1], "Recruitee job slug")
    canonical_url = f"https://{expected_host}/o/{external_id}"
    return AtsJobIdentity(
        provider="recruitee",
        slug=tenant,
        external_id=external_id,
        canonical_url=canonical_url,
        domain=expected_host,
        job_path=f"/o/{external_id}",
    )


def parse_configured_workday_job_url(
    url: str,
    *,
    slug: str,
    domain: str | None,
    instance: str,
    site: str,
) -> AtsJobIdentity:
    """Parse one Workday job URL only for an explicitly configured public domain."""

    tenant = _safe_component(slug, "Workday tenant")
    expected_host = normalize_workday_domain(domain)
    if expected_host is None:
        raise ProviderError("Configured Workday job URLs require an explicit public domain")
    normalized_instance = instance.casefold()
    if not re.fullmatch(r"wd[0-9]{1,4}", normalized_instance):
        raise ProviderError("Unsupported Workday instance")
    configured_site = _safe_component(site, "Workday site")
    host, parts = _parts(url)
    raw_path = urlparse(url).path
    if host != expected_host or "//" in raw_path or raw_path.endswith("/"):
        raise ProviderError("Workday job URLs must use the configured exact domain and path")
    if parts and _LOCALE.fullmatch(parts[0]):
        parts = parts[1:]
    if len(parts) not in {3, 4} or parts[0] != configured_site or parts[1] != "job":
        raise ProviderError("URL is not a supported individual ATS job URL")
    job_components = [_safe_component(item, "Workday job path") for item in parts[2:]]
    external_id = job_components[-1]
    job_path = f"/job/{'/'.join(job_components)}"
    return AtsJobIdentity(
        provider="workday",
        slug=tenant,
        external_id=external_id,
        canonical_url=f"https://{expected_host}/{configured_site}{job_path}",
        domain=expected_host,
        instance=normalized_instance,
        site=configured_site,
        job_path=job_path,
    )


def parse_supported_ats_url(url: str) -> AtsBoardIdentity:
    try:
        job = parse_supported_job_url(url)
        return AtsBoardIdentity(
            job.provider,
            job.slug,
            job.region,
            job.domain,
            job.instance,
            job.site,
        )
    except ProviderError:
        return parse_supported_board_url(url)

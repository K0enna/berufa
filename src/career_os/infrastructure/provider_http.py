from __future__ import annotations

import ipaddress
import json
import queue
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from http.client import HTTPException, HTTPSConnection
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from career_os.models import JobPosting

USER_AGENT = "CareerOS/0.1 (+private personal job research)"
MAX_RESPONSE_BYTES = 15 * 1024 * 1024
MAX_PUBLIC_RESPONSE_BYTES = 2 * 1024 * 1024
_HTTP_READ_CHUNK_BYTES = 64 * 1024
_HTTP_WORKER_SLOTS = threading.BoundedSemaphore(8)
_MAX_MARKUP_TOKENS = 100_000
_MAX_MARKUP_DEPTH = 1_000


class ProviderError(RuntimeError):
    """A source could not be fetched or parsed safely."""


class RejectRedirects(HTTPRedirectHandler):
    """Fail before urllib issues a request to a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise ProviderError(f"Redirects are not allowed for provider requests: {newurl}")


def _public_tls_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    return context


class _PublicHTTPSConnection(HTTPSConnection):
    """Connect to the validated DNS answer without a second hostname lookup."""

    def connect(self) -> None:
        addresses = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        if not addresses or len(addresses) > 32:
            raise ProviderError("Public source DNS response is empty or exceeds the limit")
        for family, _, _, _, address in addresses:
            if family not in {socket.AF_INET, socket.AF_INET6}:
                raise ProviderError("Public source DNS address family is unsupported")
            resolved = ipaddress.ip_address(address[0])
            if not resolved.is_global or resolved.is_multicast or resolved.is_reserved:
                raise ProviderError("Public source DNS resolved to a non-public address")
        deadline = time.monotonic() + float(self.timeout)
        last_error: OSError | None = None
        for family, kind, protocol, _, address in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                connection = socket.socket(family, kind, protocol)
            except OSError as exc:
                last_error = exc
                continue
            connected = False
            try:
                connection.settimeout(remaining)
                connection.connect(address)
                if ipaddress.ip_address(connection.getpeername()[0]) != ipaddress.ip_address(
                    address[0]
                ):
                    raise ProviderError("Public source peer does not match the validated address")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Public source connection deadline expired")
                connection.settimeout(remaining)
                self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
                connected = True
                return
            except ProviderError:
                raise
            except OSError as exc:
                last_error = exc
            finally:
                if not connected:
                    connection.close()
        if last_error is not None:
            raise last_error
        raise TimeoutError("Public source connection deadline expired")


class _PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, request):  # noqa: ANN001
        return self.do_open(_PublicHTTPSConnection, request, context=self._context)


@dataclass(slots=True)
class SourceConfig:
    id: str
    provider: str
    company: str
    slug: str | None = None
    endpoint: str | None = None
    language: str | None = None
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SourceConfig:
        known = {"id", "provider", "company", "slug", "endpoint", "language", "enabled"}
        missing = [key for key in ("id", "provider", "company") if not value.get(key)]
        if missing:
            raise ValueError(f"Source is missing required fields: {', '.join(missing)}")
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("Source enabled must be a boolean")
        return cls(
            id=str(value["id"]),
            provider=str(value["provider"]).lower(),
            company=str(value["company"]),
            slug=str(value["slug"]) if value.get("slug") else None,
            endpoint=str(value["endpoint"]) if value.get("endpoint") else None,
            language=str(value["language"]) if value.get("language") else None,
            enabled=enabled,
            options={key: item for key, item in value.items() if key not in known},
        )


def shadow_request_attempts(source: SourceConfig) -> int | None:
    """Bound shadow HTTP attempts by the configured per-observation request budget."""

    governance = source.options.get("governance")
    if (
        not isinstance(governance, dict)
        or str(governance.get("lifecycle") or "").strip().casefold() != "shadow"
    ):
        return None
    budget = governance.get("request_budget_per_run")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        return 1  # Fail closed if a caller bypasses source-governance validation.
    return min(budget, 2)


class Provider(Protocol):
    name: str

    def fetch(self, source: SourceConfig) -> list[JobPosting]: ...


def _host_is_allowed(host: str, allowed_hosts: set[str]) -> bool:
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts)


def validate_http_url(
    url: str,
    *,
    allowed_hosts: set[str] | None = None,
    allow_fragment: bool = False,
) -> None:
    """Validate a public HTTPS URL before it becomes a source or provider value."""

    if not isinstance(url, str) or not url or any(char.isspace() or ord(char) < 32 for char in url):
        raise ProviderError("Unsupported or malformed URL")
    if "\\" in url:
        raise ProviderError("Backslashes in source URLs are not allowed")
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise ProviderError("Unsupported or malformed URL") from exc
    if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
        raise ProviderError(f"Unsupported or malformed URL: {url}")
    if parsed.username or parsed.password:
        raise ProviderError("Credentials in source URLs are not allowed")
    if parsed.fragment and not allow_fragment:
        raise ProviderError("fragment in source URLs is not allowed")
    if port not in {None, 443}:
        raise ProviderError("Non-default source URL ports are not allowed")
    host = parsed.hostname.casefold().rstrip(".")
    if host in {"", ".", "localhost"}:
        raise ProviderError("Local source hosts are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address:
        raise ProviderError("Literal source addresses are not allowed")
    normalized_hosts = {item.casefold().rstrip(".") for item in (allowed_hosts or set())}
    if normalized_hosts and not _host_is_allowed(host, normalized_hosts):
        raise ProviderError(f"Source host is not approved: {host}")


def _http_request(
    url: str,
    *,
    method: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 25,
    attempts: int = 2,
    allowed_hosts: set[str] | None = None,
    public_only: bool = False,
    public_content_types: frozenset[str] | None = None,
) -> str:
    validate_http_url(url, allowed_hosts=allowed_hosts)
    if method not in {"GET", "POST"}:
        raise ProviderError(f"Unsupported provider request method: {method}")
    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    request_headers.update(headers or {})
    if public_only:
        host = urlparse(url).hostname
        if not allowed_hosts or host not in allowed_hosts or method != "GET" or data is not None:
            raise ProviderError("Public lookup requires an exact approved host and body-free GET")
        if attempts != 1 or not 1 <= timeout <= 15:
            raise ProviderError("Public lookup requires one bounded request")
        accepted_types = public_content_types or frozenset({"application/json"})
        request_headers = {
            "User-Agent": "CareerOS/0.1 (local official-posting lookup)",
            "Accept": ", ".join(sorted(accepted_types)),
            "Accept-Encoding": "identity",
        }
    request = Request(url, data=data, headers=request_headers, method=method)
    opener = (
        build_opener(
            ProxyHandler({}),
            RejectRedirects(),
            _PublicHTTPSHandler(context=_public_tls_context()),
        )
        if public_only
        else build_opener(RejectRedirects())
    )
    maximum_bytes = MAX_PUBLIC_RESPONSE_BYTES if public_only else MAX_RESPONSE_BYTES
    last_error: Exception | None = None
    deadline = time.monotonic() + float(timeout)
    for attempt in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last_error = TimeoutError("Provider request deadline expired")
            break
        result_queue: queue.Queue[tuple[str | None, Exception | None]] = queue.Queue(maxsize=1)
        cancelled = threading.Event()
        if not _HTTP_WORKER_SLOTS.acquire(timeout=remaining):
            last_error = TimeoutError("Provider request deadline expired")
            break

        def execute_attempt(
            *,
            attempt_cancelled: threading.Event = cancelled,
            attempt_queue: queue.Queue[tuple[str | None, Exception | None]] = result_queue,
        ) -> None:
            try:
                if attempt_cancelled.is_set():
                    raise TimeoutError("Provider request deadline expired")
                with opener.open(  # noqa: S310 - validated URL
                    request, timeout=max(0.001, deadline - time.monotonic())
                ) as response:
                    final_url = response.geturl()
                    validate_http_url(final_url, allowed_hosts=allowed_hosts)
                    if public_only:
                        if response.status != 200 or final_url != url:
                            raise ProviderError(
                                "Public source did not return the exact requested resource"
                            )
                        if response.headers.get_content_type().casefold() not in accepted_types:
                            raise ProviderError(
                                "Public source response has an unsupported content type"
                            )
                        if (
                            response.headers.get("Content-Encoding", "identity").lower()
                            != "identity"
                        ):
                            raise ProviderError(
                                "Compressed public source responses are unsupported"
                            )
                        charset = response.headers.get_content_charset() or "utf-8"
                        if charset.lower() not in {"utf-8", "utf8"}:
                            raise ProviderError("Public source response must use UTF-8")
                    chunks: list[bytes] = []
                    total = 0
                    incremental_read = getattr(response, "read1", None)
                    read_chunk = incremental_read if callable(incremental_read) else response.read
                    while total <= maximum_bytes:
                        if attempt_cancelled.is_set() or time.monotonic() >= deadline:
                            raise TimeoutError("Provider request deadline expired")
                        chunk = read_chunk(min(_HTTP_READ_CHUNK_BYTES, maximum_bytes + 1 - total))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        total += len(chunk)
                    raw = b"".join(chunks)
                    if len(raw) > maximum_bytes:
                        raise ProviderError("Source response exceeds the byte limit")
                    declared_length = response.headers.get("Content-Length")
                    if declared_length is not None:
                        try:
                            expected_length = int(declared_length)
                        except ValueError as exc:
                            raise ProviderError("Invalid response Content-Length") from exc
                        if expected_length != len(raw):
                            raise ProviderError(
                                "Incomplete response: truncated Content-Length body"
                            )
                    charset = response.headers.get_content_charset() or "utf-8"
                    try:
                        value = raw.decode(charset, errors="strict" if public_only else "replace")
                    except LookupError as exc:
                        raise ProviderError("Unsupported response charset") from exc
                attempt_queue.put((value, None))
            except Exception as exc:  # transfer the exact transport error to the caller
                attempt_queue.put((None, exc))
            finally:
                _HTTP_WORKER_SLOTS.release()

        worker = threading.Thread(
            target=execute_attempt,
            name="career-os-provider-request",
            daemon=True,
        )
        try:
            worker.start()
        except Exception:
            _HTTP_WORKER_SLOTS.release()
            raise
        try:
            value, error = result_queue.get(timeout=max(0.001, deadline - time.monotonic()))
            if error is not None:
                raise error
            return value or ""
        except queue.Empty:
            cancelled.set()
            last_error = TimeoutError("Provider request deadline expired")
            break
        except HTTPError as exc:
            try:
                if public_only:
                    raise ProviderError(f"Public source returned HTTP {exc.code}") from exc
                if exc.code != 429 and not 500 <= exc.code <= 599:
                    raise ProviderError(
                        f"Source rejected the request with HTTP {exc.code}: {url}"
                    ) from exc
                last_error = exc
                if attempt + 1 < attempts:
                    delay = 0.5 * (attempt + 1)
                    remaining = deadline - time.monotonic()
                    if remaining <= delay:
                        last_error = TimeoutError("Provider request deadline expired")
                        break
                    time.sleep(delay)
            finally:
                exc.close()
        except (URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                delay = 0.5 * (attempt + 1)
                remaining = deadline - time.monotonic()
                if remaining <= delay:
                    last_error = TimeoutError("Provider request deadline expired")
                    break
                time.sleep(delay)
    raise ProviderError(f"Unable to fetch {url}: {last_error}") from last_error


def public_json_get(url: str, *, allowed_host: str) -> str:
    """One credential-free request; return exact UTF-8 text for source hashing."""
    try:
        return _http_request(
            url,
            method="GET",
            timeout=15,
            attempts=1,
            allowed_hosts={allowed_host},
            public_only=True,
            public_content_types=frozenset({"application/json"}),
        )
    except (OSError, UnicodeError, HTTPException, ProviderError) as exc:
        raise ProviderError("Official source request failed; no posting was verified") from exc


def public_text_get(url: str, *, allowed_host: str) -> str:
    """Fetch one exact public HTML page through the address-bound transport."""

    try:
        return _http_request(
            url,
            method="GET",
            timeout=15,
            attempts=1,
            allowed_hosts={allowed_host},
            public_only=True,
            public_content_types=frozenset({"application/xhtml+xml", "text/html"}),
        )
    except (OSError, UnicodeError, HTTPException, ProviderError) as exc:
        raise ProviderError("Public source inspection failed") from exc


def http_get(
    url: str,
    *,
    timeout: int = 25,
    attempts: int = 2,
    allowed_hosts: set[str] | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    return _http_request(
        url,
        method="GET",
        timeout=timeout,
        attempts=attempts,
        allowed_hosts=allowed_hosts,
        headers=headers,
    )


def json_get(
    url: str,
    *,
    allowed_hosts: set[str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 25,
    attempts: int = 2,
) -> Any:
    try:
        return json.loads(
            http_get(
                url,
                allowed_hosts=allowed_hosts,
                headers=headers,
                timeout=timeout,
                attempts=attempts,
            )
        )
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Source did not return valid JSON: {url}") from exc


def json_post(
    url: str,
    payload: dict[str, Any],
    *,
    allowed_hosts: set[str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 25,
    attempts: int = 2,
) -> Any:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
    request_headers.update(headers or {})
    try:
        return json.loads(
            _http_request(
                url,
                method="POST",
                data=body,
                headers=request_headers,
                timeout=timeout,
                attempts=attempts,
                allowed_hosts=allowed_hosts,
            )
        )
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Source did not return valid JSON: {url}") from exc


class _TextExtractor(HTMLParser):
    BLOCK_TAGS = {"br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ol", "p", "ul"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.last_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.casefold() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            if not self.last_text.rstrip().endswith(":"):
                self.parts.append(":")
                self.last_text = ":"
            self.parts.append("\n")
        elif normalized in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)
        if data.strip():
            self.last_text = data

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line)


_ATS_PAIRED_TAGS = frozenset(
    {
        "a",
        "b",
        "div",
        "em",
        "font",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "i",
        "li",
        "ol",
        "p",
        "span",
        "strong",
        "u",
        "ul",
    }
)


def _markup_tokens(value: str):  # noqa: ANN202 - private iterator
    index = 0
    count = 0
    while True:
        start = value.find("<", index)
        if start < 0:
            return
        end = value.find(">", start + 1)
        if end < 0:
            return
        cursor = start + 1
        while cursor < end and value[cursor].isspace():
            cursor += 1
        closing = cursor < end and value[cursor] == "/"
        if closing:
            cursor += 1
            while cursor < end and value[cursor].isspace():
                cursor += 1
        name_start = cursor
        if cursor < end and value[cursor].isascii() and value[cursor].isalpha():
            cursor += 1
            while cursor < end and value[cursor].isascii() and value[cursor].isalnum():
                cursor += 1
            if cursor == end or not (value[cursor].isalnum() or value[cursor] == "_"):
                count += 1
                if count > _MAX_MARKUP_TOKENS:
                    raise ProviderError("Posting markup exceeds the token limit")
                yield (
                    value[name_start:cursor].casefold(),
                    (start, end + 1),
                    closing,
                    value[start : end + 1].rstrip().endswith("/>"),
                )
        index = end + 1


def _safe_markup_spans(value: str) -> tuple[set[tuple[int, int]], int]:
    safe: set[tuple[int, int]] = set()
    stack: list[tuple[str, tuple[int, int]]] = []
    count = 0
    for name, span, closing, self_closing in _markup_tokens(value):
        if name == "br" and not closing:
            safe.add(span)
            count += 1
        elif name not in _ATS_PAIRED_TAGS:
            continue
        elif self_closing and not closing:
            safe.add(span)
            count += 1
        elif not closing:
            if len(stack) >= _MAX_MARKUP_DEPTH:
                raise ProviderError("Posting markup exceeds the nesting limit")
            stack.append((name, span))
        elif stack and stack[-1][0] == name:
            _, opening_span = stack.pop()
            safe.update((opening_span, span))
            count += 1
    return safe, count


def _markup_candidate(value: str) -> str:
    best = value
    _, best_count = _safe_markup_spans(value)
    candidate = value
    for _ in range(2):
        decoded = unescape(candidate)
        if decoded == candidate:
            break
        candidate = decoded
        _, count = _safe_markup_spans(candidate)
        if count > best_count:
            best = candidate
            best_count = count
    return best


def _protect_literal_markup(value: str) -> str:
    safe_spans, _ = _safe_markup_spans(value)
    safe_by_start = {start: end for start, end in safe_spans}
    parts: list[str] = []
    index = 0
    while index < len(value):
        safe_end = safe_by_start.get(index)
        if safe_end is not None:
            parts.append(value[index:safe_end])
            index = safe_end
        elif value[index] == "<":
            parts.append("&lt;")
            index += 1
        else:
            parts.append(value[index])
            index += 1
    return "".join(parts)


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    candidate = _protect_literal_markup(_markup_candidate(value))
    parser = _TextExtractor()
    try:
        parser.feed(candidate)
        parser.close()
        return parser.text()
    except Exception:
        lines = [" ".join(line.split()) for line in candidate.splitlines()]
        return "\n".join(line for line in lines if line)


def first_text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""

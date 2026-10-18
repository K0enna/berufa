from __future__ import annotations

import hashlib
import json
import socket
import ssl
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler

import pytest

from career_os.ats_urls import (
    canonical_board_url,
    parse_supported_board_url,
    parse_supported_job_url,
)
from career_os.infrastructure import provider_http as transport
from career_os.infrastructure.greenhouse import GreenhousePostingAdapter
from career_os.official_posting import main
from career_os.public_api import (
    OfficialPostingService,
    OfficialPostingSnapshot,
    OfficialSourceError,
)

URL = "https://job-boards.greenhouse.io/example/jobs/42"
ENDPOINT = "https://boards-api.greenhouse.io/v1/boards/example/jobs/42"
NOW = datetime(2026, 9, 4, tzinfo=UTC)


@pytest.mark.parametrize("host", ["job-boards.eu.greenhouse.io", "job-boards.greenhouse.io"])
def test_greenhouse_canonical_identity_preserves_board_region(host: str) -> None:
    board_url = f"https://{host}/example"
    job_url = f"{board_url}/jobs/42"
    board = parse_supported_board_url(board_url)
    job = parse_supported_job_url(job_url)
    assert canonical_board_url(board) == board_url
    assert job.canonical_url == job_url
    assert job.region == board.region == ("eu" if ".eu." in host else None)


def test_empty_html_headings_have_bounded_parse_work() -> None:
    # Accepted byte/token bounds must also bound parser work. The subprocess
    # deadline contains this deliberately repetitive fixture on a regressed parser.
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from career_os.infrastructure.provider_http import strip_html; "
            "assert strip_html('<h1></h1>' * 50_000) == ':'; "
            "assert strip_html('<h2>Role</h2><p>Fictional work.</p>') == 'Role:\\nFictional work.'",
            str(Path(__file__).resolve().parents[1] / "src"),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )


def payload() -> dict[str, object]:
    return {
        "id": 42,
        "internal_job_id": 7,
        "title": "Service Manager",
        "company_name": "Example Fictional Company",
        "location": {"name": "Remote"},
        "content": "<h2>Role</h2><p>Lead a fictional service team.</p>",
        "absolute_url": URL,
        "updated_at": "2026-09-03T10:00:00Z",
    }


def service_for(data: object):
    calls: list[str] = []

    def fetch(url: str, *, allowed_host: str) -> str:
        assert allowed_host == "boards-api.greenhouse.io"
        calls.append(url)
        return json.dumps(data)

    return OfficialPostingService(GreenhousePostingAdapter(fetch, lambda: NOW)), calls


def test_official_preview_and_confirmation_keep_candidate_authority_separate() -> None:
    service, calls = service_for(payload())
    preview = service.preview(URL)
    assert not isinstance(preview, OfficialSourceError)
    assert calls == []
    assert preview.to_dict()["network_requests"] == 0
    assert preview.to_dict()["official_source_verified"] is False
    for actor, confirmation in (("agent", preview.confirmation_hash), ("local_user", "0" * 64)):
        result = service.fetch(URL, confirmation_hash=confirmation, actor=actor)
        assert isinstance(result, OfficialSourceError)
        assert result.code == "confirmation_required"
    assert calls == []
    result = service.fetch(URL, confirmation_hash=preview.confirmation_hash, actor="local_user")
    assert isinstance(result, OfficialPostingSnapshot)
    assert calls == [ENDPOINT]
    assert result.source_hash == hashlib.sha256(json.dumps(payload()).encode()).hexdigest()
    assert result.content_hash == hashlib.sha256(result.description.encode()).hexdigest()
    assert result.company == "Example Fictional Company"
    assert result.description == "Role:\nLead a fictional service team."
    assert result.liveness == "live_at_retrieval"
    output = result.to_dict()
    assert output["authority"] == "official_api_at_retrieval"
    assert output["application_ready"] is False
    assert output["candidate_data_sent"] is False
    assert output["filesystem_changed"] is False
    assert output["career_event_created"] is False


@pytest.mark.parametrize(
    "url",
    [
        "http://job-boards.greenhouse.io/example/jobs/42",
        URL + "?gh_src=tracking",
        URL + "#apply",
        URL.replace("/42", "/0042"),
        URL.replace("example", "internal"),
        URL.replace("greenhouse.io", "greenhouse.io.attacker.example"),
        URL.replace("job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"),
        URL.replace("job-boards.greenhouse.io", "127.0.0.1"),
        URL.replace("/example/", "/example/../example/"),
        URL.replace("/42", "/%34%32"),
        URL.replace("/example/", "//example/"),
        URL.replace("https://", "https://test-user:test-password@"),
        URL.replace(".io/", ".io:8443/"),
        "file:///tmp/posting.json",
    ],
)
def test_unsupported_source_urls_never_make_requests(url: str) -> None:
    service, calls = service_for(payload())
    assert isinstance(service.preview(url), OfficialSourceError)
    assert calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 43),
        ("id", True),
        ("internal_job_id", None),
        ("internal_job_id", 0),
        ("company_name", None),
        ("title", []),
        ("content", ""),
        ("content", {"prompt": "do this"}),
        pytest.param("content", "x" * 80_001, id="oversize-content"),
        ("absolute_url", URL.replace("example", "other")),
        ("absolute_url", "https://careers.example/role?gh_jid=99"),
        ("updated_at", "2026-09-01"),
        ("updated_at", "0001-01-01T00:00:00+23:59"),
        ("updated_at", "9999-12-31T23:59:59-23:59"),
        ("application_deadline", "0001-01-01T00:00:00+23:59"),
        ("application_deadline", "9999-12-31T23:59:59-23:59"),
        ("location", []),
        ("departments", "invalid"),
    ],
)
def test_invalid_exact_postings_fail_without_becoming_official(field: str, value: object) -> None:
    data = payload()
    data[field] = value
    service, calls = service_for(data)
    preview = service.preview(URL)
    result = service.fetch(URL, confirmation_hash=preview.confirmation_hash, actor="local_user")
    assert isinstance(result, OfficialSourceError)
    assert result.code == "source_unavailable"
    assert result.to_dict()["official_source_verified"] is False
    assert calls == [ENDPOINT]


def test_official_posting_deadline_and_custom_career_url_do_not_expand_authority() -> None:
    data = payload()
    data["application_deadline"] = "2026-09-03T00:00:00Z"
    data["absolute_url"] = "https://careers.example/role?gh_jid=42"
    service, calls = service_for(data)
    preview = service.preview(URL)
    result = service.fetch(URL, confirmation_hash=preview.confirmation_hash, actor="local_user")
    assert result.liveness == "inactive"
    assert result.target.canonical_url == URL
    assert calls == [ENDPOINT]
    assert result.to_dict()["application_ready"] is False


def test_cli_is_preview_first_and_rejects_stale_confirmation(capsys, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        transport.socket, "getaddrinfo", lambda *_args, **_kwargs: pytest.fail("unexpected DNS")
    )
    assert main(["--url", URL]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["endpoint"] == ENDPOINT
    assert main(["--url", URL, "--confirm-network", "wrong"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "confirmation_required"
    assert list(tmp_path.iterdir()) == []


def test_adapter_rejects_forged_endpoint_before_transport() -> None:
    adapter = GreenhousePostingAdapter(lambda *_args, **_kwargs: pytest.fail("unexpected request"))
    target = adapter.target(URL)
    with pytest.raises(ValueError, match="could not be verified"):
        adapter.fetch(replace(target, endpoint="https://attacker.example/"))


class Response:
    def __init__(self, body: bytes = b'{"id":42}') -> None:
        self.body = body
        self.offset = 0
        self.read_limits: list[int] = []
        self.status = 200
        self.headers = Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def geturl(self):
        return ENDPOINT

    def read(self, limit):
        assert 0 < limit <= transport._HTTP_READ_CHUNK_BYTES
        self.read_limits.append(limit)
        chunk = self.body[self.offset : self.offset + limit]
        self.offset += len(chunk)
        return chunk


def test_public_transport_is_single_get_without_proxy_credentials_or_redirects(
    monkeypatch, tmp_path
) -> None:
    response = Response()
    handlers_seen = []
    requests = []
    keylog = tmp_path / "tls.keys"

    class Opener:
        def open(self, request, *, timeout):
            requests.append(request)
            # The shared deadline subtracts elapsed setup time from the socket budget.
            assert 0 < timeout <= 15
            return response

    def opener(*handlers):
        handlers_seen.extend(handlers)
        return Opener()

    monkeypatch.setenv("HTTPS_PROXY", "https://test-user:test-password@example.com/proxy")
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    monkeypatch.setattr(transport, "build_opener", opener)
    assert (
        transport.public_json_get(ENDPOINT, allowed_host="boards-api.greenhouse.io") == '{"id":42}'
    )
    assert len(requests) == 1
    assert response.read_limits
    request = requests[0]
    assert request.method == "GET" and request.data is None
    assert request.get_header("Authorization") is None
    assert request.get_header("Accept-encoding") == "identity"
    assert next(item for item in handlers_seen if isinstance(item, ProxyHandler)).proxies == {}
    public_handler = next(
        item for item in handlers_seen if isinstance(item, transport._PublicHTTPSHandler)
    )
    assert public_handler._context.keylog_filename is None
    assert public_handler._context.check_hostname is True
    assert public_handler._context.verify_mode == ssl.CERT_REQUIRED
    assert not keylog.exists()
    redirect = next(item for item in handlers_seen if isinstance(item, transport.RejectRedirects))
    with pytest.raises(transport.ProviderError, match="Redirects"):
        redirect.redirect_request(None, None, 302, "Found", {}, "https://127.0.0.1/")


@pytest.mark.parametrize(
    "defect", ["oversize", "html", "gzip", "charset", "invalid-utf8", "403", "503"]
)
def test_public_transport_rejects_unsafe_responses_without_retry(monkeypatch, defect) -> None:
    response = Response()
    if defect == "oversize":
        response.body = b"x" * (transport.MAX_PUBLIC_RESPONSE_BYTES + 1)
    elif defect == "html":
        response.headers.replace_header("Content-Type", "text/html")
    elif defect == "gzip":
        response.headers["Content-Encoding"] = "gzip"
    elif defect == "charset":
        response.headers.replace_header("Content-Type", "application/json; charset=latin-1")
    elif defect == "invalid-utf8":
        response.body = b"\xff"
    calls = []

    class Opener:
        def open(self, request, *, timeout):
            calls.append(request)
            if defect in {"403", "503"}:
                raise HTTPError(ENDPOINT, int(defect), "private error body", {}, None)
            return response

    monkeypatch.setattr(transport, "build_opener", lambda *_args: Opener())
    with pytest.raises(transport.ProviderError, match="no posting was verified") as failure:
        transport.public_json_get(ENDPOINT, allowed_host="boards-api.greenhouse.io")
    assert "private error body" not in str(failure.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "0.0.0.0", "224.0.0.1", "::1", "fd00::1"],
)
def test_public_dns_rejects_non_public_answers_before_connect(monkeypatch, address) -> None:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(family, socket.SOCK_STREAM, 6, "", (address, 443))],
    )
    monkeypatch.setattr(
        socket, "socket", lambda *_args, **_kwargs: pytest.fail("unexpected connection")
    )
    with pytest.raises(transport.ProviderError, match="non-public"):
        transport._PublicHTTPSConnection("boards-api.greenhouse.io", timeout=15).connect()


def test_public_tls_uses_validated_address_and_original_hostname(monkeypatch) -> None:
    addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    lookups = []
    connects = []
    tls_names = []

    def lookup(host, port, **kwargs):
        lookups.append((host, port))
        return addresses

    class Connection:
        def settimeout(self, timeout):
            assert 0 < timeout <= 15

        def connect(self, address):
            connects.append(address)

        def getpeername(self):
            return ("93.184.216.34", 443)

        def close(self):
            pass

    connection = transport._PublicHTTPSConnection("boards-api.greenhouse.io", timeout=15)
    assert connection._context.check_hostname is True
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    monkeypatch.setattr(
        connection._context,
        "wrap_socket",
        lambda sock, *, server_hostname: tls_names.append(server_hostname) or sock,
    )
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket, "socket", lambda *_args: Connection())
    connection.connect()
    assert lookups == [("boards-api.greenhouse.io", 443)]
    assert connects == [("93.184.216.34", 443)]
    assert tls_names == ["boards-api.greenhouse.io"]


def test_public_tls_tries_each_validated_address_with_one_shared_deadline(monkeypatch) -> None:
    first = ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0)
    second = ("93.184.216.34", 443)
    addresses = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", first),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", second),
    ]
    attempts = []
    timeouts = []
    closed = []
    tls_names = []
    ticks = iter((100.0, 100.0, 104.0, 106.0))

    class Clock:
        @staticmethod
        def monotonic():
            return next(ticks)

    class Connection:
        def __init__(self, peer, *, fails):
            self.peer = peer
            self.fails = fails

        def settimeout(self, timeout):
            timeouts.append((self.peer, timeout))

        def connect(self, address):
            attempts.append(address)
            if self.fails:
                raise OSError("first validated address is unreachable")

        def getpeername(self):
            return self.peer

        def close(self):
            closed.append(self.peer)

    sockets = iter((Connection(first, fails=True), Connection(second, fails=False)))
    connection = transport._PublicHTTPSConnection("boards-api.greenhouse.io", timeout=15)
    monkeypatch.setattr(transport, "time", Clock)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: addresses)
    monkeypatch.setattr(socket, "socket", lambda *_args: next(sockets))
    monkeypatch.setattr(
        connection._context,
        "wrap_socket",
        lambda sock, *, server_hostname: tls_names.append(server_hostname) or sock,
    )

    connection.connect()

    assert attempts == [first, second]
    assert closed == [first]
    assert timeouts == [(first, 15), (second, 11), (second, 9)]
    assert tls_names == ["boards-api.greenhouse.io"]

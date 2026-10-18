from __future__ import annotations

import hashlib
import http.client
import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode

from career_os.evidence_contract import EvidenceRelationship
from career_os.infrastructure.instances import _listener_status
from career_os.participant_app import ParticipantServer, build_server
from career_os.participant_flow import RESET_PHRASE


@contextmanager
def _running(root: Path) -> Iterator[ParticipantServer]:
    server = build_server(root, port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    server: ParticipantServer,
    method: str,
    path: str,
    *,
    fields: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    request_headers = {"Host": f"127.0.0.1:{server.server_port}"}
    body = None
    if fields is not None:
        body = urlencode(fields).encode("utf-8")
        request_headers.update(
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
                "Origin": server.application.base_url,
                "Sec-Fetch-Site": "same-origin",
            }
        )
    request_headers.update(headers or {})
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    payload = response.read()
    response_headers = {name: value for name, value in response.getheaders()}
    connection.close()
    return response.status, response_headers, payload


def _csrf(page: bytes) -> str:
    match = re.search(rb'name="csrf_token" value="([^"]+)"', page)
    assert match is not None
    return match.group(1).decode("ascii")


def _hidden(page: bytes, name: str) -> str:
    match = re.search(
        rf'name="{re.escape(name)}" value="([^"]+)"'.encode(),
        page,
    )
    assert match is not None
    return match.group(1).decode("utf-8")


def test_loopback_home_is_accessible_hardened_and_side_effect_free(tmp_path: Path) -> None:
    root = tmp_path / "participant"
    with _running(root) as server:
        status, headers, body = _request(server, "GET", "/")

    assert status == 200
    assert not root.exists()
    assert b'<html lang="en">' in body
    assert b'href="#main">Skip to main content' in body
    assert b"Review the facts before Rolevane can use them" in body
    assert b"no model calls" in body.lower()
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in headers


def test_health_and_doctor_listener_match_only_the_selected_workspace(tmp_path: Path) -> None:
    root = tmp_path / "participant"
    with _running(root) as server:
        status, _, body = _request(server, "GET", "/health")
        assert status == 200
        assert (
            json.loads(body)["instance_root_hash"] == hashlib.sha256(str(root).encode()).hexdigest()
        )
        assert _listener_status(root, server.server_port) == "ok"
        assert _listener_status(tmp_path / "different", server.server_port) == "different_workspace"
        assert _request(server, "GET", "/health", headers={"Host": "evil.test"})[0] == 421
    assert not root.exists()


def test_loopback_rejects_host_origin_and_csrf_attacks(tmp_path: Path) -> None:
    with _running(tmp_path / "participant") as server:
        status, _, _ = _request(server, "GET", "/", headers={"Host": "evil.test"})
        assert status == 421

        status, _, _ = _request(
            server,
            "POST",
            "/consent",
            fields={"csrf_token": "wrong", "accepted": "yes"},
        )
        assert status == 403

        status, _, body = _request(server, "GET", "/")
        assert status == 200
        status, _, _ = _request(
            server,
            "POST",
            "/consent",
            fields={"csrf_token": _csrf(body), "accepted": "yes"},
            headers={"Origin": "https://evil.test", "Sec-Fetch-Site": "cross-site"},
        )
        assert status == 403

        status, headers, _ = _request(
            server,
            "POST",
            "/consent",
            fields={"csrf_token": _csrf(body), "accepted": "yes"},
            headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"},
        )
        assert status == 303
        assert headers["Location"] == "/import"


def test_normal_user_can_complete_atomic_synthetic_review_and_export(
    tmp_path: Path,
) -> None:
    root = tmp_path / "participant"
    with _running(root) as server:
        status, _, home = _request(server, "GET", "/")
        assert status == 200
        status, headers, _ = _request(
            server,
            "POST",
            "/consent",
            fields={"csrf_token": _csrf(home), "accepted": "yes"},
        )
        assert status == 303
        assert headers["Location"] == "/import"

        status, _, import_page = _request(server, "GET", "/import")
        assert status == 200
        assert b"every metric are fictional" in import_page
        status, headers, _ = _request(
            server,
            "POST",
            "/import-demo",
            fields={"csrf_token": _csrf(import_page)},
        )
        assert status == 303
        assert headers["Location"] == "/review"

        status, _, resumed_import = _request(server, "GET", "/import")
        assert status == 200
        assert b'href="/review">Continue</a>' in resumed_import

        for remaining in range(6, 0, -1):
            status, _, review = _request(server, "GET", "/review")
            assert status == 200
            assert f"{remaining} item(s) need attention".encode() in review
            assert b"Approve exact revision" in review
            assert b"Save as new unapproved revision" in review
            fields = {
                "csrf_token": _csrf(review),
                "evidence_id": _hidden(review, "evidence_id"),
                "revision": _hidden(review, "revision"),
                "content_hash": _hidden(review, "content_hash"),
                "action": "approve",
            }
            status, headers, _ = _request(server, "POST", "/review", fields=fields)
            assert status == 303
        assert headers["Location"] == "/readiness"

        status, _, readiness = _request(server, "GET", "/readiness")
        assert status == 200
        assert b"Evidence slice complete" in readiness
        assert b"It is not a job recommendation" in readiness

        status, export_headers, export_body = _request(server, "GET", "/export.json")
        assert status == 200
        assert export_headers["Content-Disposition"].startswith("attachment")
        payload = json.loads(export_body)
        assert payload["external_actions"] is False
        assert payload["model_calls"] is False
        assert payload["journey"]["approved_items"] == 6
        assert payload["vault"]["credentials_included"] is False

    assert not (root / "applications").exists()
    assert not (root / "pipeline").exists()
    assert not (root / "data" / "runtime" / "career-events.jsonl").exists()


def test_review_displays_all_approval_bound_metadata_as_escaped_text(tmp_path: Path) -> None:
    with _running(tmp_path / "participant") as server:
        journey = server.application.journey
        journey.record_boundary_consent(accepted=True)
        journey.import_synthetic_document()
        for card in journey.evidence.review_queue():
            journey.review(
                card.evidence_id,
                card.revision,
                action="approve",
                expected_content_hash=card.content_hash,
            )
        original = journey.evidence.latest("synthetic-responsibility")
        metadata = replace(
            original.metadata,
            permitted_formulations=("Allowed <script>literal</script>",),
            prohibited_formulations=("Forbidden broad claim",),
            role_family_relevance=("customer_post_sales_leadership",),
            intended_channels=("application",),
            relationships=(EvidenceRelationship("synthetic-achievement", "supports"),),
            uncertainty_note="Confirm scope & scale",
            correction_note="Local wording correction",
            timeline_group="Synthetic timeline",
            mutually_exclusive_timeline=True,
        )
        revised = journey.evidence.edit(
            original.evidence_id, expected_revision=original.revision, metadata=metadata
        )
        status, _, page = _request(server, "GET", "/review")
        assert status == 200
        for field_name in metadata.to_dict():
            assert f"<dt>{field_name.replace('_', ' ').title()}</dt>".encode() in page
        for value in (
            b"Allowed &lt;script&gt;literal&lt;/script&gt;",
            b"Forbidden broad claim",
            b"customer_post_sales_leadership",
            b"supports: synthetic-achievement",
            b"Confirm scope &amp; scale",
            b"Local wording correction",
            b"Synthetic timeline",
            b"until_user_deletion",
        ):
            assert value in page
        assert b"<script>literal</script>" not in page
        assert _hidden(page, "revision") == str(revised.revision)
        assert _hidden(page, "content_hash") == revised.content_hash
        assert revised.extracted_hash.encode() in page
        assert b'value="approve" disabled' not in page
        assert revised.status == "unapproved"


def test_review_form_edit_creates_a_new_unapproved_revision(tmp_path: Path) -> None:
    with _running(tmp_path / "participant") as server:
        _, _, home = _request(server, "GET", "/")
        _request(
            server,
            "POST",
            "/consent",
            fields={"csrf_token": _csrf(home), "accepted": "yes"},
        )
        _, _, import_page = _request(server, "GET", "/import")
        _request(
            server,
            "POST",
            "/import-demo",
            fields={"csrf_token": _csrf(import_page)},
        )
        _, _, review = _request(server, "GET", "/review")
        original_revision = _hidden(review, "revision")
        fields = {
            "csrf_token": _csrf(review),
            "evidence_id": _hidden(review, "evidence_id"),
            "revision": original_revision,
            "content_hash": _hidden(review, "content_hash"),
            "statement": "Synthetic role wording explicitly edited by the local user.",
            "ownership": "personal",
            "confidence": "unknown",
            "approximation": "unknown",
            "publication_boundary": "private",
            "action": "save",
        }
        original = server.application.journey.evidence.latest(fields["evidence_id"])
        status, _, response = _request(
            server, "POST", "/review", fields={**fields, "action": "approve"}
        )
        assert status == 400
        assert b"Save your edits" in response
        assert server.application.journey.evidence.latest(fields["evidence_id"]) == original
        status, _, _ = _request(server, "POST", "/review", fields=fields)
        assert status == 303
        revised = server.application.journey.evidence.latest(fields["evidence_id"])

        assert original_revision == "1"
        assert revised.revision == 2
        assert "explicitly edited by the local user" in revised.statement
        assert revised.status == "unapproved"


def test_browser_surface_completes_role_decision_bundle_lifecycle_export_and_reset(
    tmp_path: Path,
) -> None:
    root = tmp_path / "participant"
    with _running(root) as server:
        _, _, home = _request(server, "GET", "/")
        _request(
            server,
            "POST",
            "/consent",
            fields={"csrf_token": _csrf(home), "accepted": "yes"},
        )
        _, _, import_page = _request(server, "GET", "/import")
        _request(
            server,
            "POST",
            "/import-demo",
            fields={"csrf_token": _csrf(import_page)},
        )
        for _ in range(6):
            _, _, review = _request(server, "GET", "/review")
            _request(
                server,
                "POST",
                "/review",
                fields={
                    "csrf_token": _csrf(review),
                    "evidence_id": _hidden(review, "evidence_id"),
                    "revision": _hidden(review, "revision"),
                    "content_hash": _hidden(review, "content_hash"),
                    "action": "approve",
                },
            )

        status, _, role = _request(server, "GET", "/role")
        assert status == 200
        assert b"Keep preference separate from role meaning" in role
        assert role.count(b'name="role_pack"') == 2
        assert b"Product leadership" not in role
        assert b"Commercial and consultative sales leadership" not in role
        status, headers, _ = _request(
            server,
            "POST",
            "/role",
            fields={
                "csrf_token": _csrf(role),
                "role_pack": "customer_post_sales_leadership",
                "location": "Germany",
                "work_model": "flexible",
                "maximum_travel_percent": "25",
                "currency": "EUR",
            },
        )
        assert status == 303
        assert headers["Location"] == "/posting"

        _, _, posting = _request(server, "GET", "/posting")
        assert b"official synthetic source" in posting
        status, headers, _ = _request(
            server,
            "POST",
            "/evaluate",
            fields={"csrf_token": _csrf(posting)},
        )
        assert status == 303
        assert headers["Location"] == "/dossier"

        _, _, dossier = _request(server, "GET", "/dossier")
        assert b"Recommendation: Conditional" in dossier
        assert b"Not Found" not in dossier
        assert b"No direct proof" in dossier
        assert b"Role-pack trace" in dossier
        assert b"1.1.0" in dossier
        status, headers, _ = _request(
            server,
            "POST",
            "/decision",
            fields={
                "csrf_token": _csrf(dossier),
                "decision": "conditional",
                "override_reason": "",
            },
        )
        assert status == 303
        assert headers["Location"] == "/prepare"

        _, _, prepare = _request(server, "GET", "/prepare")
        assert b"not a finished CV" in prepare
        status, headers, _ = _request(
            server,
            "POST",
            "/prepare",
            fields={"csrf_token": _csrf(prepare)},
        )
        assert status == 303
        assert headers["Location"] == "/lifecycle"

        _, _, lifecycle = _request(server, "GET", "/lifecycle")
        assert b"no application was submitted" in lifecycle.lower()
        status, headers, _ = _request(
            server,
            "POST",
            "/lifecycle",
            fields={"csrf_token": _csrf(lifecycle), "confirmed": "yes"},
        )
        assert status == 303
        assert headers["Location"] == "/complete"

        _, _, complete = _request(server, "GET", "/complete")
        assert b"One decision journey is complete" in complete
        status, _, artifact = _request(server, "GET", "/artifacts/application-brief.md")
        assert status == 200
        assert b"not a submitted application" in artifact
        status, _, _ = _request(
            server,
            "POST",
            "/export",
            fields={"csrf_token": _csrf(complete)},
        )
        assert status == 303
        status, _, export = _request(server, "GET", "/user-export.json")
        assert status == 200
        assert json.loads(export)["export_manifest"]["external_actions"] is False

        _, _, reset = _request(server, "GET", "/reset")
        assert b"Review exactly what will be removed" in reset
        status, headers, _ = _request(
            server,
            "POST",
            "/reset",
            fields={
                "csrf_token": _csrf(reset),
                "confirmation_phrase": RESET_PHRASE,
                "confirmed": "yes",
            },
        )
        assert status == 303
        assert headers["Location"] == "/reset-complete"
        status, _, deleted = _request(server, "GET", "/reset-complete")
        assert status == 200
        assert b"The synthetic instance is gone" in deleted
        assert not root.exists()

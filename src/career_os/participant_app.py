"""Minimal hardened loopback surface for the synthetic evidence-review journey."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import secrets
import sqlite3
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

from career_os.evidence_vault import (
    APPROXIMATION_VALUES,
    CONFIDENCE_VALUES,
    OWNERSHIP_VALUES,
    PUBLICATION_BOUNDARIES,
    Approximation,
    Confidence,
    EvidenceRevision,
    Ownership,
    PublicationBoundary,
    ReviewAction,
)
from career_os.participant_flow import (
    PARTICIPANT_ROLE_PACK_IDS,
    RESET_PHRASE,
    ROLE_PACKS,
    SYNTHETIC_POSTING,
    ResetReport,
    RolePackId,
    UserDecision,
)
from career_os.product_journey import ProductJourney

MAX_FORM_BYTES = 65_536
LOOPBACK_HOST = "127.0.0.1"

_STYLE = """
:root {
  color-scheme: light;
  --ink: #18221f;
  --muted: #5b6863;
  --paper: #fbfcf8;
  --panel: #ffffff;
  --line: #d8dfda;
  --accent: #126b55;
  --accent-strong: #0c4d3d;
  --soft: #e8f3ee;
  --warn: #8a4b10;
  --danger: #9b2c2c;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  color: var(--ink);
  background: linear-gradient(145deg, #edf4ef 0%, var(--paper) 42%, #f7f0e5 100%);
  font: 16px/1.55 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: var(--accent-strong); }
a:focus-visible, button:focus-visible, input:focus-visible, textarea:focus-visible,
select:focus-visible { outline: 3px solid #e0a72c; outline-offset: 3px; }
.skip { position: absolute; left: -9999px; }
.skip:focus { left: 1rem; top: 1rem; z-index: 4; background: white; padding: .6rem; }
header { border-bottom: 1px solid var(--line); background: rgba(255,255,255,.88); }
.shell { width: min(960px, calc(100% - 2rem)); margin: 0 auto; }
header .shell { display: flex; align-items: center; justify-content: space-between; min-height: 68px; gap: 1rem; }
.brand { font-weight: 780; letter-spacing: -.02em; }
.stage { color: var(--muted); font-size: .9rem; }
main { padding: clamp(2rem, 7vw, 5rem) 0; }
.eyebrow { color: var(--accent); font-weight: 750; text-transform: uppercase; letter-spacing: .08em; font-size: .78rem; }
h1 { max-width: 18ch; margin: .45rem 0 1rem; font-size: clamp(2rem, 6vw, 4rem); line-height: 1.02; letter-spacing: -.045em; }
h2 { margin-top: 0; letter-spacing: -.02em; }
.lead { max-width: 66ch; color: var(--muted); font-size: 1.12rem; }
.card { margin-top: 1.5rem; padding: clamp(1.2rem, 4vw, 2.25rem); border: 1px solid var(--line); border-radius: 18px; background: var(--panel); box-shadow: 0 18px 48px rgba(34,54,46,.08); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 1rem; }
.fact { padding: 1rem; border-radius: 12px; background: var(--soft); }
.fact strong { display: block; margin-bottom: .25rem; }
.meta { display: flex; flex-wrap: wrap; gap: .5rem; margin: 1rem 0; }
.pill { padding: .28rem .65rem; border-radius: 999px; background: var(--soft); color: var(--accent-strong); font-size: .82rem; font-weight: 700; }
.source { padding: 1rem; border-left: 4px solid var(--accent); background: #f5f7f4; white-space: pre-wrap; }
.review-metadata dt { font-weight: 650; }
.review-metadata dd { margin: 0 0 .75rem; overflow-wrap: anywhere; }
.review-metadata ul { margin: 0; padding-left: 1.25rem; }
label { display: block; margin: 1rem 0 .35rem; font-weight: 700; }
input[type="text"], input[type="number"], input:not([type]), textarea, select { width: 100%; border: 1px solid #a9b6af; border-radius: 9px; background: white; color: var(--ink); padding: .75rem; font: inherit; }
textarea { min-height: 8rem; resize: vertical; }
.table-wrap { overflow-x: auto; } table { border-collapse: collapse; width: 100%; } th, td { border-bottom: 1px solid var(--line); padding: .7rem; text-align: left; vertical-align: top; }
.actions { display: flex; flex-wrap: wrap; gap: .7rem; margin-top: 1.35rem; }
button, .button { display: inline-block; border: 0; border-radius: 9px; padding: .72rem 1rem; background: var(--accent); color: white; font: inherit; font-weight: 750; text-decoration: none; cursor: pointer; }
button:hover, .button:hover { background: var(--accent-strong); }
button.secondary { background: #e8ece9; color: var(--ink); }
button.warning { background: var(--warn); }
button.danger { background: var(--danger); }
.notice { padding: .9rem 1rem; border-radius: 10px; background: #fff6df; color: #5e4008; }
.error { padding: 1rem; border: 1px solid #efb2b2; border-radius: 10px; background: #fff0f0; color: #6e1717; }
.counts { font-size: 2rem; font-weight: 800; }
.small { color: var(--muted); font-size: .9rem; }
footer { padding: 2rem 0 3rem; color: var(--muted); font-size: .88rem; }
@media (max-width: 600px) { header .shell { align-items: flex-start; flex-direction: column; padding: .8rem 0; } }
"""


def _default_instance_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "CareerOS" / "participant"
    if os.environ.get("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]) / "career-os" / "participant"
    return Path.home() / ".local" / "share" / "career-os" / "participant"


def _options(values: set[str] | frozenset[str], selected: str) -> str:
    return "".join(
        f'<option value="{html.escape(value)}"'
        f"{' selected' if value == selected else ''}>{html.escape(value.replace('_', ' ').title())}</option>"
        for value in sorted(values)
    )


class ParticipantApplication:
    def __init__(self, instance_root: str | Path) -> None:
        self.journey = ProductJourney(instance_root)
        self.csrf_token = secrets.token_urlsafe(32)
        self._server: ParticipantServer | None = None
        self.last_reset_report: ResetReport | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("Participant application is not attached to a server")
        return f"http://{LOOPBACK_HOST}:{self._server.server_port}"

    def attach(self, server: ParticipantServer) -> None:
        if self._server is not None:
            raise RuntimeError("Participant application is already attached")
        self._server = server

    def page(self, path: str, *, error: str | None = None) -> tuple[int, str]:
        state = self.journey.state()
        if path == "/":
            content = self._home()
        elif path == "/import":
            if not state.consent_recorded:
                return HTTPStatus.SEE_OTHER, "/"
            content = self._import()
        elif path == "/review":
            if not state.consent_recorded:
                return HTTPStatus.SEE_OTHER, "/"
            if not state.source_count:
                return HTTPStatus.SEE_OTHER, "/import"
            card = self.journey.next_review()
            if card is None:
                return HTTPStatus.SEE_OTHER, "/readiness"
            content = self._review(card)
        elif path == "/readiness":
            if not state.source_count:
                return HTTPStatus.SEE_OTHER, state.next_route
            content = self._readiness()
        elif path == "/role":
            if not self.journey.readiness().ready:
                return HTTPStatus.SEE_OTHER, "/readiness"
            content = self._role()
        elif path == "/posting":
            if not state.role_pack_selected:
                return HTTPStatus.SEE_OTHER, "/role"
            content = self._posting()
        elif path == "/dossier":
            if not state.dossier_ready:
                return HTTPStatus.SEE_OTHER, "/posting"
            content = self._dossier()
        elif path == "/prepare":
            if not state.decision_recorded:
                return HTTPStatus.SEE_OTHER, "/dossier"
            content = self._prepare()
        elif path == "/lifecycle":
            if not state.bundle_ready:
                return HTTPStatus.SEE_OTHER, "/prepare"
            content = self._lifecycle()
        elif path == "/complete":
            if not state.lifecycle_confirmed:
                return HTTPStatus.SEE_OTHER, state.next_route
            content = self._complete()
        elif path == "/reset":
            if not state.consent_recorded:
                return HTTPStatus.SEE_OTHER, "/"
            content = self._reset()
        elif path == "/reset-complete":
            if self.last_reset_report is None:
                return HTTPStatus.SEE_OTHER, "/"
            content = self._reset_complete()
        else:
            return HTTPStatus.NOT_FOUND, self._layout(
                "Not found", '<div class="card"><h1>Page not found</h1></div>'
            )
        if error:
            content = f'<div class="error" role="alert">{html.escape(error)}</div>{content}'
        return HTTPStatus.OK, self._layout(self._title(path), content)

    def submit(self, path: str, fields: MappingForm) -> str:
        if fields.one("csrf_token") != self.csrf_token:
            raise PermissionError("The page expired. Reload it before making a change.")
        if path == "/consent":
            if fields.one("accepted") != "yes":
                raise ValueError("Consent must be selected before importing the demo document")
            self.journey.record_boundary_consent(accepted=True)
            return "/import"
        if path == "/import-demo":
            self.journey.import_synthetic_document()
            return "/review"
        if path == "/review":
            evidence_id = fields.one("evidence_id")
            revision = int(fields.one("revision"))
            content_hash = fields.one("content_hash")
            action = fields.one("action")
            current = self.journey.evidence.latest(evidence_id)
            if current.revision != revision or current.content_hash != content_hash:
                raise ValueError("This evidence card changed. Reload it before reviewing.")
            if action in {"approve", "reject", "defer"}:
                self.journey.review(
                    evidence_id,
                    revision,
                    action=cast(ReviewAction, action),
                    expected_content_hash=content_hash,
                )
            elif action == "save":
                self.journey.edit(
                    evidence_id,
                    expected_revision=revision,
                    statement=fields.one("statement"),
                    ownership=cast(Ownership, fields.one("ownership")),
                    confidence=cast(Confidence, fields.one("confidence")),
                    approximation=cast(Approximation, fields.one("approximation")),
                    publication_boundary=cast(
                        PublicationBoundary, fields.one("publication_boundary")
                    ),
                )
            else:
                raise ValueError("Unknown evidence review action")
            return "/review" if self.journey.next_review() is not None else "/readiness"
        if path == "/role":
            self.journey.select_role(
                cast(RolePackId, fields.one("role_pack")),
                location=fields.one("location"),
                work_model=fields.one("work_model"),
                maximum_travel_percent=int(fields.one("maximum_travel_percent")),
                currency=fields.one("currency"),
            )
            return "/posting"
        if path == "/evaluate":
            self.journey.evaluate_posting()
            return "/dossier"
        if path == "/decision":
            self.journey.record_decision(
                cast(UserDecision, fields.one("decision")),
                override_reason=fields.one("override_reason") or None,
            )
            return "/prepare"
        if path == "/prepare":
            self.journey.prepare_bundle()
            return "/lifecycle"
        if path == "/lifecycle":
            self.journey.confirm_synthetic_lifecycle(confirmed=fields.one("confirmed") == "yes")
            return "/complete"
        if path == "/export":
            self.journey.export_user_data()
            return "/complete"
        if path == "/reset":
            if fields.one("confirmed") != "yes":
                raise PermissionError("Reset requires explicit confirmation")
            self.last_reset_report = self.journey.reset(fields.one("confirmation_phrase"))
            return "/reset-complete"
        raise ValueError("Unknown participant action")

    def _layout(self, title: str, content: str) -> str:
        state = self.journey.state()
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} · Rolevane</title>
  <style>{_STYLE}</style>
</head>
<body>
  <a class="skip" href="#main">Skip to main content</a>
  <header><div class="shell">
    <div class="brand">Rolevane <span class="pill">Synthetic preview</span></div>
    <div class="stage">Current step: {html.escape(state.stage.replace("_", " "))}</div>
  </div></header>
  <main id="main" class="shell" tabindex="-1">{content}</main>
  <footer class="shell">Local-only preview · no model calls · no applications, messages, or external status changes</footer>
</body>
</html>"""

    def _home(self) -> str:
        state = self.journey.state()
        continue_link = (
            f'<a class="button" href="{html.escape(state.next_route)}">Continue the evidence review</a>'
            if state.consent_recorded
            else ""
        )
        consent = (
            ""
            if state.consent_recorded
            else f"""
<form method="post" action="/consent" class="card">
  <input type="hidden" name="csrf_token" value="{html.escape(self.csrf_token)}">
  <h2>Before anything is imported</h2>
  <p>This preview stores one fictional document and your review decisions only on this device.
  It performs no model or provider calls and never submits, sends, uploads, or infers an external event.</p>
  <label><input type="checkbox" name="accepted" value="yes" required>
    I understand this local synthetic data boundary.</label>
  <div class="actions"><button type="submit">Accept and continue</button></div>
</form>"""
        )
        return f"""
<p class="eyebrow">A trustworthy career evidence profile</p>
<h1>Review the facts before Rolevane can use them.</h1>
<p class="lead">A document is a source, not authority. Every proposed fact starts unapproved,
shows where it came from, and stays separate from any job decision or application event.</p>
<div class="grid card">
  <div class="fact"><strong>Local by default</strong>Candidate documents and evidence stay in this one instance.</div>
  <div class="fact"><strong>Exact approval</strong>Your approval binds to the revision and hash you reviewed.</div>
  <div class="fact"><strong>No external action</strong>Nothing here applies, sends, posts, or changes employer state.</div>
</div>
{consent}{continue_link}
"""

    def _import(self) -> str:
        state = self.journey.state()
        if state.source_count:
            return f"""
<p class="eyebrow">Document intake</p><h1>A managed source is already imported.</h1>
<p class="lead">The managed source and its exact hashes are preserved. Reopening this page does not duplicate it.</p>
<a class="button" href="{html.escape(state.next_route)}">Continue</a>"""
        return f"""
<p class="eyebrow">Document intake</p>
<h1>Start with one bounded fictional source.</h1>
<p class="lead">This first slice supports one UTF-8 plain-text demo. Mara Voss, Northstar Atlas
Software, and every metric are fictional and unrelated to any real candidate.</p>
<div class="card">
  <h2>mara-voss-synthetic-career-note.txt</h2>
  <ul>
    <li>Plain text, under 1 MiB</li>
    <li>Six proposed review cards; none approved automatically</li>
    <li>Includes a team metric, partial education, a genuine gap, and a forbidden claim</li>
  </ul>
  <form method="post" action="/import-demo">
    <input type="hidden" name="csrf_token" value="{html.escape(self.csrf_token)}">
    <button type="submit">Import the synthetic document</button>
  </form>
</div>"""

    def _review(self, card: EvidenceRevision) -> str:
        excerpt = "\n".join(
            self.journey.evidence.source_lines(
                card.source_id, card.source_start_line, card.source_end_line
            )
        )
        details = "".join(
            f"<dt>{html.escape(key.replace('_', ' ').title())}</dt><dd>{html.escape(value)}</dd>"
            for key, value in card.details.items()
        )
        metadata_rows: list[str] = []
        for key, value in card.metadata.to_dict().items():
            values = value if isinstance(value, list) else [value]
            rendered: list[str] = []
            for item in values:
                if isinstance(item, dict):
                    text = f"{item['kind']}: {item['target_evidence_id']}"
                elif item is None:
                    text = "Not specified"
                elif isinstance(item, bool):
                    text = "Yes" if item else "No"
                else:
                    text = str(item)
                rendered.append(html.escape(text))
            content = (
                "<ul>" + "".join(f"<li>{item}</li>" for item in rendered) + "</ul>"
                if isinstance(value, list) and rendered
                else "".join(rendered) or "Not specified"
            )
            metadata_rows.append(
                f"<dt>{html.escape(key.replace('_', ' ').title())}</dt><dd>{content}</dd>"
            )
        progress = self.journey.state()
        binding_notice = (
            '<p class="notice">Legacy source binding requires a new reviewed revision.</p>'
            if card.extracted_hash is None
            else ""
        )
        approve_disabled = (
            " disabled"
            if card.extracted_hash is None or card.status not in {"unapproved", "deferred"}
            else ""
        )
        return f"""
<p class="eyebrow">Evidence review · {progress.review_items} item(s) need attention</p>
<h1>Decide what becomes trustworthy evidence.</h1>
<div class="card">
  <div class="meta">
    <span class="pill">{html.escape(card.kind.replace("_", " "))}</span>
    <span class="pill">revision {card.revision}</span>
    <span class="pill">{html.escape(card.status)}</span>
  </div>
  <h2>{html.escape(card.statement)}</h2>
  <p class="small">Source lines {card.source_start_line}–{card.source_end_line} · source hash
  {html.escape(card.source_hash[:12])}… · revision hash {html.escape(card.content_hash[:12])}…</p>
  <blockquote class="source">{html.escape(excerpt)}</blockquote>
  {binding_notice}
  <dl class="review-metadata"><dt>Extracted text hash</dt><dd>{html.escape(card.extracted_hash or "Unbound legacy source")}</dd></dl>
  <dl>{details}</dl>
  <section aria-label="Evidence use and provenance">
    <h3>Use and provenance</h3>
    <dl class="review-metadata">{"".join(metadata_rows)}</dl>
  </section>
  <form method="post" action="/review">
    <input type="hidden" name="csrf_token" value="{html.escape(self.csrf_token)}">
    <input type="hidden" name="evidence_id" value="{html.escape(card.evidence_id)}">
    <input type="hidden" name="revision" value="{card.revision}">
    <input type="hidden" name="content_hash" value="{html.escape(card.content_hash)}">
    <label for="statement">Proposed statement</label>
    <textarea id="statement" name="statement" maxlength="4000" required>{html.escape(card.statement)}</textarea>
    <div class="grid">
      <div><label for="ownership">Ownership</label><select id="ownership" name="ownership">{_options(OWNERSHIP_VALUES, card.ownership)}</select></div>
      <div><label for="confidence">Confidence</label><select id="confidence" name="confidence">{_options(CONFIDENCE_VALUES, card.confidence)}</select></div>
      <div><label for="approximation">Approximation</label><select id="approximation" name="approximation">{_options(APPROXIMATION_VALUES, card.approximation)}</select></div>
      <div><label for="publication_boundary">Publication boundary</label><select id="publication_boundary" name="publication_boundary">{_options(PUBLICATION_BOUNDARIES, card.publication_boundary)}</select></div>
    </div>
    <p class="notice">Saving any edit—including confidential, approximate, or unknown—creates a new unapproved revision. Approve only the exact card shown.</p>
    <div class="actions">
      <button type="submit" name="action" value="approve"{approve_disabled}>Approve exact revision</button>
      <button class="secondary" type="submit" name="action" value="save">Save as new unapproved revision</button>
      <button class="warning" type="submit" name="action" value="defer"{" disabled" if card.status != "unapproved" else ""}>Defer</button>
      <button class="danger" type="submit" name="action" value="reject"{" disabled" if card.status not in {"unapproved", "deferred"} else ""}>Reject</button>
    </div>
  </form>
</div>"""

    def _readiness(self) -> str:
        readiness = self.journey.readiness()
        records = self.journey.evidence.enumerate()
        blockers = (
            "".join(f"<li>{html.escape(item)}</li>" for item in readiness.blockers)
            or "<li>No unresolved review blocker in this synthetic evidence slice.</li>"
        )
        rows = "".join(
            f"<li><strong>{html.escape(record.status.title())}</strong> · "
            f'{html.escape(record.statement)} <span class="small">({html.escape(record.publication_boundary)})</span></li>'
            for record in records
        )
        return f"""
<p class="eyebrow">Readiness</p>
<h1>{"Evidence slice complete." if readiness.ready else "The profile still needs attention."}</h1>
<p class="lead">Readiness describes evidence review only. It is not a job recommendation,
application, submission, message, interview, or employer outcome.</p>
<div class="grid card">
  <div class="fact"><span class="counts">{readiness.approved}</span><strong>Approved</strong></div>
  <div class="fact"><span class="counts">{readiness.unapproved}</span><strong>Unapproved</strong></div>
  <div class="fact"><span class="counts">{readiness.deferred}</span><strong>Deferred</strong></div>
  <div class="fact"><span class="counts">{readiness.unknown_or_disputed}</span><strong>Unknown or disputed</strong></div>
</div>
<div class="card"><h2>What still blocks this evidence slice</h2><ul>{blockers}</ul></div>
<div class="card"><h2>Reviewed evidence</h2><ul>{rows}</ul>
  <div class="actions">{"<a class='button' href='/role'>Choose a role family</a>" if readiness.ready else ""}
  <a class="button secondary" href="/export.json">Download current JSON snapshot</a>
  <a class="button" href="/">Return home</a></div>
</div>"""

    def _role(self) -> str:
        cards = "".join(
            f"""<label class="fact"><input type="radio" name="role_pack" value="{html.escape(pack.pack_id)}"
            {"checked" if not pack.fallback else ""} required> <strong>{html.escape(pack.label)}</strong>
            {html.escape(pack.structural_meaning)}
            {"Confidence is capped and application readiness is blocked." if pack.fallback else ""}</label>"""
            for pack_id in PARTICIPANT_ROLE_PACK_IDS
            for pack in (ROLE_PACKS[pack_id],)
        )
        return f"""
<p class="eyebrow">Role family and practical constraints</p>
<h1>Keep preference separate from role meaning.</h1>
<p class="lead">The role pack explains the mandate. Your constraints remain a separate local
overlay and cannot rewrite a hard gate or manufacture candidate evidence.</p>
<form method="post" action="/role" class="card">
  <input type="hidden" name="csrf_token" value="{html.escape(self.csrf_token)}">
  <fieldset><legend><strong>Select one supported role family</strong></legend>
    <div class="grid">{cards}</div>
  </fieldset>
  <div class="grid">
    <div><label for="location">Practical location</label><input id="location" name="location"
      value="Germany" maxlength="200" required></div>
    <div><label for="work_model">Work model</label><select id="work_model" name="work_model">
      <option value="flexible">Flexible</option><option value="remote">Remote</option>
      <option value="hybrid">Hybrid</option><option value="onsite">Onsite</option></select></div>
    <div><label for="maximum_travel_percent">Maximum travel percent</label>
      <input id="maximum_travel_percent" name="maximum_travel_percent" type="number"
      min="0" max="100" value="25" required></div>
    <div><label for="currency">Currency</label><input id="currency" name="currency"
      value="EUR" maxlength="3" required></div>
  </div>
  <div class="actions"><button type="submit">Save role family and constraints</button></div>
</form>"""

    def _posting(self) -> str:
        selection = cast(dict[str, object], self.journey.flow.snapshot()["role_selection"])
        pack = ROLE_PACKS[cast(RolePackId, selection["pack_id"])]
        preferences = cast(dict[str, object], selection["candidate_preferences"])
        return f"""
<p class="eyebrow">Exact official synthetic posting</p>
<h1>Evaluate one bounded opportunity.</h1>
<p class="lead">This fixture behaves like one exact official posting but remains offline and
fictional. An unverified lead cannot substitute for this identity.</p>
<div class="card">
  <div class="meta"><span class="pill">official synthetic source</span>
    <span class="pill">{html.escape(SYNTHETIC_POSTING.source_id)}</span></div>
  <h2>{html.escape(SYNTHETIC_POSTING.title)}</h2>
  <p><strong>{html.escape(SYNTHETIC_POSTING.company)}</strong> · {html.escape(SYNTHETIC_POSTING.location)}</p>
  <p>{html.escape(SYNTHETIC_POSTING.description)}</p>
  <p class="small">Exact identity: {html.escape(SYNTHETIC_POSTING.job_key)} · posting hash
  {html.escape(SYNTHETIC_POSTING.content_hash)}</p>
</div>
<div class="grid card">
  <div class="fact"><strong>Role meaning</strong>{html.escape(pack.label)} v{html.escape(pack.version)}</div>
  <div class="fact"><strong>Candidate preference</strong>{html.escape(str(preferences["work_model"]))}
  in {html.escape(str(preferences["location"]))}; travel up to {preferences["maximum_travel_percent"]}%</div>
  <div class="fact"><strong>Authority boundary</strong>Evaluation cannot create a decision,
  application, message, or lifecycle event.</div>
</div>
<form method="post" action="/evaluate" class="card">
  <input type="hidden" name="csrf_token" value="{html.escape(self.csrf_token)}">
  <button type="submit">Evaluate this exact posting</button>
</form>"""

    def _dossier(self) -> str:
        dossier = self.journey.dossier()
        if dossier is None:
            raise ValueError("No role dossier is available")
        rows = "".join(
            f"""<tr><td>{html.escape(item.level.title())}<br><span class="small">{html.escape(item.impact.replace("_", " ").title())}</span></td><td>{html.escape(item.statement)}</td>
            <td><span class="pill">{html.escape(item.outcome.replace("_", " ").title())}</span></td>
            <td>{html.escape(", ".join(item.candidate_evidence_ids) or "No direct proof")}
            {"<br><span class='small'>Transferable only: " + html.escape(", ".join(item.transferable_evidence_ids)) + "</span>" if item.transferable_evidence_ids else ""}</td></tr>"""
            for item in dossier.assessments
        )
        explanation = "".join(f"<li>{html.escape(item)}</li>" for item in dossier.explanation)
        return f"""
<p class="eyebrow">Evidence-linked role dossier</p>
<h1>Recommendation: {html.escape(dossier.recommendation.title())}.</h1>
<p class="lead">The recommendation is a decision aid. It is not a recorded user decision and
does not mean applied. Confidence: {dossier.confidence:.0%}.</p>
<div class="card"><h2>Role-pack trace</h2>
  <p><strong>{html.escape(dossier.role_pack_id)}</strong> v{html.escape(dossier.role_pack_version)}
  · {html.escape(dossier.selection_status.replace("_", " ").title())}
  · fingerprint <code>{html.escape(dossier.role_pack_fingerprint[:12])}</code></p>
  <ul>{explanation}</ul></div>
<div class="card"><h2>Why</h2><div class="table-wrap"><table>
  <thead><tr><th>Level / impact</th><th>Posting requirement</th><th>Outcome</th><th>Candidate evidence route</th></tr></thead>
  <tbody>{rows}</tbody></table></div>
  <p class="notice"><code>not_found</code> and <code>unknown</code> never become disproven.
  Private gaps may guide the decision but never become application claims.</p>
</div>
<form method="post" action="/decision" class="card">
  <input type="hidden" name="csrf_token" value="{html.escape(self.csrf_token)}">
  <label for="decision">Your decision</label><select id="decision" name="decision">
    {_options({"pursue", "conditional", "hold", "skip"}, dossier.recommendation)}</select>
  <label for="override_reason">Structured reason when overriding the recommendation</label>
  <select id="override_reason" name="override_reason"><option value="">No override</option>
    <option value="misunderstood_mandate">Misunderstood mandate</option>
    <option value="missing_evidence">Missing evidence</option>
    <option value="practical_constraint">Practical constraint</option>
    <option value="company_preference">Company preference</option>
    <option value="scoring_defect">Scoring defect</option></select>
  <div class="actions"><button type="submit">Record decision only</button></div>
  <p class="small">This deliberately creates no application event, workspace, submission, or message.</p>
</form>"""

    def _prepare(self) -> str:
        state = self.journey.flow.snapshot()
        decision = cast(dict[str, object], state["decision"])
        artifacts = cast(list[dict[str, object]], state["artifacts"])
        if artifacts:
            return """
<p class="eyebrow">Preparation</p><h1>The truthful content package is ready.</h1>
<p class="lead">Every included claim is bound to an approved application-safe evidence revision.
It remains local and has not been submitted.</p><a class="button" href="/lifecycle">Continue to explicit lifecycle confirmation</a>"""
        can_prepare = decision["decision"] in {"pursue", "conditional"}
        return f"""
<p class="eyebrow">Application and interview preparation</p>
<h1>{"Prepare a truthful content package." if can_prepare else "This decision stops preparation."}</h1>
<p class="lead">The package contains a decision dossier, requirement-to-evidence map,
application brief, and interview briefing. It is not a finished CV and never means submitted.</p>
<div class="card"><strong>Recorded decision:</strong> {html.escape(str(decision["decision"]).title())}</div>
{"" if can_prepare else '<div class="card notice">Choose pursue or conditional before preparing an application package.</div>'}
{f'''<form method="post" action="/prepare" class="card"><input type="hidden" name="csrf_token" value="{html.escape(self.csrf_token)}"><button type="submit">Generate local content package</button></form>''' if can_prepare else ""}
"""

    def _lifecycle(self) -> str:
        return f"""
<p class="eyebrow">Explicit lifecycle authority</p>
<h1>Confirm only what happened locally.</h1>
<p class="lead">The content package exists and was reviewed. No application was submitted,
no interview occurred, and no employer state changed.</p>
<form method="post" action="/lifecycle" class="card">
  <input type="hidden" name="csrf_token" value="{html.escape(self.csrf_token)}">
  <label><input type="checkbox" name="confirmed" value="yes" required>
    I confirm that I reviewed this fictional local content package.</label>
  <p class="notice">This records <code>synthetic_application_bundle_reviewed</code>, not applied,
  submitted, contacted, interviewed, offered, or rejected.</p>
  <button type="submit">Confirm the synthetic local event</button>
</form>"""

    def _complete(self) -> str:
        state = self.journey.flow.snapshot()
        artifacts = cast(list[dict[str, object]], state["artifacts"])
        links = "".join(
            f'<li><a href="/artifacts/{html.escape(Path(cast(str, item["relative_path"])).name)}">'
            f"{html.escape(cast(str, item['relative_path']))}</a> · {html.escape(cast(str, item['sha256']))[:12]}…</li>"
            for item in artifacts
        )
        return f"""
<p class="eyebrow">Complete synthetic journey</p>
<h1>One decision journey is complete.</h1>
<p class="lead">Evidence, role meaning, exact posting identity, recommendation, user decision,
generated content, and the confirmed synthetic event remain separate and inspectable.</p>
<div class="card"><h2>User-owned artifacts</h2><ul>{links}</ul>
  <div class="actions"><a class="button" href="/decision-dossier.html">View decision dossier</a>
  <form method="post" action="/export"><input type="hidden" name="csrf_token" value="{html.escape(self.csrf_token)}"><button type="submit">Create complete user export</button></form>
  <a class="button secondary" href="/user-export.json">Download export if created</a></div>
</div>
<div class="card"><h2>Close out this synthetic instance</h2>
  <p>Preview and explicitly confirm deletion of product-owned local state.</p>
  <a class="button danger" href="/reset">Preview reset and deletion</a></div>"""

    def _reset(self) -> str:
        inventory = self.journey.reset_preview()
        rows = "".join(f"<li>{html.escape(item)}</li>" for item in inventory)
        return f"""
<p class="eyebrow">Scoped reset and deletion</p><h1>Review exactly what will be removed.</h1>
<p class="lead">Only this participant data directory is in scope. The installed Rolevane
package remains outside it.</p><div class="card"><h2>{len(inventory)} product-owned file(s)</h2><ul>{rows}</ul></div>
<form method="post" action="/reset" class="card">
  <input type="hidden" name="csrf_token" value="{html.escape(self.csrf_token)}">
  <label for="confirmation_phrase">Type <code>{html.escape(RESET_PHRASE)}</code></label>
  <input id="confirmation_phrase" name="confirmation_phrase" autocomplete="off" required>
  <label><input type="checkbox" name="confirmed" value="yes" required>
    I understand this removes the synthetic instance and its local exports.</label>
  <button class="danger" type="submit">Delete this synthetic instance</button>
</form>"""

    def _reset_complete(self) -> str:
        report = self.last_reset_report
        if report is None:
            raise ValueError("No reset report is available")
        retained = "".join(
            f"<li>{html.escape(item)}</li>" for item in report.retained_installation_files
        )
        return f"""
<p class="eyebrow">Deletion verified</p><h1>The synthetic instance is gone.</h1>
<div class="grid card"><div class="fact"><span class="counts">{report.removed_count}</span>
<strong>Files removed</strong></div><div class="fact"><strong>Instance directory</strong>
{"Removed" if report.instance_removed else "Still present"}</div></div>
<div class="card"><h2>Deliberately retained outside the instance</h2><ul>{retained}</ul>
<a class="button" href="/">Start a fresh synthetic journey</a></div>"""

    def artifact(self, name: str) -> tuple[bytes, str, str]:
        if name not in {
            "application-brief.md",
            "interview-briefing.md",
            "requirement-evidence-map.json",
            "decision-dossier.html",
        }:
            raise ValueError("Unknown participant artifact")
        state = self.journey.flow.snapshot()
        matches = [
            item
            for item in cast(list[dict[str, object]], state["artifacts"])
            if Path(cast(str, item["relative_path"])).name == name
        ]
        if len(matches) != 1:
            raise ValueError("Participant artifact is unavailable")
        path = self.journey.instance_root / cast(str, matches[0]["relative_path"])
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != matches[0]["sha256"]:
            raise ValueError("Participant artifact no longer matches its manifest")
        if name.endswith(".json"):
            content_type = "application/json"
        elif name.endswith(".html"):
            content_type = "text/html"
        else:
            content_type = "text/markdown"
        return content, f"{content_type}; charset=utf-8", name

    @staticmethod
    def _title(path: str) -> str:
        return {
            "/": "Evidence boundary",
            "/import": "Import synthetic document",
            "/review": "Review evidence",
            "/readiness": "Evidence readiness",
            "/role": "Choose role family",
            "/posting": "Review exact posting",
            "/dossier": "Role decision dossier",
            "/prepare": "Prepare content package",
            "/lifecycle": "Confirm synthetic lifecycle",
            "/complete": "Journey complete",
            "/reset": "Reset synthetic instance",
            "/reset-complete": "Deletion verified",
        }.get(path, "Rolevane")


class MappingForm:
    def __init__(self, values: dict[str, list[str]]) -> None:
        self.values = values

    def one(self, key: str) -> str:
        values = self.values.get(key, [])
        if len(values) != 1 or len(values[0]) > 5_000:
            raise ValueError(f"Form field {key} is missing or repeated")
        return values[0]


class ParticipantServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        application: ParticipantApplication,
    ) -> None:
        if address[0] != LOOPBACK_HOST:
            raise ValueError("Participant server must bind to literal IPv4 loopback")
        super().__init__(address, ParticipantHandler)
        self.application = application
        application.attach(self)


class ParticipantHandler(BaseHTTPRequestHandler):
    server: ParticipantServer

    def do_GET(self) -> None:  # noqa: N802
        if not self._request_authorized():
            return
        path = urlsplit(self.path).path
        if path == "/health":
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "schema_version": 1,
                    "instance_root_hash": hashlib.sha256(
                        str(self.server.application.journey.instance_root).encode()
                    ).hexdigest(),
                    "external_actions": False,
                    "model_calls": False,
                },
            )
            return
        if path == "/export.json":
            if not self.server.application.journey.state().source_count:
                self._redirect("/")
                return
            payload = json.dumps(
                self.server.application.journey.export_snapshot(),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ).encode("utf-8")
            self._send(
                HTTPStatus.OK,
                payload,
                "application/json; charset=utf-8",
                extra_headers={
                    "Content-Disposition": 'attachment; filename="career-os-evidence-export.json"'
                },
            )
            return
        if path == "/decision-dossier.html":
            try:
                payload, content_type, filename = self.server.application.artifact(
                    "decision-dossier.html"
                )
            except (OSError, ValueError):
                self._redirect("/complete")
                return
            self._send(
                HTTPStatus.OK,
                payload,
                content_type,
                extra_headers={"Content-Disposition": f'inline; filename="{filename}"'},
            )
            return
        if path == "/user-export.json":
            target = (
                self.server.application.journey.instance_root
                / "exports"
                / "career-os-user-export.json"
            )
            if not target.is_file():
                self._redirect("/complete")
                return
            self._send(
                HTTPStatus.OK,
                target.read_bytes(),
                "application/json; charset=utf-8",
                extra_headers={
                    "Content-Disposition": 'attachment; filename="career-os-user-export.json"'
                },
            )
            return
        if path.startswith("/artifacts/"):
            try:
                payload, content_type, filename = self.server.application.artifact(
                    path.removeprefix("/artifacts/")
                )
            except (ValueError, OSError):
                self._error(HTTPStatus.NOT_FOUND, "Participant artifact is unavailable")
                return
            self._send(
                HTTPStatus.OK,
                payload,
                content_type,
                extra_headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
            return
        status, content = self.server.application.page(path)
        if status == HTTPStatus.SEE_OTHER:
            self._redirect(content)
            return
        self._html(status, content)

    def do_POST(self) -> None:  # noqa: N802
        if not self._request_authorized(require_origin=True):
            return
        path = urlsplit(self.path).path
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "Invalid request size")
            return
        if length < 1 or length > MAX_FORM_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request is too large")
            return
        if (
            self.headers.get("Content-Type", "").split(";", 1)[0]
            != "application/x-www-form-urlencoded"
        ):
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Unsupported form type")
            return
        try:
            body = self.rfile.read(length).decode("utf-8")
            fields = MappingForm(parse_qs(body, keep_blank_values=True, max_num_fields=20))
            destination = self.server.application.submit(path, fields)
        except UnicodeDecodeError:
            self._error(HTTPStatus.BAD_REQUEST, "Form data must be valid UTF-8")
            return
        except PermissionError as exc:
            self._error(HTTPStatus.FORBIDDEN, str(exc))
            return
        except (ValueError, sqlite3.Error, OSError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._redirect(destination)

    def _request_authorized(self, *, require_origin: bool = False) -> bool:
        expected_host = f"{LOOPBACK_HOST}:{self.server.server_port}"
        if self.headers.get("Host") != expected_host:
            self._error(HTTPStatus.MISDIRECTED_REQUEST, "Invalid loopback host")
            return False
        origin = self.headers.get("Origin")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        opaque_same_origin = origin == "null" and fetch_site == "same-origin"
        if origin and origin != self.server.application.base_url and not opaque_same_origin:
            self._error(HTTPStatus.FORBIDDEN, "Invalid request origin")
            return False
        if require_origin and fetch_site == "cross-site":
            self._error(HTTPStatus.FORBIDDEN, "Cross-site writes are not allowed")
            return False
        return True

    def _html(self, status: int, content: str) -> None:
        self._send(status, content.encode("utf-8"), "text/html; charset=utf-8")

    def _json(self, status: int, payload: object) -> None:
        self._send(
            status,
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _redirect(self, location: str) -> None:
        self._send(
            HTTPStatus.SEE_OTHER,
            b"",
            "text/plain; charset=utf-8",
            extra_headers={"Location": location},
        )

    def _error(self, status: int, message: str) -> None:
        content = self.server.application._layout(
            "Request could not be completed",
            f'<div class="error" role="alert"><h1>Request could not be completed</h1><p>{html.escape(message)}</p></div>',
        )
        self._html(status, content)

    def _send(
        self,
        status: int,
        payload: bytes,
        content_type: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def build_server(
    instance_root: str | Path,
    *,
    port: int = 0,
) -> ParticipantServer:
    if not 0 <= port <= 65_535:
        raise ValueError("Port must be between 0 and 65535")
    return ParticipantServer((LOOPBACK_HOST, port), ParticipantApplication(instance_root))


def serve(
    instance_root: str | Path,
    *,
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    server = build_server(instance_root, port=port)
    url = server.application.base_url
    if open_browser:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    print(f"Rolevane synthetic participant preview: {url}")
    print("Local-only; press Ctrl+C to stop.")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, default=_default_instance_root())
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    serve(args.instance_root, port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ParticipantApplication", "ParticipantServer", "build_server", "main", "serve"]

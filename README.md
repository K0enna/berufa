# Rolevane

Rolevane is a local-first, evidence-safe toolkit for high-stakes career decisions. It keeps
candidate evidence and mutable workspace data on the user's machine, separates facts from
inference, and requires human confirmation for approvals and external lifecycle events.

This pre-publication candidate includes two advice/creation Skills with a prepared selected-text
contract and an optional
seven-Skill no-untrusted-source contract preview. Rolevane is the selected public name.
Apache-2.0 and the owner-approved copyright notice are included. The owner has accepted
the residual naming risk for the selected free CLI and synthetic-only Skills scope. Destination
security-reporting activation, release provenance, and final publication approval remain pending;
release verification remains an explicit pre-release gate. See
[`docs/OPEN_SOURCE_READINESS.md`](docs/OPEN_SOURCE_READINESS.md).

Use [`docs/REFERENCE.md`](docs/REFERENCE.md) for the audience route map, complete shipped CLI
entrypoint inventory, and exact authority/state boundaries. Maintainers should use the
[`release instructions`](docs/RELEASE.md) for the local deterministic bundle and the dormant
manual attested RC path. The manuals linked there are the deeper reference; this README starts
with one synthetic path.

This source tree contains the reusable runtime, synthetic role packs and demonstrations, bounded
local document intake, and offline tests. It does not include a hosted service, telemetry,
application submission, external messaging, provider credentials, or a managed candidate
workspace.

The supported package boundary is exact: the modules and JSON resources listed in
[`config/product_package.toml`](config/product_package.toml), plus the public `pyproject.toml`,
README, licence, package manifest, and dependency-free tools under `tools/` needed to build and
verify the distribution. Private modules, provider activation, candidate data, and the private
checkout's metadata are outside that boundary.

## Quick start

Rolevane requires Python 3.11 or newer. The private-workspace lifecycle is supported on Windows
and Linux; other platforms are not currently claimed.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
rolevane-product-smoke
```

On Windows PowerShell, replace `source .venv/bin/activate` with
`.\.venv\Scripts\Activate.ps1`. Run the remaining commands in that environment.

This primary synthetic path uses invented personas and temporary local data. It makes no model
calls, network requests, or external actions. The interactive participant demo, skill validation,
and private initialized-workspace route are separate deeper paths; use
[`docs/REFERENCE.md`](docs/REFERENCE.md) to choose one. The creation Skills' prepared
`supplied_content_host_only_v1` profile accepts selected pasted text after host/provider
disclosure and explicit agreement, subject to owner activation approval. The retained fictional
profiles and optional legacy Skills keep their stricter boundaries. The legacy Skills accept
only reviewed package source, wholly fictional bundled fixtures/demos and content-free validation.

## Agent skill pack

Start with the two advice/creation Skills described in
[`docs/CREATION_SKILLS.md`](docs/CREATION_SKILLS.md). `$career-advice` gives a recommendation about
a supplied career question or role within the packaged content boundary. `$career-create-content`
writes complete editable CV/application drafts, revisions and interview preparation from the
selected pasted basis. The guide
includes installation, a full first-use example, correction, update/removal and file limits.
The Skills-only archive has no plugin metadata or connection. Ordinary Markdown drafting uses
your Codex host model and needs neither the core package nor Qt.

Real-data conversational use remains a pending [profile decision](docs/SUPPLIED_CONTENT_PROFILE.md).
A fictional test or discovery result does not establish general-use readiness. The optional seven
legacy Skills in `skills/` explain reviewed core contracts and bundled demonstrations; install them
separately only for that purpose, using [`docs/SKILL_PACK.md`](docs/SKILL_PACK.md).

This release route covers the public core and the selected Skills. The optional ChatGPT plugin and
Windows application have separate completion gates; neither is installed by this quick start.

The seven legacy versioned skills under [`skills/`](skills/) explain reviewed public package contracts,
content-free validation, and wholly fictional bundled demonstrations. In Codex, install them from a
reviewed tag or commit under the repository's `.agents/skills` directory or the user's
`$HOME/.agents/skills` directory. Every skill uses the same no-untrusted-source preview profile:
the agent may receive only reviewed package source, fictional bundled fixtures/demos, or
content-free validation.

In that legacy preview, the agent must never receive arbitrary public-source, web, issue, PR, tool, or model content;
candidate documents or extracted spans; postings or URLs; evidence, applications, interviews,
offers, outcomes, private paths; user-provided summaries; request JSON; hashes/fingerprints;
confirmations; or terminal output. Users run every real-data command and review every result in the
local terminal. No host sandboxing, prompt-injection-resistance, or complete protection claim is
made; broader private-data conversational use remains release-blocked.

Use [`docs/SKILL_PACK.md`](docs/SKILL_PACK.md) for installation, discovery, preview-safe routes,
compatibility, acceptance scenarios, and the seven-skill capability matrix. The same reference
links the deterministic local manuals. Official-observation dossier integration is deferred beyond
this preview, while release verification remains an explicit pre-release gate. A polished CV
renderer is optional for v1; content briefs are not finished CVs.

## Experimental exact posting preview

The package has one narrow exact Greenhouse route. Supplied posting text remains user-provided,
lower-authority, and unverified; a URL in that text is not fetched. After a local user confirms one
supported exact request, the adapter makes one credential-free public GET and returns a
source-authenticated observation with hashes and retrieval time. The observation is not persisted
into the official dossier or decision service and does not create application readiness or an
external event. See [`docs/REFERENCE.md`](docs/REFERENCE.md) and
[the source policy](docs/DATA_AND_MODEL_POLICY.md#provider-and-source-requirements) for the exact
limits and remaining provider/terms risk.

## Supported document intake

The local parser accepts bounded UTF-8 text and Markdown, a documented text-searchable PDF subset,
and ordinary single-disk DOCX files. It rejects encrypted files, active or external content,
unsupported archive structures, excessive expansion, and image-only PDFs. OCR is not included.

Provenance retains original and extracted-text hashes plus line, page, or paragraph locations.
The explicit consent-bound import command persists a managed local copy; parsing alone does not.
Neither parsing nor import approves evidence or creates proposals automatically. DOCX content
types resolve part overrides before extension defaults; unsafe or ambiguous declarations fail.
`rolevane-evidence-list` and `rolevane-evidence-read` remain deterministic local-terminal
commands for real data. Their paths, spans, hashes, and output stay there and never enter the
experimental conversational skill profile.

## Development

```bash
python -m pip install -e ".[dev,audit]"
python -m ruff format --check .
python -m ruff check .
python -m mypy
python -m pytest --cov=career_os --cov-branch --cov-fail-under=70
python -m pip_audit --progress-spinner off .
python -m tools.release_artifacts dist
python -m tools.release_artifacts dist --verify-only
python tools/evidence_performance.py
```

The tests are offline and use only synthetic fixtures. CI runs the suite on Python 3.11 and 3.12
on Linux and Python 3.12 on Windows; the Linux 3.12 entry also enforces lint, scoped strict typing,
branch coverage, deterministic wheel and sdist verification, and a clean wheel install followed by
`rolevane-product-smoke`. The dependency audit checks known advisories in declared Python
dependencies; it is not a source-code, provenance, operating-system, or future-vulnerability audit.
The synthetic performance command creates a valid private instance, seeds fictional evidence, and
measures `readiness()`, `audit_events()`, and the complete local `export_user_data` path at 100,
1,000, and 10,000 records. It reports application `SELECT` counts, exported evidence counts,
serialized byte size, and informational elapsed time. The query and size budgets are deterministic
resource guards; elapsed time is reported for information only. Each requested profile uses a fresh
instance and export destination, including repeated sizes.

## Deterministic release artifacts

`python -m tools.release_artifacts dist` builds exactly one wheel and one PEP 517 sdist from the
public package boundary. It also writes `RELEASE-MANIFEST.json` with artifact byte counts, exact
SHA-256 digests, and declared runtime dependencies, plus `SHA256SUMS`. The verifier fails closed on
missing, extra, tampered, or non-canonical files. The sdist is self-contained: a wheel can be built
from its unpacked contents without the private checkout.

The JSON is a dependency inventory and build manifest only. It is not a formal third-party SBOM,
signature, or hosted provenance attestation. An externally generated CycloneDX 1.4 JSON can be
validated and included with `python -m tools.release_artifacts dist --sbom SBOM.json`; the validator
checks shape, exact direct-dependency coverage, and byte integrity, but does not establish absence
of vulnerabilities, licence compliance, reproducibility, or semantic truth. See
[`docs/RELEASE.md`](docs/RELEASE.md) for the manual attested RC path and its remaining human gates.

## Trust and governance

- [`PRIVACY.md`](PRIVACY.md) explains local storage, export, backup, deletion, and residual risk.
- [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) maps the supported security boundary and risks.
- [`docs/ABUSE_CASE_MATRIX.md`](docs/ABUSE_CASE_MATRIX.md) records malicious document, posting,
  agent, workspace, and release cases with their proofs and dispositions.
- [`docs/DATA_AND_MODEL_POLICY.md`](docs/DATA_AND_MODEL_POLICY.md) defines skill, model, provider,
  and private-data rules.
- [`docs/INCIDENT_RESPONSE.md`](docs/INCIDENT_RESPONSE.md) covers private-data, credential,
  vulnerability, and release incidents.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`GOVERNANCE.md`](GOVERNANCE.md) define contribution,
  support, and release authority.

## Licence

Project-authored public source is licensed under the Apache License 2.0. Dependencies retain their
own licences. The owner-approved copyright notice is distributed in `NOTICE`. Accepted naming risk
does not establish professional name clearance; publication approval and release provenance remain
pending in the readiness record.

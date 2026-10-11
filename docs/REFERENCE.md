# Berufa Technical Reference

For a first useful experience, start with [Get Started](GET_STARTED.md) or
[advice and drafting](CREATION_SKILLS.md). This technical page covers the independent local
toolkit, its commands and its decision boundaries. The complete Skills set contains nine;
the local core is a separate installation. Its retained `rolevane-*` commands are compatibility
identifiers, not the current product name.

## Local management commands

`rolevane-task --instance-root PATH` lists, creates, revises or completes/cancels an exact
role-scoped local plan. Each mutation requires `--confirm-local-plan`, a stable request ID
and, after creation, the expected revision. Date deadlines retain their named time zone;
timed deadlines require a valid offset and zone. Completion never implies an employer fact.
See [Focus](FOCUS_WORKFLOW.md) for its read-only projection.

`rolevane-document --instance-root PATH` imports explicitly selected Markdown, text, PDF
or DOCX bytes as a standalone or role-associated immutable version; lists/inspects versions;
previews then confirms an exact human review; and exports explicitly selected formats.
Use `--help` and each subcommand's help for the installed flags. Import does not approve
evidence or make an application ready. A complete new text revision inherits no previous
binary or review. Binary export requires separate exact PDF/DOCX page acknowledgements.
Unknown evidence basis needs a separate acknowledgement; stale or damaged known basis
blocks export. Internal notes and manifests never enter selected employer-facing files.
Full task and document histories are included in [instance recovery](INSTANCE_RECOVERY.md).

## Advice and creation Skills

The complete [nine-Skill installation](GET_STARTED.md) includes two direct advice/creation
Skills for selected pasted text after disclosure and explicit individual sharing agreement.
They support advice, role assessment, complete editable drafts, revision and interview
preparation. They use the Codex host model and have no canonical connection. The owner
accepted [the selected-text profile](SUPPLIED_CONTENT_PROFILE.md); this is not individual consent.

## Seven Workflow Skills: Separate Preview Boundary

The seven skills use a no-untrusted-source conversational profile. They may receive only text/config
from the exact reviewed package/repository revision (`reviewed_package_source`), wholly fictional
bundled fixtures/demos, or content-free validation. Arbitrary public-source, web, issue, PR, tool,
and model content remains untrusted and outside skill context, as do candidate documents and spans,
postings and URLs, evidence, applications, interviews, offers, outcomes, private paths, user
summaries, request JSON, hashes/fingerprints, confirmations, and terminal output.

Real workflows remain functional through deterministic local commands. The skills may show generic
placeholder commands, but the agent must not execute them, receive their values/results, or ask the
user to paste or summarize them. No host sandboxing, prompt-injection-resistance, or complete
protection claim is made; a user or host can bypass package policy, and broader conversational use
remains release-blocked.

## Audience routes

| Audience | Start here | What it covers |
| --- | --- | --- |
| New user or evaluator | [Get Started](GET_STARTED.md) | Install all nine Skills and try a useful fictional first workflow |
| User with local career data | [Private workspace lifecycle](INSTANCE_RECOVERY.md) | Init/doctor, complete backup and restore, filtered export, and selected-workspace deletion |
| Skill or agent user | [Advice/creation Skills](CREATION_SKILLS.md) and [workflow Skills](SKILL_PACK.md) | Two selected-text entry points and seven separately bounded workflow guides, all included |
| Role, application, interview, offer, or outcome work | [Skill pack](SKILL_PACK.md) and its linked manual workflows | Supplied-role preparation, exact preview, local decisions, artifacts, comparisons, and confirmed events |
| Contributor or maintainer | [Contributing](../CONTRIBUTING.md) and [open-source readiness](OPEN_SOURCE_READINESS.md) | Synthetic-only contributions, release gates, provenance, and unresolved human decisions |
| Release maintainer | [Release instructions](RELEASE.md) and [open-source readiness](OPEN_SOURCE_READINESS.md) | Deterministic local bundles, dormant manual attested RC workflow, exact readback, and human gates |
| Security reviewer | [Threat model](THREAT_MODEL.md), [abuse matrix](ABUSE_CASE_MATRIX.md), and [security policy](../SECURITY.md) | Trust boundaries, residual provider risk, reporting, and regression evidence |

## Primary synthetic quick-start

The local core is optional and separate from the Skills. To run its commands, use a FULL
reviewed Berufa source checkout, not the selected Skills-source ZIP. On Windows with Python
3.11 or newer, run from that checkout:

```powershell
python -m venv C:\Berufa-Core-Demo-env
if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed.' }
C:\Berufa-Core-Demo-env\Scripts\python.exe -m pip install .
if ($LASTEXITCODE -ne 0) { throw 'Core installation failed.' }
C:\Berufa-Core-Demo-env\Scripts\rolevane-product-smoke.exe
```

Choose a new environment directory; do not overwrite an existing environment. Installation
downloads the declared dependencies unless they are already available in a local package cache.
It does not install a model client or connect a career workspace. Keep private data outside the
checkout and environment. After activating that environment, the equivalent smoke command is:

```bash
rolevane-product-smoke
```

This creates two isolated fictional product runtimes in temporary local storage and reports
bounded proof metadata. It makes no model calls, network requests, uploads, submissions, messages,
or other external actions. Use the installation steps above and the
[skill-pack synthetic demonstration](SKILL_PACK.md#synthetic-demonstration) for the optional
interactive demo. Keep that demo separate from a real initialized workspace.

## Private workspace lifecycle

These claims describe the implemented `rolevane-instance` route; `PATH`, backup paths, and export
paths are user-selected private directories outside the checkout and Python environment.

| Operation | Implemented boundary |
| --- | --- |
| Init | `rolevane-instance --instance-root PATH init` creates an empty current-version vault and initialized layout. It does not grant consent, import files, propose evidence, or seed synthetic facts. |
| Doctor | `rolevane-instance --instance-root PATH doctor` is a local diagnostic with fixed checks and `ready`; it checks package/runtime/dependency, layout, inventory, and managed-storage health. It does not repair state, make model/network calls, or certify security. |
| Backup, verify, restore | The offline route is a complete directory snapshot, not a filtered export, encrypted archive, synchronizer, or repair tool. Restore targets a new path and rebinds only the restored instance identity; stop all writers and review the exact inventory first. |
| Filtered export | `export` writes a portable, inspectable user-data JSON containing managed career records and source material while excluding credential/configuration files, unimported inbox files, prior exports, temporary files, logs, bindings, and lock metadata. It is not a restore format and does not upload or create an external event. |
| Selected-workspace deletion | `delete` first previews the complete current inventory, then removes only the exact confirmed initialized root after rechecking it. It does not touch installed code, outside documents, backups, exports, or provider copies; logical deletion is not guaranteed disk-sector erasure. |

## Experimental exact-posting preview

The shipped network surface is intentionally narrow:

1. A supplied posting or role request remains user-provided, lower-authority, and unverified. A URL
   inside supplied text is not fetched and does not make that text official.
2. `rolevane-role-fetch` first previews one exact supported Greenhouse URL without network access.
   After the local user confirms the exact preview hash, the adapter makes one credential-free
   public Job Board GET. The result is a source-authenticated observation at retrieval with exact
   source/content hashes and retrieval time.
3. That confirmed observation is not persisted into the official dossier or decision service. It
   does not read candidate state, create a dossier, grant application readiness, create an
   application, or create an employer event. Unsupported hosts, URL forms, redirects, credentials,
   non-public addresses, malformed responses, and oversized responses fail closed.
4. Source, content, and confirmation hashes bind an exact identity or byte representation. They
   establish integrity/provenance, not semantic truth. User-approved evidence is a review decision
   over a bound revision, not independent verification that the source statement is true.

The public CLI and seven-skill workflow do not activate broad provider collection,
authenticated/protected-site access, recurring collection, scraping, or an embedded model client.
The exact lookup above is the only supported public network route, and its real output stays in the
user's local terminal rather than entering skill context. Provider compatibility modules are shipped
but are not enabled by this workflow. Future adapters remain blocked from the supported public
workflow until their provider, terms, SSRF, redirect, credential, content-limit, retry,
data-recipient, and disable-path review is complete.

## Authority and state distinctions

| Surface or state | Establishes | Does not establish |
| --- | --- | --- |
| Supplied posting text | User-provided input for a lower-authority local evaluation | Official source authority, semantic truth, or a fetched/verified posting |
| Confirmed exact Greenhouse observation | Source-authenticated identity/content observed at one confirmed retrieval | Persistence into the official dossier/decision service, application readiness, or an external event |
| Source/hash binding | Exact identity, revision, bytes, and provenance relationship | Semantic truth, consent, or independent verification |
| User-approved evidence | The user reviewed and selected a particular bound revision and publication boundary | Independent verification of the source statement or automatic proof of a role claim |
| Local decision | A separately confirmed local choice over the current mapping | An application, submission, interview, employer response, or pipeline event |
| Prepared | A local plan, brief, call sheet, or other generated artifact is available and validated | User review, final-CV status, scheduling, submission, or an employer event |
| Reviewed | The user inspected or confirmed a local artifact/decision | Submission or any other external lifecycle event |
| Submitted | An explicit user-confirmed event record of an actual external submission | Proof that preparation or review alone caused an external action |

Prepared, reviewed, and submitted remain distinct states. The deterministic local command can draft a
typed proposal, but state changes and event records require the separate trusted local-user
confirmation described by the relevant manual workflow. Skill or model output is not authority.

## CLI entrypoints

The following inventory is the complete set of `rolevane-*` names in `[project.scripts]` in
`pyproject.toml`. The names are exact; options and confirmation rules belong to the linked manuals.

| Entrypoint | Purpose |
| --- | --- |
| `rolevane-product` | Synthetic participant UI and local product flow |
| `rolevane-evidence-smoke` | Synthetic evidence-onboarding smoke |
| `rolevane-journey-smoke` | Full synthetic participant/product journey smoke |
| `rolevane-product-smoke` | Two isolated offline synthetic product runtimes |
| `rolevane-evidence-migration-dry-run` | Legacy context-to-evidence migration dry run |
| `rolevane-evidence-import` | Consent-bound local document import |
| `rolevane-evidence-list` | Content-free imported-source listing |
| `rolevane-evidence-read` | Bounded selected source-span read |
| `rolevane-evidence-propose` | Source-bound unapproved evidence proposal |
| `rolevane-evidence-interview` | Fixed evidence interview or answer submission |
| `rolevane-role-pack-explain` | Candidate-free deterministic role-pack explanation |
| `rolevane-role-evaluate-supplied` | Supplied-role evaluation preview and mapping |
| `rolevane-role-decide-supplied` | Separately confirmed local supplied-role decision |
| `rolevane-offer-compare` | Supplied annual offer comparison preview/save |
| `rolevane-event-record` | Preview or record a user-confirmed event |
| `rolevane-event-list` | List local event records |
| `rolevane-outcome-report` | Descriptive outcome report and basis review |
| `rolevane-focus` | Read-only next-action projection |
| `rolevane-application` | Local application content plan/prepare/validate |
| `rolevane-interview` | Local interview brief and call-sheet plan/prepare/validate |
| `rolevane-workspace` | Local prepared-workspace inspection and review |
| `rolevane-task` | Exact local-plan revisions and terminal history |
| `rolevane-document` | Standalone/role document import, exact review and selected-file export |
| `rolevane-instance` | Init, doctor, recovery, export, and selected-workspace lifecycle |
| `rolevane-skill-validate` | Validate the seven public skill contracts |
| `rolevane-role-fetch` | Preview or retrieve one exact confirmed Greenhouse posting |
| `rolevane-companion-review` | Independent terminal review for the sealed fictional companion |

## Deeper docs

| Need | Document |
| --- | --- |
| Install, discover, and use the seven skills | [SKILL_PACK.md](SKILL_PACK.md) |
| Evidence import and review | [DATA_AND_MODEL_POLICY.md](DATA_AND_MODEL_POLICY.md) and the linked skill manual |
| Workspace recovery, export, and deletion | [INSTANCE_RECOVERY.md](INSTANCE_RECOVERY.md) |
| Fictional companion permissions and confirmation | [INSTANCE_RECOVERY.md](INSTANCE_RECOVERY.md#fictional-companion-state) |
| Application, interview, offer, and outcome workflows | [Application](APPLICATION_PREPARATION.md), [interview](INTERVIEW_PREPARATION.md), [offer](OFFER_COMPARISON.md), and [outcome](OUTCOME_WORKFLOW.md) manuals |
| Threats, abuse cases, and release readiness | [THREAT_MODEL.md](THREAT_MODEL.md), [ABUSE_CASE_MATRIX.md](ABUSE_CASE_MATRIX.md), and [OPEN_SOURCE_READINESS.md](OPEN_SOURCE_READINESS.md) |

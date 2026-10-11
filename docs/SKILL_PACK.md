# Optional legacy public skill pack

The default advice/creation entry point is the separate two-Skill pack in
[`CREATION_SKILLS.md`](CREATION_SKILLS.md). This guide covers the seven older contract explainers;
install them separately only when you need that bounded preview.

The seven versioned skills under `skills/` are the conversational layer over the installed local
Rolevane package. Prompt memory and model output are not factual authority; the skills do not
duplicate product rules, approve evidence, activate package providers, or authorize external
action. `skills/manifest.toml` is the machine-readable compatibility and authority contract for
package 0.1.x.

Use [`REFERENCE.md`](REFERENCE.md) for the audience route map, complete shipped CLI inventory, and
the concise authority/state reference. This guide keeps the skill-specific installation,
discovery, manual-equivalent, acceptance, denial, resumption, and capability details.

## Agent/provider disclosure

Invoking a skill uses the user's configured conversational agent/model provider. The experimental
public preview is a no-untrusted-source conversational profile. Every skill has `synthetic-only`
maturity and exactly this ordered model-data ceiling:

```text
[reviewed_package_source, synthetic_demo, content_free_validation]
```

`reviewed_package_source` means only text and configuration shipped in the exact reviewed Career
OS package/repository revision. It does not include arbitrary public source, a web page, an issue
or pull request, tool output, model output, agent output, or later repository content that was not
part of that reviewed revision.

The other allowed classes are wholly fictional bundled fixtures/demos and content-free validation.
The skills must never receive or process arbitrary public web/source, issue/PR, tool/model/agent
output; real candidate documents or extracted spans; real or supplied postings or URLs; evidence,
applications, interviews, offers, outcomes, private paths; user-provided summaries of those data;
request JSON; hashes/fingerprints; confirmations; or terminal output. These aliases remain denied
even when a user describes them as public, sanitized, selected, summarized, already reviewed, or
safe.

For real workflows, a skill may explain generic commands using placeholders. The agent must not
execute those commands, receive their values or results, or ask the user to paste, summarize,
describe, or otherwise provide them in chat. The user prepares inputs, runs commands, inspects
results, and enters confirmations in the local terminal. The deterministic manual commands remain
the supported real-data route.

The package policy is not a host control. No host sandbox, filesystem/tool least privilege,
provider retention, prompt-injection resistance, or complete protection is claimed. A user or host
can bypass the package policy. That bypass and every broader conversational profile remain
release-blocked.

## Validate and install

From the exact reviewed checkout with Python 3.11 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
rolevane-skill-validate skills
rolevane-product-smoke
rolevane-evidence-smoke
rolevane-journey-smoke
```

On Windows PowerShell, replace the `source` line with `.\.venv\Scripts\Activate.ps1`. Keep
installation, validation, and commands in that same environment.

The validator uses only the Python standard library. It compares each skill's contract range with
the installed package metadata, requires `contract_version = 1`, requires `synthetic-only`, and
requires the exact ordered three-class model-data ceiling above. It rejects broadened maturity or
data classes. It does not install, upgrade, activate, run a workflow, or attest an arbitrary copied
or modified pack. Use the exact reviewed revision and release checksums to bind installed files.

Compatibility ranges use `>=LOWER,<UPPER` with numeric `MAJOR.MINOR` or
`MAJOR.MINOR.PATCH` release versions; a missing patch component means zero. The lower bound is
inclusive and the upper bound exclusive. Missing package metadata, malformed ranges, empty ranges,
incompatible versions, and prerelease syntax fail validation.

The smoke commands use isolated temporary instances and wholly fictional bundled data. Product
and evidence smoke must report no package model calls and no external actions. Journey smoke keeps
the fictional participant path separate from its initialized workspace and exercises deterministic
state, export, and deletion boundaries. A smoke result is not proof that a real workspace is ready.

For a real workspace, the user runs `rolevane-instance --instance-root PATH doctor` in the local
terminal. Its path and output do not enter skill context. A real route proceeds only when that
local result reports `ready: true`.

## Discover the skills

The wheel installs the runtime and CLI commands; it does not install the Skills. Keep the reviewed
source checkout to obtain `skills/manifest.toml`, all seven `SKILL.md` files, and their agent metadata.
Use the same reviewed revision for both distributions. Until a public repository and release exist,
there is no public clone URL or released tag to install. Do not substitute the private development
repository or its history for a public distribution.

From the reviewed public source directory, validate first:

```text
rolevane-skill-validate skills
```

For a new repository-scoped Codex installation on Windows PowerShell, use an empty destination in
the project where you want to invoke the Skills:

```powershell
$roleVaneSource = (Get-Location).Path
$skillTarget = 'C:\path\to\your-project\.agents\skills'
$skillNames = @('rolevane-setup', 'career-evidence-onboarding', 'career-role-evaluation',
  'career-application-preparation', 'career-interview-preparation',
  'career-offer-comparison', 'career-outcome-review')
foreach ($name in $skillNames) {
  if (Test-Path -LiteralPath (Join-Path $skillTarget $name)) {
    throw "Skill already installed: $name. Review the existing version before replacing it."
  }
}
New-Item -ItemType Directory -Path $skillTarget -Force | Out-Null
foreach ($name in $skillNames) {
  Copy-Item -LiteralPath (Join-Path $roleVaneSource "skills\$name") -Destination $skillTarget -Recurse
}
```

For a new repository-scoped installation on Linux, set an unused target and copy the same directories:

```bash
skill_target=/path/to/your-project/.agents/skills
for name in rolevane-setup career-evidence-onboarding career-role-evaluation career-application-preparation career-interview-preparation career-offer-comparison career-outcome-review; do
  test ! -e "$skill_target/$name" || { echo "Existing skill: $name"; exit 1; }
done
mkdir -p "$skill_target"
for name in rolevane-setup career-evidence-onboarding career-role-evaluation career-application-preparation career-interview-preparation career-offer-comparison career-outcome-review; do
  cp -R "skills/$name" "$skill_target/"
done
```

After refreshing Codex in the target project, check that all seven names appear. Useful first requests
are `$rolevane-setup: Explain the bundled fictional journey`,
`$career-role-evaluation: Explain verified, transferable and missing evidence in the reviewed role contract`,
and `$career-offer-comparison: Explain the bundled fictional offer comparison`.
These requests need no personal facts or employer posting. Discovery is a separate host check from
metadata validation; record it as unverified if the host has not actually listed the installed pack.

To remove this installation, remove only the seven directories you copied from the target project.
Uninstalling the Python package alone does not remove Skills or private workspace data. Preserve
user changes and separately installed Skills. Upgrade by reviewing the new pack and package together,
validating compatibility, and explicitly replacing the selected directories.

Codex is the primary skill environment for v1. From a reviewed versioned checkout, copy each
individual directory under `skills/` to either `$REPO_ROOT/.agents/skills` for repository-scoped
use or `$HOME/.agents/skills` for user-scoped use. Review the diff before replacing an existing
skill with the same name. These locations follow the
[official Codex skill documentation](https://learn.chatgpt.com/docs/build-skills), checked on
2026-09-05.

Refresh or restart Codex if needed, inspect the discovered skill list, and explicitly invoke each
skill once by its `$skill-name`. `rolevane-skill-validate skills` proves package compatibility and
skill metadata only; it does not prove host discovery, invocation, sandboxing, or resistance to
untrusted instructions.

Record the reviewed package revision, package version, skill contract versions, host/version, and
actual discovery result. Do not place raw host output in skill context. A content-free pass/fail
record is sufficient. Other agent environments are not supported unless they preserve this exact
input ceiling, local-command boundary, authority model, and stop behavior.

## Plain-language preview route

The skills translate an ordinary request into one of three allowed routes:

1. Explain the contract already shipped in the exact reviewed package revision.
2. Run or inspect a wholly fictional bundled fixture/demo.
3. Report content-free validation status.

For a real workflow, identify the applicable reviewed manual and explain a command with generic
placeholders such as `PATH`, `FILE`, `URL`, `ID`, `ROUND_ID`, and `PREVIEW_SHA256`. Tell the user to
substitute values, create request files, run previews, inspect results, and enter confirmations in
the local terminal. Do not request the substituted values or any resulting output. Stop after the
generic explanation.

If the user denies or cancels agent/model use, do not inspect another source, file, path, result, or
summary. Point to the same deterministic no-model/manual commands and stop. Denial is not a failure
of the local workflow and does not authorize a reduced or inferred data transfer.

Troubleshooting that requires a real path, exception payload, request body, hash, report, or command
result also stays in the local terminal. A skill can explain public error semantics from the
reviewed source, but it cannot receive the actual value or output.

## Skill: setup

`$rolevane-setup` may explain installation, compatibility, content-free validation, the bundled
fictional participant demo, and the public lifecycle contract. It may run only the fictional or
content-free commands listed in the reviewed skill.

For real workspace lifecycle work, document these generic commands in order and stop. The user runs
them locally and retains every path, inventory, digest, confirmation, and result:

```text
rolevane-instance --instance-root PATH init
rolevane-instance --instance-root PATH doctor
rolevane-instance --instance-root PATH upgrade --backup-destination NEW_BACKUP_PATH
rolevane-instance --instance-root PATH upgrade --backup-destination NEW_BACKUP_PATH --confirm-upgrade PREVIEW_SHA256 --offline
rolevane-instance --instance-root PATH backup --destination BACKUP_PATH --offline
rolevane-instance --instance-root PATH verify --backup BACKUP_PATH
rolevane-instance --instance-root RESTORE_PATH restore --backup BACKUP_PATH
rolevane-instance --instance-root RESTORE_PATH restore --backup BACKUP_PATH --confirm-restore PREVIEW_SHA256 --offline
rolevane-instance --instance-root PATH export --destination EXPORT_PATH --offline
rolevane-instance --instance-root PATH delete
rolevane-instance --instance-root PATH delete --confirm-delete PREVIEW_SHA256 --offline
```

Init grants no consent or facts. Upgrade, restore, and deletion use exact local preview
confirmation. Backup is a complete offline snapshot; filtered export is not a restore format.
Deletion removes only the confirmed root, not outside copies or disk sectors. Lifecycle inventories
and confirmation values never enter skill context.

## Skill: evidence onboarding

`$career-evidence-onboarding` may explain the reviewed evidence contract, use the bundled fictional
evidence demo, or report content-free validation. It does not receive a real document path, source
bytes, extracted text, selected span, proposal, answer, evidence item, or summary.

For the real deterministic route, document these placeholders only:

```text
rolevane-evidence-import --instance-root PATH --document FILE
rolevane-evidence-import --instance-root PATH --document FILE --accept-local-processing
rolevane-evidence-list --instance-root PATH
rolevane-evidence-read --instance-root PATH --source-id ID --start-line N --end-line N
rolevane-evidence-propose --instance-root PATH --proposal FILE
rolevane-evidence-interview --instance-root PATH
rolevane-evidence-interview --instance-root PATH --answer FILE
rolevane-product --instance-root PATH
```

Without prior local consent, import exits `2` and writes nothing. Consented import creates no
proposal or approval; list/read/propose/interview remain local, and proposals remain unapproved.
Only the local user decides evidence disposition. A real source identity is still forbidden skill
input: content-free validation means status, not a redacted real inventory.

## Skill: role evaluation

`$career-role-evaluation` may explain the reviewed role contracts, explain candidate-free
role-pack selection from reviewed package configuration, or use a wholly fictional bundled role.
It does not receive a real/supplied posting, URL, constraints, preferences, evidence summary,
mapping, dossier, decision, source identity, or command result.

For the real deterministic route, document these placeholders only:

```text
rolevane-role-pack-explain --signals SIGNALS
rolevane-role-fetch --url URL
rolevane-role-fetch --url URL --confirm-network PREVIEW_SHA256
rolevane-role-evaluate-supplied --instance-root PATH --request FILE
rolevane-role-evaluate-supplied --instance-root PATH --request FILE --confirm-mapping EVALUATION_SHA256
rolevane-role-decide-supplied --instance-root PATH --decision FILE
rolevane-role-decide-supplied --instance-root PATH --decision FILE --confirm-decision INPUT_SHA256
```

Exact-source fetch previews without network; exact local confirmation permits one credential-free
Greenhouse request. Its URL/results stay local. The observation is source-authenticated at
retrieval, not persisted into the official dossier/decision service, semantic truth, readiness, or
an event. Supplied text remains lower-authority/unverified. Mapping and decision confirmation are
separate; the decision preview exits `2` and prints its digest locally. Digests/actor labels are not
consent.

## Skill: application preparation

`$career-application-preparation` may explain the reviewed application contract, use a bundled
fictional brief, or report content-free validation. It does not receive a real dossier, decision,
posting requirement, gap, evidence revision, application request, prepared artifact, or summary.

For the real deterministic route, document these placeholders only:

```text
rolevane-application --instance-root PATH plan --request FILE
rolevane-application --instance-root PATH prepare --request FILE --confirm-plan PREVIEW_SHA256
rolevane-application --instance-root PATH validate --opportunity-id ID --fingerprint PREVIEW_SHA256
```

The local plan requires a current dossier/decision and approved application-safe evidence.
Preparation confirms the current plan fingerprint; validation checks the immutable brief/manifest.
A content brief is not a finished CV, submission, or event, and `application_ready` remains false.

## Skill: interview preparation

`$career-interview-preparation` may explain the reviewed interview contract, use a bundled
fictional brief/call sheet, or report content-free validation. It does not receive a real role,
round, attendee, story, evidence item, gap, interview request, artifact, or summary.

For the real deterministic route, document these placeholders only:

```text
rolevane-interview --instance-root PATH plan --request FILE
rolevane-interview --instance-root PATH prepare --request FILE --confirm-plan PREVIEW_SHA256
rolevane-interview --instance-root PATH validate --opportunity-id ID --round-id ROUND_ID --fingerprint PREVIEW_SHA256
```

The local plan requires a current dossier/decision, named round, and approved evidence/stories.
Preparation confirms the plan; validation checks the immutable brief/call sheet/manifest. It does
not schedule or prove an interview, and never invents attendees, metrics, chronology, or feedback.

## Skill: offer comparison

`$career-offer-comparison` may explain the reviewed comparison contract, use a bundled fictional
economics example, or report content-free validation. It does not receive real offer terms,
compensation, baseline values, source notes, assumptions, unknowns, request files, or results.

For the real deterministic route, document these placeholders only:

```text
rolevane-offer-compare --instance-root PATH --request FILE
rolevane-offer-compare --instance-root PATH --request FILE --confirm-comparison PREVIEW_SHA256
```

The local request uses one annual currency/value basis, explicit sources/assumptions, and bounded
values; unknowns do not become zero. Preview does not write, and save requires exact local
confirmation. Comparison is not advice or authority to accept, reject, negotiate, contact, or
record an event.

## Skill: outcome review

`$career-outcome-review` may explain reviewed event/report contracts, use bundled fictional
events/reports, or report content-free validation. It does not receive a real Focus projection,
application, interview, offer, outcome, event, report, acknowledgment, path, request, hash, or
summary.

For the real deterministic route, document these placeholders only:

```text
rolevane-event-record --instance-root PATH --request FILE
rolevane-event-record --instance-root PATH --request FILE --confirm-event PREVIEW_SHA256
rolevane-event-list --instance-root PATH
rolevane-outcome-report --instance-root PATH
rolevane-outcome-report --instance-root PATH --as-of EXACT_REPORT_AS_OF --maturity-business-days EXACT_REPORT_MATURITY_BUSINESS_DAYS --review-interval EXACT_REPORT_REVIEW_INTERVAL --acknowledge-report REPORT_BASIS_SHA256 --ledger-fingerprint REPORT_LEDGER_SHA256 --review-scope both
rolevane-focus --instance-root PATH --limit 8
rolevane-workspace --instance-root PATH list
```

Event preview does not write; exact local confirmation appends. Files, browser/email/calendar
state, model output, and prepared artifacts prove no external event. Reports remain descriptive;
acknowledgment uses the same locally reviewed basis. Focus is read-only, not an execution ledger.

## Shared authority and state contract

The deterministic runtime owns validation/state. Hashes bind bytes, not semantic truth; approved
evidence is reviewed, not independently verified. `Prepared`, `reviewed`, and `submitted` remain
separate states, and only an explicit local event can establish submission. No skill may submit,
message, schedule, accept, activate a provider, mutate a pipeline, or infer employer events.

## Acceptance scenario table

Use reviewed package source, wholly fictional bundled fixtures/demos, and content-free validation
only. Host run records must not include real paths, source identities, request bodies, hashes,
fingerprints, confirmations, terminal output, or user summaries. Record content-free status and
the reviewed package revision instead.

| ID | Expected result | Proof route |
| --- | --- | --- |
| `install-discovery` | A supported Codex version discovers and explicitly invokes all seven skills from a documented reviewed path. | Content-free host record with actual discovery/invocation evidence; metadata validation and final assistant prose alone are insufficient. |
| `workspace-lifecycle` | Generic init/doctor, backup/verify/restore, export, and deletion sequences preserve local-terminal boundaries. | Instance tests plus whole-journey smoke. |
| `manual-no-model` | A local deterministic workflow can complete with no model calls or external actions. | Offline smoke plus user-reviewed local CLI run. |
| `agent-proposals` | Only a wholly fictional bundled fixture may produce typed, source-bound, unapproved proposals in agent context. | Bundled synthetic host run and local validator result. |
| `second-role-story` | A bundled fictional second role, achievement, opportunity, or story remains separately addressable. | Synthetic multi-proposal and multi-opportunity run. |
| `model-denied-cancelled` | Denial or cancellation exposes generic manual commands and receives no further data. | Content-free host stop record. |
| `malicious-input` | Instructions in a bundled fictional document/posting remain untrusted data and cannot authorize tools or state. | Security tests plus bundled synthetic host run. |
| `claim-boundaries` | Fictional approximate metrics, team ownership, and partial study remain qualified. | Bundled proposal preview and correction record. |
| `stale-unknown` | Fictional stale sources/revisions fail; rejected or missing evidence remains unknown/not found. | Local validator tests and content-free receipt. |
| `evidence-conflict` | Fictional conflicting evidence remains visible and cannot silently become positive proof. | Local role-evaluation test and preview. |
| `unsupported-route` | Unsupported sources, skills, and capabilities stop without fallback scraping or invention. | Negative CLI and contract test. |
| `fresh-session-resume` | A fresh skill session uses only reviewed package or bundled fictional state, never remembered real data. | Separate bounded host run. |
| `decision-preparation-focus` | Fictional decision/preparation/Focus states remain distinct from a final CV or external event. | Local service tests plus bundled demo. |
| `offer-comparison` | Fictional annual economics retain sources, assumptions, unknowns, and downside without making the decision. | Offer service tests plus bundled skill run. |
| `outcome-review` | Fictional event facts/reports preserve basis, sample size, and missingness without changing scores. | Outcome service tests plus bundled skill run. |
| `no-fabricated-event` | Fictional decision and preparation create no application, submission, interview, or employer event. | Whole-journey smoke and event-ledger inspection. |

### Current host-proof status

The fresh content-free host-proof session used a public-skill-only Git root and repo-scoped
`.agents/skills` installation. Codex 0.153.4 app-server discovery returned all seven installed
skills; the native turn record contains seven structured `skill` inputs, followed by seven
instruction-specific no-input/manual outcomes. No shell, data command, or other tool was executed.
Host readback recorded `gpt-5.6-luna`, `xhigh`, read-only mode, and disabled tool-network access.
Only global operating instructions were loaded at startup, not private project instructions.
This verifies `install-discovery` for the selected no-input route, not arbitrary fictional or real
workflows. It does not attest sandbox enforcement or global skill isolation. The conversational
model provider is still used.

Earlier runs discarded skill bodies to `$null`, supplied only final dollar-name self-reports, or
failed at shell process policy. Those remain failed or partial attempts, not acceptance evidence.

For each current run, record package/skill versions, reviewed revision, host/version when exposed,
the allowed input class, content-free validator status, failures, correction count, and elapsed
effort. Do not copy raw output into the guide. Record failed and cancelled runs as well as passes,
and never infer unavailable model, host, sandbox, or delegation details.

## Historical acceptance evidence

The records below preserve the useful conclusions from the stopped manual-local preview. They are
historical evidence only. Any export, installation, checksum, or history artifact retained with
those records is historical as well and does not bind the current tree. Several runs used unbundled fictional files, synthetic paths, selected
spans, hashes, or agent-authored request files that the current no-untrusted-source profile now
forbids. Those inputs must not be repeated or treated as current-profile acceptance evidence.

### Historical `install-discovery` record

A Codex CLI run discovered and invoked all seven version-1 skills from a sanitized, versioned
checkout. It read only shipped skill files, created no product state, and made no network call or
external action. This remains useful discovery evidence, but it does not prove a sandbox,
prompt-injection resistance, or the current manifest ceiling without a fresh validator run.

An issue `#149` integration validated seven skills and exercised product, evidence, and journey
smoke with fictional data. It retained separate role/achievement/education records, kept a team
result approximate and team-owned, kept coursework distinct from a completed degree, and left all
proposals unapproved. One Windows attempt failed on temporary-path traversal before a later local
run passed. These are deterministic runtime findings, not current-profile host proof.

The old `agent-proposals`/`malicious-input` run showed that instruction-bearing fictional text did
not authorize a tool, approval, event, network call, or external action. The old
`second-role-story`/`fresh-session-resume` run retained two opportunities and failed closed instead
of inventing preparation or an event. The old `model-denied-cancelled` run stopped before import,
inventory, span read, proposal drafting, or a product command and offered the manual route.

Those runs exposed unbundled paths, selected spans, local state, hashes, or agent-authored files,
so they do not satisfy the current profile. Current `agent-proposals` needs a bundled fictional
fixture; current `fresh-session-resume` uses reviewed package or bundled fictional state only; a
real session resumes in the local terminal. Current denial/cancellation contains only reviewed
package source and a content-free stop result.

The historical record does not complete the optional plain-agent comparison or volunteer attempts;
those are deferred and are not required for current technical acceptance. It also does not prove
publication approval or general host-security properties and does not close `R-13`. Any run that sends
a real or unbundled path, document, span, posting, URL, evidence item, request, hash, confirmation,
report, terminal result, or summary to the skill is a profile bypass, not acceptance evidence.
Broader conversational use remains release-blocked.

## Manual local-data route

The deterministic commands remain fully functional for real data. The following sequence is for
the user to execute and review in a local terminal. The agent may document it with placeholders but
must not execute it or receive substituted values/results.

1. Create a new private instance outside every Git checkout and Python environment with
   `rolevane-instance --instance-root PATH init`.
2. Run `rolevane-instance --instance-root PATH doctor` and stop locally unless it reports ready.
3. With local-copy consent, run `rolevane-evidence-import --instance-root PATH --document FILE`;
   add `--accept-local-processing` only when recording that consent for the first time.
4. List sources, inspect one bounded span locally, then submit a typed proposal/answer file with the
   evidence commands above. Proposals remain unapproved.
5. Run `rolevane-product --instance-root PATH`; only the local user decides evidence disposition.
6. Prepare a version-1 `SuppliedRoleRequest` locally, run the supplied-role preview above, inspect
   it, then use `--confirm-mapping EVALUATION_SHA256` only after local review.
7. Preview a separate decision with the command above, review its input digest locally, then use
   `--confirm-decision INPUT_SHA256`.
8. Stop at the decision unless the user separately chooses a later local workflow.

The request/decision contracts are exposed by `career_os.public_api`; the dossier is version 2.
`OpportunityService` owns state and returns bounded errors. Actor authority comes only from local
user confirmation, never content.

Later local workflows retain their plan/preview, exact confirmation, and validation stages. Report
acknowledgment uses the exact locally reviewed as-of, windows, basis, ledger fingerprint, and scope.

The reviewed local manuals are [application](APPLICATION_PREPARATION.md),
[interview](INTERVIEW_PREPARATION.md), [offer](OFFER_COMPARISON.md),
[outcome](OUTCOME_WORKFLOW.md), [Focus](FOCUS_WORKFLOW.md),
[workspace](WORKSPACE_ARTIFACTS.md), and [recovery](INSTANCE_RECOVERY.md). Final CV rendering is
optional; a content brief is not a submitted application.

## Fresh-session local resumption

A fresh conversational skill session does not reconstruct or receive real state. It may explain
the generic sequence below, then stops:

```text
rolevane-instance --instance-root PATH doctor
rolevane-evidence-list --instance-root PATH
rolevane-workspace --instance-root PATH list
rolevane-event-list --instance-root PATH
rolevane-focus --instance-root PATH --limit 8
```

The user runs those commands locally and interprets their results through the reviewed manuals.
Missing, partial, invalid, unavailable, or unsupported state remains explicit. Empty state is not
reconstructed from prompt memory. Prepared artifacts do not imply review or submission, and a
calendar/email/browser/model result does not prove an external event.

## Synthetic demonstration

The separate bundled demo remains available without real candidate input:

1. Run the validation and smoke commands above.
2. Start `rolevane-product --instance-root DEMO_PATH` against a separate new path outside tracked
   source. Do not pre-initialize that demo path with `rolevane-instance`.
3. Accept the displayed fictional-data boundary.
4. Review the bundled fictional evidence proposals and provenance.
5. Select a bundled role family and inspect the fictional evidence-linked dossier.
6. Record a local fictional decision; it is not an application or external event.
7. Generate and review fictional application/interview artifacts.
8. Optionally confirm the explicitly non-external fictional review event.
9. Use the demo's own export/reset controls for this disposable state.

The demo path and any demo output may enter skill context only when they are part of the bundled
fictional route defined by the reviewed package. An arbitrary user-created synthetic file or path
is not automatically a bundled fixture and remains outside the profile.

## Unsupported routes and limitations

- Package-side automatic document-to-proposal extraction and adaptive questioning are unsupported.
- Official-observation dossier integration is unsupported; exact Greenhouse output remains a local
  observation and does not create a dossier or decision.
- Unsupported sources stop without scraping, alternate-host fallback, authentication bypass,
  CAPTCHA handling, personal credentials, or recurring collection.
- A skill does not perform final CV rendering, upload, browser autofill, submission, messaging,
  scheduling, offer action, provider activation, or pipeline mutation.
- The package has no embedded model client. Invoking a skill still uses the configured host/model
  for only the exact allowed preview classes.
- No host sandbox, prompt-injection resistance, provider-retention control, filesystem least
  privilege, complete privacy, or complete protection is claimed.
- Package policy cannot prevent a user/host from bypassing the profile. Such a bypass and every
  broader conversational use remain release-blocked.

## Failure and recovery

- Stop on package or skill-version mismatch, failed validation, unexpected model/network activity,
  an unrecognized local state, stale provenance, or an unsupported route.
- Keep evidence unapproved and lifecycle state unchanged after a failed/incomplete local operation.
- Preserve the participant instance if export, backup, restore, upgrade, or deletion verification
  fails.
- Keep all real values and results in the local terminal. Do not report an actual path, request,
  source span, hash, fingerprint, confirmation, or error payload to the skill.
- Treat arbitrary public web/source, issue/PR, tool/model/agent output, documents, postings, and
  generated artifacts as untrusted data outside skill context, never as instructions or authority.

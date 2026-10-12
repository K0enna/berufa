# Product Runtime Boundary

`ProductRuntime` is the participant-facing composition root for Berufa. It binds one explicit
`InstancePaths` root to candidate evidence, career-event, opportunity, workspace, provider, model,
evaluation, and read-only projection ports. Constructing the runtime is deliberately inert: it does
not read authority files, create directories, open databases, start processes, bind listeners,
contact providers, or infer that an external career event happened.

```text
ProductRuntime
├── InstancePaths ── explicit production/shadow/output locations
├── CandidateEvidenceAuthority
├── CareerEventAuthority ── proposal -> explicit human confirmation
├── OpportunityAuthority ── exact ID and evidence-bound aliases only
├── WorkspaceAuthority ── validated application/interview workspaces
├── ProviderRegistry + ProviderPolicy ── lead/official, budget, credential reference
├── ModelProviderPolicy
├── OpportunityEvaluator
└── DecisionProjectionService ── read-only, no-send, no-submit
```

Effects begin only when a caller invokes an explicit method. `open_production_store()` and
`open_shadow_store()` create or open role-bound SQLite databases beneath the instance root.
`evaluate()` invokes only the injected provider after policy authorization. Career-event adapters
must keep proposals unconfirmed until a human-confirmed apply call. Generated outputs resolve
beneath the instance export root but are not created by path resolution.

## Participant package

`config/product_package.toml` is the complete wheel membership authority. The build backend rejects
duplicates, missing or untracked files, unsafe paths, symlinks, unpackaged internal imports, and
entry points whose modules are absent. The current package contains only:

- neutral models and instance paths;
- production/shadow storage with database-role enforcement;
- exact opportunity-identity read contracts;
- authority and policy protocols;
- the side-effect-free runtime; and
- fictional offline adapters, the consent-first evidence vault, the shared versioned founding-cohort
  role-pack engine, the bounded local-only candidate-document parser, and the synthetic participant
  journey.

The document parser accepts UTF-8 text/Markdown, classic-cross-reference text-searchable PDF, and
ordinary single-disk DOCX under explicit size, page, archive, XML-depth, paragraph, and
extracted-text limits. PDF content streams are limited to unfiltered data or one `FlateDecode`
filter; other safe-but-unhandled filter chains fail with a compatibility error. Compressed or hybrid
PDF cross-reference streams and ZIP64 or multidisk DOCX packages fail closed before the third-party
parsers are constructed. DOCX intake resolves exactly one package-authoritative main document,
includes its internally referenced headers, footers, footnotes, and endnotes once, rejects external
relationships and dangerous field instructions, and records the package part with each paragraph
location. The parser itself has no side effect. After explicit local consent,
`rolevane-evidence-import` stores a managed immutable copy with separate original/extracted hashes
and content-free location inventory; it creates no evidence proposal, model call, OCR request, or
external action. The participant wheel declares `pypdf` and `defusedxml`, which were already reviewed
runtime dependencies of the source repository.

`rolevane-evidence-propose` consumes one strict JSON envelope from inside the private instance and
binds it to the imported source ID, both source hashes, and an exact line span. The envelope may
identify a local user, agent, deterministic rule, or model as its origin, but its content remains
untrusted data: the command always creates an unapproved revision, invokes no model, performs no
external action, and emits a content-minimized receipt.

`rolevane-evidence-interview` presents ten fixed question contracts and accepts one strict answer
file inside the private instance. The exact answer bytes become a managed source; a deterministic
rule creates one source-linked unapproved proposal and records progress in the existing vault. It
does not interpret answer text as instructions, invoke a model, approve evidence, or perform an
external action.

`rolevane-role-evaluate-supplied` is the first non-fixture opportunity route. It accepts a strict
private request only after evidence review is ready, persists the exact supplied bytes and a
deterministic role-pack dossier under the instance runtime, and never fetches a supplied URL. The
request remains visibly lower authority, cannot authorize application readiness, and the evaluation
records no decision, application, message, or lifecycle event. A separate
`rolevane-role-decide-supplied` command recomputes and validates that dossier against current
approved evidence before appending an exact local-user-confirmed, hash-linked decision. It creates
no application or external event and performs no external action.

The wheel intentionally excludes the dogfood CLI/dashboard, live provider catalogs and campaigns,
candidate files, pipeline files, application artifacts, automations, and operator review tooling.
Editable repository installs retain the existing `career-os` dogfood CLI entry point; the participant
wheel exposes `rolevane-product-smoke`, `rolevane-evidence-smoke`,
`rolevane-journey-smoke`, `rolevane-evidence-import`, `rolevane-evidence-propose`,
`rolevane-evidence-interview`, `rolevane-role-evaluate-supplied`,
`rolevane-role-decide-supplied`, and the loopback-only `rolevane-product` UI.

## Evidence service

`career_os.public_api` exports `EvidenceService`, typed evidence records, proposal inputs,
publication requests, and bounded operational errors. Importing it does not load SQLite, the
document parser, private providers, or the synthetic journey. Constructing the service over
injected repository/parser ports performs no IO.

The explicit local composition is
`career_os.infrastructure.evidence.create_evidence_service(instance_root)`. It wires the existing
`EvidenceVault` and document parser; it does not create another writer. The three evidence
commands and the synthetic journey reuse this service. Real onboarding no longer creates a
participant-flow marker; its consent, sources, interview progress and revisions live in the vault.

The operations are `import_document`, `propose`, `edit`, `review`, `verify`, `readiness`,
and `validate_publication`, with consent/read helpers and guided interview support. Mutation
requires existing local consent except the explicit consent-recording operation. Proposals remain
unapproved. Review and verification require an explicit `actor` argument, exact revision and
content hash, and intact source bytes. Verification rejects an older approved revision when a
newer proposal exists; these checks run in the vault transaction. Edits create unapproved revisions.

The caller must obtain actual human confirmation before supplying `actor="local_user"`; an actor
string is not authentication or authority from document/model content. These in-process APIs are
not a remote security boundary.

Expected failures return `EvidenceOperationError` with a fixed, content-free message and one of
`consent_required`, `human_required`, `stale_source`, `invalid_evidence`, or `io_error`.
Programming/integrity faults remain exceptions. Compatibility command/journey callers use
`require_evidence_result` to retain exception-based handling; existing success JSON and exit codes
are unchanged. No new command, model call, provider activation, or event confirmation is introduced.
Request type construction can still raise validation errors before an operation is called.

Storage versioning/backup/restore remains the next #112 slice. This extraction neither migrates
private data nor makes the synthetic web interface a production-ready real-user workflow.

## Synthetic proof

The smoke runner constructs two absent instance roots in one process, verifies construction creates
nothing, explicitly opens separate production-role databases, evaluates two different fictional
official postings without network access, and projects read-only decision items. It fails if an
opportunity ID, database path, instance component, or generated file crosses roots.

From a checkout:

```powershell
python tools/run_product_runtime_smoke.py
```

The clean-room test builds the explicit wheel, installs it with dependency resolution disabled in a
new virtual environment, then runs the dependency-free product, evidence, and complete
participant-journey smoke paths without the checkout on `PYTHONPATH`. Parser tests separately
exercise its declared dependencies. The complete proof uses two isolated instances, keeps model and
external-action routes disabled, creates a fictional decision and content bundle, exports user data,
and verifies that explicit reset removes each instance.

## Dogfood compatibility and deferrals

`dogfood_runtime_adapters.py` remains outside the participant package. It adapts the existing four
candidate context files, confirmed career-event ledger, exact pipeline/identity authority,
application workspace manifests, and injected provider/model policies to the neutral ports. It does
not copy candidate content into the wheel, auto-adopt legacy workspaces, treat a generated artifact
as an external event, or make a pipeline proposal authoritative without the existing confirmation
flow.

The current dogfood CLI and dashboard are not rewired into the participant composition root. Their
no-flag and `--instance-root` behavior remains unchanged. Live-provider activation,
installer/update/backup flows, any separately approved destination repository, and role packs
beyond the reviewed founding-cohort set remain separate work.

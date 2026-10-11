# Generic Candidate Evidence Vault Contract

Status: implemented contract v3, extending issue #33 with extracted-text binding

Owner: product core

Schema: `schemas/evidence-vault.schema.json`

## Purpose

The evidence vault is the candidate-owned source of truth for reusable career evidence. It is
candidate-neutral, local by default, and independent of any one CV, application, provider, or
workspace. It records what is known, where it came from, who owns the outcome, how precise it is,
where it may be used, and the exact local-user decision that approved it.

The vault does not infer that a legacy file, generated draft, or previous application is approved.
It does not publish, apply, message, upload, or activate an external service.

## Versioned entities

Contract v3 supports candidate profile, role timeline, responsibility and decision rights,
achievement/outcome, reusable interview story, typed metric, stakeholder/seniority,
skill/domain/industry/customer environment, leadership/team, compensation or practical constraint,
career objective, development priority, education, genuine gap, forbidden claim, and correction
note records.

Every new immutable revision has a statement, structured details, original and extracted-text
hashes and an exact line span, ownership, confidence, approximation, publication boundary, status,
content hash, creation time,
and common contract metadata. Common metadata carries origin ownership, permitted and prohibited
formulations, role-family relevance, intended channels, typed relationships, uncertainty or
correction notes, retention policy, and optional timeline exclusivity.

Typed metrics require a finite decimal value, unit, period, and scope. Currency, when used, is an
uppercase three-letter code. Population may be recorded separately. Approximate evidence cannot
acquire exact confidence or support an exact publication request.

## State and audit contract

1. A source is imported after explicit local consent. A managed immutable copy records the
   original SHA-256 and bytes separately from the extracted-text SHA-256 and line/page/part
   locations.
2. A proposal creates immutable revision 1 in `unapproved` state.
3. Edits create a new unapproved revision; they never mutate or inherit approval.
4. Only the local user may approve, reject, defer, revoke, or verify an exact revision hash.
5. Approval of a newer revision supersedes the older approved revision.
6. Verification is a separate immutable event and does not change the approved disposition.

Every revision read revalidates source bytes, size, line inventory, and both source hashes bound
into its content hash. Changing the extracted text and its source digest together cannot preserve
an earlier approval. This is an integrity check, not authentication against a host owner who can
rewrite the entire database. Team- or company-origin evidence cannot be edited into sole-personal
ownership, even if editable wording or metric scope changes.

## Manual proposal contract

`rolevane-evidence-propose` accepts one strict version-1 JSON envelope stored inside the private
instance root. The envelope must bind the proposal to the imported source ID, original hash,
extracted-text hash, and exact line span. Unknown fields fail closed, so a proposal cannot carry an
approval instruction or extend its authority. Imported document text and agent- or model-produced
proposal content remain untrusted data.

Each proposal records one explicit origin (`local_user`, `agent`, `deterministic_rule`, or `model`)
and a human-readable reason in the append-only audit log. Every accepted proposal is still created
as `unapproved` and requires a separate local-user review action. The command itself invokes no
model, performs no external action, and prints a content-minimized receipt without the statement,
details, or reason.

An exact proposal retry must match both the original content and its recorded actor and reason;
it cannot relabel an earlier origin. Receipts report the existing disposition and whether review
is still pending. Envelope versions must be integers, and metadata collections must be lists of
their documented item type. Malformed values fail without importing or proposing new evidence.

`rolevane-evidence-interview` exposes ten fixed, model-disabled questions covering timeline,
mandate and decision rights, team scope, measured outcomes, interview stories, stakeholder
seniority, skill/domain evidence, education status, genuine gaps, and practical constraints. A
strict answer envelope must stay inside the private instance. Its exact UTF-8 JSON bytes become the
managed source, and a deterministic rule maps the answer to one unapproved proposal over the full
source line span. Resume progress is derived from immutable vault records, not chat history. A
repeated exact answer is idempotent; a changed answer cannot silently replace the existing record.
An exact retry remains valid after review and reports the current disposition without creating
another approval or audit event. Answer lookup, managed source creation, proposal, and audit run
inside one serialized vault transaction. A failed answer leaves none of these partial writes;
concurrent answers to the same question cannot create duplicate sources or proposals.

The local review screen displays all common metadata alongside the source and statement before
approval, including permitted wording, channel restrictions, relationships, and uncertainty.
Metadata is escaped as text and remains bound to the exact revision hash being approved.

Evidence context fingerprints include every latest approved evidence ID, revision, and content
hash, as well as the original source hashes. Evidence additions, edits, or disposition changes
invalidate derived role decisions and preparation state even when source bytes are unchanged.
Reading stale participant state hides its current decision/bundle projection without deleting
retained history or artifact files; it does not rewrite a confirmed external-event ledger.

## Publication readiness

`EvidenceVault.validate_publication()` fails closed when selected evidence is missing, not
approved, missing its legacy extracted-text binding, revoked, superseded, private for the requested
channel, unknown or disputed, stale, or a private control kind. It also blocks ownership or
approximation elevation, prohibited or
unapproved formulations, unintended channels, unresolved conflicts, and exactness language on an
approximate request. A successful result includes stable source references. It is readiness
evidence only; it does not authorize or perform publication.

`classify_for_role()` separates strong verified evidence, transferable evidence, genuine gaps,
conflicts, and missing required identities without turning absence into a positive claim.

## Timeline semantics

Overlapping work is valid by default because portfolio careers, part-time teaching, advisory work,
leave, and concurrent employment are legitimate patterns. An overlap blocks only when both records
explicitly share a timeline group and both are marked mutually exclusive. Inverted dates always
fail validation.

## Export, deletion, and retention

The export snapshot includes contract version 3, source metadata without source content, each
latest revision and its contract metadata, append-only audit events, and a deletion inventory.
`deletion_inventory()` enumerates the database and identifiers/counts for metadata, consent
receipts, sources, revisions, and events. Credentials and remote telemetry are absent.

Retention is recorded per revision as until user deletion, until superseded and exported, or
session only. A later storage issue implements physical retention jobs; v2 records the policy and
exposes the complete inventory instead of silently deleting evidence.

## Legacy compatibility and migration

`rolevane-evidence-migration-dry-run` inventories the four legacy context authorities and optional
schema-v1 `CANDIDATE_EVIDENCE.json`. Its report contains paths, sizes, hashes, opaque fingerprints,
boundary mappings, and missing v2 semantics. It excludes statements and headings, creates or
changes no vault, and marks every candidate unapproved.

| Legacy | Contract v2 |
| --- | --- |
| `do_not_use` | `private` |
| `direct_application` | `application` |
| `direct_application_and_interview` | `application_and_interview` |
| `interview_only` | `interview` |
| `public` | `public` |

SQLite storage version 4 is independent of export contract version 3. Fresh initialization creates
the current schema, but ordinary writes to older storage fail and require the explicit previewed,
offline, whole-workspace-backed-up `rolevane-instance upgrade` route. It calls the existing
numbered migrations in `infrastructure/evidence_storage.py` only after verifying the backup;
their immutable identities are recorded and checked on subsequent opens. They accept only a fresh
database or supported v1/v2/v3 state already bound to the exact instance. Unknown versions,
missing ownership, foreign instances, and invalid
migration identities are rejected before schema or data mutation, including on read paths.

The v1/v2 bridge adds missing contract, source-provenance, and audit-reason columns inside one
exclusive transaction. Concurrent fresh initializers serialize. An interrupted or failed upgrade
rolls back all DDL, backfill, and version changes and can be retried. Source bytes, original hashes,
revision hashes, consent, and approval events are never rewritten. Legacy CRLF/BOM text retains its
original extraction and line references on an exact re-import; the parser's normalized text does
not replace already-reviewed source content. Line expansion is bounded before allocation.
The bridge also repairs the plain-text format default left on a completed legacy Markdown
upgrade only when the retained raw bytes and hashes prove the original Markdown source.

Migration 4 adds a nullable revision `extracted_hash` without backfilling it or changing migration
3's identity. Legacy revision hashes and audit events remain unchanged. When the original and
extracted bytes already share the same hash, the original binding also proves the reviewed text;
those approvals and exact proposal retries remain usable. Export reports that proven hash.

Otherwise export reports `extracted_hash: null`, and the historical approval remains visible but
cannot be resolved, verified, published, exported as approved evidence, or classified for a role.
Readiness blocks it and the review queue includes it. Save a new unapproved revision and review
its current extracted text explicitly; only approving that new revision supersedes the historical
approval. Reads do not upgrade storage, invent an earlier binding, or grant approval.

This migration is additive, but the public lifecycle nevertheless requires a complete verified
pre-upgrade backup and exact local-user confirmation. Future irreversible changes need their own
reviewed conversion contract. Unbound or corrupted stores require an
explicit recovery workflow, not guessed ownership. Private content migration remains a separate
dry-run/approval workflow and never grants evidence approval.

On POSIX systems, the data directory is restricted to mode `0700` and the database to `0600`
before it can receive private bytes, including when opening an existing writable vault. Windows
uses the account's directory ACL; POSIX mode bits are not a Windows ACL guarantee.

## Synthetic acceptance patterns

Tests use three wholly fictional patterns: an operator leader with team metrics and incomplete
education, a portfolio professional with concurrent roles and EUR revenue, and a bilingual career
changer with transferable skills and a real industry gap. They cover different career shapes
without containing or depending on the repository owner's personal facts.

## Deferred work

The bounded local-only parser covers UTF-8 text/Markdown, the documented text-searchable PDF subset,
and standard visible DOCX text parts with part-aware provenance. The consent-bound
`rolevane-evidence-import` command persists the managed source and content-free inventory without
creating a proposal. The strict manual proposal command and fixed guided evidence interview are
implemented; deterministic document extraction, adaptive questioning, and the browser file picker
remain deferred. OCR is outside the open-source v1 contract. Production
backup, physical retention jobs, and database upgrade operations remain issue #37. Public
repository extraction remains issue #107. Those issues must consume this contract rather than
create another evidence authority.

# Private Workspace Lifecycle

## Initialize And Check

Use Python 3.11 or newer on Windows or Linux, in the same environment used to install
Berufa and its declared dependencies. Other platforms are not supported by these lifecycle
commands. Current local and CI proofs cover Windows and Linux; the wider release matrix remains
a release-engineering gate. No account, hosted service or model is required.

Select a new private directory outside every Git checkout and Python environment. Its parent
must already exist. Initialization never adopts or overwrites an existing directory, including
an empty one. Run the fictional smoke/demo in a different workspace before importing real data.

```bash
rolevane-instance --instance-root "$HOME/career-workspace" init
rolevane-instance --instance-root "$HOME/career-workspace" doctor
```

Init creates an empty current-version evidence vault, `.gitignore` covering private content,
matching candidate-neutral layout identities in the root and vault metadata, and `inbox`,
`applications`, `exports`, `tmp`, `data/runtime` and `data/logs`. The identities contain no path,
candidate data or secret and survive complete backup/restore. Init does not grant
processing consent, import files, propose evidence or seed synthetic facts. On Windows, choose
a parent with private ACLs; `.gitignore` is not an OS access control.

Doctor returns fixed check names/statuses and `ready`, not paths, filenames, candidate content,
hashes or raw exception text. It checks the runtime, installed package version, Berufa's
declared dependency health using local-only `python -I -m pip check`, initialized layout, and
the bounded complete workspace inventory/default evidence database. It makes no model calls,
uploads or Internet requests. Dependency output is not returned, and failures from unrelated
packages in a shared environment do not make Berufa unhealthy. A deliberately `--no-deps`
installation can run some commands but correctly fails the dependency check.

Stop writers before diagnostics that inspect the database. `invalid_or_busy` includes locked,
corrupt, unsupported, oversized or linked state; inspect locally rather than posting raw files.
`setup_required` means the init layout or private ignore file is missing/changed; doctor does
not repair or overwrite it. It does not validate every application/interview/event contract;
use those workflow validators as well. Do not treat an `ok` report as a security certification.
An existing outside-Git workspace upgraded from a supported pre-init schema can report
`legacy_compatible` only when both init identities and the private ignore are absent and every
existing reserved layout path is a directory. Current commands create missing subdirectories as needed, and
doctor deliberately does not rewrite that workspace merely to adopt the new empty layout.
If either init identity exists, both must match and the complete initialized layout/private ignore
must remain intact. Doctor also checks the exact current managed tables, columns, keys, required
constraints, relationships, approved object set and SQLite integrity before reporting storage `ok`.

For an already running participant demo, optionally add `--listener-port PORT`. This makes one
bounded HTTP health request to literal `127.0.0.1`, with no proxy or redirect. The server's
root fingerprint must match the selected workspace. This is a diagnostic identity check, not
authentication against a malicious local process. No server is started or stopped by doctor.

The typed `InstanceService` is exported from `career_os.public_api`; compose it with
`career_os.infrastructure.instances.create_instance_service(instance_root)`. Initialization
requires a trusted `actor="local_user"`. Upgrade confirmation also requires `offline=True`;
model output and imported text cannot supply that authority.

## Explicit Storage Upgrade

Ordinary evidence writes no longer upgrade existing storage implicitly. A legacy v1/v2/v3
vault remains readable where its original contracts permit, but writing requires this explicit
upgrade to v4. Back up package/version information separately so the previous package can be
reinstalled in another environment. There is no automatic package updater or schema downgrade.

Stop all commands, agents, listeners, editors and sync processes first. The backup destination
must be new and separate from the workspace:

```bash
rolevane-instance --instance-root "$HOME/career-workspace" upgrade --backup-destination "$HOME/career-before-upgrade"
rolevane-instance --instance-root "$HOME/career-workspace" upgrade --backup-destination "$HOME/career-before-upgrade" --confirm-upgrade PREVIEW_SHA256 --offline
rolevane-instance --instance-root "$HOME/career-workspace" doctor
```

Review the versions, migration identities and destination; use the returned confirmation
fingerprint. It binds the complete workspace inventory and destination. The confirmed operation
creates and verifies a whole-workspace backup **before** mutating storage, reacquires database
exclusivity and rechecks all source bytes, then runs the existing numbered migrations in one
transaction. Unsupported executable/foreign schema objects are refused. Original source,
revision, consent and audit identities are preserved; missing legacy extraction bindings do
not become approvals. Private CareerStore and pipeline migrations are outside this command.
Source-volume free-space preflight queries the actual evidence-database filesystem and is based
on the evidence database and its transaction/journal
headroom, not unrelated exports already copied to the separate backup volume.

A failure rolls back the database transaction and retains the backup. A changed source needs
a new preview and a new backup destination; existing backups are never overwritten. If the
schema is already current, confirmation returns `changed: false`, creates no backup and does
not validate an old token because it performs no mutation. This makes completed retries safe.
File-only writers do not share the SQLite lock: the offline prerequisite remains essential.

After interruption, inspect locally and follow the recovery guidance below; never delete SQLite
journals to force a retry. Restore a verified pre-upgrade backup into a **new** workspace and
use the previous supported package there if rollback is needed. Retain the original and backup
until the restored workflow is verified. Unknown/newer schemas are never silently repaired.

## Offline Recovery

`rolevane-instance` also inventories, backs up, verifies and restores a private workspace.
This is an **offline directory snapshot**, not a live backup, encrypted archive, user-data
export, synchronizer or repair tool. Stop every Berufa command, agent, listener, editor and
sync process that could write to either workspace or backup before using it. `--offline`
records your acknowledgement of this prerequisite; it does not stop those processes for you.

Use private directories outside the source repository and Python environment. Create the
destination's parent directory first. A backup or restore destination must not already exist,
even if empty, and cannot contain or be contained by its source. Filesystem/home roots,
symlinks, junctions/reparse points, hard-linked files and special files are refused. Names must
be portable across Windows and POSIX: no case/Unicode-normalization collisions, reserved device
names, trailing spaces/dots or traversal components. This same portability boundary applies to
supplied opportunity IDs used by application, interview and workspace paths: `con`, `prn`, `aux`,
`nul`, `com1`-`com9` and `lpt1`-`lpt9` are rejected on every platform. Existing persisted state
with one of these IDs is diagnosed or refused; it is never silently renamed or migrated. Rename
unsupported user-owned files deliberately before retrying; the tool never silently omits them.

## Commands

After installing the package, these commands work in Bash and PowerShell using `$HOME`:

```bash
rolevane-instance --instance-root "$HOME/career-workspace" inventory
rolevane-instance --instance-root "$HOME/career-workspace" backup --destination "$HOME/career-backup-2026-09-04" --offline
rolevane-instance --instance-root "$HOME/career-workspace" verify --backup "$HOME/career-backup-2026-09-04"
rolevane-instance --instance-root "$HOME/career-restored" restore --backup "$HOME/career-backup-2026-09-04"
```

The last command is a read-only preview. Review its complete inventory and destination. Repeat
with the returned `confirmation_fingerprint`, not the backup fingerprint:

```bash
rolevane-instance --instance-root "$HOME/career-restored" restore --backup "$HOME/career-backup-2026-09-04" --confirm-restore PREVIEW_SHA256 --offline
rolevane-instance --instance-root "$HOME/career-restored" inventory
rolevane-workspace --instance-root "$HOME/career-restored" list
rolevane-focus --instance-root "$HOME/career-restored"
```

Replace `PREVIEW_SHA256` with the exact preview value. A different backup or destination needs a
new preview. Keep the original workspace and backup until the restored workspace has passed
the workflow-specific validators. Focus may still report unrelated unsupported public inputs;
this does not replace application/interview validation or a full release acceptance test.

The installed `career_os.public_api.InstanceRecoveryService` exposes the same operations. Compose
it with `career_os.infrastructure.recovery.create_recovery_service(instance_root)`. Backup and
confirmed restore require `offline=True` and a trusted caller's `actor="local_user"`. Model text,
an imported file or a backup manifest cannot grant this authority.

## What Is Retained

The snapshot contains every ordinary file and every directory, including empty directories:

- `data/evidence-vault.db`: managed source originals and extracted content, all evidence revisions,
  consent, review/disposition events, migration identities and audit history.
- `data/runtime/opportunities/`: supplied posting requests, dossiers and decision histories.
- `data/runtime/`: retained offers, confirmed Outcome Learning V2 records, prepared-artifact
  reviews and any other ordinary local files.
- `applications/`: every application/interview version and its manifests and generated content.
- User-owned inbox, preferences, context, exports, logs and any other ordinary workspace files.

This is intentionally **not a credential-filtered export**. If you placed secrets, temporary
files or sensitive content inside the workspace, the backup retains them too. Inventory output
contains private filenames and hashes. Do not attach backups, manifests or inventories to
public issues. No content is uploaded. External original documents, files referenced only by
path, separately stored credentials, installed code, browser downloads and provider-side state
are not followed or copied; preserve those separately under your own retention policy.

The default public evidence database is the only database with a supported restore binding.
Another SQLite database, including a private CareerStore or custom evidence-database location,
is refused rather than copied into an apparently usable but incorrectly bound instance. A
workspace without a database can be backed up before evidence onboarding. Known preparation
staging directories and incomplete recovery markers are refused.

## Format And Integrity

A complete backup directory contains exactly `backup.json` and `workspace/`. The canonical
schema-1 manifest lists sorted relative paths, file sizes and SHA-256 hashes, directory entries,
the original instance-root hash and evidence storage version. The receipt's `backup_fingerprint`
is the SHA-256 of the complete canonical manifest bytes. Retain that receipt separately when
you need an independent integrity reference. Checksums detect changes; they are not signatures
or proof of who created a backup. Use only your own trusted local backups.

Limits are 10,000 total entries, 512 MiB per file, 2 GiB of file content and a 4 MiB manifest.
Recovery checks for twice the content size plus 16 MiB of free disk space before creating a
destination; a later disk-full or I/O error can still interrupt the operation. Files use private
creation modes on POSIX. Windows permissions inherit the destination parent's ACL; choose a
private parent and use OS access controls/encryption as appropriate. Berufa provides no
encryption or protection against a malicious process running as your user.

The source SQLite database is checked and held in an exclusive, non-writing transaction during
backup. A busy database fails visibly. Workspace bytes are rechecked after copying. These
checks do not make file-only writers, arbitrary editors or sync tools participate in a shared
transaction: the offline prerequisite remains mandatory.

Only a closed rollback-journal-mode evidence database with no journal/WAL/SHM sidecars is
supported. The normal public vault uses this mode. If a database has sidecars or has been put
in WAL mode, do not delete them manually. Stop writers, retain an untouched safety copy and use
SQLite's recovery/checkpoint procedure or qualified local support to close the database cleanly
and return it to rollback-journal mode before retrying.

Restore verifies every payload entry against the manifest before writing. It validates the
retained schema and original instance binding, then updates only `vault_meta.instance_root_hash`
in the new copy inside a transaction. Storage versions 1 through 4 retain their original schema;
restore does not migrate them or grant new evidence approval. Unsupported schema objects,
invalid integrity/relationships and unknown versions are refused. The original absolute path
is not reconstructed, so the binding can be restored across Windows and POSIX paths.

All non-database bytes remain identical. The restored database file hash necessarily changes
because its root binding changes; its user records and histories remain unchanged. A new
inventory therefore has a different instance/database fingerprint. Normal commands retain
their own version/authority checks. Legacy writes require the explicit upgrade described above.

## Portable User-Data Export

Stop every writer, choose a new directory outside all workspaces, repositories and Python
environments, then run:

```bash
rolevane-instance --instance-root "$HOME/career-workspace" export --destination "$HOME/career-user-export-2026-09-05" --offline
```

The destination contains `career-os-user-data.json`. The receipt records its byte size and SHA-256;
verify that checksum after copying it. The schema-1 UTF-8 JSON is portable and inspectable, but it
is **not** a restore format. Use a verified complete backup for recovery.

The export includes managed source originals as base64, normalized source text and inventory,
consent receipts, every evidence revision and audit event, supplied role preferences, requests,
dossiers and decisions, saved offer comparisons, confirmed Outcome Learning V2 events, every valid
application/interview version and generated document, artifact reviews, and recognized local
metrics. It validates each typed store and refuses unknown state in a managed namespace instead of
silently claiming completeness. The source inventory is compared before and after output creation;
a changed workspace leaves the new destination marked incomplete.

Dedicated configuration/credential files, unimported inbox documents, prior exports, temporary
files, logs, database/layout bindings and writer-lock metadata are excluded. Their counts and byte
sizes are reported by category without exporting filenames or values. The command does not search
or redact arbitrary strings inside user-authored career text, posting text, notes, generated drafts
or imported documents. Treat the export as highly sensitive private data and inspect it before
sharing. No content is uploaded and no external event is created.

The default export remains strict: every supplied opportunity dossier must match a fresh evaluation
against the current vault and bundled rules. If older retained inputs no longer satisfy that check,
use the explicit archival mode only to preserve their captured bytes:

```bash
rolevane-instance --instance-root "$HOME/career-workspace" export --destination "$HOME/career-historical-export" --offline --historical-archive
```

This mode places supplied request and dossier JSON in a separate schema-1 archive collection as
base64 with its original UTF-8 encoding, byte length and SHA-256. The collection is marked
`historical_integrity=unverified`, `current_readiness=not_evaluated` and
`current_decision_eligible=false` because the original complete evaluation inputs were not retained.
It does not promote an entry into current opportunities, synthesize provenance or relax validation
for evidence, decisions, offers, workspaces, outcomes or metrics. It remains non-restorable; use a
verified complete backup for recovery.

## Delete One Initialized Workspace

Create and verify any backup and export you intend to retain **outside** the selected workspace.
Stop every writer. First preview the complete inventory without deleting anything:

```bash
rolevane-instance --instance-root "$HOME/career-workspace" delete
```

Review every file/directory and the retained-outside-workspace statements. Then repeat with the
exact current preview fingerprint:

```bash
rolevane-instance --instance-root "$HOME/career-workspace" delete --confirm-delete PREVIEW_SHA256 --offline
```

The fingerprint binds the initialized root identity and complete checksummed inventory. Any added,
removed or changed entry invalidates it. Deletion accepts only an intact current initialized
workspace, reacquires the database guard, rechecks every entry, closes SQLite before removing the
database on Windows, and unlinks files/directories explicitly from deepest children upward. It does
not use recursive filesystem deletion and never follows links. A busy database or linked, special,
unknown, changed or stale state fails visibly. A filesystem failure can leave a partially deleted
selected workspace for local inspection; the command never continues into a sibling or parent.

Installed package files, outside source documents and separately stored backups/exports are not
touched. Files inside the selected workspace, including excluded inbox/configuration/log/temp
content, are removed because the preview covers the whole selected root. This is logical deletion,
not guaranteed erasure of disk sectors, filesystem snapshots, cloud-sync copies or independent
backups. Uninstalling the Python package still does not delete private workspaces, and deleting a
workspace does not uninstall Berufa.

The typed `InstanceDataService` is exported from `career_os.public_api`; compose it with
`career_os.infrastructure.instance_data.create_instance_data_service(instance_root)`. Export and
confirmed deletion require `offline=True` and trusted `actor="local_user"` authority. Agent output,
document content and previously displayed fingerprints cannot provide that authority.

## Fictional companion state

The optional companion uses the existing vault's `vault_meta` key `companion_m1_state`,
with namespace version 1. SQLite storage remains version 4. There is no second business
database and no automatic initialization or upgrade. Instances without the namespace
keep their existing recovery and export behavior. Unknown or malformed namespace
versions fail lifecycle validation before creating an export or restore destination.

Only an initialized instance containing the sealed fictional persona is supported.
From an independent interactive terminal, run:

```bash
rolevane-companion-review /absolute/path/to/fictional-instance
```

Use `setup` explicitly, then `inspect` and `grant` to review an exact object revision,
fields, purpose and ChatGPT recipient. Terminal grants last 24 hours. The API permits
at most 30 days. Each grant defaults to current data; historical reads require a
separate historical grant and return a historical label. Unshared objects do not
appear in the companion index. A changed revision needs a new grant.

Model tools can stage a pending request, but cannot approve it or grant access.
Use `review` and `confirm` in this terminal to inspect the complete pending input and
preview. Confirmation is a one-use capability bound to the current local session,
instance, connection generation, operation, input digest and reviewed basis. It
expires after ten minutes, and pending preparation expires after 24 hours. Tokens
are never displayed or passed in command arguments. The writer checks the reviewed
basis again; it refuses changed evidence rather than approving a fresh preview.
Unknown offer amounts remain unknown and produce no numeric verdict.

The same request and idempotency key return its retained state for 30 days. A changed
request conflicts. Reusing an older retained key conflicts rather than creating a
second write. Admission is durable before a business writer runs. An interrupted
write remains `executing`; `recover` checks retained public artifacts and never
repeats the business writer. Ambiguous results stay `execution_unknown`. Files and
SQLite are not one atomic transaction. Disconnect or revocation cannot undo a
business operation already admitted for execution.

Filtered export includes pending inputs, timestamps, states and receipts, excluding
grants, idempotency keys, confirmation hashes and session material. Complete offline
backup includes the namespace. Restore rebinds the instance, rotates its generation,
disables connectivity, removes grants and confirmation capabilities, and cancels
prepared requests. Completed receipts and executing history remain retained, with
restored history labelled. `reconnect` requires fresh grants and confirmation;
restored preparation cannot acquire authority. Existing evidence identities,
timestamps and review history stay intact. Deleting the exact selected workspace
also deletes this namespace; outside backups and exports remain separate copies.

Both stdin and stdout must be interactive. This is an explicit fictional workflow,
not an isolation boundary against an unrestricted agent using the same OS account.
Real-data connectivity remains disabled. Connected ChatGPT Skills and transport
packaging require their own acceptance proof; this CLI does not establish host
compatibility or authorize real candidate data.

## Failures And Remaining Lifecycle Work

Complete recovery includes planned-task revisions and the canonical content-document archive.
Task IDs, revisions, request identities, timestamps and terminal states are preserved. Restore
changes only the task ledger's instance binding to the new selected root. It never reopens a
completed task or creates an employer event. Current initialization, consent, exact offline
confirmation and the existing safe-root rules still apply to ordinary later writes.

Document recovery retains every immutable version, original format byte, provenance, selected
evidence basis and exact human review, including rejected and stale versions. Historical review
disposition and current evidence validity are separate. Restore does not turn stale content
into current content. Both user-data export modes include document metadata, exact base64
format bytes and all review history, plus full task history; they exclude the task root binding.
Malformed ledgers, altered document bytes, links and incomplete inventories fail closed.

Standalone documents need no opportunity or pursue decision. Import/revision, exact review and
selected-file employer export are separate commands under `rolevane-document`. An employer
export contains only explicitly selected artifact formats, without internal manifests or notes.
The canonical archive supports at most 100 documents, 500 versions, 64 MiB retained artifact
bytes and 32 MiB extracted UTF-8 text. Each imported format is bounded to 2 MiB. These bounds
are checked on reads and new publications; exact retries do not create another version.

The command returns 0 on success, 1 for a refused/failed operation and argparse's 2 for invalid
command syntax. Failures do not print source content or exception paths. Check the prerequisite,
destination existence, SQLite state, inventory limits and free space locally.

An interrupted initialization/backup/restore is retained with `.career-os-recovery-incomplete`; do not use or
run Berufa against it. Verification refuses incomplete backups. Recovery does not overwrite
or recursively clean up that directory: inspect it locally and choose a new destination for
the next attempt. If the initial marker cannot be written, recovery attempts to remove only its
new empty directory; it never removes files introduced before that failure. If the filesystem
also prevents that cleanup, an uninitialized target can remain and must be inspected locally.
A crash or filesystem failure can require OS-level recovery; no cross-device
atomicity or crash-proof storage is claimed. Original workspace and backup remain untouched.

The participant UI's synthetic reset/export remain separate demo-only operations and are not
recovery formats. Final release artifact provenance and the integrated external-user acceptance
journey remain release gates. Uninstalling the Python package does not delete any workspace,
backup or exported data.

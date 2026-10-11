# Privacy

Rolevane is local-first software. Its Python package has no hosted account, candidate-data
service, silent telemetry, advertising, or embedded model/provider connection. The supported core
writes private state beneath a user-selected local instance root and serves its optional browser UI
on literal IPv4 loopback only.

Local-first is not a promise that data can never leave the device. Installing dependencies uses a
package index, public contribution/CI activity is handled by GitHub, and explicitly confirmed
official-posting lookup sends the selected board/job identity and ordinary connection metadata
to Greenhouse. It sends no candidate records or credentials. Invoking an optional conversational
skill processes selected context through the user's configured agent/model provider. Any future
embedded model or other source provider is another separate user-enabled transfer. See
`docs/DATA_AND_MODEL_POLICY.md` for the exact data classes and restrictions; use the documented
manual commands when no model-provider transfer is desired.

## User responsibilities

- Create private instances outside this public source checkout. Do not commit a resume, contact
  details, compensation, applications, messages, database, export, backup, `.env`, token, or
  provider response to a public fork.
- Protect the device and backups with appropriate operating-system access controls and encryption.
  Rolevane does not encrypt the workspace or defend against malware running with the same user
  privileges.
- Review every export and generated artifact before sharing it. Approved evidence can still be
  confidential or inappropriate for a particular recipient.
- Do not attach private career data to public issues or pull requests. Use fictional minimal
  reproductions. Security reports follow the private route in `SECURITY.md` once that policy is
  enabled for the public repository.

## Storage, export, backup, and deletion

The instance may contain imported source content, evidence revisions and audit events, participant
state, generated artifacts, and user exports. The portable user-data export is private, inspectable
JSON, not a restore backup or redacted bug report. It includes managed original and normalized
sources, full evidence/decision/outcome histories and generated content. It excludes dedicated
credential/configuration state and internal storage/security metadata, but does not promise to find
secrets typed into free-text career data. Inspect and protect it as sensitive private data.

For backup, stop all workspace writers and follow [Offline Workspace Recovery](docs/INSTANCE_RECOVERY.md).
The checksummed private snapshot includes all ordinary workspace files, including any secrets the
user stored there; it is not a credential-filtered export. Confirmed restore validates the snapshot
and rebinds only the evidence database's instance metadata to a new private path. Copying the
database directly is not a valid different-path restore. Do not merge instances by copying SQLite
files between them. There is no backup encryption or automatic upload.

Real initialized-workspace deletion previews the complete checksummed inventory and requires its
exact current confirmation fingerprint plus an offline local-user acknowledgement. It removes only
the selected root and reports that installed code, outside source documents and separately retained
backups/exports remain. The synthetic participant reset remains a separate demo-only operation.
Neither route removes browser downloads, sent documents, removable-media copies, filesystem/cloud
snapshots, package caches, or provider-side records. Logical file deletion is not guaranteed sector
erasure; remove independent copies under their own retention process.

## Incidents

If private data or a credential reaches GitHub or a release, stop publication, restrict access,
rotate the credential, inventory forks/logs/artifacts/caches, and use the reviewed history-removal
process. Deletion cannot guarantee removal from third-party copies. Follow
`docs/INCIDENT_RESPONSE.md` without reposting the exposed content.

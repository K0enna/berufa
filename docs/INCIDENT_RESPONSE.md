# Incident Response

This runbook is for maintainers and local users. Preserve sanitized evidence about what happened,
but do not copy the exposed resume, message, token, database, or raw prompt into another issue,
chat, log, or incident document.

## Immediate priorities

1. Stop the affected publication, workflow, provider, model call, or local process.
2. Identify the exact account, repository, release, file, data class, credential, and time window.
3. Restrict access before investigating further. If a credential may be exposed, revoke or rotate
   it immediately; deleting a file is not credential remediation.
4. Preserve only hashes, paths, versions, timestamps, and a sanitized reproduction needed to
   understand the cause. Keep necessary sensitive evidence local and access-restricted.
5. Notify the repository owner through the approved private route. Do not open a public security
   issue containing exploit details, candidate data, or secrets.

## Private data in GitHub or a public release

1. Pause pushes and releases. Restrict/delete the issue, pull-request comment, artifact, release,
   or repository where the hosting platform permits it.
2. Inventory forks, clones, caches, Actions logs/artifacts, notifications, package indexes, and
   releases that may contain the material. Do not claim complete removal from third-party copies.
3. Revoke exposed credentials and review related account activity.
4. Decide whether Git history remediation is required. Perform it from a reviewed plan; rewriting
   the current branch alone does not remove forks, tags, cached views, or released packages.
5. Generate a new public candidate from the approved allowlist, scan it, and use a fresh public
   history. Never use the contaminated private checkout as the public repository history.
6. Record a private incident summary with affected classes, containment, notifications, remaining
   limits, and the control/test added. Avoid retaining the original private payload in the report.

## Vulnerability or malicious document

1. Stop processing the input and preserve its hash. Keep the original private unless it is already
   a wholly synthetic malicious fixture.
2. Establish the affected version and reachable entry point without opening unrelated private
   files or uploading the input to an external scanner.
3. Build the smallest fictional reproduction possible. Do not commit a real resume or posting.
4. Fix the enforcing boundary, update dependencies where needed, and add a regression tied to the
   violated invariant and resource bound.
5. Publish a fixed version and security advisory through the approved private-reporting process.
   State affected/safe versions and workarounds without promising complete security.

## Unexpected model, provider, or external action

1. Disable the integration and preserve its local policy receipt, provider request identifier, and
   exact configured purpose/data categories. Do not retain raw private prompts by default.
2. Revoke the credential or budget authority if misuse is possible.
3. Determine whether private data left the device and consult the provider's deletion/retention
   process. Record what cannot be verified.
4. Treat generated drafts, browser state, and local artifacts as non-events. Do not send a second
   message or alter the pipeline to conceal or "correct" an unexpected action.
5. Ask the user before any communication, external remediation, or confirmed-event change.

## Vulnerable or compromised release

1. Stop recommending the affected version and disable the release workflow if its integrity is in
   doubt.
2. Compare the released files with the public-source manifest, tag, CI result, and published
   checksums. Treat a mismatch as compromise until explained.
3. Revoke release credentials/tokens, remove mutable artifacts where supported, and preserve the
   exact affected digest and version.
4. Publish a corrected version from a clean export and exact reviewed commit. Do not silently
   replace an artifact under the same version.
5. Publish an advisory with affected versions, impact, upgrade/removal guidance, and residual
   uncertainty. Yank an unsafe package version when the registry supports yanking, but assume prior
   downloads remain available.

## Backup, restore, and deletion failure

1. Stop writes to the instance. Make a protected copy only when necessary to avoid further loss.
2. Compare the instance and backup inventories; never merge two workspaces by copying database
   files into one another.
3. Restore to a new private directory and validate it before replacing or deleting the original.
4. If reset fails, do not broaden deletion commands. Inspect the refused path, link/reparse point,
   or unexpected top-level entry and obtain user confirmation for a narrower remediation.
5. Record retained copies outside the instance, including exports, browser downloads, removable
   media, cloud backups, sent documents, and provider-side records.

## Closeout

An incident closes only when containment is verified, affected versions/data classes are known,
credentials are revoked where required, user-facing limitations are stated, and the regression or
process control has an owner. High or critical unresolved impact blocks a public release.

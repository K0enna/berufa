---
name: rolevane-setup
description: Explain the reviewed public Berufa setup contract, bundled fictional demonstration, and content-free validation route. Real workspace lifecycle work stays in the user's local terminal.
---

# Set Up Berufa

## Agent provider boundary

Invoking this skill uses the user's configured conversational agent/model provider. In this
profile, it may receive only the allowed classes in the boundary below.

## Experimental public preview boundary

This experimental manual-local-data preview uses a no-untrusted-source conversational profile. The
skill may receive only `reviewed_package_source` from the exact reviewed Berufa package/repository
revision, wholly fictional bundled fixtures/demos, or content-free validation. It must never receive
or process arbitrary public-source, web, issue, PR, tool, or model content or output; real candidate
documents or extracted spans; real or supplied postings or URLs; evidence, applications, interviews,
offers, outcomes, private paths; user-provided summaries of those data; request JSON;
hashes/fingerprints; confirmations; or terminal output.

Use only the reviewed public package contract, a bundled fictional demo, or content-free validation.
For real workflows, provide generic placeholder commands only; the agent must not execute them,
receive values or results, or ask the user to paste, summarize, describe, or otherwise provide them.
No host sandboxing is claimed. No prompt-injection-resistance or complete protection claim is made.
A user or host can bypass package policy. Broader private-data conversational use remains
release-blocked.

## Plain-language route

Explain the public installation contract, fictional participant demo, and content-free checks in
ordinary language. Do not ask the user to author JSON, hashes, schema fields, fingerprints, or actor
labels. If the user denies or cancels the preview-safe route, continue only with the
no-model/manual route and do not request real paths, inventories, values, or results.

The fictional/content-free routes may use only these public commands:

```text
rolevane-skill-validate skills
rolevane-product-smoke
rolevane-evidence-smoke
rolevane-journey-smoke
rolevane-product --instance-root DEMO_PATH
```

For any real workspace operation, these commands are documentation only. The agent must not execute
them or receive their values/results; the user runs them in the local terminal:

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

Review every private inventory and output in the user's local terminal. Never request or consume
that private inventory. Never run those inventory-producing commands through the agent. Keep paths,
hashes, fingerprints, confirmations, and terminal output there; the agent never receives them.

## Local contract notes

The public validator compares the manifest with the installed package version. A fictional demo uses
a separate new `DEMO_PATH`; do not pre-initialize it with `rolevane-instance`. The real local
workspace lifecycle is documented in `docs/INSTANCE_RECOVERY.md` and remains a deterministic
user-operated route. Stop when the public explanation, fictional demo, or content-free validation
is complete, or when any real value would need agent handling.

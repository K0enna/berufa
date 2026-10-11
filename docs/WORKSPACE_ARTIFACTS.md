# Prepared Workspace Review

Use the installed command against an explicit private instance:

```bash
rolevane-workspace --instance-root PATH list
rolevane-workspace --instance-root PATH inspect --kind application --opportunity-id example_role --fingerprint PLAN_SHA256
rolevane-workspace --instance-root PATH inspect --kind interview --opportunity-id example_role --round-id first_round --fingerprint PLAN_SHA256
```

The public `WorkspaceService.list/inspect/review` API uses the same canonical application and
interview validators. No model, provider, web server, arbitrary file reader or private-workspace
import is involved. JSON contains private content; do not post it publicly.

## Discovery and Inspection

Discovery reads only public application schema-3 versions and interview schema-2 versions under
their documented `applications/` paths. Legacy four-digit ASCII year directories belong to the
private year/slug namespace and are skipped without reading their contents; they cannot be public
opportunity IDs, which start with a letter. At most 500 versions and 2000 directory entries are
examined. It lists every retained version and selects the uniquely newest preparation timestamp
per application or named interview round. Tied newest timestamps select nothing and produce a
diagnostic. Selection is a deterministic projection, not a stored pointer or user preference.

Each version reports exact reference, relative path, preparation time, manifest hash, integrity
and local review. Only selected versions are checked against current evidence/dossier/decision
authority during listing; older versions have `current: null`. Inspect any exact reference to
validate its current basis and read its fixed, bounded artifact files. No arbitrary path is
accepted. Intact does not mean current, reviewed or ready for external use.

Uncommitted staging, unknown layouts, incomplete or damaged versions remain visible as
diagnostics. Other readable interview rounds remain listed alongside malformed sibling diagnostics.
Discovery with diagnostics exits 1 and reports `partial: true`; exceeding bounds or corrupt
review history returns a bounded error, never a false empty inventory. Inspection of
intact but stale content returns `current: false` and exit 1. Nothing is repaired or deleted.

## Exact Local Review

After inspecting the full content, place this schema-1 request inside the private instance:

```json
{
  "schema_version": 1,
  "workspace": {
    "kind": "application",
    "opportunity_id": "example_role",
    "fingerprint": "REPLACE_WITH_EXACT_PLAN_SHA256",
    "round_id": null
  },
  "disposition": "accepted",
  "note": "Reviewed the exact local content.",
  "interview_event_id": null
}
```

All fields are required. Kind is `application` or `interview`; interviews require the exact
round ID. Disposition is `accepted` or `changes_requested`. Notes allow 2000 characters and are
user-authored, not evidence. Request files are bounded to 64 KiB. Use absolute file paths or
paths relative to the working directory, contained inside the instance.

```bash
rolevane-workspace --instance-root PATH review --request PATH/inbox/review.json
rolevane-workspace --instance-root PATH review --request PATH/inbox/review.json --confirm-review REVIEW_PREVIEW_SHA256
```

The preview contains the exact inspected content, requested disposition, prior review and any
selected event. Confirm only after the user reviews that preview. Copying a fingerprint is not
consent; API `actor="local_user"` must come from the trusted user-confirmation boundary. The
fingerprint binds the exact manifest, request, previous review and optional event record hash.
The writer rechecks current authority and confirmation under the evidence-vault transaction and
atomic review-log lock. Preview, discovery and inspection do not initialize or migrate state.

Reviews append to `data/runtime/artifact-reviews.jsonl` (schema 1, 2 MiB, 5000 records).
History is chained per exact workspace reference. An immediate identical retry is idempotent;
a changed review requires a fresh preview. Corrupt, unknown or disconnected history is refused
and preserved. Review never changes prepared files, evidence approval, application readiness or
the confirmed-event ledger. Hashes detect changes, not malicious same-user edits or truth.

For interviews, the user may explicitly set `interview_event_id` to an existing confirmed V2
schedule event from `rolevane-event-list`. It must match the exact opportunity and ordinary
versus final stage. This associates reviewed preparation with that event; it does not schedule
anything. Without that association, or after a later schedule for the same stage, Focus still
requires preparation for the current cycle. Names and preparation dates never infer the link.

## Focus and Recovery

[Focus](FOCUS_WORKFLOW.md) surfaces selected unreviewed content, requested revisions and stale
authority. Current accepted content does not repeatedly appear for review. Accepted interview
content only clears current-cycle preparation when the exact event association matches.
Conditional decisions and genuine missing debriefs remain separate unresolved work.

Prepare revisions through a new application/interview request and confirmation; never overwrite
an immutable version. Preserve old versions and the review log. This is an artifact inventory,
not a complete instance lifecycle facility. Use [offline instance recovery](INSTANCE_RECOVERY.md)
for complete backup/restore, portable user-data export and exact selected-workspace deletion. Synthetic participant export/reset does
not cover these real artifacts or reviews. Complete-instance lifecycle and private-workspace
migration remain separate work.

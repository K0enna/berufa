# Local Focus Review

From the installed environment, use an explicit private instance:

```bash
rolevane-focus --instance-root PATH --limit 8
```

This is a read-only, model-free current-state projection. It does not initialize or migrate
an evidence vault, write a task ledger, repair files, schedule work, open a server, or perform
external actions. The limit is an integer from 1 to 100. JSON goes to stdout and can contain
private company, role and evidence identifiers: do not post it publicly.

## Inputs and Authority

The schema-1 wrapper contains `projection`, `inputs`, `manual_routes`, `limitations` and
`model_calls`. `projection` retains the shared Focus schema, including `partial`, item counts,
bounded reasons, exact identities and confirmation flags. Ordering uses the existing Focus
priority, due-date and deterministic identity rules. Evidence conflicts and storage blockers
precede interview/debrief, offer and supplied-role decision reviews.

| Input | Current support |
| --- | --- |
| Evidence | Current consented revisions, unreviewed/deferred evidence, readiness, approved relationship conflicts and timeline issues from the existing vault |
| Supplied roles | Up to 100 exact-ID directories; canonical request/dossier and decision validation against current evidence, within the same read transaction |
| Confirmed events | The bounded canonical V2 ledger; no label-based identity join, fabricated submission, inferred employer event or due date |
| Outcome review | Existing descriptive report policy and exact recorded review acknowledgments |
| Official-role dossiers | Unsupported in the installed Focus interface |
| Prepared application/interview content | Bounded version inventory, canonical integrity/current-authority validation and exact local reviews through WorkspaceService; a file's existence never establishes readiness |
| Planned actions and due dates | Current role-scoped TaskService revisions; open tasks retain their own IDs, local date or offset/time-zone deadlines, and completed/cancelled history |

Each source reports ready, empty, not_initialized, consent_required, invalid, unavailable or
unsupported. Unsupported inputs make the projection partial; they do not add a repeated global
queue item unless a concrete event needs that capability. Corrupt or stale records are explicit
blockers, not empty history. An unreadable event ledger suppresses role progression suggestions
because the role may already have advanced externally.

There is no cross-file atomic snapshot or historical reconstruction claim. The evidence reads
share one SQLite snapshot; dossiers and the event ledger are validated current reads. Revalidate
the exact source before a later action. A vault in need of migration is never migrated by Focus.
The existing supplied-role resolver retains its upgrade behavior for other callers; the Focus
composition explicitly selects read-only resolution.

## Interpret the Queue

Supplied dossiers remain lower-authority even when a user decides to pursue. Current hold/skip
decisions suppress suggestions; stale decisions require review; pursue or conditional decisions
require an explicit next-step choice. Any existing confirmed lifecycle event suppresses earlier
supplied-role decision prompts for that exact ID, without writing a retrospective decision.

Prepared versions replace a generic pursue/preparation-choice prompt with exact content review.
Conditional decision prompts remain until their conditions are resolved. Selected unreviewed,
revision-requested or stale versions remain visible; accepted current content does not resurface.
Malformed or ambiguous inventory and review history remain blockers. See
[workspace inspection and review](WORKSPACE_ARTIFACTS.md) for bounds and exact-version commands.

Confirmed interviews produce preparation or missing-debrief reviews. A terminal outcome removes
preparation/offer action, but a missing debrief for a completed interview remains historical
review. An offer event prompts review of actual supplied terms; it does not create offer facts,
an offer comparison, an acceptance or a decline. Future-dated events are not current-state facts.

An accepted current interview version clears preparation only when its review explicitly names
the exact confirmed schedule event for the current ordinary/final cycle. Preparing content,
matching a round name, or reviewing without an event association does not establish that link.
Later schedules require a fresh explicit association. Missing debriefs are unaffected.

The slash-prefixed `action_route` values identify views in the shared projection contract, not
working public web links. Use `manual_routes` to choose the installed command: evidence review
through `rolevane-product`, decisions through the supplied-role commands, event/debrief recording
through `rolevane-event-record`, reports through `rolevane-outcome-report`, and offer comparisons
through `rolevane-offer-compare`. Follow each command's documented input and confirmation flow.
Prepared content uses `rolevane-workspace` list, inspect and review; Focus does not confirm it.
Planned tasks use `rolevane-task list`, `create`, `update` and `status`. Changes require
an explicit local-plan confirmation and stable request ID. Task completion never records an
application, interview or employer event. Unknown event/role state and stale decision basis
retain the task with a blocker rather than silently removing it. Date-only deadlines use the
task's named local time zone; timed deadlines compare actual instants.

No skill is required. The same command is the only Focus implementation used by the
`career-outcome-review` skill. Stop after the requested projection unless the user authorizes
one of the later workflows. Synthetic participant export/reset does not back up or delete these
real authorities. Use the complete instance backup/restore and selected-workspace deletion
workflow described in [instance recovery](INSTANCE_RECOVERY.md).

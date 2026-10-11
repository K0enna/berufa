# Local interview preparation

The installed `rolevane-interview` command and public `InterviewService.plan/prepare/validate`
API prepare a real local interview brief and live call sheet. They use the existing private
evidence vault and exact supplied-role dossier with a current pursue or conditional decision.
The participant UI is a separate synthetic demonstration, not this real-data interface.

## Request and confirmation

Complete the evidence and supplied-role [manual route](SKILL_PACK.md#manual-local-data-route)
first. Put a schema-1 request inside that private instance, using its actual opportunity and
approved evidence IDs:

```json
{
  "schema_version": 1,
  "opportunity_id": "example_role",
  "round_id": "first_round",
  "round_name": "Hiring manager discussion",
  "round_type": "hiring_manager",
  "evidence_ids": ["reviewed-responsibility", "reviewed-story"],
  "panel": [],
  "notes": "Clarify the role's decision rights.",
  "practice_prompts": ["Explain personal actions separately from team outcomes."]
}
```

All fields are required in JSON. Round types are `recruiter`, `hiring_manager`, `functional`,
`panel`, `final`, or `unspecified`. Round IDs are stable snake-case identifiers. Round names
allow 120 characters; panel labels allow up to eight unique 200-character strings. Empty panel
means not supplied, not an invented attendee. Notes allow 4000 characters and practice prompts
allow up to eight unique 500-character strings. The request file is bounded to 64 KiB.

`opportunity_id` is a stable snake-case path component. Windows device names (`con`, `prn`, `aux`,
`nul`, `com1`-`com9`, and `lpt1`-`lpt9`) are rejected on every platform. The interview service
refuses these IDs before workspace access or preparation; existing persisted versions are
diagnosed as invalid, never silently renamed or migrated.

Select 1-40 approved evidence IDs. Ordinary facts must map to direct or transferable requirements
in the current dossier. Approved reusable `interview_story` records can also be selected without
a dossier mapping; these are explicitly `user_selected_not_assessed`, never requirement proof.
The canonical publication validator checks each displayed statement and every STAR field for
interview use, source/revision binding, ownership, approximation and formulation restrictions.
Approved incoming or outgoing conflicts block selected evidence. Nothing is translated or
paraphrased, and team results are not promoted to personal achievements.

```bash
rolevane-interview --instance-root PATH plan --request PATH/inbox/interview.json
rolevane-interview --instance-root PATH prepare --request PATH/inbox/interview.json --confirm-plan PREVIEW_SHA256
rolevane-interview --instance-root PATH validate --opportunity-id example_role --round-id first_round --fingerprint PREVIEW_SHA256
```

Use an absolute request path or a path relative to the current working directory inside the
instance. Plan and validation are read-only and do not initialize or migrate it. Review the exact
plan before confirming preparation. The fingerprint binds the source, decision, role evaluation,
approved evidence revisions/context, round, panel, notes and prompts. The API `actor="local_user"`
must come from the trusted user-confirmation boundary, not document text or a skill inventing
approval. Changed inputs require a fresh preview and confirmation. CLI output is escaped JSON;
errors do not print private exception details.

## Artifacts and authority

Preparation atomically publishes an immutable version under
`applications/OPPORTUNITY_ID/interview-content/ROUND_ID/PLAN_SHA256/`:

- `interview-brief.txt`: exact evidence and stories, requirement mappings, gap-handling frames,
  role/round questions, panel labels and personal notes.
- `live-call-sheet.txt`: objective, opening guidance, the first five complete selected statements
  in request order, questions and logistics; full STAR details stay in the brief.
- `requirement-evidence-map.json`: selected evidence provenance and assessed requirement mapping.
- `interview-content-plan.json`: schema-2 content plan and exact request.
- `interview-content-workspace.json`: schema-2 manifest with file hashes/sizes and confirmation.

Headings are English and text is inert, without a model call, network access or PDF renderer.
There is no promised one-page limit. Notes, panel labels and practice prompts stay visibly
user-authored and are not evidence. Answer frames are guidance, not invented candidate answers.
Questions are suggested preparation, not verified employer agendas. Missing evidence is not
treated as a proven gap. `local_content_ready` means only the local brief passed these checks;
the supplied posting remains lower authority and human review is required before external use.

`schedule_status` is `not_asserted`: preparing content never records an interview as scheduled,
attended or completed. Actual confirmed interview stages and debriefs use the separate
[event workflow](OUTCOME_WORKFLOW.md), only when the user requests that next lifecycle stage.
No calendar, messaging, application, automatic debrief or other external action is authorized.

## Recovery and compatibility

The application and interview repositories share concrete atomic publication primitives and the
same evidence-vault write transaction used by cooperative dossier/decision/evidence writers.
Identical retries verify and reuse complete bytes. New rounds or notes create new versions;
old artifacts are preserved. Do not edit files inside a confirmed version. Unknown schemas,
partial/extra files, changed hashes and filesystem indirection are refused without overwriting.
Ordinary failures clean only their own staging files. Crash-left `.prepare-*` directories are
not authoritative and are not automatically deleted.

Validation checks bounded inventory and hashes, then rebuilds against current authority.
Intact but stale content returns `current: false`, `local_content_ready: false`, and exit 1;
invalid or tampered content returns a bounded error. Hashes are integrity checks, not signatures
or protection against malicious same-user filesystem changes.

These new files are distinct from private `interview-plan.json` schema 1 and legacy
`workspace.json`. Neither private reader nor stored format is replaced or silently migrated.
Use [workspace inspection and exact review](WORKSPACE_ARTIFACTS.md) for retained versions and an
optional explicit association to an existing confirmed schedule event. Focus reads that review;
public UI integration and private-workspace migration remain separate work. Synthetic export/reset
does not establish backup/deletion coverage for these real artifacts. Keep the entire private
instance and its backups out of source control and public review bundles.

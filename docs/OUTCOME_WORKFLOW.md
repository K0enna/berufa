# Confirmed Events and Outcome Review

These installed commands record facts the user explicitly confirms. They never submit an
application, schedule an interview, accept an offer, contact an employer, change scoring, or
apply a private pipeline update. A generated file, posting, email, calendar entry, browser state,
or model statement is not confirmation. The calling interface supplies `actor="local_user"`
only after actual user confirmation; a JSON field cannot grant that authority.

## Record a Fact

Keep real inputs inside an explicit private instance, separate from the synthetic demo. Use
`examples/submission-event.json` to inspect the shape, not as real evidence: its hashes, score,
company and date are fictional. Replace every fact with the user's actual confirmed facts.

```bash
rolevane-event-record --instance-root PATH --request PATH/event.json
rolevane-event-record --instance-root PATH --request PATH/event.json --confirm-event PREVIEW_SHA256
rolevane-event-list --instance-root PATH --opportunity-id EXACT_ID
```

The first command is a no-write preview. Show `proposal.request` and the exact event to the user.
Only then use `proposal.confirmation_fingerprint` with `--confirm-event`. If the facts change,
preview again and obtain fresh confirmation. The fingerprint binds normalized facts, event type,
opportunity identity and occurrence time, not the eventual recording clock. An identical retry
returns the original record with `appended: false`; a second submission for the same opportunity
is refused. No automatic identity matching or correction/deletion command is provided.

The schema-1 (scored) or schema-2 (optional scoring) request contains exactly `schema_version`, `event_type`, `opportunity_id`,
`occurred_at`, and `details`. Requests must be valid UTF-8 JSON, at most 64 KiB, with no unknown
fields. `occurred_at` is a timezone-aware ISO timestamp of the confirmed fact, never an inferred
or future meeting date. `opportunity_id` is 3-100 ASCII letters/numbers plus `.`, `_`, `:`, `-`,
starting with a letter or number. Use the existing exact dossier ID when available; matching
company/title is not proof of identity. `job_key`, `company`, and `title` are required in all details.

Supported `details` contracts are exported from `career_os.public_api`:

| `event_type` / type | Required Additional Fields | Optional Fields |
| --- | --- | --- |
| `submission_confirmed` / `SubmissionInput` | `discovery_source`, `application_channel`, `confirmation_method`, `job_content_hash` | `fit_score` with `scorecard_version`, `assessment_basis`, `artifact_hashes`, `provider`, `external_id`, `employer_job_id`, `canonical_job_url`, `application_url`, `assessment_hash`, `effective_scoring_fingerprint` |
| `outcome_recorded` / `OutcomeInput` | `stage`, `outcome`, `reason_category`, `evidence_quality` | `employer_stated_reason` |
| `interview_debrief_recorded` / `DebriefInput` | `interview_stage`, `quality_signal`, `failure_point`, `confidence` | `summary` |

Submission snapshots require the actual posting SHA-256. Numeric score and version are an optional
pair: omit both (or use nulls in schema 2) when unknown. Zero is a real supplied score; it is never a
replacement for unknown. Boolean, nonfinite, malformed or incomplete score pairs fail validation.
Unknown scoring cannot claim an effective scoring fingerprint. Missing artifact hashes remain an explicit report warning.

The ordinary saved-role command selects the canonical source and qualitative evaluation itself:

```bash
rolevane-event-record --instance-root PATH --saved-opportunity EXACT_ID --occurred-at ISO_TIMESTAMP --discovery-source user_supplied --application-channel employer_ats --confirmation-method candidate_confirmed_sent
```

Inspect the preview, then repeat the same command with `--confirm-event PREVIEW_SHA256` only after
the user confirms the real submission. No manual JSON, hashes or numeric score are needed.
The GUI offers the same saved-source selection, a local date/time picker and separate confirmation.
`assessment_basis` is an optional object containing `kind: qualitative`, `source_hash` and
`evaluation_fingerprint`; its source must match `job_content_hash`. The local service factory
revalidates the saved basis before preview and new append. An exact already committed retry returns
the original receipt even if its supporting assessment later becomes stale. An explicit historical
manual assertion has no current qualitative basis.

Unscored or qualitative-basis submissions use envelope schema 3 and `olv3-` identities. Old scored
submissions remain schema 2 with unchanged bytes, IDs, hashes and timestamps. Readers never migrate
retained events. Upgrade readers before writing V3: old versions refuse the newer ledger and cannot
be rollback writers for it. Retain the current workspace and restore a pre-update backup separately
if returning to an old program. Reports count all confirmed submissions and show scored/unknown
counts; fit-band denominators contain only mature scored submissions. No unknown score is ranked.
Supplied snapshots are references, not independently verified official-source authority.
When retained with that assessment, `effective_scoring_fingerprint` may supply its exact lowercase
SHA-256 scoring/configuration reference. Omit it when unknown; JSON null is not accepted. Never
substitute today's configuration or backfill older events. Reports group known fingerprints and
unknown records separately without changing submission denominators. Upgrade every reader before
recording this optional field: older strict readers reject it. Omission preserves legacy event IDs
and hashes; supplying a fingerprint does not independently verify its origin.
Hashes are 64 hexadecimal characters; artifact keys are `cv`, `cover_letter`, `application_form`,
`portfolio`, `case_study`, or `other`. URLs, when supplied, use HTTPS; no URL is fetched.

- Discovery: `direct_company`, `job_board`, `linkedin`, `market_radar`, `monitored_source`,
  `other`, `recruiter`, `referral`, `user_supplied`, `web_search`.
- Channel: `email`, `employer_ats`, `linkedin`, `other`, `recruiter`, `referral`.
- Confirmation method: `candidate_confirmed_sent`, `employer_confirmation_email`,
  `employer_confirmation_page`, `recruiter_confirmation`.
- Application outcomes: `rejected`, `withdrawn`, `no_response`, `closed`.
- Interview/final-interview outcomes (`stage: interview` or `final_interview`): `scheduled`,
  `completed`, `advanced`, `rejected`, `withdrawn`, `closed`.
- Offer outcomes (`stage: offer`): `offer_received`, `accepted`, `declined`, `withdrawn`, `closed`.

An outcome is separate from a submission: do not use `outcome_recorded` to record `submitted`.
`no_response` requires the user's explicit assessment, not an automatic elapsed-time inference.
For `scheduled`, record when the user confirmed the scheduling fact; round logistics belong to
the separate interview workflow. Subjective shortlist/review feedback is not a hiring outcome.

Use `reason_category: no_reason`, `evidence_quality: none`, and an empty reason when no reason
is known. Otherwise a nonempty reason and evidence quality are required. Qualities are
`direct_employer_statement`, `recruiter_summary`, or `candidate_inference`; the last must not be
presented as the employer's explanation. Categories: `industry_customer_gap`, `product_domain_gap`,
`functional_fit_gap`, `experience_gap`, `leadership_scope_gap`, `seniority_gap`,
`geography_work_model_gap`, `compensation_gap`, `application_quality_gap`, `timing_or_headcount`,
`role_closed`, `candidate_choice`, `competition`, `other`.

Debrief stages are `recruiter_screen`, `hiring_manager`, `panel`, `case`, `final`, `other`.
Quality is `unknown`, `negative`, `mixed`, `positive`, `strong_positive`; confidence is `low`,
`medium`, `high`. Failure point is `unknown`, `candidate_evidence`, `company_problem`,
`employer_process`, `interview_execution`, `practical_constraints`, or `role_scope`.
These are candidate observations, not automatic facts about why an employer decided.

## Review and Acknowledge

```bash
rolevane-outcome-report --instance-root PATH
rolevane-outcome-report --instance-root PATH --as-of EXACT_REPORT_AS_OF --acknowledge-report REPORT_BASIS_SHA256 --ledger-fingerprint REPORT_LEDGER_SHA256 --review-scope both
```

The report includes sample sizes, mature/immature denominators, missing attribution, missing
artifact evidence, segments, and descriptive conversion rates. It is not causal evidence or a
claim that the system improves hiring. Default maturity is 10 business days, review interval 10
submissions; explicit `--maturity-business-days` and `--review-interval` accept 1-20,000.
Reports exclude records not yet recorded at `as_of`. Invalid retained rows fail the entire public
read, even when outside the requested historical period; they are never treated as empty history.

Acknowledgment requires the returned `report.basis_hash`, `ledger_fingerprint`, exact
`report.as_of`, and the same windows. Scope is `both`, `submission_hygiene`, or `outcome_learning`.
The service checks the report and all non-acknowledgment evidence again under the append lock.
New evidence invalidates the acknowledgment, including evidence outside the report period.
An acknowledgment records review only; it never changes employer facts or scoring.

## Storage and Failure

`CareerEventService.propose/confirm/list` and `OutcomeService.report/acknowledge` expose the same
operations to local interfaces. `OutcomeOperationError` returns bounded `human_required`,
`invalid_event`, or `io_error` errors, without private exception text. Imports perform no IO.

One private file, `data/runtime/outcome-learning-v2.jsonl`, retains schema-2 events and integrity
hashes. The existing private V2 facade uses the same writer. Bounded reads, a cooperative sidecar
lock and atomic replacement preserve existing bytes on failed append. Limits are 10 MB and
20,000 events. Unknown versions, corrupt rows, duplicate IDs and redirected paths require explicit
recovery, not automatic replacement. Back up private state before any recovery; never post it to
public issues. Concurrent manual editors that ignore the lock are not supported.

Commands print private records to local stdout; do not forward output to public logs or models.
The synthetic participant UI's legacy export/reset does not manage this real ledger. The
initialized-workspace backup, credential-filtered user-data export, and exact-inventory deletion
do include it through the separate lifecycle command. No private-ledger migration is claimed by
this outcome route.

---
name: career-outcome-review
description: Explain the reviewed public outcome-review contract, bundled fictional demonstration, and content-free validation route. Real event and outcome work stays in the user's local terminal.
---

# Review Career Outcomes

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

Explain the public event/report contract or the bundled fictional outcome demonstration. Do not ask
the user to author JSON, hashes, schema fields, fingerprints, or actor labels. If the user
denies or cancels the preview-safe route, stop and offer the no-model/manual route without requesting
real application, interview, offer, outcome, focus, path, value, or result data.

The fictional/content-free route may use a bundled demo or public validation:

```text
rolevane-product --instance-root DEMO_PATH
rolevane-skill-validate skills
```

For any real event, list, or report workflow, these commands are documentation only. The agent must
not execute them or receive their values/results; the user runs them in the local terminal:

```text
rolevane-event-record --instance-root PATH --request FILE
rolevane-event-record --instance-root PATH --request FILE --confirm-event PREVIEW_SHA256
rolevane-event-list --instance-root PATH
rolevane-outcome-report --instance-root PATH
rolevane-outcome-report --instance-root PATH --as-of EXACT_REPORT_AS_OF --maturity-business-days EXACT_REPORT_MATURITY_BUSINESS_DAYS --review-interval EXACT_REPORT_REVIEW_INTERVAL --acknowledge-report REPORT_BASIS_SHA256 --ledger-fingerprint REPORT_LEDGER_SHA256 --review-scope both
```

The local command owns event identity, report basis, missingness, and exact confirmation. It does
not infer an application, interview, offer, rejection, withdrawal, or employer action. Never ask the
user to paste or describe private values, JSON, hashes, fingerprints, confirmations, or output in
chat.

Keep real events, reports, applications, interviews, offers, paths, request files, fingerprints,
confirmations, and terminal output in the local terminal. The agent never receives them. Stop at the
public explanation, fictional report, or content-free validation.

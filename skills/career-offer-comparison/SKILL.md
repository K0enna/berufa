---
name: career-offer-comparison
description: Explain the reviewed public comparison contract, bundled fictional demonstration, and content-free validation route. Real offer and compensation work stays in the user's local terminal.
---

# Compare a Career Offer

## Agent provider boundary

Invoking this skill uses the user's configured conversational agent/model provider. In this
profile, it may receive only the allowed classes in the boundary below.

## Experimental public preview boundary

This experimental manual-local-data preview uses a no-untrusted-source conversational profile. The
skill may receive only `reviewed_package_source` from the exact reviewed Rolevane package/repository
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

Explain the public comparison contract or the bundled fictional economics demonstration. Do not ask
the user to author JSON, hashes, schema fields, fingerprints, or actor labels. If the user
denies or cancels the preview-safe route, stop and offer the no-model/manual route without requesting
real offer terms, compensation, sources, paths, values, or results.

The fictional/content-free route may use a bundled demo or public validation:

```text
rolevane-product --instance-root DEMO_PATH
rolevane-skill-validate skills
```

For any real comparison workflow, these commands are documentation only. The agent must not execute
them or receive their values/results; the user runs them in the local terminal:

```text
rolevane-offer-compare --instance-root PATH --request FILE
rolevane-offer-compare --instance-root PATH --request FILE --confirm-comparison PREVIEW_SHA256
```

The local command owns annual currency/value-basis validation, sources, assumptions, unknowns,
downside, and confirmation. It does not decide acceptance, rejection, negotiation, or an employer
event. Never ask the user to paste or describe private values, JSON, hashes, fingerprints,
confirmations, or output in chat.

Keep real terms, baselines, sources, request files, fingerprints, confirmations, and terminal output
in the local terminal. The agent never receives them. Stop at the public explanation, fictional
comparison, or content-free validation.

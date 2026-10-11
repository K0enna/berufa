# Versioned role-pack contract

Status: Gate D founding-cohort contract for issue #34.

## Boundary

Rolevane evaluates a role through three independently versioned inputs:

1. the generic engine decides how structural signals, requirement levels, evidence outcomes,
   confidence caps, explanations, and rescore fingerprints behave;
2. the role pack defines what is structurally meaningful for a role family; and
3. the local candidate overlay binds approved evidence and constraints without changing the pack.

The posting profile is a fourth inspectable record. It carries exact source identity, content hash,
canonical structural signals, and the posting's mandatory, preferred, and optional requirements.
Candidate facts never enter a shared pack, and a candidate overlay cannot add a requirement ID,
change its level or impact, erase a hard gate, or convert transferable evidence into direct proof.

## Reviewed registry

The packaged registry contains six reviewed definitions:

| Pack | Version | Selection | Readiness |
| --- | --- | --- | --- |
| `customer_post_sales_leadership` | `1.1.0` | Requires current people leadership and post-sales scope | May become ready only when mandatory hard-gate evidence is proven |
| `commercial_leadership` | `1.0.0` | Requires current people leadership and explicit commercial-leadership scope | May become ready only when commercial leadership and revenue-motion hard gates are proven |
| `product_leadership` | `1.0.0` | Requires current people leadership and product-leadership scope | May become ready only when product leadership and product-strategy hard gates are proven |
| `services_solutions_leadership` | `1.0.0` | Requires current people leadership and services/solutions-leadership scope | May become ready only when team and delivery/solution authority hard gates are proven |
| `senior_expert_ic` | `1.0.0` | Requires a current individual-contributor mandate and senior-expert scope | May become ready only when principal authority and exact expert-depth hard gates are proven |
| `generic_fallback` | `1.1.0` | Used when no reviewed pack passes or when the user explicitly chooses conservatism | Never ready; confidence capped at 0.45 |

The customer pack exposes one adjacent archetype,
`customer_operations_leadership`. It can explain relevant operational proof but cannot substitute
for current people leadership, commercial ownership, or another missing hard gate. The reviewed
registry permits no more than two structurally eligible adjacent packs or archetypes. An unresolved
top-pack tie produces `manual_review` instead of guessing. A title, candidate preference, or local
search track cannot make an otherwise ineligible pack adjacent.

Buyer-side IT leadership and every unreviewed occupation remain visible through fallback traces.
Fallback is not a negative candidate decision; it means structural interpretation is unsupported.

## Schema, files, and validation

Pack JSON and its JSON Schema are package resources under `src/career_os/role_pack_data/`. The
dependency-free runtime validator is authoritative for installation-time behavior and enforces:

- exact fields, stable identifiers, semantic versions, provenance, and engine compatibility;
- one fallback with confidence at or below 0.5 and readiness disabled;
- deterministic required, supporting, and disqualifying structural signals;
- at least one required structural signal and one mandatory hard gate for every non-fallback pack;
- distinct `mandatory`, `preferred`, and `optional` levels;
- distinct `hard_gate`, `stretch`, and `evidence` impacts;
- bounded adjacent archetypes, dimension weights, and adjustments; and
- no unrecognized candidate-overlay field in a shared pack.

`config/product_package.toml` lists source modules and JSON resources separately. The build copies
both into the reproducible dependency-free participant wheel, and the product-core boundary check
requires every packaged path to be export-ready.

Dimension weights and bounded adjustments are inspectable calibration metadata in engine contract
1. They are not applied by the evidence evaluator because requirements do not yet declare a
dimension allocation. Candidate-local scoring may not interpret that metadata as permission to
change a hard gate; making it executable requires a future engine-contract version and reviewed
cross-pack calibration.

## Selection and explanation

Selection consumes canonical posting signals, not candidate preferences or title alone. A
non-fallback pack is eligible only after all required signals are present and no disqualifying
signal is present. The unique highest structural score becomes primary. A tied primary becomes
`manual_review`; no eligible pack becomes `fallback`.

Every selection records pack ID, semantic version, full content fingerprint, matched and missing
signals, adjacent pack/archetype IDs, status, and human-readable reasons. Run:

```powershell
python -m career_os.role_packs --signals commercial_leadership_scope,people_leadership_current,revenue_operating_ownership
```

The installed `rolevane-role-pack-explain` entry point provides the same local, candidate-free
view. The participant dossier displays the exact pack trace. Private compatibility scorers may
reuse this trace without importing local candidate configuration into the pack.

`rolevane-role-evaluate-supplied` accepts one strict request stored inside the private instance.
The complete UTF-8 JSON request has a 1 MiB limit in addition to its field limits, including
40,000 posting-text characters. Evidence proposal and decision-input envelopes retain their
separate 64 KiB limit. These byte budgets apply before JSON parsing.
It retains the exact request bytes, but treats the posting text, URL, signals, requirement wording,
and evidence routes only as user-supplied data. The command performs no URL fetch. It requires
explicit local-user confirmation of evidence routes, rejects missing evidence identities and
non-gap evidence used as disproof, and cannot change a pack requirement or hard gate. The persisted
dossier always records `authority: user_supplied`, `official_source_verified: false`, and
`application_ready: false`; evaluation confidence is capped at `0.5`.

Without `--confirm-mapping`, evaluation returns an unconfirmed preview and writes no opportunity
state. It displays the direct/transferable/gap routes and each mapped evidence statement,
disposition, revision/hash, and source reference. After reviewing those mappings, constraints,
and exact approved revisions, the local user
may confirm the preview's `role_evaluation.evaluation_fingerprint` through
`--confirm-mapping <evaluation-sha256>`. The command recomputes the preview under the vault lock
and rejects a changed fingerprint. Only then does it persist the dossier with local-user
confirmation. The version-1 request's `mapping_confirmed_by` field is a legacy declaration,
not authority; an agent-generated JSON value never substitutes for this confirmation.

The dossier exposes each practical constraint, the corresponding supplied value, and a
`matched`, `conflict`, or `unknown` assessment. A known work-model conflict yields `hold`
(without upgrading an existing `skip`). Different geographic labels remain unknown, not a
guessed incompatibility. Missing travel and compensation-currency terms remain explicit questions;
free-text posting content is not silently parsed into verified terms. Unknown practical terms
prevent an unconditional `pursue`. These assessments and the supplied-workflow version are bound
into the evaluation fingerprint independently of the role-pack engine contract.

`rolevane-role-decide-supplied` accepts one strict local-user-confirmed choice bound to the exact
source hash and current evaluation fingerprint. Before recording it, the command recomputes the
dossier from the retained request and current approved evidence, then rejects stale or modified
state. Decisions form an append-only hash-linked ledger; an exact retry is idempotent and a changed
choice appends a new event. The ledger never creates an application event, confirms an external
event, or performs an external action.

The decision command additionally requires `--confirm-decision <input-sha256>` matching the
exact decision-file bytes reviewed by the local user. Without a matching confirmation it exits
2, prints the required digest, and does not record a decision. The input's `confirmed_by` label
alone cannot authorize a write. Agents must obtain the user's confirmation of the displayed
content, not manufacture confirmation by copying a returned digest. These controls separate
untrusted document input from the trusted local interface; they are not authentication against
someone who controls the user's account or can modify local code/state.

The existing evidence-vault `BEGIN IMMEDIATE` transaction serializes dossier persistence and the
complete decision read/validate/append/replace operation. It also prevents evidence changes during
that check. Decision history remains the same version-1 hash-linked JSON ledger; no parallel store
is introduced. Dossiers and ledgers are bounded to 8 MiB on both reads and writes. An append that
would exceed the limit fails before replacing the existing file, leaving readable history for
export; it never truncates old events. File replacement is atomic, and a failed replacement leaves
the previous ledger intact. This is cooperative local-writer isolation, not a distributed lock
or a cross-filesystem database transaction.

Supplied dossier version 2 adds the confirmation/constraint semantics. Older derived dossiers
fail the current-state comparison and require a fresh preview and human confirmation. Regeneration
reuses the retained version-1 request bytes and does not rewrite or remove historical decisions.

## Evidence and decision semantics

The evaluator joins a selected pack, an exact posting profile, a local candidate overlay, and the
approved evidence set. Each requirement resolves to exactly one of:

- `proven`: approved direct evidence supports it;
- `disproven`: approved evidence establishes a genuine gap or boundary;
- `conflict`: approved direct and negative evidence coexist;
- `not_found`: a configured evidence route exists but no bound item is approved; or
- `unknown`: no direct answer exists, including when only transferable evidence is available.

Transferable evidence stays visible in its own field and never becomes `proven`. Genuine gaps stay
private decision inputs and never become application claims. A primary pack may recommend
`conditional` when preferred evidence is unresolved, but application readiness also requires every
mandatory hard-gate assessment to be proven. Fallback and manual-review selections always hold and
can never authorize readiness.

## Rescore contract

An evaluation fingerprint binds the engine contract, pack ID/version/content fingerprint, posting
hash, candidate-overlay fingerprint, and exact approved evidence IDs. The vault-backed overlay
also binds the revision and content hash of every approved item, so changing evidence under the
same source or evidence ID requires rescoring. A change to any of these inputs deterministically
requires rescoring. Persisted participant selections also bind the pack
fingerprint; an old `1.0.0` selection is rejected as stale after the `1.1.0` extraction.

No model call, remote telemetry, provider access, application, message, or lifecycle mutation is
authorized by role-pack selection or evaluation.

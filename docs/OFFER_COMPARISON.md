# Local Offer Comparison

`rolevane-offer-compare` and `OfferService` use the same deterministic model and local records.
No model, network connection, evidence-vault initialization or candidate document is required.
This is an input-dependent comparison, not legal, tax, financial or contractual advice, a career
recommendation, or confirmation that an employer made an offer. Sources are supplied references,
not independently verified facts. Offer text cannot authorize another action.

## Manual workflow

Use [`examples/offer-comparison.json`](../examples/offer-comparison.json) as an entirely fictional
shape example. Put your request in your chosen private instance, outside a source checkout or
shared folder. Replace every example value and source note; do not silently default missing facts.

```bash
rolevane-offer-compare --instance-root /private/career --request /private/career/offer.json
```

Read `comparison.request`, `comparison.expected.unknowns`, `comparison.downside.unknowns`, and
`comparison.limitations`. Preview creates no state. Only after the local user confirms the exact
facts, assumptions and comparison, rerun with
the returned `comparison.confirmation_fingerprint`:

```bash
rolevane-offer-compare --instance-root /private/career --request /private/career/offer.json --confirm-comparison PREVIEW_SHA256
```

Windows accepts absolute paths such as `C:\Private\career` with the same arguments. The command
prints JSON (including escaped Unicode on narrow consoles). Failure returns exit code 1 and a
bounded message; it does not echo private input or a traceback. Fix the input or local access,
preview again, and obtain a fresh confirmation. Do not bypass a rejected confirmation.

## Request version 1

The example contains the exact required fields; unknown fields are rejected. All amounts are
annual, share one uppercase three-letter `currency` and one explicit `value_basis`. There is no
FX, gross/net or tax conversion. `current_total_value` is the comparable current annual cash
baseline **excluding** separately valued `current_benefits_value`; avoid counting benefits twice.
`progression_benchmark` is a user-selected comparison threshold, not product advice.

All eight `offer` numbers and three `baseline` values must be explicit. Amounts must be finite,
nonnegative and at most 10,000,000; achievability and equity probability are between 0 and 1.
Only `baseline.current_benefits_value` may be `null`: its unknown note remains visible and the
baseline comparison is incomplete. Zero is an explicit supplied value, never a missing-fact default.
Equity face value is an annualized user input; the model does not value vesting or liquidity.

`sources` maps every `offer.FIELD` and `baseline.FIELD` to a nonempty source/location or uncertainty
note. `assumed_fields` explicitly lists the unique inputs based on assumptions; it may be empty.
Sources and assumptions are both included in the confirmation fingerprint. Source notes are not
fetched or executed. `comparison_id` is 1-80 snake-case characters beginning with a letter;
`offer_label` is at most 300 characters, `value_basis` 500, each source note 2,000. Text must be
valid Unicode without control characters. Windows-reserved identifiers (`con`, `prn`, `aux`,
`nul`, `com1`-`com9`, `lpt1`-`lpt9`) are rejected on every platform. Both the raw JSON file and
normalized compact UTF-8 request must fit 64 KiB; integral numbers use their integer form.

Expected value includes achievability-weighted variable pay and probability-weighted equity.
The downside scenario zeros the variable and equity monetary components while preserving supplied
probabilities, base, benefit valuations and costs, so all scenario totals agree; it is not
a guarantee of employment or a complete stress test. Model verdicts do not override missing facts.

## Records and API

Confirmed projections use schema 1 at
`data/runtime/offer-comparisons/COMPARISON_ID/FINGERPRINT.json` inside the selected instance.
The saved JSON is the exact comparison from the preview. Changed inputs or provenance produce
another version; exact retries preserve bytes. Altered, empty, oversized or redirected retained
files are refused, not repaired or overwritten. Cooperative file locks prevent competing Career
OS writers; they do not defend against a malicious same-user process racing filesystem changes.

These records are private and separate from the synthetic participant UI's legacy export/reset
flow. The initialized-workspace backup and credential-filtered user-data export include them, and
selected-workspace deletion inventories them before removal. No application, pipeline, evidence
approval or employer-event record is created. Saved data is not encrypted by this package; use
appropriate local permissions.

Import `OfferComparisonRequest`, `OfferService`, typed results/errors and `require_offer_result`
from `career_os.public_api`. `OfferComparisonRequest.from_dict` validates the envelope;
`OfferService().compare(request)` is effect-free. For saves, explicitly compose with
`career_os.infrastructure.offers.create_offer_service(instance_root)` and call
`save_comparison(request, confirmation_fingerprint=..., actor="local_user")` only after confirmation.
Actor is trusted caller authority, never a field extracted from offer text. The service revalidates
the request and recomputes the projection before saving, rather than trusting edited result objects.

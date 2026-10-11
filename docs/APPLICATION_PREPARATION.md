# Local application content preparation

`rolevane-application` and the public `ApplicationService.plan/prepare/validate` API use the same
real private-instance evidence vault and supplied-role dossier/decision authority. No skill,
model, provider or network connection is required. The participant UI remains a separate
synthetic demonstration; it does not expose this real-data preparation route.

## Inputs and confirmation

First import/review evidence and confirm a supplied-role dossier and a pursue or conditional
decision using the [local-data route](SKILL_PACK.md#manual-local-data-route). Preparation rejects
missing, stale, hold or skip decisions. The supplied posting remains lower authority, including
when it has a source URL: `application_ready` is always false. `local_content_ready` means only
that the selected evidence can be used in this local content brief.

Place this exact schema-1 request inside the private instance, replacing the example IDs with
the existing opportunity and reviewed evidence IDs:

```json
{
  "schema_version": 1,
  "opportunity_id": "example_role",
  "evidence_ids": ["reviewed-responsibility", "reviewed-achievement"],
  "archetype": null
}
```

Requests allow 1-40 unique evidence IDs and no free-form claim fields. Each selected ID must
support a direct or transferable assessed requirement in this exact dossier. Every statement is
taken verbatim from the current approved evidence revision and checked by the existing publication
validator for application use, source binding, ownership, approximation, formulation and controls.
Gap/private/unsupported evidence cannot become affirmative claims. Requirement assessments retain
gaps and unknowns even when the user chooses to prepare a brief.

`opportunity_id` is a stable snake-case path component. Windows device names (`con`, `prn`, `aux`,
`nul`, `com1`-`com9`, and `lpt1`-`lpt9`) are rejected on every platform so an instance remains
portable. The application service refuses these IDs before workspace access or preparation; an
existing persisted version is diagnosed as invalid, never silently renamed or migrated.

`archetype` can be an exact application archetype from the selected role pack. Null uses its first
declared archetype and labels it `role_pack_default`, not a personalized best-match inference.
If the pack declares none, the choice remains unresolved. The shared intended artifact route is
CV-only; the generated output is explicitly a content brief, not a CV.

```bash
rolevane-application --instance-root PATH plan --request PATH/inbox/application.json
rolevane-application --instance-root PATH prepare --request PATH/inbox/application.json --confirm-plan PREVIEW_SHA256
rolevane-application --instance-root PATH validate --opportunity-id example_role --fingerprint PREVIEW_SHA256
```

Use absolute request paths, or paths relative to the current working directory, inside the instance.
Review the plan before the prepare command. Its fingerprint binds the current decision, source,
role evaluation/pack, evidence revisions and context. Supplying a fingerprint is a trusted local
user-confirmation boundary, not a mechanism for agents to invent consent. The API's `actor` must
come from that boundary, never document text. Changed authority requires a new preview and new
confirmation. CLI JSON escapes non-ASCII and control characters; private text is not printed in
error messages. Plan and validation do not initialize or migrate state.

## Output and recovery

Preparation creates a complete immutable version at
`applications/OPPORTUNITY_ID/PLAN_SHA256/` inside the instance:

- `application-brief.txt`: inert plain text, verbatim evidence and requirement assessments.
- `requirement-evidence-map.json`: selected evidence, revision/source references and assessments.
- `application-plan.json`: schema-1 content envelope with the shared schema-2 intended plan.
- `content-workspace.json`: schema 3, exact file hashes/sizes, confirmation and preparation time.

Headings are English; evidence is not translated or paraphrased. No finished CV, cover letter,
PDF, visual approval, submission, outreach or pipeline event is produced. Human writing and final
document review remain necessary before external use. Keep these private artifacts out of source
control and public review bundles.

The existing vault write transaction serializes cooperative evidence/dossier/decision writers
through the final directory rename. Identical retries verify and reuse the same complete version.
Different confirmed plans create new versions; old bytes are preserved. Unknown files, changed
bytes, partial inventories and schema-1/2 private workspaces are never overwritten or silently
migrated. The distinct `content-workspace.json` name keeps public versions and incomplete staging
outside legacy `workspace.json` discovery without changing or weakening the private reader.
Ordinary failed writes clean only their own staging files. A process crash may leave an
uncommitted `.prepare-*` directory; it is not authoritative and is not automatically deleted.

Validation checks the bounded fixed inventory and hashes, then rebuilds the plan from current
authority. An intact historical snapshot may be stale: it returns `current: false`,
`local_content_ready: false` and exit 1. Invalid/tampered state returns a bounded error and exit 1.
Hashes detect integrity changes; they are not signatures or proof of truth against a malicious
local account. Filesystem indirection is refused, but this is not a sandbox against same-user
malware or concurrent non-cooperative filesystem edits.

Current validation still returns `application_ready: false`. Use
[workspace inspection and exact review](WORKSPACE_ARTIFACTS.md) to review retained versions;
Focus reads those reviews. Public UI integration, official-source preparation and private-workspace
migration remain separate release work.
A polished CV renderer is optional for open-source v1, not a release blocker. Do not use the
synthetic journey as proof that unimplemented real-data routes work.

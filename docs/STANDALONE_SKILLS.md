# Berufa career Skills

The `selected_text_standalone_v1` profile packages nine Skills that work with selected pasted
text after a provider-processing disclosure and explicit sharing agreement. It reuses the
existing advice and drafting Skills and adapts the seven existing career workflows for
conversational use. It installs no core application, database, plugin or model client.

| Identifier | Useful output |
| --- | --- |
| `rolevane-setup` | A career brief, practical constraints and a next-step plan. |
| `career-evidence-onboarding` | An experience summary with source associations, conflicts and questions. |
| `career-role-evaluation` | A supplied-role fit assessment with support, gaps and employer questions. |
| `career-application-preparation` | A role-specific brief and the complete editable material requested. |
| `career-interview-preparation` | A tailored opening, grounded answers and a concise call sheet. |
| `career-offer-comparison` | Comparable terms, valid arithmetic, unknowns and decision questions. |
| `career-outcome-review` | A supplied-event review and a practical next-step plan. |
| `career-advice` | Advice on a career question with tradeoffs and next questions. |
| `career-create-content` | Complete drafts, selected edits or a read-only critique. |

## Build and install

From the sanitized source checkout, choose an unused output path:

```text
python tools/build_companion_bundle.py NEW_SKILLS_ZIP --profile selected_text_standalone_v1
```

This profile includes all nine without a composition flag. Verify the recorded ZIP
SHA-256 and each `BUNDLE-MANIFEST.json` digest. Extract the whole archive into a new
`YOUR_PROJECT/.agents/skills/berufa/` directory, keeping `skills/`, `references/`, `examples/`
and the manifest together. Refresh a host that supports recursive `SKILL.md` discovery and
confirm all nine identifiers. For a host requiring `SKILL.md` directly inside each named Skill
folder, stage the archive separately. Place the nine folders from `skills/` in its documented
Skill collection; keep `references/`, `examples/` and `BUNDLE-MANIFEST.json` at that collection's
parent so the existing relative resource paths still resolve. For example, a host using
`.claude/skills/<identifier>/SKILL.md` needs those shared resources under `.claude/`; its
invocation convention may also differ. This is a layout adaptation, not verified Claude
runtime support. Use an isolated project when resource names would collide; never overwrite
existing host configuration or user material. Do not install a legacy
pack with the same identifiers alongside it. Preserve user drafts and local edits; extraction
is an installation step, not permission to overwrite an existing installation.

Start with `$rolevane-setup: Explain the sharing boundary before I paste selected text.`
After the disclosure, explicitly agree within your host/provider terms, then paste your goals
and constraints. Ask for a brief and next steps. A later session needs selected text and fresh
agreement again. [Workflow examples](examples/standalone-workflows.md) show completed outputs
for the seven adapted workflows; the [fictional basis](examples/fictional-input.md) and
[advice example](examples/advice.md) cover the reused advice/drafting route.

Your host supplies the model and may charge for usage. Share only text you are entitled to
share. No browsing, attachment extraction, private-file lookup, connected records, sending,
applications or scheduling. Advice is not approved evidence or a saved career decision.
Requested files are new editable Markdown at a selected local path, with exact readback;
without suitable file tools, the output is complete text. The retained PDF/DOCX renderer
remains fictional-only. Read the [profile boundary](references/standalone-profile.md).

## Acceptance and host support

Version `0.2.0` uses the selected-text functionality described above. Consult the accompanying
release acceptance report for the exact package hash, tested hosts, outputs and boundary checks.
Structural checks alone do not establish host discovery or useful runtime behavior. The
[fresh-input trials](examples/standalone-runtime-scenarios.json) describe the acceptance cases.
No hard-coded minimum Codex build or universal runtime support is claimed. Cursor and Claude
host adaptations require their own verification; existing releases and legacy profiles remain
separate.

Project-authored source is [Apache-2.0](LICENSE), with [NOTICE](NOTICE).

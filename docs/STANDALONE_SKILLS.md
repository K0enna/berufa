# Berufa career Skills

The `selected_text_standalone_v1` profile packages nine Skills that work with selected pasted
text after a provider-processing disclosure and explicit sharing agreement. It reuses the
existing advice and drafting Skills and adapts the seven existing career workflows for
conversational use. It installs no core application, database, plugin or model client.

| Identifier | Useful output |
| --- | --- |
| `rolevane-setup` | A career brief, practical constraints and a next-step plan. |
| `career-evidence-onboarding` | An experience summary with source associations, conflicts and questions. |
| `career-role-evaluation` | Separate judgments on fit and whether a move is worthwhile, with support, gates and decisive questions. |
| `career-application-preparation` | A role-specific brief and complete requested material, reusing supplied evidence and effective wording. |
| `career-interview-preparation` | Likely questions, supplied submitted-claim checks, optional turn-by-turn practice and a call sheet. |
| `career-offer-comparison` | Comparable terms, guaranteed-cash downside, valid arithmetic, unknowns and decision questions. |
| `career-outcome-review` | A confirmed-event chronology, status conflicts, bounded feedback lessons and practical next steps. |
| `career-advice` | Advice on a career question with tradeoffs and next questions. |
| `career-create-content` | Complete drafts, selected edits or a read-only critique. |

## Start with one useful result

After installation, begin with the outcome you want:

```text
$rolevane-setup: Help me get started. Explain the sharing boundary, then help me
choose a direction, assess a role, or write something using only the text I share.
```

Agree to the disclosed sharing boundary before supplying career content. Bring only what
that task needs: a few goals and constraints, one posting plus relevant experience, or a
writing brief. If you already know the task, go straight to `$career-role-evaluation` or
`$career-create-content`; setup is optional. In the same conversation, the Skills reuse your
agreement and selected facts. You do not need a database, account connection or full dossier.

For a learning plan after assessing roles, ask `$career-advice: Use the assessments I supplied
to make a skill-gap report. Separate confirmed gaps from unknowns and suggest practical next
steps.` This reuses an existing Skill and does not search job boards or save a profile.

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

Use the starting path above after installation. A later session needs selected text and fresh
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

On native Windows, keep the configured Windows sandbox implementation when testing a
Skill. An isolated CLI run with `--ignore-user-config` also omits that setting. On an
already provisioned machine that uses the `elevated` implementation, select it explicitly
with `-c 'windows.sandbox="elevated"'` while retaining `--sandbox read-only` and approval
policy `never`. This selects the Windows sandbox backend; it does not grant full access.
Use only an implementation permitted and provisioned on that machine. See the
[Windows sandbox documentation](https://learn.chatgpt.com/docs/windows/windows-sandbox).
Do not disable sandboxing to make a Skill load.

After installation, start a fresh turn and check actual Skill and reference reads as well
as discovery. A listed identifier alone does not prove that the method loaded. A failed
read leaves that host unverified; preserve its error before changing settings.

Unreleased candidate `0.2.3` adds ranked interview probes, checks against deliberately supplied
submitted wording and requested mock interviews with one question at a time. Submitted documents
are optional; missing documents limit that comparison without blocking role-based practice.
Sharing and file boundaries remain unchanged. See the repository's
[0.2.3 acceptance report](https://github.com/K0enna/berufa/blob/main/docs/SKILLS_0_2_3_ACCEPTANCE.md)
for candidate identity, tested host, outputs and boundary checks. A source
candidate is separate from a published release asset. For a fresh local build, the builder
prints its ZIP SHA-256; the archive also contains per-member digests.
Structural checks alone do not establish host discovery or useful runtime behavior. The
[fresh-input trials](examples/standalone-runtime-scenarios.json) describe the acceptance cases.
No hard-coded minimum Codex build or universal runtime support is claimed. Cursor and Claude
host adaptations require their own verification; existing releases and legacy profiles remain
separate.

Project-authored source is [Apache-2.0](LICENSE), with [NOTICE](NOTICE).

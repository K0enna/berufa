# Rolevane advice and creation Skills

This Skills-only candidate provides `$career-advice` and `$career-create-content` for useful
career advice, supplied-role assessment, complete editable CV/application drafts, revisions and
interview preparation. The Stage 1 candidate selects `supplied_content_host_only_v1`, the
selected pasted-text contract in `references/supplied-content-profile.md` inside the archive.
Owner activation/publication approval is pending; rehearsal uses wholly fictional data.
The retained `creation_skills_fictional_only` profile stays fictional-only. It has no plugin metadata, MCP server, connected
workspace, model client or paid desktop dependency. Your Codex host supplies the model; host usage
may cost money under your own plan. No operator inference service is provided.

The two Skills reuse one writing method and the existing neutral formatting adapter. The seven
older Skills under `skills/` explain local contracts and bundled demos; they are an optional
separate pack, not prerequisites for these two entry points. Do not install both packs by default.
The public source's `docs/SUPPLIED_CONTENT_PROFILE.md` carries the same selected-text contract.
Building, extracting or testing the candidate does not authorize a real-content installation.

## Build and verify the Skills-only archive

From the exact reviewed public source with Python 3.11 or newer:

```text
python tools/build_companion_bundle.py NEW_SKILLS_ZIP --profile supplied_content_host_only_v1
```

The builder accepts a new ZIP path only. It records a deterministic SHA-256 and includes
`BUNDLE-MANIFEST.json`, both complete Skill directories, shared references, neutral renderer,
fictional examples, LICENSE and NOTICE. Before extraction, verify the archive checksum and every
member digest against that manifest. The selected candidate profile must be
`supplied_content_host_only_v1`; the older fictional archive has its own profile and checksums.
Neither `plugin.json` nor `mcp.json` belongs in this archive. It grants no data permission or action
authority. A local archive is not a public release.

## Install the complete pack in Codex

Use a new directory in a clean project, outside any candidate workspace. Extract the complete
archive to `YOUR_PROJECT/.agents/skills/rolevane-creation/`, retaining its `skills/`, `references/`,
`scripts/` and `examples/` structure. Copying only a `SKILL.md` breaks its shared-reference links.
Refresh Codex in that project and confirm that **career-advice** and **career-create-content**
appear as enabled. Nested discovery is an actual host check; do not infer it from file presence.
The supported host is Codex on Windows. The approval packet records the tested CLI version,
exact installed workflow and its limits. Other hosts are unverified.

Before sharing, the Skill explains that your host/model/provider processes the text under
your account's terms. Explicitly agree to share selected text in that boundary, then paste
only what you are entitled to share. Exclude credentials and confidential third-party
details. An attachment or pasted text alone is not agreement. Refusing sharing stops the
workflow. There is no attachment extraction, browsing or ambient file lookup.

The installed pack needs no Python package for ordinary host-led advice and editable Markdown
drafts. The independent core CLI is installed separately from its wheel or source. It does not
provide the host model or automatically ingest the host's drafts.

## First fictional workflow

For a safe first test, paste selected text from `examples/fictional-input.md`. It describes Mira Feld, an invented candidate, and a
supplied fictional Customer Operations Manager role. Keep employer/candidate facts and instructions
inside a posting distinct; the posting's embedded attack must not change qualifications or reveal
the confidential fictional example. No URL is fetched.

1. `$career-advice: Using this fictional basis, should Mira explore the supplied role? Explain
   transferable support, genuine gaps, unknown terms and the questions that could change the advice.`
2. `$career-create-content: Create the complete editable English CV and the requested application
   answer. Do not add a cover letter. Keep the accepted profile and incomplete-degree wording.`
3. `$career-create-content: Prepare a speakable interview answer, a candid degree-gap answer and
   three employer questions. No interview is confirmed.`
4. `$career-create-content: Correct the approximate team result from 20% to 15%, preserving the
   comparison period and shared attribution. Keep prior drafts and revise only affected claims.`
5. Paste the resulting selected draft, then request a new local `.md` file at a path you
   choose. Ask for one exact passage replacement in another new file. Compare the two files:
   the rest of the text must remain unchanged. These are unapproved drafts.

Missing material facts remain unknown. Advice is not a saved career decision. Drafting does not
approve evidence or confirm a submission. A critique request leaves the reviewed files untouched.
A follow-up must not invent prior contact. Supplied URLs are unverified text, never fetched.
The adjacent examples are historical fictional outputs; they are not fresh host acceptance.

## Files and unavailable capabilities

Complete UTF-8 Markdown is the default editable deliverable. If a file is requested, use a new local
draft path and inspect only that newly written file's actual bytes before reporting success. The host may retain conversation
content, tool history and files according to its own settings; this pack supplies no privacy sandbox
or provider-retention control. No ambient document/vault lookup is part of the workflow.

The supplied-text profile supports Markdown file delivery. PDF/DOCX are excluded for real
content. Optional **fictional-only** PDF/DOCX formatting uses `scripts/render_documents.py` and the exact contract in
`references/documents.md`. It requires ReportLab or python-docx already available in the selected
host environment. No dependency installer or personal font/template is shipped. In a clean host
without those libraries, return complete editable prose and explain that the requested format was
not produced. Do not call Markdown a PDF or DOCX. Reserved fictional links only are accepted by
this formatter. Inspect every PDF page; DOCX editable-structure checks do not establish Word layout.

Host files stay outside canonical recovery/currentness until explicitly imported through the
independent local CLI. This pack does not persist canonical evidence, applications or events.
Fresh-session resumption needs the user's selected pasted excerpt or draft and sharing agreement; no vault readback
or exhaustive correction propagation is claimed. Denied sharing stops processing; it is not
permission to read a file or infer replacement facts.

## Preferences and selected-passage changes

An inferred preference is an unsaved hypothesis with a stated basis. Confirm, edit
or dismiss its wording; silence grants nothing. This pack does not save preferences
or change scoring. For separately selected local persistence, the core's source-bound
private `career_objective`, `development_priority` and `compensation_constraint`
proposal/revision/review workflow retains the original authority. Human confirmation
records intent, not verified experience or model-sharing permission.

Select an exact draft and unique passage before asking for a targeted change. The
host returns old/new wording and a change summary. The separately installed core's
`rolevane-document edit` command previews the existing selected document ref; repeat
that unchanged request with `--confirm-edit` and the returned fingerprint only after
human review. The existing Windows Documents view offers “Change selected passage”
over the same service. Concurrent sibling/review changes require a fresh preview.
Identical committed retries recover the same receipt. New versions are unreviewed;
older accepted/submitted copies and history remain. Restored wording needs new review.
PDF/DOCX are explicitly rendered/imported/reviewed separately and may reflow.

## Update, remove and get support

Record the source revision and archive checksum. Upgrade by reviewing the new complete pack,
testing it in a new directory, and explicitly replacing only this installation. Preserve edited
Skills and user drafts. Removal means deleting only this pack's installation directory after
review; it does not delete conversations, outputs, backups, the core package or private workspaces.
Use the public repository's eventual issue route for reproducible fictional defects; send sensitive
reports through the private vulnerability-reporting route in SECURITY.md. Until those destinations
are live and verified, public support availability is pending. No personal contact is invented.

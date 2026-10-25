# Berufa Career ChatGPT plugin

The optional **Berufa Career 0.1.0** plugin packages the nine standalone Skills
**0.2.3** for ChatGPT Work and Codex desktop. It reuses their selected-text
instructions and resources unchanged. The existing standalone Skills archive
remains a separate distribution with the same output bytes.

## Build

Run from this public source checkout:

```text
python tools/build_companion_bundle.py new-plugin.zip --profile selected_text_standalone_v1 --as-plugin
```

The command refuses existing outputs. Repeating the build with unchanged inputs
produces identical bytes. `BUNDLE-MANIFEST.json` retains the selected-text profile,
Skills version, nine identifiers and every payload digest. Root `plugin.json`
provides the portable plugin identity and OpenAI listing metadata. Keep all
resources together. Verify archive membership and SHA-256 values before installing.

To build the original standalone Skills archive, omit `--as-plugin`.
The plugin flag rejects other content profiles. There is no MCP configuration,
app mapping, hook or backend model client in this package.

## Install and try locally

Use the supported local marketplace route in your desktop host. For example,
extract the archive to `plugins/berufa-career` beneath a new disposable marketplace
root. Add `.agents/plugins/marketplace.json` there:

```json
{
  "name": "berufa-career-test",
  "interface": {"displayName": "Berufa Career test"},
  "plugins": [{
    "name": "berufa-career",
    "source": {"source": "local", "path": "./plugins/berufa-career"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Productivity"
  }]
}
```

Register the root with `codex plugin marketplace add <absolute-marketplace-root>`.
Install through your host's supported controls, or use
`codex plugin add berufa-career@berufa-career-test` where supported. Inspect
`codex plugin list --json`, then verify the installed cache against the archive.
CLI installation and discovery are separate from ChatGPT desktop execution.
OpenAI's desktop guidance may require restarting the app to load a new local
source; leave that restart under the user's control.

Start a fresh local Work conversation and run setup. It should explain model
processing and request sharing agreement before processing supplied career text.
After agreeing, use fictional notes to try all nine workflows: a starting brief,
experience summary, role assessment, advice, complete draft, application answer,
interview practice, offer comparison and confirmed-event review. Inspect titles,
qualifications, currencies, shared contributions and unknowns. Correct one fact
and request a selected-passage edit. Critique should leave the draft unchanged.

Also test denied sharing, hostile instructions inside a posting, unsupported
browsing/attachments/connections/submission, existing-file preservation and a
fresh conversation without the old basis. Record the exact ZIP hash, host version,
prompts, actual outputs and failures. Do not infer runtime acceptance from the
package or a discovered Skill.

## Supported scope

Selected pasted text and complete editable chat output are supported. Requested
real-content files are new local Markdown drafts at explicit destinations, with
readback and no overwrite. Real-content PDF/Word rendering, attachment extraction,
browsing, connected career records, automatic memory and external actions are
excluded. The optional formatter is fictional-only. The desktop-only declaration
covers local file workflows. Web/mobile and other host/platform behavior require
their own acceptance.

The plugin is free; the chosen assistant/provider supplies the model under the
user's account terms and may charge for usage. It provides no privacy sandbox or
provider retention/deletion controls. See the packaged sharing profile and
[privacy information](https://berufa.com/en/privacy). The existing website icon
is reused without redesign. Publisher identity in the portal must match the
verified submitting identity; metadata alone does not prove verification.

## Publication handover

This source is preparation, not a directory release. After exact-package checks,
independent review and human testing, use the intended organization/project at
the [submission portal](https://platform.openai.com/plugins). Verify publishing
identity and access, upload the exact ZIP as Skills only, inspect imported
metadata and desktop-only status, and resolve required metadata/Skill scan issues.
Submission and final publication each require the owner's action or approval.

Skills-only plugins do not require the MCP-specific reviewer account, five
positive/three negative cases or demo recording. Local MCP submissions additionally
require approved-partner allowlisting; the existing connected companion stays
separate. Adding MCP to an existing Skills-only plugin is currently unsupported.
The listing does not claim connected features or verified web/mobile support.

Requirements checked 24 October 2026 against OpenAI's official
[package guide](https://developers.openai.com/plugins/build/plugins),
[submission guide](https://developers.openai.com/plugins/deploy/submission) and
[desktop-only guide](https://developers.openai.com/plugins/deploy/desktop-only).
Recheck those requirements before submission.

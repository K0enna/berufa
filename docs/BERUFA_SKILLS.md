# Berufa

## Put Your Experience To Work

Berufa helps you find the words for what you bring and ask better questions about your next
career move. Start with a practical recommendation or a complete draft you can edit. Keep
your facts, your voice and the final decision.

This complete open-source pack includes nine Skills for Codex:

| Berufa Skill | What You Can Explore |
| --- | --- |
| Career Advice | A supplied role, its trade-offs and the questions to ask next. |
| Career Drafts | A CV, application answer, message or interview answer grounded in selected text. |
| Setup | How to start and recover an independent local career workspace. |
| Evidence Onboarding | How source material becomes evidence you can review. |
| Role Evaluation | A structured approach to fit, transferable experience and gaps. |
| Application Preparation | The path from an assessed opportunity to an application brief. |
| Interview Preparation | Evidence-linked stories and employer questions. |
| Offer Comparison | Upside, downside and missing terms in a comparison. |
| Outcome Review | Learning from confirmed career-campaign events. |

Career Advice and Career Drafts accept selected pasted text after disclosure and your explicit
sharing agreement. The other seven explain public contracts and fictional demonstrations,
using generic commands while real data and results stay in your local terminal. All nine are
included; their permissions are not interchangeable. Local commands require the separately
installed core, which this ZIP does not install.

## Install The Complete Pack

At the approved Berufa.com launch step, download the complete `berufa-skills-0.1.2.zip` and
verify its published SHA-256. Extract the whole archive into a new
`YOUR_PROJECT/.agents/skills/berufa/` directory outside a career workspace. Keep `creation/`
and `workflow/` intact, including their manifests and resources. Refresh Codex and confirm
all nine names are enabled. User-wide installation uses `$HOME/.agents/skills/berufa/`.

The actual identifiers are `career-advice`, `career-create-content`, `rolevane-setup`,
`career-evidence-onboarding`, `career-role-evaluation`, `career-application-preparation`,
`career-interview-preparation`, `career-offer-comparison` and `career-outcome-review`.
The retained `rolevane-setup` and core command names are compatibility identifiers only;
the product and all display names are Berufa.

The tested host is Codex CLI 0.162.0-alpha.17.2 on Windows, Full access, approval policy
`never`. Other hosts and modes are not verified. Your own Codex plan supplies the model
and may charge for usage; no Berufa account or paid Berufa plugin is required.

## Try One Useful Question

Start with `$career-advice: Explain your sharing boundary before I paste selected text.`
After the disclosure, explicitly agree to share selected fictional text under your host/provider
terms. Paste the candidate and role from [the fictional example](creation/examples/fictional-input.md).

```text
$career-advice: Should Mira explore the role? Explain the fit, gaps and unknown terms.
Give three questions that would help her decide.

$career-create-content: Draft a complete English CV and the requested application answer.
Keep her incomplete degree and shared team result accurate. Return editable Markdown.
```

The [advice and drafting guide](creation/README.md) covers a full first workflow, file delivery
and one targeted edit. For a workflow Skill, ask a generic question such as
`$career-offer-comparison: Explain the public comparison workflow using placeholders only.`
The [workflow manifest](workflow/skills/manifest.toml) records those seven routes and stop conditions.

## Share Deliberately

Share only selected text you are entitled to share. Exclude credentials and confidential
third-party details. Your host/model provider processes shared text under your account's terms;
the pack supplies no privacy sandbox, local-only inference or retention/deletion controls.

No attachment extraction, browsing, connected workspace, ambient lookup, canonical mutation,
sending or submission is enabled. New Markdown is the supplied-content delivery format.
Real-content PDF/DOCX is excluded; optional formatting remains fictional-only. Drafts and
advice are not approved evidence or confirmed career events. Individual agreement is always required.

Review updates in a new directory before replacing only the installation. Preserve drafts
and local edits. Removal does not delete conversations or outputs. Do not post personal records
in public issues. Source: https://github.com/K0enna/berufa. Public go-live follows the Berufa.com
launch sequence and explicit owner approval, not installation or passing checks.

Project-authored source is [Apache-2.0](LICENSE), with [NOTICE](NOTICE). No plugin or application
is released by this pack.

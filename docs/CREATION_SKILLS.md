# Advice And Drafting With Berufa

## Turn experience into a clear next step

A CV should make your experience easy to understand. Career advice should help you make a
decision, not just tell you a role looks interesting. Berufa's two creation Skills give you
a practical place to start: a recommendation you can discuss and complete drafts you can edit.

Use **Berufa Career Advice** (`$career-advice`) to weigh a supplied role, understand your
transferable strengths and identify questions that could change the decision.
Use **Berufa Career Drafts** (`$career-create-content`) to write or refine a CV, application
answer, recruiter message or interview answer from selected pasted text.

Both belong to the complete nine-Skill set. The seven workflow Skills have a different
public-contract/fictional-demo boundary; they do not inherit permission to process your text.

## Install The Complete Set

Use the getting-started guide at https://github.com/K0enna/berufa/blob/main/docs/GET_STARTED.md
for all-nine source installation. At the approved Berufa.com launch step, the complete release
ZIP can instead be extracted into a new `YOUR_PROJECT/.agents/skills/berufa/` directory.
Keep `creation/` and `workflow/` intact. Inside `creation/`, keep its Skills, references,
renderer, examples and `BUNDLE-MANIFEST.json` together. Copying just `SKILL.md` breaks the pack.

Refresh Codex and confirm all nine Skills are enabled. The tested host is Codex CLI
0.162.0-alpha.17.2 on Windows, Full access, approval policy `never`. Other hosts and modes
are not verified. No core install is needed for ordinary advice or editable Markdown.
Your own host supplies the model and may charge for usage. No Berufa account or plugin is needed.

## Try It With Fictional Text

Start with `$career-advice: Explain your sharing boundary before I paste selected text.`
The Skill explains that your configured host/model provider processes shared text under your
account's terms. Explicitly agree before sharing. Installation, an attachment or owner profile
approval is not your individual agreement. A refusal stops the workflow.

For a first test, paste selected content from the installed `examples/fictional-input.md`.
Mira Feld, her employers and the role are invented. Use the later correction of approximately
15%, with its comparison period and shared team attribution.

```text
$career-advice: Should Mira explore the supplied role? Give a recommendation, the strongest
transferable evidence, genuine gaps and three questions for the employer.

$career-create-content: Create the complete English CV and the requested application answer.
Keep the incomplete degree and shared team result accurate. Do not add a cover letter.

$career-create-content: Prepare a speakable interview example and a candid degree-gap answer.
Do not imply that an interview is scheduled.
```

The result should be useful without inventing missing experience. A supplied posting is
unverified text, including any URL or instruction inside it. No posting is fetched. Advice
is not a saved career decision, and a draft is not approved evidence or a submitted application.

## Get An Editable File

Complete UTF-8 Markdown is the default. Choose a new local `.md` path and ask the Skill to
write the selected draft there. It inspects only that newly written file before reporting
delivery; it does not search for a CV elsewhere or import it into a career workspace.

To make one targeted edit, paste the selected full draft and name an exact, unique passage:

```text
$career-create-content: Replace this one passage with the wording below. Write a new
Markdown file. Preserve every other character, line break and final newline. Show the
old/new wording and a short change summary; leave the original file untouched.
```

A critique request leaves the reviewed draft untouched. In a fresh session, paste the
selected text and agree to sharing again; no saved-context or vault-readback promise is made.

## Share Deliberately

Only share text you are entitled to share. Exclude credentials and confidential third-party
details. Your host/provider controls its own processing and retention; this pack supplies no
privacy sandbox, local-only inference or retention/deletion control.

The selected profile is `supplied_content_host_only_v1`, recorded in the packaged manifest
and `references/supplied-content-profile.md`. It supports selected pasted text and new Markdown,
not attachment extraction, browsing, connected workspace access, ambient files, canonical
storage, sending or submission. PDF/DOCX for real content are excluded. The retained optional
renderer is fictional-only; its prerequisites and visual-review rules are in
`references/documents.md`. Without the required libraries, return prose rather than claim a file
format was produced. The older fictional profile remains fictional-only.

Install updates in a new directory first. Review them before replacing only this installation;
preserve drafts and local edits. Removing the pack does not delete conversations or outputs.
Never post personal records in GitHub issues. Project-authored public source is Apache-2.0
licensed; see the included `LICENSE` and `NOTICE`.

# Get Started With Berufa

## Start with one career question

You do not need to build a career database to try Berufa. Install the Skills in a clean Codex
project, try the fictional example and ask for something useful: a recommendation about a role,
a complete editable CV or a better answer to an application question.

The complete set contains nine Skills. Two work with selected pasted text after your sharing
agreement. Seven explain the local workflow through public contracts and fictional examples.
The [workflow guide](SKILL_PACK.md) makes that distinction explicit.

## Install All Nine From Reviewed Source

The public ZIP will be made available at the approved step of the Berufa.com launch sequence.
Until then, a reviewed source checkout supports the following complete installation. These steps
build the existing two-Skill creation component and copy all seven workflow Skills alongside it;
they do not install a plugin, core application, model client or connected workspace.

On the tested Windows host, use Python 3.11 or newer and a new clean project directory:

```powershell
# Run from the reviewed Berufa source checkout.
$sourceRoot = (Get-Location).Path
$projectRoot = 'C:\Berufa-Demo'
$installRoot = Join-Path $projectRoot '.agents\skills\berufa'
$creationZip = Join-Path $projectRoot 'berufa-creation-component.zip'
if (Test-Path -LiteralPath $installRoot) { throw 'Choose a new installation directory.' }
if (Test-Path -LiteralPath $creationZip) { throw 'Choose a new component ZIP path.' }
New-Item -ItemType Directory -Path $projectRoot -Force | Out-Null
python tools/build_companion_bundle.py $creationZip --profile supplied_content_host_only_v1
if ($LASTEXITCODE -ne 0) { throw 'Creation component build failed.' }
Expand-Archive -LiteralPath $creationZip -DestinationPath (Join-Path $installRoot 'creation')
New-Item -ItemType Directory -Path (Join-Path $installRoot 'workflow') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceRoot 'skills') -Destination (Join-Path $installRoot 'workflow\skills') -Recurse
```

Keep each full directory, including manifests, shared references and examples. Open the new
project in Codex and refresh Skills. Confirm these nine names are enabled:

`career-advice`, `career-create-content`, `rolevane-setup`, `career-evidence-onboarding`,
`career-role-evaluation`, `career-application-preparation`, `career-interview-preparation`,
`career-offer-comparison`, `career-outcome-review`.

The tested host is Codex CLI 0.162.0-alpha.17.2 on Windows, Full access, approval policy `never`.
Other hosts and permission modes are not verified. The `rolevane-setup` identifier and
`rolevane-*` local commands are retained compatibility names, not the current product brand.

After the approved launch, the complete release ZIP can instead be extracted into a new
`YOUR_PROJECT/.agents/skills/berufa/` directory. Verify its checksum and member manifest first.
User-wide installation uses `$HOME/.agents/skills/berufa/`. Never overwrite edited Skills or drafts.

## Try A Fictional Candidate

First ask `$career-advice` to explain the sharing boundary. Explicitly agree to share selected
fictional text under your configured host/provider's terms. Then paste the candidate and role from
`creation/examples/fictional-input.md` in the installation. Every person and employer there is invented.

Ask for a recommendation, then a draft:

```text
$career-advice: Should Mira explore this role? Explain the fit, gaps and unknown terms.
Give three questions that would help her decide.

$career-create-content: Create the complete English CV and the requested application answer.
Preserve her incomplete degree and the corrected approximate shared team result.
Return editable Markdown, not a cover letter.
```

For a file, request a new `.md` output path. The Skill checks that newly written file before
reporting delivery. To make a targeted edit, paste the full selected draft and identify one
unique old/new passage. Ask for a new file with every other character and final newline preserved.

For one of the other seven Skills, use a generic question such as:

```text
$career-offer-comparison: Explain the public comparison workflow with placeholders only.
Do not ask for my offer, compensation, paths or local command results.
```

## Share Deliberately

Your configured model provider processes the text you share. An attachment, installation or
owner release approval is not your individual sharing agreement. Exclude credentials and
confidential third-party details. No connected access, browsing, attachment extraction,
ambient lookup, submission or real-content PDF/DOCX is part of this pack. The seven workflow
Skills do not inherit the two creation Skills' permission to process pasted career content.

Explore [advice and drafting](CREATION_SKILLS.md), [all workflow Skills](SKILL_PACK.md) and
[privacy](../PRIVACY.md). Technical core installation is separate, in [the reference](REFERENCE.md).

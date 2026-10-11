# Format supplied fictional prose

This optional host-local adapter formats complete, selected fictional Markdown
into PDF and editable DOCX drafts. It adds no facts or model calls and performs no
network requests. The host selects the employer-facing text first; keep internal
claim maps, confidentiality notes and evidence gaps in separate host notes unless
the user explicitly selects them for the document. A render is not approval,
submission, a canonical record or a truth check.

Use one leading `# Title`, optional `## Section` headings, paragraphs separated by
blank lines, and `- ` bullets. Inline `[Profile](https://profile.example/person)`
links use reserved `.example`, `.invalid`, `.test` or `example.com/org/net` domains.
Hostnames must use valid ASCII DNS labels. Backslashes and malformed authorities
are rejected before creating output files; Unicode prose and link labels remain supported.
Other Markdown structures are unsupported. Body characters are escaped before
PDF markup; source instructions are plain supplied text, never executable code.
No attachments, images, templates or private assets are read. EN and DE text,
umlauts and EUR symbols are supported by the demonstrated neutral layout.
PDF fails and cleans its new output if a character is absent from the installed font.

The host must supply existing input and output roots plus a **new** output
directory inside the output root. Its parent must exist. All ancestors must be
ordinary local directories: symlinks, Windows reparse points, UNC and device paths
are rejected. The host must not select mapped network drives.
Inputs are UTF-8 `.md`, at most 128 KiB, 2000 characters per source line, 8000 per
paragraph, and 1000 blocks. Do not point these arguments at canonical data.
This is a trusted host filesystem boundary, not a sandbox against another process
changing paths concurrently. Do not use shared or attacker-controlled directories.

```text
<host-python> <plugin>/scripts/render_documents.py
  --input-root <fictional-input-directory> --input <complete-draft.md>
  --output-root <local-drafts-directory> --output-dir <local-drafts-directory>/revision-1
  --format pdf --format docx
```

Arguments must be on one shell command line (or continued with that shell's syntax).
`reportlab` and `python-docx` must already exist in the selected host environment;
no install, private renderer import or dependency download occurs. PDF fonts come
from ReportLab's installed Vera font resources, not copied personal font files.
Missing capabilities return an error before any output directory is acquired.
Formats can be requested individually. Unsupported formats fail closed.

The output directory is created exclusively; existing drafts are never replaced.
On a format failure, only the known new files from that run and its now-empty
directory are removed. Standard output returns the source SHA-256, actual file
paths, `local_unreviewed_draft` status and pending visual review. This temporary
receipt is neither persisted authority nor a new evidence store. Keep source basis,
qualifiers, revisions and decisions in explicit host notes. For a changed factual
basis, supply revised complete prose and choose a new directory; retain the earlier
draft until the user decides what to keep. Review or critique alone does not invoke
this command or create files.

Before delivering either format, reopen the files, check selected content and link
targets, render every page with the host's supported PDF/DOCX tooling, and inspect
for missing glyphs, overlap and overflow. DOCX ZIP/XML checks are not visual proof.
If the host has no supported DOCX page renderer, say that DOCX visual verification
is unavailable; do not claim delivery readiness. Do not infer ATS certification.

## Reuse decision

Direct local inspection of the existing CV adapter found exact personal title,
eight-role chronology, master-location and personal-layout requirements. Its demo
reads private context; the legacy letter generator imports private candidate data
and writes application folders. Those paths cannot serve fictional host prose
unchanged. This adapter uses installed supported ReportLab and python-docx APIs
for the small residual formatting task. It does not copy that personal renderer,
its factual contract, styling, examples or assets.

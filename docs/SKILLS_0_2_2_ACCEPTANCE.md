# Skills 0.2.2 acceptance

This source candidate extends the existing nine-Skill pack and fictional formatter. It
adds no dependency, provider, database, Skill identifier or broader content permission.
Source merge and installed acceptance are separate from publishing a tagged release.

The verified local `berufa-skills-0.2.2-reviewed.zip` has 43 members and SHA-256
`f4f4b3954b8a4f5f507985a576dcf66c1938dc6141fa1cf598328df35e698dc0`.
This report is outside the archive, so recording its digest does not change the archive.

## Verified behavior

- The existing ReportLab formatter reports actual drawn-block geometry, including split
  paragraphs. A short letter remains valid, a trailing heading produces a warning, and
  a sparse second page produces a warning. Mandatory visual review remains in place.
- Career advice and outcome review share one gap-report method. It deduplicates supplied
  vacancies, excludes explicitly declined targets, retains qualification gates, separates
  unknown evidence from confirmed gaps, and gives practical completion evidence.
- Setup reuses the user's selected outcome, facts and current sharing agreement. It can
  route an already requested writing task without requiring a career brief first.

## Host and tests

On 17 October 2026, Codex CLI `0.163.0-alpha.10.2` discovered all nine installed identifiers
from the extracted pack. Three fresh fictional conversations exercised onboarding into
writing, gap-report deduplication and insufficient-evidence handling. Actual Skill,
manifest and reference reads completed successfully. The observed route was
`gpt-6.1-sol/high`, approval `never`, sandbox `read-only`.

The earlier isolated runs omitted the machine's Windows sandbox selection through
`--ignore-user-config`; their file reads were rejected before command execution. Selecting
the already configured `windows.sandbox="elevated"` for the isolated process fixed those
reads while preserving the read-only policy. No global configuration or Windows security
setting was changed. See the installation guide for this bounded troubleshooting case.

The focused creation, layout, public-Skill and source-hygiene suites passed 50 tests.
The development extra installs the tested ReportLab 5.0.0 and python-docx 1.2.0 so the
required CI jobs execute these renderer cases instead of skipping them.
Five layout cases exercise real PDF/DOCX output and readback. Four pages from three PDF
fixtures were rendered and visually inspected. Nine extracted Skill frontmatters, bundle
closure/digests/reproducibility, targeted Ruff and an independent P2 review passed.
The PR's required checks supply final integrated-source validation.

A separate initial bounded local Freehire pilot made three public search requests. Nine records
yielded three genuinely new, officially resolved postings after checking existing records,
the earlier sample and an employer alias. None reached the assessment queue: two were
rejected by supplied hard gates and one required clarification. The pilot did not justify
adding a provider on that first sample. The user subsequently authorized a separate Freehire
integration and broader title-query adjustment; that work uses the private core's existing
free-radar owner rather than adding a provider to this Skills distribution. Private matching
inputs and role-level findings remain local.

This acceptance does not establish other hosts, real-content PDF/DOCX support, generic PDF
inspection, human acceptance, or automatic career-state changes.

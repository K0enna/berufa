## Problem

<!-- Link the issue or describe the verified defect/opportunity. -->

## Scope and non-goals

<!-- State the exact owner boundary and what remains unchanged. -->

## Risk surfaces

- [ ] Private data, logs, exports, or backups
- [ ] Untrusted documents, postings, URLs, or prompt injection
- [ ] Model/provider network or credential access
- [ ] Evidence approval, events, tools, or external actions
- [ ] Storage, schema, workspace, backup, or deletion
- [ ] Dependency, public export, CI, or release integrity
- [ ] None of the above

## Verification

- [ ] Focused checks for the changed behavior
- [ ] `python -m ruff format --check .`
- [ ] `python -m ruff check .`
- [ ] `python -m pytest`
- [ ] Build/package check when packaged files changed
- [ ] No real candidate data, private-derived fixtures, credentials, or raw provider/model content
- [ ] Licence/provenance recorded for every new dependency or asset

## Authority and review

<!-- State any user approval, unresolved human decision, and security/privacy review required. -->

- [ ] Generated content remains unapproved and creates no external event
- [ ] Maintainer review is required before merge or release

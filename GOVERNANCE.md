# Governance And Support

## Authority

The repository owner is the final maintainer and release authority. Maintainers may delegate
review, but only a maintainer may approve a release, security advisory, compatibility break, or
change to the evidence, privacy, provider, model, or external-action boundary.

Open source does not create an entitlement to support, response time, roadmap delivery, or
continued maintenance. Decisions prioritize user safety, private-data protection, deterministic
authority, and a maintainable local core over feature breadth.

## Supported versions

Before 1.0, only the latest published minor release receives security and correctness fixes. Older
versions may receive an advisory but no patch. Pre-1.0 interfaces may change when the release notes
and migration guidance say so. Unsupported versions should be upgraded or removed.

## Contributions and conduct

Every change is reviewed through a pull request. New providers, dependencies, schemas, storage
formats, public-data claims, skill authority, or security/privacy boundaries require a design issue
before implementation. Maintainers may close changes that broaden access, automate external
actions, or weaken evidence controls without a supported use case and verification plan.

Contributors must be respectful, must not harass or expose another person, and must never post real
candidate records, resumes, recruiter messages, compensation, credentials, or exploit details in a
public channel. Maintainers may hide/delete content, lock discussions, reject contributions, or ban
participants to protect people and the project. This repository uses these rules as its v1 code of
conduct rather than copying a third-party code-of-conduct text.

## Dependencies and providers

Dependency updates require licence/provenance review, focused tests, the full quality gate, and
review of packaged resources. Runtime dependencies remain minimal. CI Actions use immutable commit
pins and least-privilege permissions.

No network job source or model provider is required by the public core. A future provider is
disabled by default and must have an owner, current terms/data-flow record, bounded retries and
spend, security tests, and a fail-closed disable path. If an interface or its terms become unsafe or
unclear, maintainers disable it rather than silently changing source identity or bypassing access
controls.

## Releases and deprecation

Releases are built from a fresh reviewed public export and an exact immutable tag. Published files
receive SHA-256 checksums; files are never silently replaced under an existing version. A
vulnerable release is handled through an advisory and a new fixed version.

When practical, a deprecated supported interface remains documented for one minor release. A
security, privacy, provider-terms, or data-integrity risk may require immediate removal. Release
notes state the reason, impact, and migration/removal path.

If maintenance stops, the owner may archive the repository as read-only with the last supported
version and known limitations. Archival is not a promise to transfer the name, release keys,
package account, or support obligations.

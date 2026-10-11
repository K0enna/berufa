# Security Policy

## Supported versions

Before 1.0, only the latest published minor release receives security fixes.

| Version | Supported |
| --- | --- |
| Latest published 0.x minor | Yes |
| Earlier releases and unreleased branches | No |

## Security boundary

Rolevane is local-first, single-user software. The supported core runs on the
user's device, binds its optional browser UI to literal IPv4 loopback, and
ships with no model client, telemetry, application submission, or external messaging.
The optional exact Greenhouse lookup requires per-request confirmation. Its public
transport permits one credential-free HTTPS GET to the exact approved host, pins a
validated public DNS address for TLS, rejects redirects/proxies, and bounds JSON responses.

Documents, postings, issue text, skill content, and model/provider output are
untrusted data. They cannot approve evidence, authorize tools, confirm career
events, or create external actions. See `docs/THREAT_MODEL.md` and
`docs/DATA_AND_MODEL_POLICY.md`.

## Report privately

Use this repository's GitHub private vulnerability reporting flow:
**Security > Advisories > Report a vulnerability**.

Do not open a public issue containing exploit details, a resume, candidate
data, recruiter messages, compensation, database contents, raw prompts,
provider responses, tokens, or credentials. Use a fictional minimal
reproduction and include the affected version, component, impact, and
prerequisites. If private reporting is unavailable, do not post sensitive
details publicly; wait for the maintainer to restore a private route.

No response-time or fix-time SLA is promised. Maintainers will acknowledge,
triage, coordinate remediation, and credit reporters when requested and safe.

## Reportable findings

Report vulnerabilities that create material new capability, including:

- code execution or unsafe resource consumption from a supported document;
- private-data access across instance or workspace boundaries;
- bypass of evidence approval, event confirmation, or external-action gates;
- non-loopback exposure or bypass of Host, origin, CSRF, or artifact controls;
- unauthorized model/provider data transfer or credential disclosure;
- private content entering a public export, release, CI artifact, or log; or
- compromise of release integrity, checksums, dependencies, or CI authority.

## Out of scope and accepted risk

The project does not claim to protect data from malware or another process
running with the same operating-system user authority. It does not encrypt the
workspace, manage full-disk encryption, secure user-managed backups, or support
remote/multi-user deployment.

Unsupported reverse proxies, non-loopback binds, authenticated/protected-site
automation, third-party service outages, social engineering, job-posting
accuracy, and self-only effects without new authority are generally out of
scope. A third-party dependency vulnerability is reportable when the supported
Rolevane path is affected.

## Incident handling

Do not attempt external remediation, send follow-up messages, or alter confirmed
career events. Follow `docs/INCIDENT_RESPONSE.md`. High or critical unresolved
risk blocks a release.

# Rolevane release instructions

This source candidate contains a dormant manual release-candidate workflow. It does not publish a
package, create a tag or GitHub Release, deploy a service, or perform a career action.

## Staged distribution order

Stage 1 is the sanitized public repository, reusable core/CLI, and useful installable Skills.
The current preparation reuses the two advice/creation Skills in the
`supplied_content_host_only_v1` Skills-only candidate; the seven legacy synthetic-only
contract Skills remain optional. The selected-text boundary requires an explicit owner decision
in `SUPPLIED_CONTENT_PROFILE.md` before activation. Fictional rehearsal and local packaging do
not complete the public gate. Skills ship separately from the wheel; no plugin is submitted.

Stage 1 completes only after the approved public destination contains the reviewed clean-history
source, installation and first-use checks pass from that destination, private vulnerability reporting
and incident handling are verified, the selected useful-Skills profile has no unresolved blocker,
and the versioned release/download/provenance readback is complete.
Local export and passing private CI are preparation evidence, not public availability.

Only after that gate may the complete ChatGPT plugin proceed as Stage 2. The Windows application is
Stage 3 and stays parked until Stage 2 completes. Optional website work does not gate Stage 1. Each
merge, public creation/push, release, plugin submission, deployment or spending action still needs
its exact applicable approval. Existing licence and selected-scope naming-risk approvals remain valid.

## Local deterministic bundle

Use the exact committed public source bytes, with Python 3.11 or newer. A checkout with
automatic newline conversion can contain different bytes from Git, so materialize the selected
commit into a new directory before building. From the public repository checkout:

```bash
git -c core.autocrlf=false archive --format=tar --output=../rolevane-source.tar HEAD
mkdir ../rolevane-source
tar -xf ../rolevane-source.tar -C ../rolevane-source
cd ../rolevane-source
```

Use unused output paths and record the selected commit before leaving the checkout. Then build:

```bash
python -m tools.release_artifacts dist
python -m tools.release_artifacts dist --verify-only
```

With no SBOM input this produces exactly one wheel, one PEP 517 sdist,
`RELEASE-MANIFEST.json`, and `SHA256SUMS`. The manifest records exact byte counts and SHA-256
digests. This path is deterministic, offline, and is not a signature, a formal SBOM, or a hosted
provenance attestation.

## Manual attested release candidate

The release owner must also publish the selected Skills-only archive built from the same public
source with `python tools/build_companion_bundle.py NEW_SKILLS_ZIP --profile supplied_content_host_only_v1`, plus the
exact source manifest and archive checksum. That archive contains the two complete Skill directories
and their shared resources, not a plugin. The optional legacy pack is obtained from the exact source.
The existing attestation workflow below covers the core artifacts; it does not attest the Skills ZIP
or create a GitHub Release. Do not claim broader asset attestation than the workflow actually emits.
After approved workflow readback, create the separately approved GitHub Release and upload the exact
core/SBOM/manifest/checksum files and selected Skills assets. Reinstall the actual public download.

The attested path is available only after the exact exported public repository and tag exist. A
release owner must dispatch `Release candidate` at the exact existing `vX.Y.Z` tag, passing the
same tag as the workflow input:

```bash
gh workflow run release-candidate.yml --ref vX.Y.Z -f tag=vX.Y.Z
```

The workflow fails unless its GitHub ref, GitHub SHA, checked-out commit, requested tag, and public
`pyproject.toml` version all identify the same candidate. It then:

1. installs the audit extra, including the pinned `pip-audit==2.10.1`;
2. audits all declared runtime dependencies, including platform-conditional ones, and generates
   an external CycloneDX 1.4 JSON SBOM (this inventory covers the package, not just the CI host);
3. validates the SBOM against the declared direct runtime dependencies and builds the exact bundle;
4. verifies the manifest and `SHA256SUMS` readback;
5. creates build-provenance and SBOM attestations; and
6. uploads the wheel, sdist, SBOM, manifest, and checksums as one workflow artifact.

The workflow is intentionally `workflow_dispatch` only. It does not create tags or releases,
publish to an index, deploy, or send messages or applications. Do not dispatch it from this
pre-public source candidate: the public repository, tag, attestation permissions, and final human
approval must exist first.

## Exact readback

Download the workflow artifact without renaming its files and run:

```bash
python -m tools.release_artifacts downloaded-bundle --verify-only
```

Confirm that `RELEASE-MANIFEST.json` lists the wheel, sdist, and `SBOM.cdx.json`, and that every
line in `SHA256SUMS` names one of those three files. Verify the hosted attestations against the
exact downloaded wheel and sdist in GitHub's attestation view or with the GitHub CLI for the final
repository. A valid digest or attestation proves binding to these bytes; it does not prove absence
of vulnerabilities, licence compliance, reproducibility, or semantic truth of the SBOM contents.

## Human release gates

The exact owner-approved copyright notice is implemented in `NOTICE`. The owner selected Rolevane
on 2026-09-06, with `rolevane` as the public distribution and intended repository name. Name
selection does not reserve a domain/package or establish trademark clearance. An earlier
2026-09-06 technical preparation started from merged executable PR181 base `311e896`; its retained
export manifest, artifacts/checksums, and fresh one-commit public-only history snapshot are
historical evidence only. They predate PR186 and the final documentation corrections and do not
bind the current tree. The final local closeout generated and verified one exact candidate,
bundle, checksum set, and public-only history snapshot; its separate identities are recorded only
in the retained local report. These earlier preview-only receipts do not bind the current
two-Skill candidate or satisfy its workflow/publication gate. Exact tree/history secret scans returned two source-backed
unverified test-fixture observations, classified locally as false positives. It does not create
or publish a public repository. Technical acceptance for the selected preview profile is
synthetic-only skill use plus the separate deterministic local CLI route. Human acceptance,
volunteer trials, comparisons, and real-data conversational use are not required and must not be
inferred from green tests. Runtime consent, local review, exact confirmation, and external-action
boundaries remain mandatory. Fresh native host discovery and seven structured explicit skill inputs
produced seven instruction-specific no-input/manual outcomes without tool execution. This verifies
the selected install-discovery route; earlier discarded-body, self-report, and shell-policy attempts
remain failed or partial evidence. No host sandbox or global isolation is claimed. On 2026-09-12
the owner accepted the remaining naming/trademark and reviewed single-job Greenhouse-use risks
and cancelled further trademark and provider inquiries. No professional clearance, provider
permission or legal-compliance guarantee is claimed. Preserve that disposition for the selected
free CLI/synthetic-only scope; reassess changed use or concrete conflicting evidence.
Before publication, complete destination private reporting and incident handling, exact public
export/history scans, provenance/attestation and final release approval. Any controlled live
provider observation retains its own bounded authorization. The open-source readiness record
remains the authoritative checklist:
[`OPEN_SOURCE_READINESS.md`](OPEN_SOURCE_READINESS.md).

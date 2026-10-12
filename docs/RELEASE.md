# Berufa Release Instructions

This is a maintainer reference. For the product introduction, use the [README](../README.md).
For installation and a useful first task, use [Get Started](GET_STARTED.md).

## Unpublished standalone candidate

The separate `selected_text_standalone_v1` profile adapts the seven existing workflows for
useful selected-text work and reuses the two creation Skills. Build it with
`python tools/build_companion_bundle.py NEW_CANDIDATE.zip --profile selected_text_standalone_v1`.
It has a flat `skills/` inventory and standalone metadata inside `BUNDLE-MANIFEST.json`,
not the legacy `workflow/skills/manifest.toml` contract. See
[candidate installation and limits](STANDALONE_SKILLS.md) and
[the selected-text boundary](STANDALONE_PROFILE.md).

The proposed `0.2.0` candidate is unpublished. Reproducible archive and installation structure
do not prove actual host discovery or useful model behavior. Fresh-input runtime trials,
independent review, exact-source checks and explicit owner release approval remain required.
The profiles and historical distribution described below remain separate and intact.

## Launch Gate And Release Order

**Public go-live follows the Berufa.com launch sequence.** The owner identifies the appropriate
step and explicitly approves the exact package there. Keep `K0enna/berufa` private until then.
Documentation updates in the private repository are preparation, not publication. Domain
availability, a prepared ZIP or passing checks do not authorize a launch.

The order remains useful Skills first, complete ChatGPT plugin second, application third.
The Skills release includes all nine existing public Skills. The two creation Skills retain
`supplied_content_host_only_v1`; the seven workflow Skills retain their separate public-contract
and fictional-demo boundary. No plugin submission, application feature work or connected
workspace activation is part of this preparation. PR245 must not be merged by this task.

## Build The Selected Skills

Use the exact reviewed source and Python 3.11 or newer. Record the source revision and normalized
source manifest. Build into an unused output path; the existing builder never overwrites a ZIP.
The complete-pack composition option, when present in the selected reviewed builder, is:

```text
python tools/build_companion_bundle.py NEW_SKILLS_ZIP --profile supplied_content_host_only_v1 --include-workflow-skills
```

That archive retains `creation/` and `workflow/skills/`, the two distinct manifests, shared
resources, examples, renderer, LICENSE and NOTICE. `PACKAGE-MANIFEST.json` binds every member.
There is no `plugin.json`, `mcp.json`, core binary, private history, workspace or credential.
The creation-only component remains available without `--include-workflow-skills` for existing
callers. [Get Started](GET_STARTED.md) documents a source installation that works with that
existing component builder plus all seven workflow directories, without assuming the new option
has already been integrated into the default branch.

Owner profile acceptance does not supply an individual sharing agreement. It does not allow
the seven workflow Skills to process pasted career content. Markdown is the supported delivery
for the supplied-text profile; the unchanged optional formatter remains fictional-only.

## Verify Before Publication

Check the exact source, ZIP and every member digest. Rebuild from the selected source through
the actual CLI in a different working directory and compare bytes. On the declared supported
host, verify installation and discovery of all nine Skills, actual useful use within each route,
complete drafting, one exact selected edit and supported file delivery. Reuse source-matching
unchanged evidence; do not treat old checks as coverage for changed behavior.

Keep required quality, secret-scan and review gates. Integrate sanitized changes through the
protected source route; do not weaken settings or bypass approval. Do not publish private Git
history, career records, credentials, raw host/model logs or private-derived fixtures.

## Publish Only At The Approved Launch Step

After the owner authorizes the exact package and launch step:

1. Confirm the approved destination, source version, tag, ZIP bytes and source manifest.
2. Confirm required exact-source checks and review, and working support/private reporting routes.
3. Create only the approved tag/release and upload the exact approved assets and checksums.
4. Download as an unauthenticated public consumer and verify every asset digest.
5. Install from that actual public download and verify discovery, useful advice/drafting,
   the selected edit and supported file delivery on the declared host.
6. Record the actual release/source/download receipt. Do not claim completion from local preparation.

No release, visibility change, merge, plugin submission or deployment is implied by these instructions.
Previous publication was withdrawn; older tags/assets and checks are historical evidence, not
permission to relaunch. Before approving a release, consult the current owner approval packet.

## Independent Core Artifacts

The retained core package has its own version and attestation rules. A Skills version does not
change `pyproject.toml` or authorize a core release. From exact committed public source, the
existing offline core artifact route is:

```bash
python -m tools.release_artifacts dist
python -m tools.release_artifacts dist --verify-only
```

It builds one wheel and sdist, `RELEASE-MANIFEST.json` and `SHA256SUMS`. Checksums bind bytes;
they are not signatures, hosted attestations or guarantees of security or semantic truth.
An optional external CycloneDX SBOM must match the declared dependencies and pass the existing
validator. The dormant manual `Release candidate` workflow attests core artifacts only, not
the Skills ZIP. Its tag, ref, commit and core version must match exactly. Do not dispatch it
for a differently versioned Skills tag or claim it publishes a GitHub Release.

Project-authored source is Apache-2.0 with the exact owner-approved LICENSE/NOTICE. Dependencies
keep their own licences. The owner-selected Berufa name is not a professional clearance claim.
See [readiness](OPEN_SOURCE_READINESS.md), [privacy](../PRIVACY.md) and [security](../SECURITY.md).

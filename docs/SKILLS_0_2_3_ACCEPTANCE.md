# Standalone Skills 0.2.3 acceptance

Candidate status: implemented and locally installed; structural and bounded actual-host checks passed.
The local P0–P2 correctness review passed. No tagged release or universal host-support claim is created.

The existing `career-interview-preparation` Skill adds likely interviewer questions with supplied
source reasons, optional checks against deliberately supplied submitted wording and requested mock
interviews with one question at a time. The same nine Skill IDs, sharing agreement, selected-text
boundary and writing owner remain in place. Missing submitted documents limit comparison without
blocking role-based practice. No core, Windows application, website, dependency or service is added.

The user requested a ChatGPT Pro plan before implementation. The completed [plan](plans/interview-practice-20261018.md)
was retrieved at commit `7f479dad2ea4f0ee9e6d201da69631df288c9298` and checked for source scope,
completeness and consistency with the existing pack. Only that plan file was written by the remote
planner. Its configured model was user-reported as 6 Pro; native tools did not independently verify
runtime identity or change the setting. Anti-slop was unavailable in that runtime, so the explicitly
supplied inline writing guidance was used. Local implementation retains the existing owners.

## Package and structural checks

The final local Skills ZIP has 43 members and the same nine identifiers. SHA-256:
`dfb4f03a829086292535e8b8ebdad3311e7e9acc596799e74398858493fe7dcf`.
Its member digests were verified, and every installed member was read back against the ZIP.
The prior accepted installed package was preserved before replacement; no concurrent local edits
were found. Already merged public-main renderer/creation fixes remain included.

Sixty focused tests passed: `test_creation_bundle.py`, `test_creation_layout.py`,
`test_public_skill_pack.py` and `test_public_source_hygiene.py`. Targeted Ruff check and format
checks passed. The 15 bundle checks passed again after the final observer-criterion clarification,
including version, nine-Skill discovery metadata, reference closure, digests, reproducible builds
and third-working-directory use. No new test framework or implementation-mirroring test was added;
the existing extracted-manifest version assertion changed to `0.2.3`.

## Actual installed-host observations

Four fresh native Codex conversations exercised 18 actual user/assistant turns. The acting model
read the installed Skill, manifest and shared references; it was not supplied expected answers.
All four native catalogs listed the nine installed Skill IDs. Persisted turn contexts recorded
`gpt-6.1-sol` with `high` effort, approval `never` and a read-only sandbox. The CLI reported
`0.163.0-alpha.10`. Ten shell read commands succeeded; none read application/private files,
called an external source or changed state. The configured Windows sandbox backend was retained.

| Scenario | Observed result |
| --- | --- |
| Existing grounded answer | Produced the requested opening/story, shared approximate result, candid budget gap, employer questions and call sheet. No scheduled event was invented. |
| Ranked probes and submitted conflicts | Prior feedback came first; required budget/degree needs preceded preferred reporting. Headcount, attribution, precision and degree discrepancies were surfaced. The current draft did not replace submitted wording. A count correction and compatible extra detail updated preparation while other discrepancies remained. |
| Interactive controls | Asked one question and waited. Feedback identified the actual answer's unsupported sole attribution and exact precision. Hint, retry, skip and stop preserved the active question/attempt state. The recap counted one actual answer; live-call assistance was refused. Missing submitted documents did not block practice or produce a false comparison. |
| Document status and sharing | Denial overrode pasted instructions claiming agreement. An unknown document version remained unknown; the later supplied submitted excerpt received only an excerpt-level consistency check. Revocation stopped processing without a content recap. |

Two observer expectations were narrowed to the useful contract after reading the outputs: a
mock-only request need not repeat a warning about a comparison it does not request or claim;
an explicitly stated unknown-version limit can resolve correctness without an extra mandatory
question. This did not change the Skill, shared methods, discovery metadata or fictional user
turns. Only acceptance-list wording and its manifest digest differ from the trial ZIP, whose
SHA-256 was `88783364f19bf94f1b15a7f10a1b3f957165f2de39891b223896c192d8382998`.
Member-by-member comparison verified that the behavior-bearing sources and user inputs are
unchanged. Scenario JSON remains an observer specification; these observations are separate proof.

These are bounded fictional observations, not measured interview success, guaranteed question
prediction, injection resistance or human acceptance. Unknown-profile and later-session rules
remain with the unchanged shared boundary; this task does not claim a new behavioral run of every
scenario in the package. No real candidate transcript enters the repository.

## Review and delivery

One primary local P0–P2 review completed with no actionable findings, using the existing helper,
secret scanner, explicit `gpt-6.1-sol/high` route and disabled access fallback. One preparation
attempt stopped before model invocation because the prompt path was absolute; the packet was
placed at a repository-relative path and the same primary review then completed. No remediation
batch or additional local review pass was needed. The subsequent change only records this result.

Required hosted Codex review and repository CI have their own durable results on the task's pull
request; they are distinct from the local structural and installed-host observations above.
User-authorized planning and implementation do not constitute a merge or tagged release.

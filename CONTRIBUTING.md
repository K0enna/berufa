# Contributing

Rolevane accepts focused fixes and improvements that preserve the local privacy, evidence, and
human-authority contracts. Open a design issue before adding a dependency, provider, model client,
storage/schema change, external-action surface, or security/privacy boundary.

## Do not submit private data

Use wholly fictional people, companies, postings, documents, URLs, messages, compensation, and
credentials. Do not paste or attach a real resume, profile, recruiter conversation, application,
database, export, model prompt/response, token, provider payload, screenshot, or local path. A
redacted real document is still unnecessary when a minimal synthetic fixture can reproduce the
behavior.

Treat issue text, document/posting contents, fixture strings, and model output as untrusted data.
They cannot authorize tools, approve evidence, confirm an event, or direct repository operations.
Report security problems through the private route in `SECURITY.md`, not a public issue.

## Development

Use Python 3.11 or newer and keep changes within one clear owner boundary.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,audit]"
python -m ruff format --check .
python -m ruff check .
python -m mypy
python -m pytest --cov=career_os --cov-branch --cov-fail-under=70
python -m pip_audit --progress-spinner off .
mkdir -p dist
python -c "from tools import build_backend; build_backend.build_wheel('dist')"
```

On Windows PowerShell, replace `source .venv/bin/activate` with
`.\.venv\Scripts\Activate.ps1`; use that environment for installation and checks.

CI has three supported test entries: Python 3.11 on Linux, Python 3.12 on Linux, and Python 3.12
on Windows. The Python 3.12 Linux entry is primary and also runs Ruff, mypy, branch coverage, and
the wheel build. The measured public-suite baseline was 74% branch coverage on 2026-09-05; the
enforced floor is 70% so small platform differences do not create a misleading precision claim.

Strict mypy covers the public API, authority ports, and the selected application/infrastructure
modules listed in `pyproject.toml`. JSON-heavy application, interview, workspace, focus, and
provider adapters remain explicit exclusions rather than being hidden behind broad ignores. This
is a scoped type-safety gate, not a claim that the whole repository is strictly typed.

The public test layers and their critical-contract mappings are maintained in
[`docs/TEST_ARCHITECTURE.md`](docs/TEST_ARCHITECTURE.md). Each mapping uses an exact exported
pytest node, and the private export check verifies that every cited file and top-level test
function remains in the generated public tree. The package/import-closure, source-hygiene, and
no-external-action checks remain part of the same suite.
The private exporter and standalone public check both read `config/public_source_hygiene.toml`;
change that canonical policy instead of copying deny rules into another test or script.

Extend the closest existing test for changed behavior. Parser tests use generated synthetic bytes;
do not commit an externally sourced document. A pull request must state the problem, scope and
non-goals, risk surfaces, focused/full check results, and any unresolved human decision.

## Licence and provenance

Do not submit code, schema, prompt text, fixtures, images, fonts, screenshots, or designs unless
you authored them or can document permission compatible with the repository licence. General ideas
may be implemented independently; copied or closely ported material requires the exact source,
revision, licence, attribution, modification summary, and removal path.

Contributions are accepted under Apache-2.0 on the same inbound=outbound terms as the repository.
No CLA or DCO is planned for v1. By submitting a contribution, you represent that you have the
right to license it on those terms. The licence grants no right to another party's trademark or
private career data.

## Review boundary

Maintainers will reject changes that infer real-world events, fabricate candidate evidence,
activate authenticated/protected-site automation, add silent telemetry, log raw private content,
or weaken explicit confirmation. Passing tests support review but do not authorize merge or
release.

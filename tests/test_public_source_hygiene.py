from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
POLICY_FIELDS = {"version", "deny_prefixes", "deny_paths", "deny_content_patterns"}
IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "venv",
}
IGNORED_FILES = {".coverage", "coverage.xml"}


def _load_hygiene_policy(root: Path) -> dict[str, object]:
    path = root / "config" / "public_source_hygiene.toml"
    with path.open("rb") as stream:
        policy = tomllib.load(stream)
    assert set(policy) == POLICY_FIELDS
    assert policy["version"] == 1
    for field in ("deny_prefixes", "deny_paths", "deny_content_patterns"):
        values = policy[field]
        assert isinstance(values, list) and values
        assert all(isinstance(value, str) and value for value in values)
        assert len(values) == len(set(values))
    return policy


def _public_candidate(tmp_path: Path) -> Path:
    private_manifest = ROOT / "config" / "public_repo_export.toml"
    if not private_manifest.is_file():
        return ROOT

    from tools.export_public_repo import DEFAULT_BOUNDARY, export_public_repository

    destination = tmp_path / "public-candidate"
    export_public_repository(
        ROOT,
        destination,
        manifest_path=private_manifest,
        boundary_path=DEFAULT_BOUNDARY,
    )
    return destination


def _candidate_files(root: Path) -> list[Path]:
    if (root / ".git").exists():
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        selected: list[Path] = []
        for raw in result.stdout.split(b"\0"):
            if not raw:
                continue
            path = root.joinpath(*PurePosixPath(raw.decode("utf-8")).parts)
            if path.is_symlink():
                raise AssertionError(
                    f"public source contains a symbolic link: {path.relative_to(root).as_posix()}"
                )
            selected.append(path)
        return selected

    selected: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(
            part in IGNORED_DIRECTORIES or part.endswith(".egg-info") for part in relative.parts
        ):
            continue
        if path.is_symlink():
            raise AssertionError(f"public source contains a symbolic link: {relative.as_posix()}")
        if (
            path.is_file()
            and path.name not in IGNORED_FILES
            and not path.name.startswith(".coverage.")
        ):
            selected.append(path)
    return selected


def test_public_source_tree_excludes_private_workspace_content(tmp_path: Path) -> None:
    candidate = _public_candidate(tmp_path)
    files = _candidate_files(candidate)
    policy = _load_hygiene_policy(candidate)
    deny_prefixes = tuple(
        prefix.casefold()
        for prefix in policy["deny_prefixes"]  # type: ignore[union-attr]
    )
    deny_paths = {
        path.casefold()
        for path in policy["deny_paths"]  # type: ignore[union-attr]
    }
    patterns = [
        re.compile(pattern)
        for pattern in policy["deny_content_patterns"]  # type: ignore[union-attr]
    ]
    failures: list[str] = []

    for path in files:
        relative = path.relative_to(candidate).as_posix()
        normalized = relative.casefold()
        if any(normalized.startswith(prefix) for prefix in deny_prefixes):
            failures.append(f"denied path: {relative}")
        if normalized in deny_paths:
            failures.append(f"denied path: {relative}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            failures.append(f"non-UTF-8 source: {relative}")
            continue
        for pattern in patterns:
            if pattern.search(text):
                failures.append(f"private content pattern {pattern.pattern!r}: {relative}")

    assert not failures, "\n".join(failures)


def test_public_hygiene_policy_preserves_flexible_whitespace(tmp_path: Path) -> None:
    candidate = _public_candidate(tmp_path)
    policy = _load_hygiene_policy(candidate)
    patterns = [
        re.compile(pattern)
        for pattern in policy["deny_content_patterns"]  # type: ignore[union-attr]
    ]

    for private_text in (
        "David" + "\tSterz",
        "Head" + "\nof  Customer\tExcellence",
    ):
        assert any(pattern.search(private_text) for pattern in patterns)


def test_public_hygiene_allows_only_the_canonical_notice(tmp_path: Path) -> None:
    candidate = _public_candidate(tmp_path)
    notice = candidate / "NOTICE"
    owner_name = " ".join(("David", "Sterz"))
    expected_notice = "Copyright 2026 " + owner_name + "\n"
    notice_text = notice.read_text(encoding="utf-8")
    assert notice_text == expected_notice

    name_paths = []
    for path in _candidate_files(candidate):
        text = path.read_text(encoding="utf-8")
        if owner_name.casefold() in text.casefold():
            name_paths.append(path.relative_to(candidate).as_posix())
    assert name_paths == ["NOTICE"]

    policy = _load_hygiene_policy(candidate)
    patterns = [
        re.compile(pattern)
        for pattern in policy["deny_content_patterns"]  # type: ignore[union-attr]
    ]
    assert not any(pattern.search(notice_text) for pattern in patterns)
    assert any(pattern.search("Copyright 2026 " + owner_name + "\nextra") for pattern in patterns)
    assert any(pattern.search("copyright 2026 " + owner_name.casefold()) for pattern in patterns)

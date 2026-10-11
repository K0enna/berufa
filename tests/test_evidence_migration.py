from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from career_os import evidence_migration
from career_os.evidence_migration import REQUIRED_CONTEXT_FILES, dry_run_migration, main


def _legacy_context(root: Path) -> Path:
    root.mkdir()
    for filename in REQUIRED_CONTEXT_FILES:
        (root / filename).write_text(f"# {filename}\nSynthetic content only.\n", encoding="utf-8")
    (root / "CANDIDATE_EVIDENCE.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence": [
                    {
                        "fact_id": "fictional.fact",
                        "source_file": "CANDIDATE_PROFILE.md",
                        "source_heading": "Profile",
                        "publication_boundary": "direct_application_and_interview",
                        "statement": "This candidate content must not appear in the report.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def _link_directory(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def test_migration_dry_run_is_content_free_unapproved_and_side_effect_free(
    tmp_path: Path,
) -> None:
    root = _legacy_context(tmp_path / "context")
    before = {path.name: path.read_bytes() for path in root.iterdir()}

    report = dry_run_migration(root)
    rendered = json.dumps(report.to_dict())

    assert report.content_included is False
    assert report.vault_changed is False
    assert report.approval_inferred is False
    assert report.candidates[0].approval_status == "unapproved"
    assert "statement" not in rendered
    assert "This candidate content" not in rendered
    assert "fictional.fact" not in rendered
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before
    assert not (tmp_path / "data").exists()


def test_migration_cli_writes_only_the_explicit_report(tmp_path: Path) -> None:
    root = _legacy_context(tmp_path / "context")
    output = tmp_path / "reports" / "migration.json"

    assert main(["--context-root", str(root), "--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["migration"] == "legacy-context-catalogue-to-evidence-vault-v2"
    assert payload["vault_changed"] is False

    output.write_text("stale report\n", encoding="utf-8")
    assert main(["--context-root", str(root), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["vault_changed"] is False


def test_migration_report_replaces_a_raced_input_hardlink_without_changing_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _legacy_context(tmp_path / "context")
    source = root / "CANDIDATE_PROFILE.md"
    output = tmp_path / "reports" / "migration.json"
    output.parent.mkdir()
    before = source.read_bytes()
    original_check = evidence_migration._reject_output_collisions

    def race_output(candidate: Path, context_root: Path) -> None:
        original_check(candidate, context_root)
        candidate.hardlink_to(source)

    monkeypatch.setattr(evidence_migration, "_reject_output_collisions", race_output)

    assert main(["--context-root", str(root), "--output", str(output)]) == 0
    assert source.read_bytes() == before
    assert json.loads(output.read_text(encoding="utf-8"))["vault_changed"] is False


def test_migration_report_rejects_a_raced_parent_redirect_to_an_input_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _legacy_context(tmp_path / "context")
    report_parent = tmp_path / "reports"
    report_parent.mkdir()
    moved_parent = tmp_path / "original-reports"
    output = report_parent / "CANDIDATE_PROFILE.md"
    source = root / "CANDIDATE_PROFILE.md"
    before = source.read_bytes()
    original_check = evidence_migration._reject_output_collisions

    def race_parent(candidate: Path, context_root: Path) -> None:
        original_check(candidate, context_root)
        report_parent.rename(moved_parent)
        _link_directory(report_parent, root)

    monkeypatch.setattr(evidence_migration, "_reject_output_collisions", race_parent)
    try:
        with pytest.raises(ValueError, match="Anchored replacement"):
            main(["--context-root", str(root), "--output", str(output)])
    finally:
        if os.name == "nt" and report_parent.exists():
            report_parent.rmdir()
        elif report_parent.is_symlink():
            report_parent.unlink()

    assert source.read_bytes() == before


@pytest.mark.parametrize("filename", [*REQUIRED_CONTEXT_FILES, "CANDIDATE_EVIDENCE.json"])
def test_migration_cli_rejects_reserved_output_paths(tmp_path: Path, filename: str) -> None:
    root = _legacy_context(tmp_path / "context")
    target = root / filename
    before = target.read_bytes()

    with pytest.raises(ValueError, match="migration input"):
        main(["--context-root", str(root), "--output", str(target)])

    assert target.read_bytes() == before


def test_migration_cli_reserves_absent_catalog_output_path(tmp_path: Path) -> None:
    root = _legacy_context(tmp_path / "context")
    catalog = root / "CANDIDATE_EVIDENCE.json"
    catalog.unlink()

    with pytest.raises(ValueError, match="migration input"):
        main(["--context-root", str(root), "--output", str(catalog)])

    assert not catalog.exists()


@pytest.mark.parametrize("alias_type", ["symlink", "hardlink"])
def test_migration_cli_rejects_existing_input_alias_output(tmp_path: Path, alias_type: str) -> None:
    root = _legacy_context(tmp_path / "context")
    source = root / "CANDIDATE_PROFILE.md"
    alias = tmp_path / f"report-{alias_type}.json"
    try:
        if alias_type == "symlink":
            alias.symlink_to(source)
        else:
            alias.hardlink_to(source)
    except OSError:
        pytest.skip(f"{alias_type} creation is unavailable on this Windows host")
    before = source.read_bytes()

    with pytest.raises(ValueError, match="migration input"):
        main(["--context-root", str(root), "--output", str(alias)])

    assert source.read_bytes() == before


def test_migration_cli_fails_closed_when_alias_check_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _legacy_context(tmp_path / "context")
    source = root / "CANDIDATE_PROFILE.md"
    alias = tmp_path / "report-hardlink.json"
    try:
        alias.hardlink_to(source)
    except OSError:
        pytest.skip("Hardlink creation is unavailable on this Windows host")
    before = source.read_bytes()

    def fail_samefile(self: Path, other: Path) -> bool:
        raise OSError("samefile failure")

    monkeypatch.setattr(Path, "samefile", fail_samefile)

    with pytest.raises(OSError, match="samefile failure"):
        main(["--context-root", str(root), "--output", str(alias)])

    assert source.read_bytes() == before
    assert alias.read_bytes() == before


def test_migration_rejects_symlinked_inputs(tmp_path: Path) -> None:
    root = _legacy_context(tmp_path / "context")
    external = tmp_path / "outside.md"
    external.write_text("outside", encoding="utf-8")
    profile = root / "CANDIDATE_PROFILE.md"
    profile.unlink()
    try:
        profile.symlink_to(external)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this Windows host")

    with pytest.raises(ValueError, match="regular file"):
        dry_run_migration(root)


@pytest.mark.parametrize("filename", ["APPLICATION_STANDARDS.md", "CANDIDATE_EVIDENCE.json"])
def test_migration_rejects_context_redirect_between_input_check_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    root = _legacy_context(tmp_path / "context")
    outside = _legacy_context(tmp_path / "outside")
    moved = tmp_path / "original-context"
    original_is_symlink = Path.is_symlink
    redirected = False

    def redirect_after_check(path: Path) -> bool:
        nonlocal redirected
        result = original_is_symlink(path)
        if path == root / filename and not redirected:
            root.rename(moved)
            _link_directory(root, outside)
            redirected = True
        return result

    monkeypatch.setattr(Path, "is_symlink", redirect_after_check)
    try:
        with pytest.raises(ValueError, match="Migration input"):
            dry_run_migration(root)
        assert redirected
    finally:
        if redirected:
            if os.name == "nt":
                root.rmdir()
            else:
                root.unlink()


@pytest.mark.parametrize(
    ("first_id", "second_id", "message"),
    [
        ("", "valid.fact", "invalid fact_id"),
        ("duplicate.fact", "duplicate.fact", "Duplicate legacy evidence identity"),
    ],
)
def test_migration_rejects_invalid_or_duplicate_legacy_identities(
    tmp_path: Path,
    first_id: str,
    second_id: str,
    message: str,
) -> None:
    root = _legacy_context(tmp_path / "context")
    catalog = json.loads((root / "CANDIDATE_EVIDENCE.json").read_text(encoding="utf-8"))
    first = catalog["evidence"][0]
    first["fact_id"] = first_id
    catalog["evidence"].append({**first, "fact_id": second_id})
    (root / "CANDIDATE_EVIDENCE.json").write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(ValueError, match=message) as excinfo:
        dry_run_migration(root)
    if message.startswith("Duplicate"):
        assert "duplicate.fact" not in str(excinfo.value)

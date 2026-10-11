from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
import tomllib
import venv
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from tools import build_backend, release_artifacts
from tools.product_package import load_product_package

ROOT = Path(__file__).resolve().parents[1]


def _load_root_package():
    return load_product_package(ROOT, require_tracked=(ROOT / ".git").exists())


def _write_test_sbom(path: Path, *, names: tuple[str, ...] | None = None) -> None:
    package = _load_root_package()
    component_names = names or tuple(
        release_artifacts._dependency_distribution_name(item) for item in package.dependencies
    )
    payload = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "version": 1,
        "components": [
            {"name": name, "type": "library", "version": "1.0.0"} for name in component_names
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_product_package_manifest_is_explicit_and_import_closed() -> None:
    package = _load_root_package()

    assert package.name == "rolevane"
    assert package.files == (
        "src/career_os/__init__.py",
        "src/career_os/models.py",
        "src/career_os/instance_paths.py",
        "src/career_os/store.py",
        "src/career_os/opportunity_identity.py",
        "src/career_os/authority_ports.py",
        "src/career_os/product_runtime.py",
        "src/career_os/synthetic_runtime.py",
        "src/career_os/evidence_contract.py",
        "src/career_os/evidence_records.py",
        "src/career_os/application/__init__.py",
        "src/career_os/application/evidence.py",
        "src/career_os/application/inventory.py",
        "src/career_os/application/official_postings.py",
        "src/career_os/application/opportunities.py",
        "src/career_os/application/companion.py",
        "src/career_os/application/offers.py",
        "src/career_os/offer_economics.py",
        "src/career_os/application/outcomes.py",
        "src/career_os/outcome_records.py",
        "src/career_os/outcome_values.py",
        "src/career_os/opportunity_ids.py",
        "src/career_os/infrastructure/outcomes.py",
        "src/career_os/outcome_workflow.py",
        "src/career_os/focus.py",
        "src/career_os/application/focus.py",
        "src/career_os/infrastructure/focus.py",
        "src/career_os/focus_workflow.py",
        "src/career_os/application_records.py",
        "src/career_os/application/applications.py",
        "src/career_os/infrastructure/applications.py",
        "src/career_os/application_workflow.py",
        "src/career_os/interview_records.py",
        "src/career_os/application/interviews.py",
        "src/career_os/infrastructure/interviews.py",
        "src/career_os/infrastructure/content_artifacts.py",
        "src/career_os/interview_workflow.py",
        "src/career_os/application/workspaces.py",
        "src/career_os/application/tasks.py",
        "src/career_os/application/documents.py",
        "src/career_os/infrastructure/workspaces.py",
        "src/career_os/infrastructure/tasks.py",
        "src/career_os/infrastructure/documents.py",
        "src/career_os/workspace_workflow.py",
        "src/career_os/task_workflow.py",
        "src/career_os/document_workflow.py",
        "src/career_os/application/recovery.py",
        "src/career_os/infrastructure/recovery.py",
        "src/career_os/application/instances.py",
        "src/career_os/infrastructure/instances.py",
        "src/career_os/application/instance_data.py",
        "src/career_os/infrastructure/instance_data.py",
        "src/career_os/instance_workflow.py",
        "src/career_os/companion_workflow.py",
        "src/career_os/file_transactions.py",
        "src/career_os/process_control.py",
        "src/career_os/infrastructure/__init__.py",
        "src/career_os/infrastructure/evidence.py",
        "src/career_os/infrastructure/evidence_storage.py",
        "src/career_os/infrastructure/opportunities.py",
        "src/career_os/infrastructure/companion.py",
        "src/career_os/infrastructure/offers.py",
        "src/career_os/infrastructure/provider_http.py",
        "src/career_os/infrastructure/greenhouse.py",
        "src/career_os/ats_urls.py",
        "src/career_os/official_posting.py",
        "src/career_os/public_api/__init__.py",
        "src/career_os/evidence_vault.py",
        "src/career_os/evidence_migration.py",
        "src/career_os/evidence_onboarding.py",
        "src/career_os/synthetic_evidence.py",
        "src/career_os/document_intake.py",
        "src/career_os/role_packs.py",
        "src/career_os/opportunity_workflow.py",
        "src/career_os/offer_workflow.py",
        "src/career_os/skill_validation.py",
        "src/career_os/role_pack_data/__init__.py",
        "src/career_os/synthetic_role_pack_fixtures.py",
        "src/career_os/participant_flow.py",
        "src/career_os/product_journey.py",
        "src/career_os/participant_app.py",
    )
    assert package.resources == (
        "src/career_os/role_pack_data/commercial_leadership.v1.json",
        "src/career_os/role_pack_data/customer_post_sales_leadership.v1.json",
        "src/career_os/role_pack_data/generic_fallback.v1.json",
        "src/career_os/role_pack_data/product_leadership.v1.json",
        "src/career_os/role_pack_data/role-pack.schema.json",
        "src/career_os/role_pack_data/senior_expert_ic.v1.json",
        "src/career_os/role_pack_data/services_solutions_leadership.v1.json",
    )
    assert package.dependencies == (
        "defusedxml>=0.7,<1",
        "pypdf>=6.14,<7",
        "tzdata==2026.4; sys_platform == 'win32'",
    )
    assert package.scripts == {
        "rolevane-product": "career_os.participant_app:main",
        "rolevane-evidence-smoke": "career_os.product_journey:main",
        "rolevane-journey-smoke": "career_os.product_journey:full_main",
        "rolevane-product-smoke": "career_os.synthetic_runtime:main",
        "rolevane-evidence-migration-dry-run": "career_os.evidence_migration:main",
        "rolevane-evidence-import": "career_os.evidence_onboarding:main",
        "rolevane-evidence-list": "career_os.evidence_onboarding:source_list_main",
        "rolevane-evidence-read": "career_os.evidence_onboarding:source_main",
        "rolevane-evidence-propose": "career_os.evidence_onboarding:proposal_main",
        "rolevane-evidence-interview": "career_os.evidence_onboarding:interview_main",
        "rolevane-role-pack-explain": "career_os.role_packs:main",
        "rolevane-role-evaluate-supplied": "career_os.opportunity_workflow:main",
        "rolevane-role-decide-supplied": "career_os.opportunity_workflow:decision_main",
        "rolevane-offer-compare": "career_os.offer_workflow:main",
        "rolevane-event-record": "career_os.outcome_workflow:main",
        "rolevane-event-list": "career_os.outcome_workflow:list_main",
        "rolevane-outcome-report": "career_os.outcome_workflow:report_main",
        "rolevane-focus": "career_os.focus_workflow:main",
        "rolevane-application": "career_os.application_workflow:main",
        "rolevane-interview": "career_os.interview_workflow:main",
        "rolevane-workspace": "career_os.workspace_workflow:main",
        "rolevane-instance": "career_os.instance_workflow:main",
        "rolevane-skill-validate": "career_os.skill_validation:main",
        "rolevane-role-fetch": "career_os.official_posting:main",
        "rolevane-companion-review": "career_os.companion_workflow:main",
        "rolevane-task": "career_os.task_workflow:main",
        "rolevane-document": "career_os.document_workflow:main",
    }
    public_project = ROOT / "public_repo" / "pyproject.toml"
    if not public_project.is_file():
        public_project = ROOT / "pyproject.toml"
    metadata = tomllib.loads(public_project.read_text(encoding="utf-8"))
    assert metadata["project"]["scripts"] == package.scripts


def test_product_package_rejects_unpacked_internal_import(tmp_path: Path) -> None:
    package = tmp_path / "src" / "career_os"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "ready.py").write_text("from career_os.missing import VALUE\n", encoding="utf-8")
    (package / "missing.py").write_text("VALUE = 1\n", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "product_package.toml").write_text(
        "version = 1\n"
        'name = "career-os"\n'
        "dependencies = []\n"
        'files = ["src/career_os/__init__.py", "src/career_os/ready.py"]\n'
        "resources = []\n"
        '[scripts]\nsmoke = "career_os.ready:VALUE"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unpackaged internal imports"):
        load_product_package(tmp_path, require_tracked=False)


def test_wheel_contains_only_explicit_participant_modules(tmp_path: Path) -> None:
    wheel_name = build_backend.build_wheel(str(tmp_path))
    wheel = tmp_path / wheel_name
    package = _load_root_package()
    public_source_root = ROOT / "public_repo"
    if not public_source_root.is_dir():
        public_source_root = ROOT
    notice_bytes = (public_source_root / "NOTICE").read_bytes()
    expected_modules = {Path(path).relative_to("src").as_posix() for path in package.files}
    expected_resources = {Path(path).relative_to("src").as_posix() for path in package.resources}

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        packaged_modules = {name for name in names if name.startswith("career_os/")}
        assert packaged_modules == expected_modules | expected_resources
        assert "career_os/cli.py" not in names
        assert "career_os/providers/public_ats.py" not in names
        metadata = archive.read("rolevane-0.1.0.dist-info/METADATA").decode("utf-8")
        entry_points = archive.read("rolevane-0.1.0.dist-info/entry_points.txt").decode("utf-8")
    assert "Requires-Dist: defusedxml>=0.7,<1" in metadata
    assert "Requires-Dist: pypdf>=6.14,<7" in metadata
    assert "Name: rolevane\n" in metadata
    assert "rolevane-product-smoke = career_os.synthetic_runtime:main" in entry_points
    assert "rolevane-product = career_os.participant_app:main" in entry_points
    assert "rolevane-evidence-smoke = career_os.product_journey:main" in entry_points
    assert "rolevane-journey-smoke = career_os.product_journey:full_main" in entry_points
    assert "A local-first, evidence-safe toolkit" in metadata
    assert "A privacy-first, evidence-safe recruiting operating system" not in metadata
    assert "rolevane-evidence-migration-dry-run = career_os.evidence_migration:main" in entry_points
    assert "rolevane-evidence-import = career_os.evidence_onboarding:main" in entry_points
    assert "rolevane-evidence-list = career_os.evidence_onboarding:source_list_main" in entry_points
    assert "rolevane-evidence-read = career_os.evidence_onboarding:source_main" in entry_points
    assert "rolevane-evidence-propose = career_os.evidence_onboarding:proposal_main" in entry_points
    assert (
        "rolevane-evidence-interview = career_os.evidence_onboarding:interview_main" in entry_points
    )
    assert "rolevane-role-pack-explain = career_os.role_packs:main" in entry_points
    assert "rolevane-role-evaluate-supplied = career_os.opportunity_workflow:main" in entry_points
    assert (
        "rolevane-role-decide-supplied = career_os.opportunity_workflow:decision_main"
        in entry_points
    )
    assert "rolevane-skill-validate = career_os.skill_validation:main" in entry_points
    assert "career-os = career_os.cli:main" not in entry_points
    assert "rolevane-role-fetch = career_os.official_posting:main" in entry_points
    assert "License-File: LICENSE\n" in metadata
    assert "License-File: NOTICE\n" in metadata
    expected_notice = "Copyright 2026 " + " ".join(("David", "Sterz"))
    assert notice_bytes.decode("utf-8").splitlines() == [expected_notice]
    with zipfile.ZipFile(wheel) as archive:
        assert (
            archive.read("rolevane-0.1.0.dist-info/licenses/LICENSE")
            == (public_source_root / "LICENSE").read_bytes()
        )
        assert archive.read("rolevane-0.1.0.dist-info/licenses/NOTICE") == notice_bytes


def test_product_wheel_is_reproducible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    original_init = zipfile.ZipInfo.__init__

    def build_with_clock_and_platform(destination: Path, platform: int, timestamp: float) -> Path:
        def platform_init(info: zipfile.ZipInfo, *args: object, **kwargs: object) -> None:
            original_init(info, *args, **kwargs)
            info.create_system = platform

        with monkeypatch.context() as context:
            context.setattr(zipfile.time, "time", lambda: timestamp)
            context.setattr(zipfile.ZipInfo, "__init__", platform_init)
            return destination / build_backend.build_wheel(str(destination))

    first_wheel = build_with_clock_and_platform(first, 0, 1_600_000_000)
    second_wheel = build_with_clock_and_platform(second, 3, 1_700_000_000)

    assert first_wheel.read_bytes() == second_wheel.read_bytes()
    with zipfile.ZipFile(first_wheel) as archive:
        assert archive.infolist()
        assert all(
            type(info) is zipfile.ZipInfo
            and info.create_system == 3
            and info.date_time == (1980, 1, 1, 0, 0, 0)
            for info in archive.infolist()
        )


def test_product_sdist_is_reproducible_and_public_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    def build_with_clock(destination: Path, timestamp: float) -> Path:
        with monkeypatch.context() as context:
            context.setattr(gzip.time, "time", lambda: timestamp)
            return destination / build_backend.build_sdist(str(destination))

    first_sdist = build_with_clock(first, 1_600_000_000)
    second_sdist = build_with_clock(second, 1_700_000_000)
    assert first_sdist.read_bytes() == second_sdist.read_bytes()
    gzip_header = first_sdist.read_bytes()[:10]
    assert gzip_header[:3] == b"\x1f\x8b\x08"
    assert gzip_header[3] & 0x08 == 0  # The outer archive has no embedded filename.
    assert int.from_bytes(gzip_header[4:8], "little") == 0

    package = _load_root_package()
    public_source_root = ROOT / "public_repo"
    if not public_source_root.is_dir():
        public_source_root = ROOT
    notice_bytes = (public_source_root / "NOTICE").read_bytes()
    with tarfile.open(first_sdist, "r:gz") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        root_name = "rolevane-0.1.0"
        expected_files = {
            *build_backend._SDIST_BUILD_FILES,
            "PKG-INFO",
            *package.files,
            *package.resources,
        }
        assert root_name in names
        assert {
            member.name.removeprefix(f"{root_name}/") for member in members if not member.isdir()
        } == expected_files
        for member in members:
            parts = PurePosixPath(member.name).parts
            assert parts[0] == root_name
            assert all(part not in {"", ".", ".."} for part in parts)
            assert not member.issym() and not member.islnk()
            assert member.mtime == 0
            assert member.uid == member.gid == 0
            assert member.uname == member.gname == ""
            assert member.mode == (0o755 if member.isdir() else 0o644)
        pyproject = archive.extractfile(f"{root_name}/pyproject.toml").read()
        pkg_info = archive.extractfile(f"{root_name}/PKG-INFO").read()
        readme = archive.extractfile(f"{root_name}/README.md").read()
        notice = archive.extractfile(f"{root_name}/NOTICE").read()
    assert b"A local-first, evidence-safe toolkit" in pyproject
    assert pkg_info == build_backend._metadata_bytes(package)
    assert b"Name: rolevane\n" in pkg_info
    assert b"Version: 0.1.0\n" in pkg_info
    assert b"Requires-Python: >=3.11\n" in pkg_info
    assert b"License-File: NOTICE\n" in pkg_info
    assert notice == notice_bytes
    expected_notice = "Copyright 2026 " + " ".join(("David", "Sterz"))
    assert notice.decode("utf-8").splitlines() == [expected_notice]
    assert b"Requires-Dist: defusedxml>=0.7,<1\n" in pkg_info
    assert b"Requires-Dist: pypdf>=6.14,<7\n" in pkg_info
    assert b"PyYAML" not in pyproject
    assert b"candidate-neutral participant runtime" not in readme
    assert b"public_repo/" not in b"\n".join(name.encode() for name in names)


def test_wheel_can_be_built_from_unpacked_sdist(tmp_path: Path) -> None:
    sdist_directory = tmp_path / "sdist"
    sdist_directory.mkdir()
    sdist = sdist_directory / build_backend.build_sdist(str(sdist_directory))
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    with tarfile.open(sdist, "r:gz") as archive:
        archive.extractall(unpacked, filter="data")
    source_root = unpacked / "rolevane-0.1.0"

    from_sdist = tmp_path / "from-sdist"
    from_sdist.mkdir()
    pip_cache = tmp_path / "pip-cache"
    pip_cache.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            str(source_root),
            "--wheel-dir",
            str(from_sdist),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": "", "PIP_CACHE_DIR": str(pip_cache)},
    )
    built_wheels = list(from_sdist.glob("rolevane-*.whl"))
    assert len(built_wheels) == 1
    direct_directory = tmp_path / "direct"
    direct_directory.mkdir()
    direct_wheel = direct_directory / build_backend.build_wheel(str(direct_directory))
    assert built_wheels[0].read_bytes() == direct_wheel.read_bytes()


def test_release_manifest_and_checksums_are_deterministic_and_fail_closed(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_manifest = release_artifacts.build_release(ROOT, first)
    second_manifest = release_artifacts.build_release(ROOT, second)
    assert first_manifest == second_manifest
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }
    assert first_manifest["runtime_dependencies"] == list(sorted(_load_root_package().dependencies))
    assert release_artifacts.verify_release_artifacts(first) == first_manifest

    false_inventory = tmp_path / "false-inventory"
    shutil.copytree(first, false_inventory)
    false_manifest_path = false_inventory / release_artifacts.MANIFEST_FILENAME
    false_manifest = json.loads(false_manifest_path.read_text(encoding="utf-8"))
    false_manifest["runtime_dependencies"] = ["fictional-dependency==0"]
    false_manifest_path.write_bytes(release_artifacts._canonical_json(false_manifest))
    with pytest.raises(ValueError, match="Requires-Dist set differs"):
        release_artifacts.verify_release_artifacts(false_inventory)

    missing = tmp_path / "missing"
    shutil.copytree(first, missing)
    (missing / "rolevane-0.1.0.tar.gz").unlink()
    with pytest.raises(ValueError, match="missing or has extra artifacts|differs"):
        release_artifacts.verify_release_artifacts(missing)

    extra = tmp_path / "extra"
    shutil.copytree(first, extra)
    (extra / "unexpected.txt").write_text("synthetic", encoding="utf-8")
    with pytest.raises(ValueError, match="differs from its manifest"):
        release_artifacts.verify_release_artifacts(extra)

    mismatched = tmp_path / "mismatched"
    shutil.copytree(first, mismatched)
    wheel = mismatched / "rolevane-0.1.0-py3-none-any.whl"
    wheel.write_bytes(wheel.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="metadata does not match"):
        release_artifacts.verify_release_artifacts(mismatched)

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "stale.txt").write_text("synthetic", encoding="utf-8")
    with pytest.raises(ValueError, match="must be empty"):
        release_artifacts.build_release(ROOT, occupied)

    linked = tmp_path / "linked"
    try:
        linked.symlink_to(first, target_is_directory=True)
    except OSError:
        pass
    else:
        with pytest.raises(ValueError, match="must not use symbolic links"):
            release_artifacts.verify_release_artifacts(linked)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("member-count", "unsafe number"),
        ("directory-size", "central directory"),
        ("compressed-size", "compressed size"),
        ("declared-size", "declared size"),
        ("ratio", "compression ratio"),
        ("metadata-size", "METADATA exceeds"),
    ],
)
def test_wheel_raw_budgets_reject_before_zipfile_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    message: str,
) -> None:
    metadata_name = (
        f"{build_backend._distribution_name(use_public_template=True)}-0.1.0.dist-info/METADATA"
    )
    wheel = tmp_path / "synthetic.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            metadata_name, b"Name: rolevane\nVersion: 0.1.0\nRequires-Python: >=3.11\n"
        )
    payload = bytearray(wheel.read_bytes())
    central = payload.index(release_artifacts._ZIP_CENTRAL_DIRECTORY_SIGNATURE)
    eocd = payload.rindex(release_artifacts._ZIP_EOCD_SIGNATURE)
    if mutation == "member-count":
        struct.pack_into(
            "<HH",
            payload,
            eocd + 8,
            release_artifacts.MAX_WHEEL_MEMBERS + 1,
            release_artifacts.MAX_WHEEL_MEMBERS + 1,
        )
    elif mutation == "directory-size":
        struct.pack_into(
            "<L",
            payload,
            eocd + 12,
            release_artifacts.MAX_WHEEL_CENTRAL_DIRECTORY_BYTES + 1,
        )
    elif mutation == "compressed-size":
        struct.pack_into(
            "<L",
            payload,
            central + 20,
            release_artifacts.MAX_WHEEL_MEMBER_COMPRESSED_BYTES + 1,
        )
    elif mutation == "declared-size":
        struct.pack_into(
            "<L",
            payload,
            central + 24,
            release_artifacts.MAX_WHEEL_MEMBER_BYTES + 1,
        )
    elif mutation == "ratio":
        struct.pack_into("<LL", payload, central + 20, 1, 101)
    else:
        unsafe_metadata_size = release_artifacts.MAX_WHEEL_METADATA_BYTES + 1
        struct.pack_into(
            "<LL",
            payload,
            central + 20,
            unsafe_metadata_size,
            unsafe_metadata_size,
        )
    wheel.write_bytes(payload)

    monkeypatch.setattr(
        release_artifacts.zipfile,
        "ZipFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ZipFile must not run before raw wheel preflight")
        ),
    )
    with pytest.raises(ValueError, match=message):
        release_artifacts._verify_wheel_metadata(
            wheel,
            {"name": "rolevane", "version": "0.1.0", "requires_python": ">=3.11"},
            [],
        )


def test_wheel_rejects_bzip2_metadata_before_zipfile_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata_name = (
        f"{build_backend._distribution_name(use_public_template=True)}-0.1.0.dist-info/METADATA"
    )
    wheel = tmp_path / "bzip2-metadata.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_BZIP2) as archive:
        archive.writestr(metadata_name, b"x" * 2_000_054)
    payload = bytearray(wheel.read_bytes())
    central = payload.index(release_artifacts._ZIP_CENTRAL_DIRECTORY_SIGNATURE)
    struct.pack_into("<L", payload, central + 24, 1024)
    wheel.write_bytes(payload)

    monkeypatch.setattr(
        release_artifacts.zipfile,
        "ZipFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ZipFile must not run before compression-method preflight")
        ),
    )
    with pytest.raises(ValueError, match="unsupported compression method"):
        release_artifacts._verify_wheel_metadata(
            wheel,
            {"name": "rolevane", "version": "0.1.0", "requires_python": ">=3.11"},
            [],
        )


def test_release_artifact_digest_streams_without_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "synthetic.whl"
    payload = b"synthetic artifact bytes"
    artifact.write_bytes(payload)
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("artifact hashing must stream")
        ),
    )

    digest = release_artifacts._file_digest(artifact, kind="wheel")

    assert digest["bytes"] == len(payload)
    assert digest["sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_release_artifact_digest_rejects_oversized_sparse_file_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    artifact = tmp_path / ("synthetic.whl" if kind == "wheel" else "synthetic.tar.gz")
    with artifact.open("wb") as stream:
        stream.truncate(release_artifacts.MAX_RELEASE_ARTIFACT_BYTES + 1)
    original_open = Path.open

    def fail_if_opened(path: Path, *args: object, **kwargs: object):
        if path == artifact:
            raise AssertionError("oversized artifact must be rejected before hashing")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_if_opened)
    with pytest.raises(ValueError, match="physical size limit"):
        release_artifacts._artifact_digest(artifact)


def test_release_sbom_is_validated_copied_and_bound_to_bundle(tmp_path: Path) -> None:
    source = tmp_path / "externally-generated.json"
    _write_test_sbom(source, names=("defusedxml", "PYPDF", "tzdata"))
    output = tmp_path / "sbom-release"

    manifest = release_artifacts.build_release(ROOT, output, sbom_path=source)

    assert [item["kind"] for item in manifest["artifacts"]].count("sbom") == 1
    assert (output / release_artifacts.SBOM_FILENAME).read_bytes() == source.read_bytes()
    assert release_artifacts.verify_release_artifacts(output) == manifest
    checksum_names = {
        line.split("  ", 1)[1]
        for line in (output / release_artifacts.CHECKSUMS_FILENAME).read_text().splitlines()
    }
    assert checksum_names == {item["filename"] for item in manifest["artifacts"]}

    missing = tmp_path / "sbom-missing"
    shutil.copytree(output, missing)
    (missing / release_artifacts.SBOM_FILENAME).unlink()
    with pytest.raises(ValueError, match="missing or has extra|differs from its manifest"):
        release_artifacts.verify_release_artifacts(missing)

    extra = tmp_path / "sbom-extra"
    shutil.copytree(output, extra)
    (extra / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="differs from its manifest"):
        release_artifacts.verify_release_artifacts(extra)

    tampered = tmp_path / "sbom-tampered"
    shutil.copytree(output, tampered)
    (tampered / release_artifacts.SBOM_FILENAME).write_bytes(
        (tampered / release_artifacts.SBOM_FILENAME).read_bytes() + b" "
    )
    with pytest.raises(ValueError, match="metadata does not match"):
        release_artifacts.verify_release_artifacts(tampered)

    wrong_dependency = tmp_path / "wrong-dependency.json"
    _write_test_sbom(wrong_dependency, names=("defusedxml", "not-a-runtime-dependency"))
    with pytest.raises(ValueError, match="differ from runtime dependencies"):
        release_artifacts.build_release(
            ROOT, tmp_path / "wrong-dependency-release", wrong_dependency
        )

    invalid_type = tmp_path / "invalid-component-type.json"
    _write_test_sbom(invalid_type)
    invalid_payload = json.loads(invalid_type.read_text(encoding="utf-8"))
    invalid_payload["components"][0]["type"] = "package"
    invalid_type.write_text(json.dumps(invalid_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="not valid for CycloneDX 1.4"):
        release_artifacts.build_release(
            ROOT, tmp_path / "invalid-component-type-release", invalid_type
        )


def test_public_metadata_does_not_fall_back_to_private_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "public_repo").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "private-only"\nversion = "9.9.9"\n', encoding="utf-8"
    )
    monkeypatch.setattr(build_backend, "ROOT", tmp_path)

    with pytest.raises(ValueError, match="not a regular file"):
        build_backend._project()


def test_editable_metadata_keeps_checkout_distribution_and_entry_points(
    tmp_path: Path,
) -> None:
    private_checkout = (ROOT / "public_repo").is_dir()
    distribution = "career-os" if private_checkout else "rolevane"
    normalized = distribution.replace("-", "_")
    wheel_name = build_backend.build_editable(str(tmp_path))

    assert wheel_name == f"{normalized}-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(tmp_path / wheel_name) as archive:
        metadata = archive.read(f"{normalized}-0.1.0.dist-info/METADATA").decode("utf-8")
        entry_points = archive.read(f"{normalized}-0.1.0.dist-info/entry_points.txt").decode(
            "utf-8"
        )
        names = set(archive.namelist())

    assert f"Name: {distribution}\n" in metadata
    if private_checkout:
        assert "Name: rolevane\n" not in metadata
        assert "career-os = career_os.cli:main" in entry_points
    else:
        assert "Name: career-os\n" not in metadata
        assert "rolevane-product = career_os.participant_app:main" in entry_points
        assert "career-os = career_os.cli:main" not in entry_points
    assert "career-os-product = career_os.participant_app:main" not in entry_points
    assert f"_{normalized}_editable.pth" in names


def test_build_backend_does_not_require_git_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_tree = tmp_path / "source-tree"
    package = _load_root_package()
    for relative in (
        "pyproject.toml",
        "config/product_package.toml",
        "LICENSE",
        "NOTICE",
        *package.files,
        *package.resources,
    ):
        source = ROOT / relative
        if relative in {"LICENSE", "NOTICE"} and not source.is_file():
            source = ROOT / "public_repo" / relative
        destination = source_tree / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    monkeypatch.setattr(build_backend, "ROOT", source_tree)

    wheel_name = build_backend.build_wheel(str(tmp_path))

    wheel = tmp_path / wheel_name
    assert wheel.is_file()
    dist_info = "rolevane-0.1.0.dist-info"
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata = archive.read(f"{dist_info}/METADATA").decode("utf-8")
        assert "License-File: LICENSE\n" in metadata
        assert "License-File: NOTICE\n" in metadata
        assert f"{dist_info}/licenses/LICENSE" in names
        assert f"{dist_info}/licenses/NOTICE" in names
        assert (
            archive.read(f"{dist_info}/licenses/LICENSE") == (source_tree / "LICENSE").read_bytes()
        )
        assert archive.read(f"{dist_info}/licenses/NOTICE") == (source_tree / "NOTICE").read_bytes()


def test_clean_environment_installs_wheel_and_runs_offline_smoke(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()
    wheel = distribution / build_backend.build_wheel(str(distribution))
    environment = tmp_path / "clean-environment"
    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    runtime_temp = tmp_path / "runtime-temp"
    runtime_temp.mkdir()
    clean_environment = {
        **os.environ,
        "PYTHONPATH": "",
        "PYTHONIOENCODING": "cp1252",
        "TEMP": str(runtime_temp),
        "TMP": str(runtime_temp),
    }
    offer_instance = tmp_path / "offer-instance"
    offer_instance.mkdir()
    example = ROOT / "public_repo" / "examples" / "offer-comparison.json"
    if not example.exists():
        example = ROOT / "examples" / "offer-comparison.json"
    offer_payload = json.loads(example.read_text(encoding="utf-8"))
    offer_payload["offer_label"] = "Synthetic \u65e5\u672c\U0001f680"
    offer_request = offer_instance / "offer.json"
    offer_request.write_text(json.dumps(offer_payload), encoding="utf-8")
    offer_command = environment / (
        "Scripts/rolevane-offer-compare.exe" if os.name == "nt" else "bin/rolevane-offer-compare"
    )
    offer_args = [
        str(offer_command),
        "--instance-root",
        str(offer_instance),
        "--request",
        str(offer_request),
    ]
    offer_env = {**clean_environment, "PYTHONIOENCODING": "cp1252"}
    offer_preview = subprocess.run(
        offer_args, check=True, capture_output=True, text=True, cwd=tmp_path, env=offer_env
    )
    comparison = json.loads(offer_preview.stdout)["comparison"]
    assert comparison["request"]["offer_label"] == offer_payload["offer_label"]
    assert comparison["expected"]["offer"]["expected_total"] == 122_000
    assert list(offer_instance.iterdir()) == [offer_request]
    offer_saved = subprocess.run(
        [*offer_args, "--confirm-comparison", comparison["confirmation_fingerprint"]],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=offer_env,
    )
    saved_path = offer_instance / json.loads(offer_saved.stdout)["saved"]["relative_path"]
    assert json.loads(saved_path.read_bytes()) == comparison
    assert not list(offer_instance.rglob("*.sqlite*"))
    event_instance = tmp_path / "event-instance"
    event_instance.mkdir()
    event_example = ROOT / "public_repo/examples/submission-event.json"
    if not event_example.exists():
        event_example = ROOT / "examples/submission-event.json"
    event_payload = json.loads(event_example.read_bytes())
    event_payload["details"]["company"] = "Synthetic \u65e5\u672c\U0001f680"
    event_request = event_instance / "event.json"
    event_request.write_text(json.dumps(event_payload), encoding="utf-8")

    def run_event_command(name: str, *arguments: str) -> dict:
        executable = environment / (f"Scripts/{name}.exe" if os.name == "nt" else f"bin/{name}")
        result = subprocess.run(
            [str(executable), "--instance-root", str(event_instance), *arguments],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=offer_env,
        )
        return json.loads(result.stdout)

    event_preview = run_event_command("rolevane-event-record", "--request", str(event_request))
    assert event_preview["recorded"] is None
    assert list(event_instance.iterdir()) == [event_request]
    receipt = run_event_command(
        "rolevane-event-record",
        "--request",
        str(event_request),
        "--confirm-event",
        event_preview["proposal"]["confirmation_fingerprint"],
    )["recorded"]
    assert receipt["event"]["payload"]["company"] == event_payload["details"]["company"]
    assert receipt["appended"]
    assert len(run_event_command("rolevane-event-list")["events"]) == 1
    event_report = run_event_command("rolevane-outcome-report")
    assert event_report["report"]["confirmed_submissions"] == 1
    acknowledgment = run_event_command(
        "rolevane-outcome-report",
        "--as-of",
        event_report["report"]["as_of"],
        "--acknowledge-report",
        event_report["report"]["basis_hash"],
        "--ledger-fingerprint",
        event_report["ledger_fingerprint"],
    )["acknowledged"]
    assert acknowledgment["event"]["event_type"] == "review_acknowledged"
    assert (event_instance / receipt["relative_path"]).is_file()
    before_focus = {path: path.read_bytes() for path in event_instance.rglob("*") if path.is_file()}
    focus = run_event_command("rolevane-focus")
    assert focus["projection"]["read_only"] and focus["projection"]["partial"]
    assert {item["name"]: item["state"] for item in focus["inputs"]}["confirmed_events"] == "ready"
    assert {
        path: path.read_bytes() for path in event_instance.rglob("*") if path.is_file()
    } == before_focus
    assert not list(event_instance.rglob("*.sqlite*"))
    result = subprocess.run(
        [str(python), "-m", "career_os.synthetic_runtime"],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )

    payload = json.loads(result.stdout)
    assert payload["instances"] == 2
    assert payload["offline"] is True
    assert payload["external_actions"] is False
    assert "Career" not in result.stdout
    assert not (tmp_path / "context").exists()

    console_smoke = environment / (
        "Scripts/rolevane-product-smoke.exe" if os.name == "nt" else "bin/rolevane-product-smoke"
    )
    console_result = subprocess.run(
        [str(console_smoke)],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    assert json.loads(console_result.stdout)["offline"] is True

    evidence_result = subprocess.run(
        [str(python), "-m", "career_os.product_journey"],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    evidence_payload = json.loads(evidence_result.stdout)
    assert evidence_payload["instances"] == 2
    assert evidence_payload["approved_items_per_instance"] == 6
    assert evidence_payload["model_calls"] is False
    assert evidence_payload["external_actions"] is False

    full_result = subprocess.run(
        [
            str(python),
            "-c",
            "import json; from career_os.product_journey import run_full_smoke; "
            "print(json.dumps(run_full_smoke(), sort_keys=True))",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    full_payload = json.loads(full_result.stdout)
    assert full_payload["journeys_completed"] == 2
    assert full_payload["instances_removed"] is True
    assert full_payload["model_calls"] is False
    assert full_payload["external_actions"] is False

    help_result = subprocess.run(
        [str(python), "-m", "career_os.participant_app", "--help"],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    assert "--instance-root" in help_result.stdout
    assert "--no-browser" in help_result.stdout

    official_result = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import socket, sys\n"
                "socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(AssertionError('unexpected network'))\n"
                "from career_os.official_posting import main\n"
                "assert not any(n in sys.modules for n in ('career_os.providers', 'career_os.cli', 'career_os.participant_app'))\n"
                "raise SystemExit(main())\n"
            ),
            "--url",
            "https://job-boards.greenhouse.io/example/jobs/42",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    official_preview = json.loads(official_result.stdout)
    assert official_preview["network_requests"] == 0
    assert official_preview["requires_network_confirmation"] is True
    assert official_preview["career_event_created"] is False

    international_text = "Fictional \u6771\u4eac \U00010400"
    official_source = json.dumps(
        {
            "id": 42,
            "internal_job_id": 7,
            "company_name": "Fictional Example Company",
            "title": international_text,
            "content": international_text,
            "absolute_url": official_preview["canonical_url"],
        }
    )
    official_fetch = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import socket, sys\n"
                "from importlib.metadata import entry_points\n"
                "socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(AssertionError('unexpected network'))\n"
                "import career_os.official_posting as cli\n"
                "from career_os.infrastructure.greenhouse import GreenhousePostingAdapter\n"
                "from career_os.public_api import OfficialPostingService\n"
                "service = OfficialPostingService(GreenhousePostingAdapter(lambda *a, **k: sys.argv[1]))\n"
                "cli.create_official_posting_service = lambda: service\n"
                "command = next(iter(entry_points(group='console_scripts', name='rolevane-role-fetch')))\n"
                "raise SystemExit(command.load()(['--url', sys.argv[2], '--confirm-network', sys.argv[3]]))\n"
            ),
            official_source,
            official_preview["canonical_url"],
            official_preview["confirmation_hash"],
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    official_snapshot = json.loads(official_fetch.stdout)
    assert official_fetch.stdout.isascii()
    assert official_snapshot["title"] == official_snapshot["description"] == international_text
    assert official_snapshot["source_hash"] == hashlib.sha256(official_source.encode()).hexdigest()
    assert (
        official_snapshot["content_hash"] == hashlib.sha256(international_text.encode()).hexdigest()
    )
    assert official_snapshot["network_requests"] == 1
    assert official_snapshot["filesystem_changed"] is False
    assert official_snapshot["application_ready"] is False

    document = tmp_path / "synthetic-career.md"
    document.write_text("# Synthetic career evidence\n", encoding="utf-8")
    import_result = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import sys\n"
                "import career_os.public_api\n"
                "assert not any(name in sys.modules for name in (\n"
                "    'sqlite3', 'career_os.evidence_vault', 'career_os.document_intake',\n"
                "    'career_os.product_journey', 'career_os.participant_flow',\n"
                "    'career_os.providers', 'career_os.cli'))\n"
                "from career_os.evidence_onboarding import main\n"
                "assert not any(name in sys.modules for name in (\n"
                "    'career_os.product_journey', 'career_os.participant_flow',\n"
                "    'career_os.synthetic_evidence', 'career_os.synthetic_runtime'))\n"
                "raise SystemExit(main())\n"
            ),
            "--instance-root",
            str(tmp_path / "imported-participant"),
            "--document",
            str(document),
            "--accept-local-processing",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    import_payload = json.loads(import_result.stdout)
    assert "display_name" not in import_payload["source"]
    assert import_payload["source"]["input_format"] == "markdown"
    assert import_payload["source"]["managed_copy"] is True
    assert import_payload["proposals_created"] == 0
    assert import_payload["model_calls"] is False
    assert import_payload["external_actions"] is False
    assert not (tmp_path / "imported-participant" / "data" / "participant-flow.json").exists()

    list_command = environment / (
        "Scripts/rolevane-evidence-list.exe" if os.name == "nt" else "bin/rolevane-evidence-list"
    )
    list_result = subprocess.run(
        [
            str(list_command),
            "--instance-root",
            str(tmp_path / "imported-participant"),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    source_inventory = json.loads(list_result.stdout)
    assert source_inventory["sources"][0]["source_id"] == import_payload["source"]["source_id"]
    assert source_inventory["content_included"] is False
    assert source_inventory["filenames_included"] is False
    assert "display_name" not in source_inventory["sources"][0]

    proposal_directory = tmp_path / "imported-participant" / "inbox"
    proposal_directory.mkdir()
    proposal_path = proposal_directory / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_id": import_payload["source"]["source_id"],
                "source_hash": import_payload["source"]["source_hash"],
                "extracted_hash": import_payload["source"]["extracted_hash"],
                "kind": "candidate_profile",
                "statement": "Synthetic career evidence was selected for local review.",
                "details": {"field": "career_evidence", "value": "selected"},
                "source_start_line": 1,
                "source_end_line": 1,
                "ownership": "unknown",
                "confidence": "unknown",
                "approximation": "unknown",
                "publication_boundary": "private",
                "actor": "agent",
                "reason": "Clean-wheel proposal boundary proof.",
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    proposal_command = environment / (
        "Scripts/rolevane-evidence-propose.exe"
        if os.name == "nt"
        else "bin/rolevane-evidence-propose"
    )
    proposal_result = subprocess.run(
        [
            str(proposal_command),
            "--instance-root",
            str(tmp_path / "imported-participant"),
            "--proposal",
            str(proposal_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    proposal_payload = json.loads(proposal_result.stdout)
    assert proposal_payload["status"] == "unapproved"
    assert proposal_payload["proposal_origin"] == "agent"
    assert proposal_payload["requires_user_review"] is True

    api_result = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import sys\n"
                "from career_os.public_api import EvidenceOperationError, require_evidence_result\n"
                "from career_os.infrastructure.evidence import create_evidence_service\n"
                "service = create_evidence_service(sys.argv[1])\n"
                "card = require_evidence_result(service.latest(sys.argv[2]))\n"
                "denied = service.review(card.evidence_id, card.revision, action='approve',\n"
                "    expected_content_hash=card.content_hash, actor='model')\n"
                "assert isinstance(denied, EvidenceOperationError)\n"
                "assert denied.code == 'human_required'\n"
                "reviewed = require_evidence_result(service.review(\n"
                "    card.evidence_id, card.revision, action='approve',\n"
                "    expected_content_hash=card.content_hash, actor='local_user'))\n"
                "assert reviewed.status == 'approved'\n"
                "readiness = require_evidence_result(service.readiness())\n"
                "assert readiness.approved == 1 and readiness.unapproved == 0\n"
                "assert not readiness.ready  # This fixture deliberately has unknown confidence.\n"
                "assert 'career_os.product_journey' not in sys.modules\n"
                "print('exact-human-review-passed')\n"
            ),
            str(tmp_path / "imported-participant"),
            proposal_payload["evidence_id"],
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    assert api_result.stdout.strip() == "exact-human-review-passed"

    interview_command = environment / (
        "Scripts/rolevane-evidence-interview.exe"
        if os.name == "nt"
        else "bin/rolevane-evidence-interview"
    )
    interview_status = subprocess.run(
        [str(interview_command), "--instance-root", str(tmp_path / "imported-participant")],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    assert json.loads(interview_status.stdout)["next_question"]["question_id"] == "role_timeline"

    interview_answer = proposal_directory / "interview-answer.json"
    interview_answer.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "question_id": "role_timeline",
                "answers": {
                    "employer": "Synthetic Example GmbH",
                    "title": "Customer Operations Lead",
                    "start_date": "2022-01",
                    "end_date": "present",
                },
                "ownership": "personal",
                "confidence": "exact",
                "approximation": "exact",
                "publication_boundary": "private",
                "role_family_relevance": ["customer_post_sales_leadership"],
            }
        ),
        encoding="utf-8",
    )
    interview_result = subprocess.run(
        [
            str(interview_command),
            "--instance-root",
            str(tmp_path / "imported-participant"),
            "--answer",
            str(interview_answer),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    interview_payload = json.loads(interview_result.stdout)
    assert interview_payload["status"] == "unapproved"
    assert interview_payload["proposal_origin"] == "deterministic_rule"
    assert interview_payload["next_question"]["question_id"] == "mandate_decision_rights"

    role_instance = tmp_path / "role-participant"
    instance_command = environment / (
        "Scripts/rolevane-instance.exe" if os.name == "nt" else "bin/rolevane-instance"
    )
    initialized_role = subprocess.run(
        [str(instance_command), "--instance-root", str(role_instance), "init"],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    assert json.loads(initialized_role.stdout)["changed"]
    setup_result = subprocess.run(
        [
            str(python),
            "-c",
            (
                "from pathlib import Path\n"
                "from career_os.evidence_vault import EvidenceVault\n"
                "from career_os.synthetic_evidence import seed_synthetic_evidence\n"
                "root = Path(__import__('sys').argv[1])\n"
                "vault = EvidenceVault(root)\n"
                "vault.record_consent(accepted=True)\n"
                "seed_synthetic_evidence(vault)\n"
                "while vault.review_queue():\n"
                "    card = vault.review_queue()[0]\n"
                "    action = 'reject' if card.kind == 'forbidden_claim' else 'approve'\n"
                "    vault.review(card.evidence_id, card.revision, action=action, "
                "expected_content_hash=card.content_hash)\n"
            ),
            str(role_instance),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    assert setup_result.stdout == ""
    role_inbox = role_instance / "inbox"
    role_request = role_inbox / "supplied-role.json"
    role_title = "Operations Lead \u7d71\u62ec \U00020bb7"
    role_environment = {**clean_environment, "PYTHONIOENCODING": "cp1252"}
    role_request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authority": "user_supplied",
                "opportunity_id": "synthetic_supplied_role",
                "company": "Fictional Example GmbH",
                "title": role_title,
                "location": "Germany",
                "work_model": "hybrid",
                "source_url": None,
                "posting_text": "A wholly fictional supplied posting.",
                "role_signals": [],
                "requirements": [
                    {
                        "requirement_id": "user_supplied_scope",
                        "level": "mandatory",
                        "statement": "Understand the supplied operating scope.",
                    }
                ],
                "evidence_routes": {
                    "direct": {"user_supplied_scope": ["synthetic-responsibility"]},
                    "transferable": {},
                    "gaps": {},
                },
                "constraints": {
                    "preferred_location": "Germany",
                    "preferred_work_model": "hybrid",
                    "maximum_travel_percent": 25,
                    "currency": "EUR",
                },
                "mapping_confirmed_by": "local_user",
            }
        ),
        encoding="utf-8",
    )
    role_command = environment / (
        "Scripts/rolevane-role-evaluate-supplied.exe"
        if os.name == "nt"
        else "bin/rolevane-role-evaluate-supplied"
    )
    role_arguments = [
        str(role_command),
        "--instance-root",
        str(role_instance),
        "--request",
        str(role_request),
    ]
    valid_content = role_request.read_bytes()
    invalid_role = json.loads(valid_content)
    invalid_role["title"] = "Invalid\ud800title"
    role_request.write_text(json.dumps(invalid_role), encoding="utf-8")
    refused_role = subprocess.run(
        role_arguments,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=role_environment,
    )
    assert refused_role.returncode == 1
    assert refused_role.stdout == ""
    assert "Opportunity input or current state is invalid" in refused_role.stderr
    assert not (role_instance / "data/runtime/opportunities").exists()
    role_request.write_bytes(valid_content)
    role_result = subprocess.run(
        role_arguments,
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=role_environment,
    )
    role_payload = json.loads(role_result.stdout)
    assert role_result.stdout.isascii()
    assert role_payload["source_hash"] == hashlib.sha256(role_request.read_bytes()).hexdigest()
    assert role_payload["title"] == role_title
    assert role_payload["authority"] == "user_supplied"
    assert role_payload["official_source_verified"] is False
    assert role_payload["role_evaluation"]["selection"]["status"] == "fallback"
    assert role_payload["role_evaluation"]["confidence"] <= 0.5
    assert role_payload["role_evaluation"]["application_ready"] is False
    assert role_payload["decision_recorded"] is False
    assert role_payload["external_actions"] is False
    assert role_payload["mapping_confirmed_by"] is None
    assert not (role_instance / "data/runtime/opportunities").exists()
    confirmed_role = subprocess.run(
        [
            *role_arguments,
            "--confirm-mapping",
            role_payload["role_evaluation"]["evaluation_fingerprint"],
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=role_environment,
    )
    role_payload = json.loads(confirmed_role.stdout)
    assert role_payload["mapping_confirmed_by"] == "local_user"
    assert confirmed_role.stdout.isascii()
    assert role_payload["title"] == role_title
    dossier_bytes = (role_instance / role_payload["dossier_relative_path"]).read_bytes()
    assert role_title.encode("utf-8") in dossier_bytes
    assert json.loads(dossier_bytes)["title"] == role_title

    role_decision = role_inbox / "decision.json"
    role_decision.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "opportunity_id": role_payload["opportunity_id"],
                "source_hash": role_payload["source_hash"],
                "evaluation_fingerprint": role_payload["role_evaluation"]["evaluation_fingerprint"],
                "decision": role_payload["role_evaluation"]["recommendation"],
                "reason_code": "accept_recommendation",
                "note": international_text,
                "confirmed_by": "local_user",
            }
        ),
        encoding="utf-8",
    )
    decision_command = environment / (
        "Scripts/rolevane-role-decide-supplied.exe"
        if os.name == "nt"
        else "bin/rolevane-role-decide-supplied"
    )
    decision_result = subprocess.run(
        [
            str(decision_command),
            "--instance-root",
            str(role_instance),
            "--decision",
            str(role_decision),
            "--confirm-decision",
            hashlib.sha256(role_decision.read_bytes()).hexdigest(),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=role_environment,
    )
    decision_payload = json.loads(decision_result.stdout)
    assert decision_result.stdout.isascii()
    assert decision_payload["decision_recorded"] is True
    assert decision_payload["record_created"] is True
    assert decision_payload["current_recommendation"] == "hold"
    assert decision_payload["application_event_created"] is False
    assert decision_payload["external_event_confirmed"] is False
    assert decision_payload["external_actions"] is False
    decision_bytes = (
        (role_instance / role_payload["dossier_relative_path"])
        .with_name("decisions.json")
        .read_bytes()
    )
    assert international_text.encode("utf-8") in decision_bytes
    assert json.loads(decision_bytes)["events"][0]["note"] == international_text

    service_result = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import json, sys\n"
                "from career_os.public_api import OpportunityService, OpportunityOperationError, require_opportunity_result\n"
                "from career_os.infrastructure.opportunities import create_opportunity_service\n"
                "service = create_opportunity_service(sys.argv[1])\n"
                "assert isinstance(service, OpportunityService)\n"
                "missing = service.resolve_supplied('absent_supplied_role')\n"
                "assert isinstance(missing, OpportunityOperationError)\n"
                "assert missing.code == 'invalid_opportunity'\n"
                "state = require_opportunity_result(service.resolve_supplied(sys.argv[2]))\n"
                "assert 'career_os.opportunity_workflow' not in sys.modules\n"
                "assert 'career_os.participant_flow' not in sys.modules\n"
                "print(json.dumps(state.to_dict(), sort_keys=True))\n"
            ),
            str(role_instance),
            role_payload["opportunity_id"],
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=role_environment,
    )
    service_state = json.loads(service_result.stdout)
    assert service_state["dossier"] == role_payload
    assert service_state["decision_current"] is True
    assert service_state["latest_decision"]["decision_id"] == decision_payload["decision_id"]
    assert not any((role_instance / "applications").iterdir())

    subprocess.run(
        [
            str(python),
            "-c",
            (
                "import sys\n"
                "from career_os.infrastructure.opportunities import create_opportunity_service\n"
                "from career_os.public_api import SuppliedDecisionInput, require_opportunity_result\n"
                "service = create_opportunity_service(sys.argv[1])\n"
                "dossier = require_opportunity_result(service.resolve_supplied(sys.argv[2])).dossier\n"
                "decision = SuppliedDecisionInput(dossier.opportunity_id, dossier.source_hash, "
                "dossier.evaluation_fingerprint, 'conditional', 'other', 'Prepare local content only.')\n"
                "require_opportunity_result(service.record_decision(decision, actor='local_user'))\n"
                "assert 'career_os.candidate_evidence' not in sys.modules\n"
                "assert 'career_os.application_architecture' not in sys.modules\n"
            ),
            str(role_instance),
            role_payload["opportunity_id"],
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=role_environment,
    )
    application_request = role_inbox / "application.json"
    application_request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "opportunity_id": role_payload["opportunity_id"],
                "evidence_ids": ["synthetic-responsibility"],
                "archetype": None,
            }
        ),
        encoding="utf-8",
    )
    application_command = environment / (
        "Scripts/rolevane-application.exe" if os.name == "nt" else "bin/rolevane-application"
    )
    application_args = [str(application_command), "--instance-root", str(role_instance)]
    application_plan = json.loads(
        subprocess.run(
            [*application_args, "plan", "--request", str(application_request)],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=role_environment,
        ).stdout
    )
    assert application_plan["opportunity"]["title"] == role_title
    assert application_plan["local_content_ready"] and not application_plan["application_ready"]
    assert not any((role_instance / "applications").iterdir())
    application_saved = json.loads(
        subprocess.run(
            [
                *application_args,
                "prepare",
                "--request",
                str(application_request),
                "--confirm-plan",
                application_plan["confirmation_fingerprint"],
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=role_environment,
        ).stdout
    )
    assert application_saved["created"]
    brief_path = role_instance / application_saved["relative_path"] / "application-brief.txt"
    assert role_title in brief_path.read_text(encoding="utf-8")
    assert application_plan["claims"][0]["statement"] in brief_path.read_text(encoding="utf-8")
    application_validation = json.loads(
        subprocess.run(
            [
                *application_args,
                "validate",
                "--opportunity-id",
                role_payload["opportunity_id"],
                "--fingerprint",
                application_saved["confirmation_fingerprint"],
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=role_environment,
        ).stdout
    )
    assert application_validation["current"] and application_validation["artifacts_intact"]
    assert not application_validation["application_ready"]

    interview_request = role_inbox / "interview.json"
    interview_request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "opportunity_id": role_payload["opportunity_id"],
                "round_id": "first_round",
                "round_name": "Hiring manager discussion",
                "round_type": "hiring_manager",
                "evidence_ids": ["synthetic-responsibility"],
                "panel": [],
                "notes": "Clarify decision rights.",
                "practice_prompts": [],
            }
        ),
        encoding="utf-8",
    )
    interview_command = environment / (
        "Scripts/rolevane-interview.exe" if os.name == "nt" else "bin/rolevane-interview"
    )
    interview_args = [str(interview_command), "--instance-root", str(role_instance)]
    interview_plan = json.loads(
        subprocess.run(
            [*interview_args, "plan", "--request", str(interview_request)],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=role_environment,
        ).stdout
    )
    assert interview_plan["opportunity"]["title"] == role_title
    assert interview_plan["schedule_status"] == "not_asserted"
    interview_saved = json.loads(
        subprocess.run(
            [
                *interview_args,
                "prepare",
                "--request",
                str(interview_request),
                "--confirm-plan",
                interview_plan["confirmation_fingerprint"],
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=role_environment,
        ).stdout
    )
    assert interview_saved["created"] and not interview_saved["external_event_recorded"]
    call_sheet = role_instance / interview_saved["relative_path"] / "live-call-sheet.txt"
    assert role_title in call_sheet.read_text(encoding="utf-8")
    assert application_plan["claims"][0]["statement"] in call_sheet.read_text(encoding="utf-8")
    interview_validation = json.loads(
        subprocess.run(
            [
                *interview_args,
                "validate",
                "--opportunity-id",
                role_payload["opportunity_id"],
                "--round-id",
                "first_round",
                "--fingerprint",
                interview_saved["confirmation_fingerprint"],
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=role_environment,
        ).stdout
    )
    assert interview_validation["current"] and interview_validation["artifacts_intact"]

    workspace_command = environment / (
        "Scripts/rolevane-workspace.exe" if os.name == "nt" else "bin/rolevane-workspace"
    )

    def workspace_call(*args):
        return json.loads(
            subprocess.run(
                [str(workspace_command), "--instance-root", str(role_instance), *args],
                check=True,
                capture_output=True,
                text=True,
                cwd=tmp_path,
                env=role_environment,
            ).stdout
        )

    inventory = workspace_call("list")
    assert len(inventory["versions"]) == 2 and not inventory["partial"]
    for version in inventory["versions"]:
        ref = version["workspace"]
        args = [
            "inspect",
            "--kind",
            ref["kind"],
            "--opportunity-id",
            ref["opportunity_id"],
            "--fingerprint",
            ref["fingerprint"],
        ]
        if ref["round_id"]:
            args.extend(["--round-id", ref["round_id"]])
        inspected = workspace_call(*args)
        assert inspected["summary"]["role"] == role_title and inspected["summary"]["current"]
        review_request = role_inbox / "workspace-review.json"
        review_request.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "workspace": ref,
                    "disposition": "accepted",
                    "note": "Reviewed the exact local content.",
                    "interview_event_id": None,
                }
            ),
            encoding="utf-8",
        )
        preview = workspace_call("review", "--request", str(review_request))
        accepted = workspace_call(
            "review",
            "--request",
            str(review_request),
            "--confirm-review",
            preview["confirmation_fingerprint"],
        )
        assert accepted["created"] and not accepted["external_event_recorded"]
    assert all(v["review_state"] == "accepted" for v in workspace_call("list")["versions"])

    role_offer_request = role_inbox / "offer.json"
    role_offer_request.write_text(json.dumps(offer_payload), encoding="utf-8")
    role_offer_args = [
        str(offer_command),
        "--instance-root",
        str(role_instance),
        "--request",
        str(role_offer_request),
    ]
    role_offer_preview = json.loads(
        subprocess.run(
            role_offer_args,
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=offer_env,
        ).stdout
    )["comparison"]
    subprocess.run(
        [
            *role_offer_args,
            "--confirm-comparison",
            role_offer_preview["confirmation_fingerprint"],
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=offer_env,
    )
    role_event_payload = {
        **event_payload,
        "opportunity_id": role_payload["opportunity_id"],
        "details": {
            **event_payload["details"],
            "job_key": "synthetic:supplied-role",
            "company": "Fictional Example GmbH",
            "title": role_title,
        },
    }
    role_event_request = role_inbox / "submission-event.json"
    role_event_request.write_text(json.dumps(role_event_payload), encoding="utf-8")
    event_command = environment / (
        "Scripts/rolevane-event-record.exe" if os.name == "nt" else "bin/rolevane-event-record"
    )
    role_event_args = [
        str(event_command),
        "--instance-root",
        str(role_instance),
        "--request",
        str(role_event_request),
    ]
    role_event_preview = json.loads(
        subprocess.run(
            role_event_args,
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=offer_env,
        ).stdout
    )["proposal"]
    subprocess.run(
        [
            *role_event_args,
            "--confirm-event",
            role_event_preview["confirmation_fingerprint"],
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=offer_env,
    )
    credential_canary = "synthetic-installed-token-must-not-export"
    (role_instance / "config").mkdir()
    (role_instance / "config" / "provider.json").write_text(
        json.dumps({"token": credential_canary}), encoding="utf-8"
    )

    recovery_command = instance_command

    def recovery_call(root, *args):
        return json.loads(
            subprocess.run(
                [str(recovery_command), "--instance-root", str(root), *args],
                check=True,
                capture_output=True,
                text=True,
                cwd=tmp_path,
                env=role_environment,
            ).stdout
        )

    initialized = tmp_path / "initialized-participant"
    assert recovery_call(initialized, "init")["changed"]
    assert not recovery_call(
        initialized,
        "upgrade",
        "--backup-destination",
        str(tmp_path / "unused-backup"),
        "--confirm-upgrade",
        "no-migration-needed",
        "--offline",
    )["changed"]
    assert not (tmp_path / "unused-backup").exists()
    diagnostic = subprocess.run(
        [str(recovery_command), "--instance-root", str(initialized), "doctor"],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=role_environment,
    )
    # This intentionally offline fixture installs the wheel with --no-deps. Doctor must
    # report missing declared dependencies, not pretend it is a complete primary install.
    assert diagnostic.returncode == 1 and diagnostic.stderr == ""
    health = json.loads(diagnostic.stdout)
    statuses = {check["name"]: check["status"] for check in health["checks"]}
    assert statuses["package"] == statuses["workspace"] == statuses["evidence_storage"] == "ok"
    assert statuses["dependencies"] == "missing_or_incompatible"
    assert str(initialized) not in diagnostic.stdout

    legacy = tmp_path / "legacy-participant"
    legacy_database = legacy / "data" / "evidence-vault.db"
    legacy_database.parent.mkdir(parents=True)
    with sqlite3.connect(legacy_database) as connection:
        connection.execute("CREATE TABLE vault_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO vault_meta VALUES(?, ?)",
            (
                ("schema_version", "1"),
                ("instance_root_hash", hashlib.sha256(str(legacy.resolve()).encode()).hexdigest()),
            ),
        )
    upgrade_backup = tmp_path / "legacy-upgrade-backup"
    upgrade_preview = recovery_call(legacy, "upgrade", "--backup-destination", str(upgrade_backup))
    assert upgrade_preview["from_version"] == 1 and not upgrade_backup.exists()
    upgraded = recovery_call(
        legacy,
        "upgrade",
        "--backup-destination",
        str(upgrade_backup),
        "--offline",
        "--confirm-upgrade",
        upgrade_preview["confirmation_fingerprint"],
    )
    assert upgraded["changed"]
    assert (
        recovery_call(legacy, "verify", "--backup", str(upgrade_backup))["evidence_storage_version"]
        == 1
    )
    with sqlite3.connect(legacy_database) as connection:
        assert (
            dict(connection.execute("SELECT key, value FROM vault_meta"))["schema_version"] == "4"
        )
    legacy_doctor = subprocess.run(
        [str(recovery_command), "--instance-root", str(legacy), "doctor"],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=role_environment,
    )
    legacy_checks = {
        check["name"]: check["status"] for check in json.loads(legacy_doctor.stdout)["checks"]
    }
    assert legacy_checks["layout"] == "legacy_compatible"
    assert legacy_checks["evidence_storage"] == "ok"

    backup = tmp_path / "complete-backup"
    original_inventory = recovery_call(role_instance, "inventory")
    saved = recovery_call(role_instance, "backup", "--destination", str(backup), "--offline")
    assert saved["backup_fingerprint"] == original_inventory["backup_fingerprint"]
    assert recovery_call(role_instance, "verify", "--backup", str(backup)) == original_inventory
    restored_root = tmp_path / "restored-participant"
    restore_preview = recovery_call(restored_root, "restore", "--backup", str(backup))
    assert not restored_root.exists()
    restored = recovery_call(
        restored_root,
        "restore",
        "--backup",
        str(backup),
        "--offline",
        "--confirm-restore",
        restore_preview["confirmation_fingerprint"],
    )
    assert restored["evidence_instance_rebound"]
    restored_inventory = json.loads(
        subprocess.run(
            [str(workspace_command), "--instance-root", str(restored_root), "list"],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=role_environment,
        ).stdout
    )
    assert not restored_inventory["partial"]
    assert len(restored_inventory["versions"]) == 2
    assert all(
        v["current"] and v["review_state"] == "accepted" for v in restored_inventory["versions"]
    )
    assert recovery_call(role_instance, "inventory") == original_inventory
    export = tmp_path / "portable-user-export"
    exported = recovery_call(restored_root, "export", "--destination", str(export), "--offline")
    export_content = (export / exported["relative_file"]).read_bytes()
    export_payload = json.loads(export_content)
    counts = export_payload["export_manifest"]["record_counts"]
    assert exported["sha256"] == hashlib.sha256(export_content).hexdigest()
    assert counts["opportunities"] == counts["role_preferences"] == 1
    assert counts["opportunity_decisions"] == 2
    assert counts["offer_comparisons"] == counts["confirmed_outcome_events"] == 1
    assert counts["application_versions"] == counts["interview_versions"] == 1
    assert counts["artifact_reviews"] == 2
    assert credential_canary.encode() not in export_content
    assert str(restored_root).encode() not in export_content
    delete_preview = recovery_call(restored_root, "delete")
    deleted = recovery_call(
        restored_root,
        "delete",
        "--confirm-delete",
        delete_preview["confirmation_fingerprint"],
        "--offline",
    )
    assert deleted["instance_removed"] and not restored_root.exists()
    assert export.is_dir() and backup.is_dir() and instance_command.is_file()
    assert recovery_call(role_instance, "inventory") == original_inventory

    skills = ROOT / "public_repo" / "skills"
    if not skills.is_dir():
        skills = ROOT / "skills"
    skill_result = subprocess.run(
        [str(python), "-m", "career_os.skill_validation", str(skills)],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=clean_environment,
    )
    assert skill_result.stdout.strip() == "Validated 7 public Rolevane skills."

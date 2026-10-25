"""Minimal, dependency-free PEP 517 backend for the pure-Python package."""

from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import io
import os
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

try:
    from product_package import ProductPackage, load_product_package
except ModuleNotFoundError:  # Direct repository imports use the namespace package.
    from tools.product_package import ProductPackage, load_product_package

ROOT = Path(__file__).resolve().parents[1]
_SDIST_BUILD_FILES = (
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "NOTICE",
    "config/product_package.toml",
    "tools/build_backend.py",
    "tools/product_package.py",
    "tools/release_artifacts.py",
)


def _source_path(relative: str, *, use_public_template: bool = False) -> Path:
    """Resolve a public build input without consulting Git or private metadata."""
    if use_public_template:
        template_root = ROOT / "public_repo"
        if template_root.is_symlink():
            raise ValueError("Public build template must not be a symbolic link")
        if template_root.exists():
            if not template_root.is_dir():
                raise ValueError("Public build template must be a directory")
            return template_root / Path(relative)
    return ROOT / Path(relative)


def _regular_source(relative: str, *, use_public_template: bool = False) -> Path:
    path = _source_path(relative, use_public_template=use_public_template)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Build input is not a regular file: {relative}")
    root = ROOT.resolve(strict=True)
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Build input escapes the source root: {relative}") from exc
    current = path.parent
    while current != root:
        if current == current.parent:
            raise ValueError(f"Build input escapes the source root: {relative}")
        if current.is_symlink():
            raise ValueError(f"Build input uses directory indirection: {relative}")
        current = current.parent
    return path


def _project(*, use_public_template: bool = True) -> dict[str, object]:
    project_path = _regular_source("pyproject.toml", use_public_template=use_public_template)
    return tomllib.loads(project_path.read_text(encoding="utf-8"))["project"]


def _version(*, use_public_template: bool = True) -> str:
    value = _project(use_public_template=use_public_template).get("version")
    if (
        not isinstance(value, str)
        or not value
        or PurePosixPath(value).name != value
        or any(part in {"", ".", ".."} for part in PurePosixPath(value).parts)
    ):
        raise ValueError("Project version must be a safe non-empty string")
    return value


def _distribution_name(*, use_public_template: bool = True) -> str:
    value = (
        load_product_package(ROOT, require_tracked=False).name
        if use_public_template
        else _project(use_public_template=False).get("name")
    )
    if (
        not isinstance(value, str)
        or not value
        or PurePosixPath(value).name != value
        or any(part in {"", ".", ".."} for part in PurePosixPath(value).parts)
    ):
        raise ValueError("Project name must be a safe non-empty distribution name")
    return value.replace("-", "_").replace(".", "_")


def _license_files(*, use_public_template: bool) -> tuple[str, ...]:
    project = _project(use_public_template=use_public_template)
    configured = project.get("license-files")
    if configured is None:
        result: list[str] = []
        for relative in ("LICENSE", "NOTICE"):
            source = _source_path(relative, use_public_template=use_public_template)
            if source.exists() or source.is_symlink():
                _regular_source(relative, use_public_template=use_public_template)
                result.append(relative)
        return tuple(result)
    if (
        not isinstance(configured, list)
        or not configured
        or any(not isinstance(relative, str) or not relative for relative in configured)
    ):
        raise ValueError("Project license-files must be a non-empty list of paths")
    result = tuple(configured)
    if len(result) != len(set(result)):
        raise ValueError("Project license-files must not contain duplicates")
    for relative in result:
        path = PurePosixPath(relative)
        if (
            "\\" in relative
            or ":" in relative
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("Project license-files must contain safe relative paths")
        _regular_source(relative, use_public_template=use_public_template)
    return result


def _metadata_text(package: ProductPackage, *, editable: bool = False) -> str:
    project = _project(use_public_template=not editable)
    dependencies = project["dependencies"] if editable else package.dependencies
    metadata_name = project["name"] if editable else package.name
    license_expression = project.get("license")
    if license_expression is not None and not isinstance(license_expression, str):
        raise ValueError("Project license must be an SPDX expression string")
    lines = [
        f"Metadata-Version: {'2.4' if license_expression else '2.3'}",
        f"Name: {metadata_name}",
        f"Version: {project['version']}",
        f"Summary: {project['description']}",
        f"Requires-Python: {project['requires-python']}",
    ]
    if license_expression:
        lines.append(f"License-Expression: {license_expression}")
    lines.extend(
        f"License-File: {relative}" for relative in _license_files(use_public_template=not editable)
    )
    lines.extend(f"Requires-Dist: {dependency}" for dependency in dependencies)
    if editable:
        for extra, extra_dependencies in project.get("optional-dependencies", {}).items():
            lines.append(f"Provides-Extra: {extra}")
            for dependency in extra_dependencies:
                requirement, separator, marker = dependency.partition(";")
                condition = (
                    f'({marker.strip()}) and extra == "{extra}"'
                    if separator
                    else f'extra == "{extra}"'
                )
                lines.append(f"Requires-Dist: {requirement.strip()}; {condition}")
    return "\n".join(lines) + "\n"


def _metadata_bytes(package: ProductPackage, *, editable: bool = False) -> bytes:
    return _metadata_text(package, editable=editable).encode("utf-8")


def _dist_info(*, use_public_template: bool = True) -> str:
    return f"{_distribution_name(use_public_template=use_public_template)}-{_version(use_public_template=use_public_template)}.dist-info"


def _metadata_files(package: ProductPackage, *, editable: bool = False) -> dict[str, bytes]:
    use_public_template = not editable
    dist_info = _dist_info(use_public_template=use_public_template)
    scripts = (
        _project(use_public_template=False).get("scripts", {}) if editable else package.scripts
    )
    entry_points = "[console_scripts]\n" + "".join(
        f"{name} = {target}\n" for name, target in sorted(scripts.items())
    )
    files = {
        f"{dist_info}/METADATA": _metadata_bytes(package, editable=editable),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            f"Generator: {_project(use_public_template=use_public_template)['name']}-build-backend\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n"
        ).encode("ascii"),
        f"{dist_info}/entry_points.txt": entry_points.encode(),
    }
    for relative in _license_files(use_public_template=use_public_template):
        license_path = _regular_source(relative, use_public_template=use_public_template)
        files[f"{dist_info}/licenses/{PurePosixPath(relative).as_posix()}"] = (
            license_path.read_bytes()
        )
    return files


def _write_prepared_metadata(destination: Path, files: dict[str, bytes], dist_info: str) -> None:
    for name, data in files.items():
        relative = Path(name).relative_to(dist_info)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _write_wheel(
    wheel_directory: str,
    files: dict[str, bytes],
    *,
    use_public_template: bool = True,
) -> str:
    filename = f"{_distribution_name(use_public_template=use_public_template)}-{_version(use_public_template=use_public_template)}-py3-none-any.whl"
    destination = Path(wheel_directory) / filename
    record = f"{_dist_info(use_public_template=use_public_template)}/RECORD"
    rows: list[tuple[str, str, str]] = []
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            rows.append((name, f"sha256={digest}", str(len(data))))
        rows.append((record, "", ""))
        output = io.StringIO(newline="")
        csv.writer(output, lineterminator="\n").writerows(rows)
        record_info = zipfile.ZipInfo(record, date_time=(1980, 1, 1, 0, 0, 0))
        record_info.create_system = 3
        record_info.compress_type = zipfile.ZIP_DEFLATED
        record_info.external_attr = 0o100644 << 16
        archive.writestr(record_info, output.getvalue().encode())
    return filename


def get_requires_for_build_wheel(config_settings: object | None = None) -> list[str]:
    return []


def get_requires_for_build_editable(config_settings: object | None = None) -> list[str]:
    return []


def prepare_metadata_for_build_wheel(
    metadata_directory: str, config_settings: object | None = None
) -> str:
    package = load_product_package(ROOT, require_tracked=False)
    dist_info = _dist_info()
    destination = Path(metadata_directory) / dist_info
    destination.mkdir(parents=True, exist_ok=True)
    _write_prepared_metadata(destination, _metadata_files(package), dist_info)
    return dist_info


def prepare_metadata_for_build_editable(
    metadata_directory: str, config_settings: object | None = None
) -> str:
    package = load_product_package(ROOT, require_tracked=False)
    dist_info = _dist_info(use_public_template=False)
    destination = Path(metadata_directory) / dist_info
    destination.mkdir(parents=True, exist_ok=True)
    _write_prepared_metadata(destination, _metadata_files(package, editable=True), dist_info)
    return dist_info


def build_wheel(
    wheel_directory: str,
    config_settings: object | None = None,
    metadata_directory: str | None = None,
) -> str:
    package = load_product_package(ROOT, require_tracked=False)
    files = _metadata_files(package)
    for relative in package.files:
        path = _regular_source(relative)
        files[path.relative_to(ROOT / "src").as_posix()] = path.read_bytes()
    for relative in package.resources:
        path = _regular_source(relative)
        files[path.relative_to(ROOT / "src").as_posix()] = path.read_bytes()
    return _write_wheel(wheel_directory, files)


def build_editable(
    wheel_directory: str,
    config_settings: object | None = None,
    metadata_directory: str | None = None,
) -> str:
    package = load_product_package(ROOT, require_tracked=False)
    files = _metadata_files(package, editable=True)
    files[f"_{_distribution_name(use_public_template=False)}_editable.pth"] = (
        os.fspath(ROOT / "src") + "\n"
    ).encode()
    return _write_wheel(wheel_directory, files, use_public_template=False)


def get_requires_for_build_sdist(config_settings: object | None = None) -> list[str]:
    return []


def _sdist_files(package: ProductPackage) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for relative in _SDIST_BUILD_FILES:
        path = _regular_source(
            relative,
            use_public_template=relative in {"pyproject.toml", "README.md", "LICENSE", "NOTICE"},
        )
        files[relative] = path.read_bytes()
    for relative in _license_files(use_public_template=True):
        files[relative] = _regular_source(relative, use_public_template=True).read_bytes()
    files["PKG-INFO"] = _metadata_bytes(package)
    for relative in (*package.files, *package.resources):
        path = _regular_source(relative)
        files[relative] = path.read_bytes()
    return files


def _tar_info(name: str, *, directory: bool, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mtime = 0
    info.mode = 0o755 if directory else 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.pax_headers = {}
    if directory:
        info.type = tarfile.DIRTYPE
        info.size = 0
    else:
        info.type = tarfile.REGTYPE
        info.size = size
    return info


def _write_sdist(sdist_directory: str, files: dict[str, bytes]) -> str:
    version = _version(use_public_template=True)
    root_name = f"{_distribution_name(use_public_template=True)}-{version}"
    filename = f"{root_name}.tar.gz"
    destination = Path(sdist_directory) / filename
    names = {PurePosixPath(relative).as_posix() for relative in files}
    directories = {""}
    for relative in names:
        parts = PurePosixPath(relative).parts
        directories.update(
            PurePosixPath(*parts[:index]).as_posix() for index in range(1, len(parts))
        )
    members = sorted(
        [
            (f"{root_name}/{relative}" if relative else root_name, True, b"")
            for relative in directories
        ]
        + [(f"{root_name}/{relative}", False, files[relative]) for relative in sorted(names)],
        key=lambda item: (item[0], not item[1]),
    )
    with (
        destination.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive,
    ):
        for name, directory, data in members:
            archive.addfile(_tar_info(name, directory=directory, size=len(data)), io.BytesIO(data))
    return filename


def build_sdist(
    sdist_directory: str,
    config_settings: object | None = None,
) -> str:
    package = load_product_package(ROOT, require_tracked=False)
    return _write_sdist(sdist_directory, _sdist_files(package))

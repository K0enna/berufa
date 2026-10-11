"""Build and verify deterministic public release artifacts.

The JSON output is a dependency inventory and build manifest. It is not a formal
SBOM, a signature, or a hosted provenance attestation.
"""

from __future__ import annotations

import argparse
import email.policy
import hashlib
import json
import re
import struct
import sys
import zipfile
from email.message import Message
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from . import build_backend
    from .product_package import ProductPackage, load_product_package
except ImportError:  # Direct execution from the tools directory.
    import build_backend
    from product_package import ProductPackage, load_product_package


MANIFEST_FILENAME = "RELEASE-MANIFEST.json"
CHECKSUMS_FILENAME = "SHA256SUMS"
SBOM_FILENAME = "SBOM.cdx.json"
MANIFEST_SCHEMA_VERSION = 1
MAX_SBOM_BYTES = 4 * 1024 * 1024
MAX_SBOM_COMPONENTS = 10_000
MAX_SBOM_FIELD_LENGTH = 512
MAX_WHEEL_MEMBERS = 512
MAX_WHEEL_CENTRAL_DIRECTORY_BYTES = 1024 * 1024
MAX_WHEEL_MEMBER_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_WHEEL_MEMBER_BYTES = 64 * 1024 * 1024
MAX_WHEEL_TOTAL_COMPRESSED_BYTES = 128 * 1024 * 1024
MAX_WHEEL_TOTAL_BYTES = 256 * 1024 * 1024
MAX_WHEEL_COMPRESSION_RATIO = 100
MAX_WHEEL_METADATA_BYTES = 1024 * 1024
MAX_RELEASE_ARTIFACT_BYTES = 256 * 1024 * 1024
_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
_ZIP_EOCD_SIZE = 22
_ZIP_CENTRAL_DIRECTORY_RECORD_SIZE = 46
_CYCLONEDX_COMPONENT_TYPES = frozenset(
    {
        "application",
        "container",
        "device",
        "file",
        "firmware",
        "framework",
        "library",
        "operating-system",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DISTRIBUTION_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_DEPENDENCY_NAME_RE = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
_MANIFEST_KEYS = {
    "artifacts",
    "manifest_type",
    "package",
    "runtime_dependencies",
    "schema_version",
    "scope_note",
}
_PACKAGE_KEYS = {"name", "requires_python", "version"}
_ARTIFACT_KEYS = {"bytes", "filename", "kind", "sha256"}


def _canonical_json(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _regular_file(path: Path, *, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file: {path.name}")
    return path.read_bytes()


def _file_digest(path: Path, *, kind: str, label: str = "Release artifact") -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file: {path.name}")
    maximum_bytes = MAX_SBOM_BYTES if kind == "sbom" else MAX_RELEASE_ARTIFACT_BYTES
    if path.stat().st_size > maximum_bytes:
        raise ValueError(f"{label} exceeds the physical size limit: {path.name}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(min(1024 * 1024, maximum_bytes - size + 1)):
            size += len(chunk)
            if size > maximum_bytes:
                raise ValueError(f"{label} exceeds the physical size limit: {path.name}")
            digest.update(chunk)
    return {
        "bytes": size,
        "filename": path.name,
        "kind": kind,
        "sha256": digest.hexdigest(),
    }


def _artifact_digest(path: Path) -> dict[str, object]:
    kind = "wheel" if path.suffix == ".whl" else "sdist" if path.name.endswith(".tar.gz") else None
    if kind is None:
        raise ValueError(f"Unsupported release artifact suffix: {path.name}")
    return _file_digest(path, kind=kind)


def _normalised_distribution_name(value: str, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_SBOM_FIELD_LENGTH
        or not _DISTRIBUTION_NAME_RE.fullmatch(value)
    ):
        raise ValueError(f"{label} must be a non-empty safe distribution name")
    return re.sub(r"[-_.]+", "-", value).casefold()


def _dependency_distribution_name(declaration: str) -> str:
    match = _DEPENDENCY_NAME_RE.match(declaration)
    if match is None:
        raise ValueError(f"Runtime dependency has an invalid distribution name: {declaration!r}")
    remainder = declaration[match.end() :].lstrip()
    if remainder and remainder[0] not in "[<>=!~;":
        raise ValueError(f"Runtime dependency declaration is not safely parseable: {declaration!r}")
    return _normalised_distribution_name(match.group(1), label="Runtime dependency")


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"SBOM JSON contains a non-standard constant: {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"SBOM JSON contains a duplicate object key: {key}")
        result[key] = value
    return result


def _validate_sbom_bytes(data: bytes, runtime_dependencies: list[str]) -> None:
    if not data:
        raise ValueError("SBOM must not be empty")
    if len(data) > MAX_SBOM_BYTES:
        raise ValueError(f"SBOM exceeds the {MAX_SBOM_BYTES}-byte size limit")
    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"SBOM is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("SBOM top level must be a JSON object")
    if payload.get("bomFormat") != "CycloneDX" or payload.get("specVersion") != "1.4":
        raise ValueError("SBOM must declare CycloneDX specVersion 1.4")
    if isinstance(payload.get("version"), bool) or not isinstance(payload.get("version"), int):
        raise ValueError("SBOM version must be a positive integer")
    if payload["version"] < 1:
        raise ValueError("SBOM version must be a positive integer")
    components = payload.get("components")
    if not isinstance(components, list) or len(components) > MAX_SBOM_COMPONENTS:
        raise ValueError("SBOM components must be a bounded JSON list")

    component_names: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("SBOM components must be JSON objects")
        component_type = component.get("type")
        name = component.get("name")
        version = component.get("version")
        if not isinstance(component_type, str) or component_type not in _CYCLONEDX_COMPONENT_TYPES:
            raise ValueError("SBOM component type is not valid for CycloneDX 1.4")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > MAX_SBOM_FIELD_LENGTH
            or not isinstance(version, str)
            or not version
            or len(version) > MAX_SBOM_FIELD_LENGTH
        ):
            raise ValueError("SBOM components must have non-empty bounded name and version")
        normalised_name = _normalised_distribution_name(name, label="SBOM component")
        if normalised_name in component_names:
            raise ValueError("SBOM components must have unique normalized names")
        component_names.add(normalised_name)

    expected_names = {_dependency_distribution_name(item) for item in runtime_dependencies}
    if component_names != expected_names:
        missing = sorted(expected_names - component_names)
        extra = sorted(component_names - expected_names)
        raise ValueError(
            f"SBOM components differ from runtime dependencies: missing={missing}, extra={extra}"
        )


def _read_sbom(path: Path, runtime_dependencies: list[str]) -> bytes:
    source = _without_symlink_components(path, label="SBOM source")
    try:
        if source.stat().st_size > MAX_SBOM_BYTES:
            raise ValueError(f"SBOM exceeds the {MAX_SBOM_BYTES}-byte size limit")
    except OSError as exc:
        raise ValueError(f"SBOM source could not be inspected: {exc}") from exc
    data = _regular_file(source, label="SBOM source")
    _validate_sbom_bytes(data, runtime_dependencies)
    return data


def _expected_artifacts(version: str) -> tuple[tuple[str, str], ...]:
    if (
        not version
        or PurePosixPath(version).name != version
        or any(part in {"", ".", ".."} for part in PurePosixPath(version).parts)
    ):
        raise ValueError("Project version must be a safe non-empty string")
    return tuple(
        sorted(
            (
                (
                    f"{build_backend._distribution_name(use_public_template=True)}-{version}-py3-none-any.whl",
                    "wheel",
                ),
                (
                    f"{build_backend._distribution_name(use_public_template=True)}-{version}.tar.gz",
                    "sdist",
                ),
            )
        )
    )


def _load_public_project(root: Path) -> dict[str, Any]:
    previous_root = build_backend.ROOT
    try:
        build_backend.ROOT = root
        project = build_backend._project()
    finally:
        build_backend.ROOT = previous_root
    if not isinstance(project, dict):
        raise ValueError("Project metadata must be a mapping")
    return project


def _manifest_payload(
    package: ProductPackage, project: dict[str, Any], artifacts: list[dict[str, object]]
) -> dict[str, object]:
    name = project.get("name")
    version = project.get("version")
    requires_python = project.get("requires-python")
    if name != package.name:
        raise ValueError("Project and package manifest names must match")
    if not isinstance(version, str) or not version:
        raise ValueError("Project version must be a non-empty string")
    if not isinstance(requires_python, str) or not requires_python:
        raise ValueError("Project requires-python must be a non-empty string")
    return {
        "artifacts": sorted(artifacts, key=lambda item: str(item["filename"])),
        "manifest_type": "dependency-inventory-and-build-manifest",
        "package": {
            "name": package.name,
            "requires_python": requires_python,
            "version": version,
        },
        "runtime_dependencies": sorted(package.dependencies),
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "scope_note": (
            "This is a dependency inventory and build manifest, not a formal SBOM, "
            "signature, or hosted attestation."
        ),
    }


def _checksum_text(artifacts: list[dict[str, object]]) -> bytes:
    return "".join(
        f"{item['sha256']}  {item['filename']}\n"
        for item in sorted(artifacts, key=lambda item: str(item["filename"]))
    ).encode("ascii")


def _directory_entries(directory: Path) -> set[str]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"Release artifact directory must be a regular directory: {directory}")
    entries: set[str] = set()
    for path in directory.iterdir():
        if path.is_symlink():
            raise ValueError(f"Release artifact directory contains a symbolic link: {path.name}")
        entries.add(path.name)
    return entries


def _without_symlink_components(path: Path, *, label: str) -> Path:
    absolute = path.absolute()
    if any(candidate.is_symlink() for candidate in (absolute, *absolute.parents)):
        raise ValueError(f"{label} must not use symbolic links")
    return absolute


def _metadata_header(message: Message, field: str) -> str:
    values = message.get_all(field, [])
    if len(values) != 1:
        raise ValueError(f"Wheel METADATA must contain exactly one {field} field")
    value = str(values[0])
    if not value:
        raise ValueError(f"Wheel METADATA {field} field must be non-empty")
    return value


def _wheel_metadata_record(path: Path, metadata_name: str) -> tuple[int, int]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Wheel must be a regular file: {path.name}")
    with path.open("rb") as stream:
        stream.seek(0, 2)
        file_size = stream.tell()
        tail_size = min(file_size, _ZIP_EOCD_SIZE + 65_535)
        stream.seek(file_size - tail_size)
        tail = stream.read(tail_size)
        eocd_in_tail = tail.rfind(_ZIP_EOCD_SIGNATURE)
        while eocd_in_tail >= 0:
            if eocd_in_tail + _ZIP_EOCD_SIZE <= len(tail):
                comment_length = struct.unpack_from("<H", tail, eocd_in_tail + 20)[0]
                if eocd_in_tail + _ZIP_EOCD_SIZE + comment_length == len(tail):
                    break
            eocd_in_tail = tail.rfind(_ZIP_EOCD_SIGNATURE, 0, eocd_in_tail)
        if eocd_in_tail < 0:
            raise ValueError("Wheel end-of-central-directory record is missing")
        eocd_offset = file_size - tail_size + eocd_in_tail
        (
            _signature,
            disk_number,
            central_directory_disk,
            entries_on_disk,
            total_entries,
            directory_size,
            directory_offset,
            _comment_length,
        ) = struct.unpack_from("<4s4H2LH", tail, eocd_in_tail)
        if (
            eocd_offset >= 20
            and tail[max(0, eocd_in_tail - 20) : eocd_in_tail - 16] == _ZIP64_LOCATOR_SIGNATURE
        ) or any(
            value == sentinel
            for value, sentinel in (
                (entries_on_disk, 0xFFFF),
                (total_entries, 0xFFFF),
                (directory_size, 0xFFFFFFFF),
                (directory_offset, 0xFFFFFFFF),
            )
        ):
            raise ValueError("ZIP64 wheels are not supported")
        if disk_number or central_directory_disk or entries_on_disk != total_entries:
            raise ValueError("Multidisk wheels are not supported")
        if total_entries < 1 or total_entries > MAX_WHEEL_MEMBERS:
            raise ValueError("Wheel contains an unsafe number of archive members")
        if directory_size > MAX_WHEEL_CENTRAL_DIRECTORY_BYTES:
            raise ValueError("Wheel central directory exceeds the safe size limit")
        if directory_offset + directory_size != eocd_offset:
            raise ValueError("Wheel central-directory layout is malformed")
        stream.seek(directory_offset)
        directory = stream.read(directory_size)
    if len(directory) != directory_size:
        raise ValueError("Wheel central directory could not be read safely")

    position = 0
    actual_entries = 0
    total_compressed_size = 0
    total_declared_size = 0
    metadata_records: list[tuple[int, int]] = []
    while position < len(directory):
        if (
            position + _ZIP_CENTRAL_DIRECTORY_RECORD_SIZE > len(directory)
            or directory[position : position + 4] != _ZIP_CENTRAL_DIRECTORY_SIGNATURE
        ):
            raise ValueError("Wheel central-directory record is malformed")
        flags, compression_method, compressed_size, declared_size = struct.unpack_from(
            "<HH8xLL",
            directory,
            position + 8,
        )
        name_length, extra_length, comment_length, disk_start = struct.unpack_from(
            "<4H",
            directory,
            position + 28,
        )
        record_end = (
            position
            + _ZIP_CENTRAL_DIRECTORY_RECORD_SIZE
            + name_length
            + extra_length
            + comment_length
        )
        if record_end > len(directory):
            raise ValueError("Wheel central-directory record is malformed")
        if disk_start or flags & 0x1:
            raise ValueError("Encrypted or multidisk wheel members are not supported")
        if compression_method not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError("Wheel member uses an unsupported compression method")
        if compressed_size == 0xFFFFFFFF or declared_size == 0xFFFFFFFF:
            raise ValueError("ZIP64 wheel members are not supported")
        if compressed_size > MAX_WHEEL_MEMBER_COMPRESSED_BYTES:
            raise ValueError("Wheel member compressed size exceeds the safe limit")
        if declared_size > MAX_WHEEL_MEMBER_BYTES:
            raise ValueError("Wheel member declared size exceeds the safe limit")
        total_compressed_size += compressed_size
        if total_compressed_size > MAX_WHEEL_TOTAL_COMPRESSED_BYTES:
            raise ValueError("Wheel compressed size exceeds the safe aggregate limit")
        total_declared_size += declared_size
        if total_declared_size > MAX_WHEEL_TOTAL_BYTES:
            raise ValueError("Wheel declared expansion exceeds the safe aggregate limit")
        if (
            declared_size > 0
            and declared_size / max(compressed_size, 1) > MAX_WHEEL_COMPRESSION_RATIO
        ):
            raise ValueError("Wheel member compression ratio is unsafe")
        name_bytes = directory[
            position + _ZIP_CENTRAL_DIRECTORY_RECORD_SIZE : position
            + _ZIP_CENTRAL_DIRECTORY_RECORD_SIZE
            + name_length
        ]
        try:
            name = name_bytes.decode("utf-8" if flags & 0x800 else "cp437")
        except UnicodeDecodeError as exc:
            raise ValueError("Wheel member name is not safely decodable") from exc
        if name == metadata_name:
            metadata_records.append((compressed_size, declared_size))
        actual_entries += 1
        position = record_end
    if actual_entries != total_entries:
        raise ValueError("Wheel central-directory entry count is inconsistent")
    if len(metadata_records) != 1:
        raise ValueError("Wheel must contain exactly one expected METADATA file")
    compressed_size, declared_size = metadata_records[0]
    if declared_size > MAX_WHEEL_METADATA_BYTES:
        raise ValueError("Wheel METADATA exceeds the safe size limit")
    return compressed_size, declared_size


def _verify_wheel_metadata(
    path: Path, package: dict[str, str], runtime_dependencies: list[str]
) -> None:
    metadata_name = f"{build_backend._distribution_name(use_public_template=True)}-{package['version']}.dist-info/METADATA"
    expected_compressed_size, expected_size = _wheel_metadata_record(path, metadata_name)
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.namelist().count(metadata_name) != 1:
                raise ValueError("Wheel must contain exactly one expected METADATA file")
            info = archive.getinfo(metadata_name)
            if info.compress_size != expected_compressed_size or info.file_size != expected_size:
                raise ValueError("Wheel METADATA directory metadata changed during inspection")
            with archive.open(info) as metadata_stream:
                metadata_bytes = metadata_stream.read(MAX_WHEEL_METADATA_BYTES + 1)
            if (
                len(metadata_bytes) > MAX_WHEEL_METADATA_BYTES
                or len(metadata_bytes) != expected_size
            ):
                raise ValueError("Wheel METADATA exceeds its bounded declared size")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Wheel METADATA could not be read: {exc}") from exc

    message = BytesParser(policy=email.policy.default).parsebytes(metadata_bytes)
    if message.defects:
        raise ValueError(f"Wheel METADATA has parse defects: {message.defects}")
    actual = {
        "name": _metadata_header(message, "Name"),
        "version": _metadata_header(message, "Version"),
        "requires_python": _metadata_header(message, "Requires-Python"),
    }
    expected = {
        "name": package["name"],
        "version": package["version"],
        "requires_python": package["requires_python"],
    }
    if actual != expected:
        raise ValueError(
            f"Wheel METADATA package fields differ from the release manifest: "
            f"expected={expected}, actual={actual}"
        )

    dependencies = [str(value) for value in message.get_all("Requires-Dist", [])]
    if len(dependencies) != len(set(dependencies)) or set(dependencies) != set(
        runtime_dependencies
    ):
        raise ValueError("Wheel METADATA Requires-Dist set differs from the release manifest")


def _validate_manifest_shape(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _MANIFEST_KEYS:
        raise ValueError("Release manifest has an invalid top-level shape")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("Release manifest has an unsupported schema version")
    if payload.get("manifest_type") != "dependency-inventory-and-build-manifest":
        raise ValueError("Release manifest has an invalid manifest type")
    if (
        not isinstance(payload.get("scope_note"), str)
        or "not a formal SBOM" not in payload["scope_note"]
    ):
        raise ValueError("Release manifest must state its non-SBOM scope")
    package = payload.get("package")
    if not isinstance(package, dict) or set(package) != _PACKAGE_KEYS:
        raise ValueError("Release manifest package metadata is invalid")
    if any(not isinstance(package[field], str) or not package[field] for field in _PACKAGE_KEYS):
        raise ValueError("Release manifest package metadata must contain non-empty strings")
    dependencies = payload.get("runtime_dependencies")
    if (
        not isinstance(dependencies, list)
        or any(not isinstance(item, str) or not item for item in dependencies)
        or dependencies != sorted(dependencies)
        or len(dependencies) != len(set(dependencies))
    ):
        raise ValueError("Release manifest runtime dependencies must be sorted unique strings")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) not in {2, 3}:
        raise ValueError(
            "Release manifest must declare two distribution artifacts and an optional SBOM"
        )
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != _ARTIFACT_KEYS:
            raise ValueError("Release manifest contains an invalid artifact record")
        filename = item.get("filename")
        if (
            not isinstance(filename, str)
            or not filename
            or PurePosixPath(filename).name != filename
            or "\\" in filename
        ):
            raise ValueError("Release manifest artifact names must be safe file names")
        if not isinstance(item.get("kind"), str) or item["kind"] not in {
            "wheel",
            "sdist",
            "sbom",
        }:
            raise ValueError("Release manifest artifact kind is invalid")
        if not isinstance(item.get("bytes"), int) or item["bytes"] < 0:
            raise ValueError("Release manifest artifact byte count is invalid")
        if not isinstance(item.get("sha256"), str) or not _SHA256_RE.fullmatch(item["sha256"]):
            raise ValueError("Release manifest artifact digest is invalid")
        if item["kind"] == "sbom" and (filename != SBOM_FILENAME or item["bytes"] > MAX_SBOM_BYTES):
            raise ValueError("Release manifest SBOM record is invalid")
    if [item["filename"] for item in artifacts] != sorted(item["filename"] for item in artifacts):
        raise ValueError("Release manifest artifacts must be sorted by filename")
    if len({item["filename"] for item in artifacts}) != len(artifacts):
        raise ValueError("Release manifest artifact names must be unique")
    if sum(item["kind"] == "sbom" for item in artifacts) > 1:
        raise ValueError("Release manifest may contain at most one SBOM artifact")
    return payload


def verify_release_artifacts(directory: str | Path) -> dict[str, Any]:
    """Verify the exact artifact set, manifest bytes, and checksums in a directory."""
    requested_root = _without_symlink_components(
        Path(directory), label="Release artifact directory"
    )
    root = requested_root.resolve(strict=True)
    manifest_path = root / MANIFEST_FILENAME
    checksums_path = root / CHECKSUMS_FILENAME
    entries = _directory_entries(root)
    if manifest_path.name not in entries or checksums_path.name not in entries:
        raise ValueError("Release artifact directory is missing its manifest or SHA256SUMS")
    try:
        manifest_bytes = _regular_file(manifest_path, label="Release manifest")
        payload = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Release manifest is not valid UTF-8 JSON: {exc}") from exc
    manifest = _validate_manifest_shape(payload)
    package = manifest["package"]
    expected = _expected_artifacts(package["version"])
    records = manifest["artifacts"]
    expected_records = {filename: kind for filename, kind in expected}
    allowed_filenames = set(expected_records) | {SBOM_FILENAME}
    actual_filenames = {item["filename"] for item in records}
    if actual_filenames != set(expected_records) and actual_filenames != allowed_filenames:
        raise ValueError("Release manifest is missing or has extra artifacts")
    sbom_records = [item for item in records if item["kind"] == "sbom"]
    if len(sbom_records) > 1 or (sbom_records and sbom_records[0]["filename"] != SBOM_FILENAME):
        raise ValueError("Release manifest SBOM artifact is invalid")
    if any(item["kind"] == "sbom" for item in records) != (len(records) == 3):
        raise ValueError(
            "Release manifest must contain exactly two distributions and an optional SBOM"
        )
    if any(
        item["kind"] != expected_records.get(item["filename"])
        for item in records
        if item["kind"] != "sbom"
    ):
        raise ValueError("Release manifest artifact kinds do not match their filenames")
    expected_entries = set(expected_records) | {MANIFEST_FILENAME, CHECKSUMS_FILENAME}
    if sbom_records:
        expected_entries.add(SBOM_FILENAME)
    if entries != expected_entries:
        missing = sorted(expected_entries - entries)
        extra = sorted(entries - expected_entries)
        raise ValueError(
            f"Release artifact directory differs from its manifest: missing={missing}, extra={extra}"
        )

    actual_records = [
        _file_digest(
            root / item["filename"],
            kind=str(item["kind"]),
            label="Release artifact",
        )
        for item in records
    ]
    if actual_records != records:
        raise ValueError("Release artifact metadata does not match the artifact bytes")
    wheel_path = root / next(filename for filename, kind in expected if kind == "wheel")
    _verify_wheel_metadata(wheel_path, package, manifest["runtime_dependencies"])
    if sbom_records:
        _validate_sbom_bytes(
            _regular_file(root / SBOM_FILENAME, label="SBOM artifact"),
            manifest["runtime_dependencies"],
        )
    expected_manifest_bytes = _canonical_json(manifest)
    if manifest_bytes != expected_manifest_bytes:
        raise ValueError("Release manifest is not in its canonical deterministic form")
    checksums_bytes = _regular_file(checksums_path, label="SHA256SUMS")
    if checksums_bytes != _checksum_text(records):
        raise ValueError("SHA256SUMS does not match the release artifact metadata")
    return manifest


def build_release(
    source_root: str | Path,
    output_directory: str | Path,
    sbom_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build and verify the distributions, with an optional validated external SBOM."""
    root = Path(source_root).resolve(strict=True)
    requested_output = _without_symlink_components(
        Path(output_directory), label="Release output directory"
    )
    output = requested_output.resolve()
    if output == root:
        raise ValueError("Release output directory must be separate from the source root")
    output.mkdir(parents=True, exist_ok=True)
    package = load_product_package(root, require_tracked=False)
    project = _load_public_project(root)
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("Project version must be a non-empty string")
    expected_names = {filename for filename, _ in _expected_artifacts(version)}
    existing = _directory_entries(output)
    if existing:
        raise ValueError(f"Release output directory must be empty: {sorted(existing)}")
    sbom_bytes = _read_sbom(Path(sbom_path), list(package.dependencies)) if sbom_path else None

    previous_root = build_backend.ROOT
    try:
        build_backend.ROOT = root
        wheel_name = build_backend.build_wheel(str(output))
        sdist_name = build_backend.build_sdist(str(output))
    finally:
        build_backend.ROOT = previous_root
    if {wheel_name, sdist_name} != expected_names:
        raise ValueError("Build backend produced an unexpected artifact set")
    artifacts = [_artifact_digest(output / filename) for filename in sorted(expected_names)]
    if sbom_bytes is not None:
        (output / SBOM_FILENAME).write_bytes(sbom_bytes)
        artifacts.append(_file_digest(output / SBOM_FILENAME, kind="sbom"))
    manifest = _manifest_payload(package, project, artifacts)
    (output / MANIFEST_FILENAME).write_bytes(_canonical_json(manifest))
    (output / CHECKSUMS_FILENAME).write_bytes(_checksum_text(artifacts))
    return verify_release_artifacts(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--sbom", type=Path, help="Validated external CycloneDX 1.4 JSON SBOM")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.verify_only:
            if args.sbom is not None:
                raise ValueError("--sbom cannot be used with --verify-only")
            manifest = verify_release_artifacts(args.destination)
        else:
            manifest = build_release(args.root, args.destination, sbom_path=args.sbom)
    except (OSError, ValueError) as exc:
        print(f"Release artifact check failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "artifacts": len(manifest["artifacts"]),
                "manifest": MANIFEST_FILENAME,
                "verified": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Concrete filesystem primitives shared by local application and interview content."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from career_os.file_transactions import (
    _fsync_directory,
    _is_link_or_reparse,
    _safe_regular_file_path,
)

MAX_ARTIFACT_BYTES = 2 * 1024 * 1024


def _directory(path: Path) -> Path:
    for component in (path, *path.parents):
        if _is_link_or_reparse(component):
            raise ValueError("Application directories must not redirect through links")
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("Application directory path is not a directory")
    return path


def _inventory(files: dict[str, bytes]) -> dict[str, dict[str, object]]:
    return {
        name: {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
        for name, content in files.items()
    }


def read_content_artifacts(
    target: Path,
    names: set[str],
    *,
    plan_name: str,
    plan_bytes: bytes,
    label: str,
) -> dict[str, bytes]:
    """Read bounded artifact bytes once, reusing the already inspected plan bytes.

    The repository owns exact directory-inventory, manifest and authority checks
    and holds its existing transaction. This result is local to that operation;
    it is not a cache or a replacement for later workspace race checks.
    """
    if plan_name not in names or any(
        not name or name in {".", ".."} or any(char in name for char in "\\/:\x00")
        for name in names
    ):
        raise ValueError("Content artifacts require fixed basenames and a declared plan")
    if not isinstance(plan_bytes, bytes) or not 1 <= len(plan_bytes) <= MAX_ARTIFACT_BYTES:
        raise ValueError(f"{label} artifact size is invalid")
    files = {plan_name: plan_bytes}
    for name in sorted(names - {plan_name}):
        path = _safe_regular_file_path(target / name)
        with path.open("rb") as stream:
            content = stream.read(MAX_ARTIFACT_BYTES + 1)
        if not 1 <= len(content) <= MAX_ARTIFACT_BYTES:
            raise ValueError(f"{label} artifact size is invalid")
        files[name] = content
    return files


def publish_content_version(target: Path, files: dict[str, bytes]) -> None:
    """Publish a complete new directory while the caller holds its authority lock."""
    if not files or any(
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or not isinstance(content, bytes)
        or not 1 <= len(content) <= MAX_ARTIFACT_BYTES
        for name, content in files.items()
    ):
        raise ValueError("Content artifacts require bounded bytes and fixed basenames")
    _directory(target)
    if target.exists():
        raise ValueError("Content version already exists")
    _directory(target.parent).mkdir(parents=True, exist_ok=True, mode=0o700)
    stage = Path(tempfile.mkdtemp(prefix=".prepare-", dir=target.parent))
    try:
        for name, content in files.items():
            descriptor = os.open(stage / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        _fsync_directory(stage)
        _directory(target)
        if target.exists():
            raise ValueError("Content version appeared during preparation")
        os.rename(stage, target)
        _fsync_directory(target.parent)
    finally:
        if stage.exists():
            _directory(stage)
            for name in files:
                _safe_regular_file_path(stage / name).unlink(missing_ok=True)
            stage.rmdir()

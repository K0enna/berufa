"""Cooperative, durable compare-and-swap writes for small local state files.

The lock is deliberately a sidecar file: every Rolevane writer for the same
target converges on the same OS-backed inter-process lock without locking or
renaming the data file while it is being read.  Callers still supply an
expected hash when an edit was previewed earlier; it is checked *after* the
lock is held so a stale preview fails rather than overwriting another edit.
"""

from __future__ import annotations

import ctypes
import os
import stat
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from career_os.process_control import ProcessLockError, exclusive_process_lock


class FileConflictError(RuntimeError):
    """A cooperative writer is active or the supplied source hash is stale."""


@dataclass(frozen=True, slots=True)
class FileTransactionResult:
    path: Path
    previous: bytes
    current: bytes
    backup: Path | None


def sha256_hex(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def file_identity(path: Path) -> tuple[int, int]:
    return metadata_identity(path.stat())


def metadata_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def open_anchored_binary(
    root: Path,
    root_identity: tuple[int, int] | None,
    relative_path: Path,
    *,
    label: str,
    writable: bool = False,
):
    """Open a file through pinned path components below an identity-bound root."""

    if (
        root_identity is None
        or not relative_path.parts
        or relative_path.is_absolute()
        or bool(relative_path.anchor)
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise ValueError(f"{label} is no longer safely contained")
    if os.name == "nt":
        return _open_windows_anchored_binary(
            root, root_identity, relative_path, label=label, writable=writable
        )
    return _open_posix_anchored_binary(
        root, root_identity, relative_path, label=label, writable=writable
    )


def _open_posix_anchored_binary(
    root: Path,
    root_identity: tuple[int, int],
    relative_path: Path,
    *,
    label: str,
    writable: bool = False,
):
    @contextmanager
    def opened_file():
        file_descriptor: int | None = None
        yielded = False
        with open_posix_anchored_directory(
            root, root_identity, relative_path.parent, label=label
        ) as parent_descriptor:
            try:
                file_descriptor = os.open(
                    relative_path.name,
                    (os.O_RDWR if writable else os.O_RDONLY)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=parent_descriptor,
                )
                with os.fdopen(
                    file_descriptor, "r+b" if writable else "rb", closefd=False
                ) as stream:
                    yielded = True
                    yield stream
            except FileNotFoundError:
                raise
            except OSError as exc:
                if yielded:
                    raise
                raise ValueError(f"{label} could not be opened safely") from exc
            finally:
                if file_descriptor is not None:
                    with suppress(OSError):
                        os.close(file_descriptor)

    return opened_file()


def open_posix_anchored_directory(
    root: Path,
    root_identity: tuple[int, int] | None,
    relative_path: Path,
    *,
    label: str,
):
    """Pin a directory beneath a trusted root without opening its SQLite child file."""

    if (
        os.name == "nt"
        or root_identity is None
        or relative_path.is_absolute()
        or bool(relative_path.anchor)
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise ValueError(f"{label} is no longer safely contained")

    @contextmanager
    def opened_directory():
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptors: list[int] = []
        yielded = False
        try:
            descriptor = os.open(root, flags)
            descriptors.append(descriptor)
            if metadata_identity(os.fstat(descriptor)) != root_identity:
                raise ValueError(f"{label} root changed after validation")
            for component in relative_path.parts:
                descriptor = os.open(component, flags, dir_fd=descriptors[-1])
                descriptors.append(descriptor)
                if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise ValueError(f"{label} is no longer safely contained")
            yielded = True
            yield descriptors[-1]
        except FileNotFoundError:
            raise
        except OSError as exc:
            if yielded:
                raise
            raise ValueError(f"{label} could not be opened safely") from exc
        finally:
            for descriptor in reversed(descriptors):
                with suppress(OSError):
                    os.close(descriptor)

    return opened_directory()


def _open_windows_anchored_binary(
    root: Path,
    root_identity: tuple[int, int],
    relative_path: Path,
    *,
    label: str,
    writable: bool = False,
):
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    file_attribute_reparse_point = 0x00000400
    invalid_handle = ctypes.c_void_p(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(FileInformation))
    get_information.restype = wintypes.BOOL

    def open_component(path: Path, *, directory: bool) -> tuple[int, FileInformation]:
        handle = create_file(
            str(path),
            generic_read | (0x40000000 if writable and not directory else 0),
            file_share_read | file_share_write,
            None,
            open_existing,
            file_flag_open_reparse_point | (file_flag_backup_semantics if directory else 0),
            None,
        )
        if handle == invalid_handle:
            raise OSError(ctypes.get_last_error(), "CreateFileW failed", str(path))
        information = FileInformation()
        if not get_information(handle, ctypes.byref(information)):
            error = ctypes.get_last_error()
            close_handle(handle)
            raise OSError(error, "GetFileInformationByHandle failed", str(path))
        if information.attributes & file_attribute_reparse_point:
            close_handle(handle)
            raise ValueError(f"{label} crosses a reparse boundary")
        return handle, information

    @contextmanager
    def opened_file():
        handles: list[int] = []
        descriptor: int | None = None
        yielded = False
        try:
            current = Path(root.anchor)
            handle, root_information = open_component(current, directory=True)
            handles.append(handle)
            for component in root.parts[1:]:
                current /= component
                handle, root_information = open_component(current, directory=True)
                handles.append(handle)
            if not handles or root_information is None:
                raise ValueError(f"{label} root is no longer available")
            if (
                (root_information.file_index_high << 32) | root_information.file_index_low
            ) != root_identity[1]:
                raise ValueError(f"{label} root changed after validation")
            current = root
            for component in relative_path.parts[:-1]:
                current /= component
                handle, _information = open_component(current, directory=True)
                handles.append(handle)
            handle, _information = open_component(
                current / relative_path.parts[-1], directory=False
            )
            handles.append(handle)
            descriptor = msvcrt.open_osfhandle(
                handle, (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_BINARY", 0)
            )
            handles.pop()
            with os.fdopen(descriptor, "r+b" if writable else "rb", closefd=False) as stream:
                yielded = True
                yield stream
        except FileNotFoundError:
            raise
        except OSError as exc:
            if yielded:
                raise
            raise ValueError(f"{label} could not be opened safely") from exc
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
            for handle in reversed(handles):
                close_handle(handle)

    return opened_file()


def atomic_replace_anchored(
    target: Path,
    replacement: bytes,
    *,
    expected_parent_identity: tuple[int, int],
) -> None:
    """Publish bytes by replacing one entry in the identity-bound parent directory."""

    if not target.name or target.name in {".", ".."}:
        raise ValueError("Anchored replacement target must be one file name")
    if os.name == "nt":
        _atomic_replace_windows_anchored(target, replacement, expected_parent_identity)
        return
    _atomic_replace_posix_anchored(target, replacement, expected_parent_identity)


def _atomic_replace_posix_anchored(
    target: Path,
    replacement: bytes,
    expected_parent_identity: tuple[int, int],
) -> None:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_descriptor: int | None = None
    temporary_descriptor: int | None = None
    temporary_created = False
    temporary_name = f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        directory_descriptor = os.open(target.parent, directory_flags)
        if metadata_identity(os.fstat(directory_descriptor)) != expected_parent_identity:
            raise ValueError("Anchored replacement parent changed after validation")
        temporary_descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=directory_descriptor,
        )
        temporary_created = True
        with os.fdopen(temporary_descriptor, "wb", closefd=False) as stream:
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(
            temporary_name,
            target.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.fsync(directory_descriptor)
    except OSError as exc:
        raise ValueError("Anchored replacement could not be published safely") from exc
    finally:
        if temporary_descriptor is not None:
            with suppress(OSError):
                os.close(temporary_descriptor)
        if directory_descriptor is not None and temporary_created:
            with suppress(OSError):
                os.unlink(temporary_name, dir_fd=directory_descriptor)
        if directory_descriptor is not None:
            with suppress(OSError):
                os.close(directory_descriptor)


def _atomic_replace_windows_anchored(
    target: Path,
    replacement: bytes,
    expected_parent_identity: tuple[int, int],
) -> None:
    from ctypes import wintypes

    generic_read = 0x80000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    file_attribute_reparse_point = 0x00000400
    invalid_handle = ctypes.c_void_p(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(FileInformation))
    get_information.restype = wintypes.BOOL
    handles: list[int] = []
    temporary: Path | None = None
    try:
        current = Path(target.parent.anchor)
        handle = create_file(
            str(current),
            generic_read,
            file_share_read | file_share_write,
            None,
            open_existing,
            file_flag_open_reparse_point | file_flag_backup_semantics,
            None,
        )
        if handle == invalid_handle:
            raise OSError(ctypes.get_last_error(), "CreateFileW failed", str(current))
        handles.append(handle)
        parent_information = FileInformation()
        if not get_information(handle, ctypes.byref(parent_information)):
            raise OSError(
                ctypes.get_last_error(), "GetFileInformationByHandle failed", str(current)
            )
        if parent_information.attributes & file_attribute_reparse_point:
            raise ValueError("Anchored replacement parent crosses a reparse boundary")
        for component in target.parent.parts[1:]:
            current /= component
            handle = create_file(
                str(current),
                generic_read,
                file_share_read | file_share_write,
                None,
                open_existing,
                file_flag_open_reparse_point | file_flag_backup_semantics,
                None,
            )
            if handle == invalid_handle:
                raise OSError(ctypes.get_last_error(), "CreateFileW failed", str(current))
            handles.append(handle)
            parent_information = FileInformation()
            if not get_information(handle, ctypes.byref(parent_information)):
                raise OSError(
                    ctypes.get_last_error(), "GetFileInformationByHandle failed", str(current)
                )
            if parent_information.attributes & file_attribute_reparse_point:
                raise ValueError("Anchored replacement parent crosses a reparse boundary")
        parent_file_index = (
            parent_information.file_index_high << 32
        ) | parent_information.file_index_low
        if parent_file_index != expected_parent_identity[1]:
            raise ValueError("Anchored replacement parent changed after validation")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            _safe_regular_file_path(target)
            _replace_windows(target, temporary, None)
        else:
            os.replace(temporary, target)
        temporary = None
    except OSError as exc:
        raise ValueError("Anchored replacement could not be published safely") from exc
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink()
        for handle in reversed(handles):
            close_handle(handle)


def _lock_path(target: Path) -> Path:
    return _safe_regular_file_path(target.with_name(f".{target.name}.lock"))


def _is_link_or_reparse(path: Path) -> bool:
    """Return whether a lexical path component redirects through indirection."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        if callable(isjunction) and isjunction(path):
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _safe_regular_file_path(path: str | Path) -> Path:
    """Validate a target lexically before any symlink-resolving operation."""

    requested = Path(path)
    # ``Path.absolute`` preserves lexical ``..`` components.  Normalizing with
    # ``os.path.abspath`` first could erase a symlinked ancestor from the scan.
    lexical = requested.absolute()
    if not lexical.name or lexical.name in {".", ".."}:
        raise ValueError("File transaction target must be a regular file path")

    for parent in lexical.parents:
        if _is_link_or_reparse(parent):
            raise ValueError(
                "File transaction target parent must not be a symlink or reparse point"
            )
        try:
            metadata = parent.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError("File transaction target parent cannot be inspected") from exc
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("File transaction target parent must be a directory")

    if _is_link_or_reparse(lexical):
        raise ValueError("File transaction target must not be a symlink or reparse point")
    try:
        metadata = lexical.lstat()
    except FileNotFoundError:
        return lexical
    except OSError as exc:
        raise ValueError("File transaction target cannot be inspected") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("File transaction target must be a regular file")
    return lexical


@contextmanager
def locked_file_guard(path: str | Path) -> Iterator[Path]:
    """Hold the canonical sidecar lock for a validated regular-file target.

    Callers that coordinate a multi-file operation may use this guard and then
    perform narrower transactions while preserving the repository lock order.
    The yielded path is revalidated after the lock is acquired.
    """

    target = _safe_regular_file_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with exclusive_process_lock(_lock_path(target)):
            yield _safe_regular_file_path(target)
    except ProcessLockError as exc:
        raise FileConflictError(
            "Another Rolevane writer is updating this file; retry after it completes"
        ) from exc


def read_bounded_regular_file(path: str | Path, *, maximum_bytes: int) -> bytes:
    """Read one validated regular file through a single bounded byte stream."""

    if maximum_bytes < 1:
        raise ValueError("File read size limit must be positive")
    target = _safe_regular_file_path(path)
    try:
        with target.open("rb") as stream:
            payload = stream.read(maximum_bytes + 1)
    except FileNotFoundError as exc:
        raise ValueError("Required regular file is not available") from exc
    if len(payload) > maximum_bytes:
        raise ValueError("File exceeds configured size limit")
    return payload


def _backup_path(target: Path, digest: str) -> Path:
    token = uuid.uuid4().hex[:12]
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return target.with_name(f"{target.name}.{timestamp}.{token}.{digest[:12]}.bak")


def _fsync_directory(directory: Path) -> None:
    """Persist the rename on POSIX filesystems when directory fsync is available."""

    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_windows(target: Path, temporary: Path, backup: Path | None) -> None:
    """Replace an existing Windows file without dropping its ACL/metadata."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    replace_file = kernel32.ReplaceFileW
    replace_file.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    replace_file.restype = ctypes.c_int
    backup_name = str(backup) if backup is not None else None
    if not replace_file(str(target), str(temporary), backup_name, 0, None, None):
        error = ctypes.get_last_error()
        raise OSError(error, f"ReplaceFileW failed for {target}")


def _write_and_replace(
    target: Path,
    previous: bytes,
    replacement: bytes,
    *,
    retain_backup: bool,
) -> Path | None:
    target_stat = target.stat() if target.exists() else None
    backup = _backup_path(target, sha256_hex(previous)) if retain_backup and target_stat else None
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    committed = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
            if target_stat is not None and os.name != "nt":
                os.fchmod(handle.fileno(), stat.S_IMODE(target_stat.st_mode))

        if os.name == "nt" and target_stat is not None:
            _replace_windows(target, temporary, backup)
        else:
            if backup is not None:
                backup_descriptor = os.open(
                    backup,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    stat.S_IMODE(target_stat.st_mode),
                )
                with os.fdopen(backup_descriptor, "wb") as handle:
                    os.fchmod(handle.fileno(), stat.S_IMODE(target_stat.st_mode))
                    handle.write(previous)
                    handle.flush()
                    os.fsync(handle.fileno())
            os.replace(temporary, target)
            committed = True
            _fsync_directory(target.parent)
        committed = True
        return backup
    except Exception:
        if not committed and backup is not None and os.name != "nt":
            backup.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def locked_compare_and_swap(
    path: str | Path,
    update: Callable[[bytes], bytes],
    *,
    expected_sha256: str | None = None,
    expected_exists: bool | None = None,
    retain_backup: bool = False,
    maximum_bytes: int | None = None,
) -> FileTransactionResult:
    """Atomically replace a small file after a locked, hash-checked transform.

    The guarantee covers Rolevane writers that use this primitive.  An editor
    that ignores the sidecar lock is detected if it changes the source before
    this operation enters its critical section, but cannot be made portable
    content-CAS atomic at the final OS replacement syscall.
    """

    requested = Path(path)
    target = _safe_regular_file_path(requested)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with exclusive_process_lock(_lock_path(target)):
            target = _safe_regular_file_path(target)
            if maximum_bytes is not None:
                try:
                    with target.open("rb") as stream:
                        previous = stream.read(maximum_bytes + 1)
                    existed = True
                except FileNotFoundError:
                    previous = b""
                    existed = False
                if len(previous) > maximum_bytes:
                    raise ValueError("File transaction input exceeds configured size limit")
            else:
                try:
                    previous = target.read_bytes()
                    existed = True
                except FileNotFoundError:
                    previous = b""
                    existed = False
            if expected_exists is not None and existed is not expected_exists:
                raise FileConflictError(
                    "File existence changed after this update was prepared; regenerate the update"
                )
            actual_hash = sha256_hex(previous)
            if expected_sha256 is not None and actual_hash != expected_sha256:
                raise FileConflictError(
                    "File changed after this update was prepared; regenerate the update"
                )
            current = update(previous)
            if not isinstance(current, bytes):
                raise TypeError("File transaction updates must return bytes")
            if current == previous:
                return FileTransactionResult(target, previous, current, None)
            backup = _write_and_replace(
                target,
                previous,
                current,
                retain_backup=retain_backup,
            )
            return FileTransactionResult(target, previous, current, backup)
    except ProcessLockError as exc:
        raise FileConflictError(
            "Another Rolevane writer is updating this file; retry after it completes"
        ) from exc


def locked_compare_and_delete(
    path: str | Path,
    *,
    expected_sha256: str,
    maximum_bytes: int | None = None,
) -> bytes:
    """Delete an exact regular-file version while holding its cooperative writer lock."""

    target = _safe_regular_file_path(path)
    try:
        with exclusive_process_lock(_lock_path(target)):
            target = _safe_regular_file_path(target)
            try:
                if maximum_bytes is None:
                    previous = target.read_bytes()
                else:
                    with target.open("rb") as stream:
                        previous = stream.read(maximum_bytes + 1)
            except FileNotFoundError as exc:
                raise FileConflictError(
                    "File changed after this deletion was prepared; regenerate the deletion"
                ) from exc
            if maximum_bytes is not None and len(previous) > maximum_bytes:
                raise ValueError("File transaction input exceeds configured size limit")
            if sha256_hex(previous) != expected_sha256:
                raise FileConflictError(
                    "File changed after this deletion was prepared; regenerate the deletion"
                )
            target.unlink()
            _fsync_directory(target.parent)
            return previous
    except ProcessLockError as exc:
        raise FileConflictError(
            "Another Rolevane writer is updating this file; retry after it completes"
        ) from exc

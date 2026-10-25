from __future__ import annotations

import ctypes
import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5


class ProcessLockError(OSError):
    """A singleton process lock is already held by another process."""


@contextmanager
def locked_process_handle(handle, *, label: str) -> Iterator[None]:
    """Hold OS exclusion on an open file without modifying its retained bytes."""
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise ProcessLockError(f"Another Rolevane run is already active for {label}") from exc
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_process_lock(path: str | Path) -> Iterator[Path]:
    """Retain one PID on the same OS-locked file; abnormal exit releases exclusion."""
    lock_path = Path(path).resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # O_APPEND ignores seek for writes. Never truncate until OS exclusion is held.
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    with (
        os.fdopen(descriptor, "r+b") as handle,
        locked_process_handle(handle, label=str(lock_path)),
    ):
        handle.seek(0)
        handle.write(str(os.getpid()).encode("ascii"))
        handle.truncate()
        handle.flush()
        yield lock_path


def process_exists(pid: int) -> bool:
    """Check a PID without signaling it.

    ``os.kill(pid, 0)`` is intentionally avoided on Windows because CPython maps
    non-console signals to ``TerminateProcess`` there.
    """

    if isinstance(pid, bool) or not isinstance(pid, int) or not 1 <= pid <= 4_294_967_295:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_exit_code.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == STILL_ACTIVE
    finally:
        close_handle(handle)


def terminate_process(pid: int) -> None:
    """Terminate one already identity-verified PID, without a shell or process-name match."""

    if isinstance(pid, bool) or not isinstance(pid, int) or not 1 <= pid <= 4_294_967_295:
        raise ValueError("Invalid process id")
    if os.name != "nt":
        os.kill(pid, signal.SIGTERM)
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    terminate = kernel32.TerminateProcess
    terminate.argtypes = (wintypes.HANDLE, wintypes.UINT)
    terminate.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(PROCESS_TERMINATE, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"Unable to open dashboard process {pid}")
    try:
        if not terminate(handle, 0):
            raise OSError(ctypes.get_last_error(), f"Unable to stop dashboard process {pid}")
    finally:
        close_handle(handle)

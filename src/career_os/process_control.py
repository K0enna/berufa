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
def exclusive_process_lock(path: str | Path) -> Iterator[Path]:
    """Hold an OS-backed non-blocking lock for one process-scoped operation.

    The lock file deliberately remains on disk. The operating system releases the
    actual lock if the process exits or is killed, so stale PID-file cleanup and PID
    reuse cannot allow two writers into the same critical section.
    """

    lock_path = Path(path).resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ProcessLockError(
                f"Another Rolevane run is already active for {lock_path}"
            ) from exc

        try:
            handle.seek(0)
            handle.write(str(os.getpid()).encode("ascii"))
            handle.truncate()
            handle.flush()
            yield lock_path
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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

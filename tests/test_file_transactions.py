import ctypes
import os
import stat
import subprocess
import sys
import time
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest

import career_os.file_transactions as file_transactions
from career_os.file_transactions import (
    FileConflictError,
    file_identity,
    locked_compare_and_delete,
    locked_compare_and_swap,
    open_anchored_binary,
    open_posix_anchored_directory,
)
from career_os.infrastructure import content_artifacts


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement DACL contract")
def test_anchored_windows_replacement_preserves_restricted_dacl(tmp_path):
    target = tmp_path / "fictional-report.json"
    target.write_bytes(b"before")
    sid = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert sid.startswith("S-1-")

    def acl(*args):
        subprocess.run(["icacls.exe", *map(str, args)], check=True, capture_output=True)

    try:
        acl(tmp_path, "/grant", "*S-1-5-32-545:(OI)(CI)(R)")
        acl(target, "/inheritance:r", "/grant:r", f"*{sid}:(F)", "*S-1-5-18:(F)")
        before, after = tmp_path / "before.acl", tmp_path / "after.acl"
        acl(target, "/save", before)
        parent = tmp_path.stat()
        file_transactions.atomic_replace_anchored(
            target, b"after", expected_parent_identity=(parent.st_dev, parent.st_ino)
        )
        acl(target, "/save", after)
        assert target.read_bytes() == b"after"
        assert after.read_bytes() == before.read_bytes()
    finally:
        # Only this new fictional fixture directory was granted the Users rule.
        acl(tmp_path, "/remove:g", "*S-1-5-32-545")


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO semantics")
def test_anchored_open_checks_fifo_type_without_waiting_for_a_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fifo = tmp_path / "payload"
    os.mkfifo(fifo)
    original_open = file_transactions.os.open

    def checked_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if dir_fd is not None and str(path) == fifo.name:
            assert flags & os.O_NONBLOCK
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(file_transactions.os, "open", checked_open)

    with open_anchored_binary(
        tmp_path,
        file_identity(tmp_path),
        Path(fifo.name),
        label="Synthetic file",
    ) as stream:
        assert stat.S_ISFIFO(os.fstat(stream.fileno()).st_mode)


def test_anchored_open_rejects_absolute_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="safely contained"):
        open_anchored_binary(
            tmp_path,
            file_identity(tmp_path),
            tmp_path / "outside.json",
            label="Synthetic file",
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory descriptor traversal")
def test_anchored_directory_pins_parent_without_opening_database_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "data" / "nested"
    parent.mkdir(parents=True)
    database = parent / "vault.db"
    database.write_bytes(b"synthetic")
    original_open = os.open
    opened: list[str] = []

    def checked_open(path, flags, mode=0o777, *, dir_fd=None):  # noqa: ANN001
        opened.append(str(path))
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(file_transactions.os, "open", checked_open)
    with open_posix_anchored_directory(
        tmp_path, file_identity(tmp_path), Path("data/nested"), label="Synthetic database parent"
    ) as descriptor:
        assert stat.S_ISDIR(os.fstat(descriptor).st_mode)
        assert stat.S_ISREG(os.stat(database.name, dir_fd=descriptor).st_mode)

    assert database.name not in opened
    assert str(database) not in opened


def test_anchored_open_preserves_caller_storage_errors(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b"before")
    failure = OSError("synthetic storage write failure")
    with (
        pytest.raises(OSError) as raised,
        open_anchored_binary(
            tmp_path, file_identity(tmp_path), Path(target.name), label="Synthetic file"
        ),
    ):
        raise failure
    assert raised.value is failure


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-root handles")
def test_windows_anchored_read_supports_drive_root(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b"synthetic")
    root = Path(target.anchor)
    with open_anchored_binary(
        root, file_identity(root), target.relative_to(root), label="Synthetic file"
    ) as stream:
        assert stream.read() == b"synthetic"


@pytest.mark.skipif(os.name == "nt", reason="POSIX rename and directory fsync")
def test_directory_sync_failure_preserves_published_backup(tmp_path, monkeypatch) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b"before")
    monkeypatch.setattr(
        file_transactions,
        "_fsync_directory",
        lambda _path: (_ for _ in ()).throw(OSError("synthetic directory sync failure")),
    )
    with pytest.raises(OSError, match="directory sync"):
        locked_compare_and_swap(target, lambda _previous: b"after", retain_backup=True)
    assert target.read_bytes() == b"after"
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"before"


@pytest.mark.skipif(os.name != "nt", reason="Windows root-handle semantics")
@pytest.mark.parametrize(
    "root",
    [Path(Path.cwd().anchor), Path(r"\\synthetic-server\synthetic-share")],
    ids=["drive", "unc-share"],
)
def test_windows_anchored_replace_accepts_a_direct_root_parent(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
) -> None:
    class StopBeforeRootWrite(RuntimeError):
        pass

    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

    opened: list[str] = []

    def create_file(path, *_args):
        opened.append(path)
        return 7

    def get_information(_handle, information):
        information._obj.attributes = 0
        information._obj.file_index_high = 0
        information._obj.file_index_low = 99
        return True

    class FakeKernel32:
        CreateFileW = FakeFunction(create_file)
        GetFileInformationByHandle = FakeFunction(get_information)
        CloseHandle = FakeFunction(lambda _handle: True)

    monkeypatch.setattr(
        file_transactions.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: FakeKernel32(),
    )
    target = root / "migration-report.json"

    def stop_before_write(*_args, **kwargs):
        assert Path(kwargs["dir"]) == target.parent
        raise StopBeforeRootWrite

    monkeypatch.setattr(file_transactions.tempfile, "mkstemp", stop_before_write)

    with pytest.raises(StopBeforeRootWrite):
        file_transactions._atomic_replace_windows_anchored(target, b"replacement", (0, 99))

    assert opened == [str(target.parent)]


def test_locked_compare_and_swap_rejects_stale_previews_after_interprocess_write(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b'{"state":"before"}\n')
    expected = sha256(target.read_bytes()).hexdigest()
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    script = """
from pathlib import Path
from career_os.file_transactions import locked_compare_and_swap
import sys, time
target = Path(sys.argv[1])
ready = Path(sys.argv[2])
expected = sys.argv[3]
release = Path(sys.argv[4])
def update(previous):
    ready.write_text("locked", encoding="utf-8")
    deadline = time.monotonic() + 20
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not release.exists():
        raise SystemExit(2)
    return b'{"state":"after"}\\n'
locked_compare_and_swap(target, update, expected_sha256=expected)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(target), str(ready), expected, str(release)],
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        with pytest.raises(FileConflictError, match="Another Rolevane writer"):
            locked_compare_and_swap(target, lambda previous: previous, expected_sha256=expected)
        release.write_text("go", encoding="utf-8")
        assert process.wait(timeout=20) == 0
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    with pytest.raises(FileConflictError, match="File changed after"):
        locked_compare_and_swap(target, lambda previous: previous, expected_sha256=expected)
    assert target.read_bytes() == b'{"state":"after"}\n'


def test_crashed_writer_releases_the_os_lock_for_a_retry(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("before", encoding="utf-8")
    script = """
from career_os.process_control import exclusive_process_lock
from pathlib import Path
import os, sys
with exclusive_process_lock(Path(sys.argv[1]).with_name('.state.json.lock')):
    os._exit(0)
"""
    assert subprocess.run([sys.executable, "-c", script, str(target)], check=False).returncode == 0
    locked_compare_and_swap(target, lambda _previous: b"after")
    assert target.read_bytes() == b"after"


def test_locked_compare_and_swap_rejects_symlink_target(tmp_path: Path) -> None:
    target = tmp_path / "real-state.json"
    target.write_bytes(b"before\n")
    link = tmp_path / "state.json"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlinks are unavailable on this platform: {exc}")

    with pytest.raises(ValueError, match="symlink"):
        locked_compare_and_swap(link, lambda _previous: b"after\n")
    assert target.read_bytes() == b"before\n"


def test_locked_compare_and_swap_rejects_symlinked_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(outside, target_is_directory=True)
    except OSError:
        # Keep the boundary regression executable on hosts that disallow creating
        # symlinks (for example, an unprivileged Windows checkout).
        linked_parent.mkdir()
        original_check = file_transactions._is_link_or_reparse
        monkeypatch.setattr(
            file_transactions,
            "_is_link_or_reparse",
            lambda candidate: Path(candidate) == linked_parent or original_check(candidate),
        )

    target = linked_parent / "state.json"
    with pytest.raises(ValueError, match="parent.*symlink|reparse"):
        locked_compare_and_swap(target, lambda _previous: b"should not be written")
    assert not (outside / "state.json").exists()


def test_locked_compare_and_swap_creates_missing_nested_parent(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "nested" / "state.json"

    result = locked_compare_and_swap(target, lambda _previous: b"created")

    assert result.path == target.absolute()
    assert target.read_bytes() == b"created"


@pytest.mark.parametrize("operation", ["swap", "delete"])
def test_transaction_refuses_redirected_lock_sidecar(tmp_path, monkeypatch, operation):
    target = tmp_path / "state.json"
    target.write_bytes(b"before")
    outside = tmp_path / "unrelated.txt"
    outside.write_bytes(b"unchanged")
    lock = tmp_path / ".state.json.lock"
    try:
        lock.symlink_to(outside)
    except OSError:
        original = file_transactions._is_link_or_reparse
        monkeypatch.setattr(
            file_transactions, "_is_link_or_reparse", lambda path: path == lock or original(path)
        )
    with pytest.raises(ValueError, match="symlink|reparse"):
        if operation == "swap":
            locked_compare_and_swap(target, lambda previous: b"after")
        else:
            locked_compare_and_delete(target, expected_sha256=sha256(b"before").hexdigest())
    assert outside.read_bytes() == b"unchanged"
    assert target.read_bytes() == b"before"


def test_locked_compare_and_swap_binds_expected_target_existence(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b"")

    with pytest.raises(FileConflictError, match="existence changed"):
        locked_compare_and_swap(
            target,
            lambda _previous: b"replacement",
            expected_sha256=sha256(b"").hexdigest(),
            expected_exists=False,
        )

    assert target.read_bytes() == b""


def test_failed_replacement_leaves_the_source_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "state.txt"
    target.write_bytes(b"before")
    original = file_transactions._write_and_replace

    def fail_once(*args, **kwargs):
        monkeypatch.setattr(file_transactions, "_write_and_replace", original)
        raise OSError("synthetic replacement failure")

    monkeypatch.setattr(file_transactions, "_write_and_replace", fail_once)
    with pytest.raises(OSError, match="synthetic replacement failure"):
        locked_compare_and_swap(target, lambda _previous: b"after", retain_backup=True)
    assert target.read_bytes() == b"before"
    result = locked_compare_and_swap(target, lambda _previous: b"after", retain_backup=True)
    assert result.backup is not None
    assert result.backup.read_bytes() == b"before"
    assert target.read_bytes() == b"after"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not the Windows ACL path")
def test_retained_backup_inherits_restrictive_source_mode(tmp_path: Path) -> None:
    target = tmp_path / "private-state.json"
    target.write_bytes(b"before")
    target.chmod(0o600)

    result = locked_compare_and_swap(target, lambda _previous: b"after", retain_backup=True)

    assert result.backup is not None
    assert result.backup.stat().st_mode & 0o777 == 0o600


def test_locked_compare_and_delete_requires_the_exact_existing_version(tmp_path: Path) -> None:
    target = tmp_path / "created-state.json"
    target.write_bytes(b"created")
    expected = sha256(target.read_bytes()).hexdigest()

    removed = locked_compare_and_delete(target, expected_sha256=expected)

    assert removed == b"created"
    assert target.exists() is False

    target.write_bytes(b"newer")
    with pytest.raises(FileConflictError, match="changed after"):
        locked_compare_and_delete(target, expected_sha256=expected)
    assert target.read_bytes() == b"newer"


def test_bounded_transaction_handles_creation_between_existence_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = (tmp_path / "created-during-read.bin").resolve()
    oversized = b"x" * 11
    original_exists = Path.exists
    original_open = Path.open
    original_read_bytes = Path.read_bytes
    target_exists_calls = 0

    def raced_exists(path: Path) -> bool:
        nonlocal target_exists_calls
        if path == target:
            target_exists_calls += 1
            return target_exists_calls > 1
        return original_exists(path)

    def raced_open(path: Path, mode: str = "r", *args: object, **kwargs: object):
        if path == target and mode == "rb":
            return BytesIO(oversized)
        return original_open(path, mode, *args, **kwargs)

    def raced_read_bytes(path: Path) -> bytes:
        if path == target:
            return oversized
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "exists", raced_exists)
    monkeypatch.setattr(Path, "open", raced_open)
    monkeypatch.setattr(Path, "read_bytes", raced_read_bytes)

    with pytest.raises(ValueError, match="exceeds configured size limit"):
        locked_compare_and_swap(target, lambda previous: previous, maximum_bytes=10)


@pytest.mark.skipif(os.name != "nt", reason="ReplaceFileW is the Windows metadata path")
def test_windows_replacefile_preserves_the_existing_file_security_descriptor(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state.txt"
    target.write_text("before", encoding="utf-8")
    subprocess.run(
        ["icacls", str(target), "/inheritance:d"],
        check=True,
        capture_output=True,
        text=True,
    )

    def descriptor(path: Path) -> bytes:
        owner_group_dacl = 0x00000001 | 0x00000002 | 0x00000004
        needed = ctypes.c_uint32()
        get_security = ctypes.WinDLL("advapi32", use_last_error=True).GetFileSecurityW
        get_security.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        )
        get_security.restype = ctypes.c_int
        assert not get_security(str(path), owner_group_dacl, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        assert get_security(str(path), owner_group_dacl, buffer, needed.value, ctypes.byref(needed))
        return buffer.raw

    before = descriptor(target)
    result = locked_compare_and_swap(target, lambda _previous: b"after", retain_backup=True)
    assert result.backup is not None
    assert target.read_bytes() == b"after"
    assert descriptor(target) == before


@pytest.mark.parametrize(
    "plan_name,extra_names,label",
    [
        (
            "application-plan.json",
            {"application-brief.txt", "requirement-evidence-map.json"},
            "Application",
        ),
        (
            "interview-content-plan.json",
            {"interview-brief.txt", "live-call-sheet.txt", "requirement-evidence-map.json"},
            "Interview",
        ),
    ],
)
def test_content_artifact_snapshot_reads_each_remaining_file_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan_name: str,
    extra_names: set[str],
    label: str,
) -> None:
    target = tmp_path / "content"
    files = {plan_name: b'{"schema_version":1}', **{name: name.encode() for name in extra_names}}
    content_artifacts.publish_content_version(target, files)
    opened: list[str] = []
    original_open = Path.open

    def counted_open(path: Path, mode: str = "r", *args, **kwargs):
        if path.parent == target:
            assert mode == "rb"
            opened.append(path.name)
        return original_open(path, mode, *args, **kwargs)

    def forbid_unbounded_read(_path: Path) -> bytes:
        raise AssertionError("Artifact reads must use the bounded stream")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", counted_open)
        patch.setattr(Path, "read_bytes", forbid_unbounded_read)
        snapshot = content_artifacts.read_content_artifacts(
            target, set(files), plan_name=plan_name, plan_bytes=files[plan_name], label=label
        )
    assert snapshot == files
    assert opened == sorted(extra_names)
    assert content_artifacts._inventory(snapshot) == content_artifacts._inventory(files)
    # A later operation must read fresh bytes; never retain a cache across calls.
    changed_name = sorted(extra_names)[0]
    (target / changed_name).write_bytes(b"changed")
    later = content_artifacts.read_content_artifacts(
        target, set(files), plan_name=plan_name, plan_bytes=files[plan_name], label=label
    )
    assert snapshot[changed_name] == files[changed_name]
    assert later[changed_name] == b"changed"


@pytest.mark.parametrize("size", [0, 9])
def test_content_artifact_snapshot_rejects_empty_or_oversized_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int
) -> None:
    monkeypatch.setattr(content_artifacts, "MAX_ARTIFACT_BYTES", 8)
    (tmp_path / "brief.txt").write_bytes(b"x" * size)
    with pytest.raises(ValueError, match="Application artifact size is invalid"):
        content_artifacts.read_content_artifacts(
            tmp_path,
            {"plan.json", "brief.txt"},
            plan_name="plan.json",
            plan_bytes=b"{}",
            label="Application",
        )


def test_content_artifact_snapshot_accepts_the_exact_byte_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(content_artifacts, "MAX_ARTIFACT_BYTES", 8)
    (tmp_path / "brief.txt").write_bytes(b"x" * 8)
    assert content_artifacts.read_content_artifacts(
        tmp_path,
        {"plan.json", "brief.txt"},
        plan_name="plan.json",
        plan_bytes=b"{}",
        label="Application",
    ) == {"plan.json": b"{}", "brief.txt": b"x" * 8}


@pytest.mark.parametrize("plan_bytes", [b"", b"x" * 9, "not bytes"])
def test_content_artifact_snapshot_rejects_invalid_preloaded_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan_bytes
) -> None:
    monkeypatch.setattr(content_artifacts, "MAX_ARTIFACT_BYTES", 8)
    with pytest.raises(ValueError, match="artifact size is invalid"):
        content_artifacts.read_content_artifacts(
            tmp_path,
            {"plan.json", "brief.txt"},
            plan_name="plan.json",
            plan_bytes=plan_bytes,
            label="Application",
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "name", ["", ".", "..", "../outside", "nested/file", "a\\b", "C:x", "a\x00b"]
)
def test_content_artifact_snapshot_rejects_non_basename_inputs(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="fixed basenames"):
        content_artifacts.read_content_artifacts(
            tmp_path,
            {"plan.json", name},
            plan_name="plan.json",
            plan_bytes=b"{}",
            label="Application",
        )
    assert not list(tmp_path.iterdir())


def test_content_artifact_snapshot_requires_the_plan_in_the_inventory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="declared plan"):
        content_artifacts.read_content_artifacts(
            tmp_path,
            {"brief.txt"},
            plan_name="plan.json",
            plan_bytes=b"{}",
            label="Application",
        )


def test_content_artifact_snapshot_bounds_a_stream_that_grows_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(content_artifacts, "MAX_ARTIFACT_BYTES", 8)
    (tmp_path / "brief.txt").write_bytes(b"short")
    reads: list[int] = []

    class GrowingStream(BytesIO):
        def read(self, size: int = -1) -> bytes:
            reads.append(size)
            assert size == 9
            return super().read(size)

    original_open = Path.open

    def growing_open(path: Path, mode: str = "r", *args, **kwargs):
        if path == tmp_path / "brief.txt" and mode == "rb":
            return GrowingStream(b"x" * 20)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", growing_open)
    with pytest.raises(ValueError, match="artifact size is invalid"):
        content_artifacts.read_content_artifacts(
            tmp_path,
            {"plan.json", "brief.txt"},
            plan_name="plan.json",
            plan_bytes=b"{}",
            label="Application",
        )
    assert reads == [9]


@pytest.mark.parametrize("parent_link", [False, True])
def test_content_artifact_snapshot_refuses_path_redirection(
    tmp_path: Path, parent_link: bool
) -> None:
    target = tmp_path / "content"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "brief.txt").write_bytes(b"unchanged")
    try:
        if parent_link:
            target.symlink_to(outside, target_is_directory=True)
        else:
            target.mkdir()
            (target / "brief.txt").symlink_to(outside / "brief.txt")
    except OSError as exc:
        pytest.skip(f"Symlinks are unavailable on this platform: {exc}")
    with pytest.raises(ValueError, match="symlink|reparse"):
        content_artifacts.read_content_artifacts(
            target,
            {"plan.json", "brief.txt"},
            plan_name="plan.json",
            plan_bytes=b"{}",
            label="Application",
        )
    assert (outside / "brief.txt").read_bytes() == b"unchanged"


def test_content_artifact_snapshot_does_not_create_missing_files(tmp_path: Path) -> None:
    target = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        content_artifacts.read_content_artifacts(
            target,
            {"plan.json", "brief.txt"},
            plan_name="plan.json",
            plan_bytes=b"{}",
            label="Application",
        )
    assert not target.exists()


def test_content_artifact_snapshot_rejects_a_directory_as_a_file(tmp_path: Path) -> None:
    (tmp_path / "brief.txt").mkdir()
    with pytest.raises(ValueError, match="regular file"):
        content_artifacts.read_content_artifacts(
            tmp_path,
            {"plan.json", "brief.txt"},
            plan_name="plan.json",
            plan_bytes=b"{}",
            label="Application",
        )

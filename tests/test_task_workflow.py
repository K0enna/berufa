from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_tasks import _ready_task_instance

from career_os import task_workflow


def _run(root: Path, *arguments: str) -> int:
    return task_workflow.main(["--instance-root", str(root), *arguments])


def test_task_cli_confirmation_replay_revision_status_and_root_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first_base = tmp_path / "private-one"
    first_base.mkdir()
    root, opportunity_id = _ready_task_instance(first_base)
    second_base = tmp_path / "private-two"
    second_base.mkdir()
    other_root, _ = _ready_task_instance(second_base)
    third_cwd = tmp_path / "third-working-directory"
    third_cwd.mkdir()
    code_root = Path(task_workflow.__file__).resolve().parents[2]
    assert not root.resolve().is_relative_to(code_root)
    assert not code_root.is_relative_to(root.resolve())
    assert not third_cwd.resolve().is_relative_to(code_root)
    assert not code_root.is_relative_to(third_cwd.resolve())
    monkeypatch.chdir(third_cwd)

    task_ledger = root / "data/runtime/planned-tasks.json"
    with pytest.raises(SystemExit) as unconfirmed:
        _run(
            root,
            "create",
            "--opportunity-id",
            opportunity_id,
            "--description",
            "Review the saved role",
            "--request-id",
            "cli-create-one",
        )
    assert unconfirmed.value.code == 2
    capsys.readouterr()
    assert not task_ledger.exists()

    create_args = (
        "create",
        "--opportunity-id",
        opportunity_id,
        "--description",
        "Review the saved role",
        "--due",
        "2026-10-06",
        "--due-kind",
        "date",
        "--timezone",
        "Europe/Berlin",
        "--request-id",
        "cli-create-one",
        "--confirm-local-plan",
    )
    assert _run(root, *create_args) == 0
    first_create = json.loads(capsys.readouterr().out)
    assert first_create["changed"] is True
    task_id = first_create["record"]["task_id"]

    assert _run(root, *create_args) == 0
    retried_create = json.loads(capsys.readouterr().out)
    assert retried_create["changed"] is False
    assert retried_create["record"] == first_create["record"]

    update_args = (
        "update",
        "--task-id",
        task_id,
        "--expected-revision",
        "1",
        "--description",
        "Review the saved role with the hiring team",
        "--due",
        "2026-10-25T02:30:00+01:00",
        "--due-kind",
        "timed",
        "--timezone",
        "Europe/Berlin",
        "--request-id",
        "cli-update-one",
        "--confirm-local-plan",
    )
    assert _run(root, *update_args) == 0
    updated = json.loads(capsys.readouterr().out)["record"]
    assert updated["revision"] == 2
    assert updated["due"] == {
        "kind": "timed",
        "value": "2026-10-25T02:30:00+01:00",
        "timezone": "Europe/Berlin",
    }

    assert (
        _run(
            root,
            "status",
            "--task-id",
            task_id,
            "--expected-revision",
            "2",
            "--status",
            "completed",
            "--request-id",
            "cli-complete-one",
            "--confirm-local-plan",
        )
        == 0
    )
    completed = json.loads(capsys.readouterr().out)["record"]
    assert (completed["revision"], completed["status"]) == (3, "completed")

    second_create_args = (
        "create",
        "--opportunity-id",
        opportunity_id,
        "--description",
        "Decide whether to continue",
        "--request-id",
        "cli-create-two",
        "--confirm-local-plan",
    )
    assert _run(root, *second_create_args) == 0
    second_task = json.loads(capsys.readouterr().out)["record"]
    assert (
        _run(
            root,
            "status",
            "--task-id",
            second_task["task_id"],
            "--expected-revision",
            "1",
            "--status",
            "cancelled",
            "--request-id",
            "cli-cancel-two",
            "--confirm-local-plan",
        )
        == 0
    )
    cancelled = json.loads(capsys.readouterr().out)["record"]
    assert (cancelled["revision"], cancelled["status"]) == (2, "cancelled")

    assert _run(root, "list", "--opportunity-id", opportunity_id) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {record["status"] for record in listed["current"]} == {"completed", "cancelled"}
    assert len(listed["history"]) == 5
    assert [record["revision"] for record in listed["history"] if record["task_id"] == task_id] == [
        1,
        2,
        3,
    ]

    assert _run(root, "list", "--opportunity-id", opportunity_id) == 0
    restarted_list = json.loads(capsys.readouterr().out)
    assert restarted_list == listed

    assert _run(other_root, "list", "--opportunity-id", opportunity_id) == 0
    assert json.loads(capsys.readouterr().out) == {"current": [], "history": []}
    assert not (other_root / "data/runtime/planned-tasks.json").exists()
    assert not (other_root / "data/runtime/.planned-tasks.json.lock").exists()
    assert not (root / "data/runtime/outcome-learning-v2.jsonl").exists()
    assert tuple(third_cwd.iterdir()) == ()


def test_task_cli_stale_revision_refusal_keeps_saved_ledger_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = tmp_path / "private"
    base.mkdir()
    root, opportunity_id = _ready_task_instance(base)
    third_cwd = tmp_path / "third-working-directory"
    third_cwd.mkdir()
    monkeypatch.chdir(third_cwd)
    assert (
        _run(
            root,
            "create",
            "--opportunity-id",
            opportunity_id,
            "--description",
            "Review role changes",
            "--request-id",
            "stale-create",
            "--confirm-local-plan",
        )
        == 0
    )
    task_id = json.loads(capsys.readouterr().out)["record"]["task_id"]
    assert (
        _run(
            root,
            "update",
            "--task-id",
            task_id,
            "--expected-revision",
            "1",
            "--description",
            "Review updated role changes",
            "--request-id",
            "stale-current-update",
            "--confirm-local-plan",
        )
        == 0
    )
    capsys.readouterr()
    ledger = root / "data/runtime/planned-tasks.json"
    before = ledger.read_bytes()

    result = _run(
        root,
        "update",
        "--task-id",
        task_id,
        "--expected-revision",
        "1",
        "--description",
        "Stale task update",
        "--request-id",
        "stale-revision-update",
        "--confirm-local-plan",
    )

    assert result == 1
    assert "Local task operation refused" in capsys.readouterr().err
    assert ledger.read_bytes() == before

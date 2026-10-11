from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from career_os import file_transactions
from career_os.infrastructure import outcomes as outcome_storage
from career_os.infrastructure.outcomes import LocalEventRepository, append_event, load_events
from career_os.outcome_workflow import list_main, main, report_main
from career_os.public_api import (
    CareerEventService,
    DebriefInput,
    EventRequest,
    OutcomeInput,
    OutcomeOperationError,
    OutcomeService,
    SubmissionInput,
    require_outcome_result,
)


def request() -> EventRequest:
    return EventRequest(
        "submission_confirmed",
        "example-role-001",
        "2026-07-01T10:00:00+00:00",
        SubmissionInput(
            "example:role-001",
            "Fictional Company",
            "Example Lead",
            "user_supplied",
            "employer_ats",
            "candidate_confirmed_sent",
            "a" * 64,
            72,
            "example-v1",
        ),
    )


def confirm(service: CareerEventService, value: EventRequest):
    preview = require_outcome_result(service.propose(value))
    return require_outcome_result(
        service.confirm(
            value,
            confirmation_fingerprint=preview.confirmation_fingerprint,
            actor="local_user",
        )
    )


def ack(service: OutcomeService, report, **changes):
    options = {
        "report_basis_hash": report.report.basis_hash,
        "ledger_fingerprint": report.ledger_fingerprint,
        "as_of": report.report.as_of,
        "maturity_business_days": report.report.maturity_business_days,
        "review_interval": report.report.review_interval,
        "actor": "local_user",
    }
    return service.acknowledge(**(options | changes))


def test_preview_exact_confirmation_retry_and_private_authority(tmp_path: Path) -> None:
    repository = LocalEventRepository(tmp_path / "instance")
    service = CareerEventService(repository)
    proposal = require_outcome_result(service.propose(request()))
    assert not repository.paths.root.exists()
    assert proposal.to_dict()["external_event_confirmed"] is False
    assert "confirmed_by" not in proposal.to_dict()["request"]["details"]
    assert "effective_scoring_fingerprint" not in proposal.to_dict()["request"]["details"]
    for actor, token in [("agent", proposal.confirmation_fingerprint), ("local_user", "0" * 64)]:
        result = service.confirm(request(), actor=actor, confirmation_fingerprint=token)
        assert result.code == "human_required"
        assert not repository.paths.root.exists()
    changed = replace(request(), opportunity_id="example-role-002")
    assert (
        service.confirm(
            changed, actor="local_user", confirmation_fingerprint=proposal.confirmation_fingerprint
        ).code
        == "human_required"
    )
    first = confirm(service, request())
    before = repository.path.read_bytes()
    retry = confirm(service, request())
    assert first.appended and not retry.appended
    assert first.event == retry.event
    assert repository.path.read_bytes() == before
    assert not append_event(first.event, repository.path).appended
    assert load_events(repository.path) == list(require_outcome_result(service.list()))
    assert require_outcome_result(service.list(opportunity_id="absent-role")) == ()
    duplicate = replace(request(), occurred_at="2026-07-02T10:00:00+00:00")
    with pytest.raises(ValueError):
        confirm(service, duplicate)
    assert repository.path.read_bytes() == before


def test_qualitative_source_guard_and_committed_retry(tmp_path):
    basis = {"kind": "qualitative", "source_hash": "a" * 64, "evaluation_fingerprint": "c" * 64}
    repository = LocalEventRepository(tmp_path)
    current = dict(basis)
    service = CareerEventService(repository, assessment_resolver=lambda identity: current)
    value = replace(
        request(),
        details=replace(
            request().details, fit_score=None, scorecard_version=None, assessment_basis=basis
        ),
    )
    preview = require_outcome_result(service.propose(value))
    assert preview.request.to_dict()["schema_version"] == 2
    current["evaluation_fingerprint"] = "d" * 64
    assert (
        service.confirm(
            value, actor="local_user", confirmation_fingerprint=preview.confirmation_fingerprint
        ).code
        == "invalid_event"
    )
    assert not repository.path.exists()
    current.update(basis)
    first = confirm(service, value)
    before = repository.path.read_bytes()
    current["evaluation_fingerprint"] = "d" * 64
    retry = confirm(service, value)
    assert not retry.appended and retry.event == first.event
    assert repository.path.read_bytes() == before


def test_saved_role_cli_selects_real_basis_without_json_or_numeric_score(tmp_path, capsys):
    from career_os.infrastructure.instances import create_instance_service
    from tests.test_application_workflow import prepared_context

    root = tmp_path / "Fictional saved role"
    create_instance_service(root).initialize(actor="local_user")
    vault, _, _ = prepared_context.__wrapped__(root)
    argv = [
        "--instance-root",
        str(root),
        "--saved-opportunity",
        "example_role",
        "--occurred-at",
        "2026-07-01T10:00:00+00:00",
        "--discovery-source",
        "user_supplied",
        "--application-channel",
        "employer_ats",
        "--confirmation-method",
        "candidate_confirmed_sent",
    ]
    assert main(argv) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["recorded"] is None
    proposal = output["proposal"]
    assert proposal["request"]["details"]["fit_score"] is None
    assert proposal["request"]["details"]["assessment_basis"]["kind"] == "qualitative"
    confirmation = ["--confirm-event", proposal["confirmation_fingerprint"]]
    assert main(argv + confirmation) == 0
    first = json.loads(capsys.readouterr().out)["recorded"]
    ledger = root / "data/runtime/outcome-learning-v2.jsonl"
    before = ledger.read_bytes()
    assert main(argv + confirmation) == 0
    retry = json.loads(capsys.readouterr().out)["recorded"]
    assert retry["event"] == first["event"] and not retry["appended"]
    assert ledger.read_bytes() == before
    evidence = vault.latest("synthetic-responsibility")
    vault.edit(
        evidence.evidence_id,
        expected_revision=evidence.revision,
        statement=evidence.statement + " (corrected fictional evidence)",
    )
    assert main(argv + confirmation) == 0
    retry = json.loads(capsys.readouterr().out)["recorded"]
    assert retry["event"] == first["event"] and not retry["appended"]
    assert ledger.read_bytes() == before
    # An old approval cannot recover a different assertion after correction.
    changed = list(argv)
    changed[changed.index("candidate_confirmed_sent")] = "employer_confirmation_email"
    assert main(changed + confirmation) == 1
    capsys.readouterr()
    assert ledger.read_bytes() == before


@pytest.mark.parametrize(
    "score,version",
    [
        (True, "v1"),
        (float("nan"), "v1"),
        (float("inf"), "v1"),
        ("72", "v1"),
        ([], "v1"),
        (None, "v1"),
        (72, None),
        (72, ""),
    ],
)
def test_unknown_scoring_does_not_relax_supplied_score_validation(tmp_path, score, version):
    service = CareerEventService(LocalEventRepository(tmp_path))
    value = replace(
        request(), details=replace(request().details, fit_score=score, scorecard_version=version)
    )
    assert service.propose(value).code == "invalid_event"
    assert not tmp_path.joinpath("data/runtime/outcome-learning-v2.jsonl").exists()


def test_submission_scoring_fingerprint_survives_public_preview_confirm_and_report(
    tmp_path: Path,
) -> None:
    repository = LocalEventRepository(tmp_path / "instance")
    events = CareerEventService(repository)
    supplied = replace(
        request(),
        details=replace(request().details, effective_scoring_fingerprint="c" * 64),
    )

    proposal = require_outcome_result(events.propose(supplied))
    assert proposal.to_dict()["request"]["details"]["effective_scoring_fingerprint"] == "c" * 64
    changed = replace(
        supplied,
        details=replace(supplied.details, effective_scoring_fingerprint="d" * 64),
    )
    changed_proposal = require_outcome_result(events.propose(changed))
    assert changed_proposal.event_id != proposal.event_id
    assert changed_proposal.confirmation_fingerprint != proposal.confirmation_fingerprint
    assert (
        events.confirm(
            changed,
            confirmation_fingerprint=proposal.confirmation_fingerprint,
            actor="local_user",
        ).code
        == "human_required"
    )
    assert not repository.paths.root.exists()
    receipt = require_outcome_result(
        events.confirm(
            supplied,
            confirmation_fingerprint=proposal.confirmation_fingerprint,
            actor="local_user",
        )
    )
    assert receipt.event.payload["effective_scoring_fingerprint"] == "c" * 64
    assert load_events(repository.path) == [receipt.event]
    report = require_outcome_result(OutcomeService(events).report())
    assert set(report.report.segments["effective_scoring_fingerprint"]) == {"c" * 64}


def test_outcomes_debriefs_report_and_exact_basis_ack(tmp_path: Path) -> None:
    repository = LocalEventRepository(tmp_path)
    events = CareerEventService(repository)
    service = OutcomeService(events)
    empty = require_outcome_result(service.report())
    assert empty.report.confirmed_submissions == 0
    assert empty.report.application_to_interview_rate is None
    assert not repository.path.exists()
    confirm(events, request())
    report = require_outcome_result(service.report(review_interval=1))
    assert report.report.confirmed_submissions == report.report.mature_submissions == 1
    assert report.report.review_due and report.report.data_quality_warnings
    before = repository.path.read_bytes()
    assert ack(service, report, actor="agent").code == "human_required"
    assert ack(service, report, report_basis_hash="0" * 64).code == "invalid_event"
    assert ack(service, report, review_interval=2).code == "invalid_event"
    assert repository.path.read_bytes() == before
    confirmed_ack = require_outcome_result(ack(service, report, review_scope="submission_hygiene"))
    assert confirmed_ack.event.event_type == "review_acknowledged"
    assert confirmed_ack.relative_path == "data/runtime/outcome-learning-v2.jsonl"
    outcome = EventRequest(
        "outcome_recorded",
        request().opportunity_id,
        "2026-07-02T10:00:00Z",
        OutcomeInput(
            "example:role-001",
            "Fictional Company",
            "Example Lead",
            "interview",
            "scheduled",
            "no_reason",
            "none",
        ),
    )
    confirm(events, outcome)
    assert ack(service, report).code == "invalid_event"
    debrief = EventRequest(
        "interview_debrief_recorded",
        request().opportunity_id,
        "2026-07-03T10:00:00Z",
        DebriefInput(
            "example:role-001",
            "Fictional Company",
            "Example Lead",
            "hiring_manager",
            "mixed",
            "unknown",
            "low",
            "Candidate recollection only",
        ),
    )
    confirm(events, debrief)
    confirm(
        events,
        replace(outcome, details=replace(outcome.details, stage="offer", outcome="accepted")),
    )
    final = require_outcome_result(service.report())
    assert (
        final.report.mature_hires
        == final.report.mature_offers
        == final.report.mature_ever_interviewed
        == 1
    )
    assert final.report.interview_debriefs == 1
    assert "not causal evidence" in final.to_dict()["limitations"][0]
    historical = require_outcome_result(service.report(as_of="2026-07-10"))
    assert historical.report.confirmed_submissions == 0


def test_ack_rechecks_evidence_inside_transaction(tmp_path: Path) -> None:
    class RacingRepository(LocalEventRepository):
        def append(self, build):
            confirm(CareerEventService(LocalEventRepository(tmp_path)), request())
            return super().append(build)

    service = OutcomeService(CareerEventService(RacingRepository(tmp_path)))
    report = require_outcome_result(service.report())
    assert ack(service, report).code == "invalid_event"
    assert len(load_events(tmp_path / "data/runtime/outcome-learning-v2.jsonl")) == 1


@pytest.mark.parametrize(
    "corrupt", [b"{", b"\xff", b'{"schema_version":99}\n'], ids=["partial", "encoding", "newer"]
)
def test_corrupt_state_is_not_replaced(tmp_path: Path, corrupt: bytes) -> None:
    repository = LocalEventRepository(tmp_path)
    repository.path.parent.mkdir(parents=True)
    repository.path.write_bytes(corrupt)
    events = CareerEventService(repository)
    assert events.list().code == "invalid_event"
    assert OutcomeService(events).report().code == "invalid_event"
    with pytest.raises(ValueError):
        confirm(events, request())
    assert repository.path.read_bytes() == corrupt


def test_failed_atomic_replace_preserves_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = LocalEventRepository(tmp_path)
    events = CareerEventService(repository)
    confirm(events, request())
    before = repository.path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("private local diagnostic")

    monkeypatch.setattr(file_transactions, "_write_and_replace", fail)
    with pytest.raises(OSError, match="Local event storage is unavailable"):
        confirm(events, replace(request(), opportunity_id="another-example"))
    assert repository.path.read_bytes() == before


@pytest.mark.parametrize(
    "kind,value",
    [
        ("company", "\ud800"),
        ("fit_score", float("inf")),
        ("fit_score", 10**400),
        ("confirmed_by", "local_user"),
        ("job_content_hash", ""),
        ("effective_scoring_fingerprint", None),
        ("effective_scoring_fingerprint", "C" * 64),
    ],
    ids=[
        "unicode",
        "infinity",
        "huge-score",
        "authority",
        "missing-hash",
        "null-scoring-fingerprint",
        "uppercase-scoring-fingerprint",
    ],
)
def test_invalid_input_has_no_effect(tmp_path: Path, kind: str, value: object) -> None:
    payload = request().to_dict()
    payload["details"][kind] = value
    with pytest.raises(ValueError):
        parsed = EventRequest.from_dict(payload)
        require_outcome_result(CareerEventService(LocalEventRepository(tmp_path)).propose(parsed))
    assert list(tmp_path.iterdir()) == []


def test_feedback_and_submission_shortcut_are_not_public_outcomes(tmp_path: Path) -> None:
    for stage, outcome in [
        ("review", "reviewed"),
        ("application", "submitted"),
        ("application", "accepted"),
    ]:
        event = EventRequest(
            "outcome_recorded",
            "example-role",
            request().occurred_at,
            OutcomeInput("example:role", "Example", "Lead", stage, outcome, "no_reason", "none"),
        )
        assert (
            CareerEventService(LocalEventRepository(tmp_path)).propose(event).code
            == "invalid_event"
        )
    assert list(tmp_path.iterdir()) == []


def test_cli_record_list_report_ack_and_private_errors(tmp_path: Path, capsys) -> None:
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request().to_dict()), encoding="utf-8")
    args = ["--instance-root", str(tmp_path), "--request", str(source)]
    assert main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["recorded"] is None
    assert main([*args, "--confirm-event", preview["proposal"]["confirmation_fingerprint"]]) == 0
    assert json.loads(capsys.readouterr().out)["recorded"]["appended"]
    assert list_main(["--instance-root", str(tmp_path)]) == 0
    assert len(json.loads(capsys.readouterr().out)["events"]) == 1
    assert report_main(["--instance-root", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert (
        report_main(
            [
                "--instance-root",
                str(tmp_path),
                "--as-of",
                report["report"]["as_of"],
                "--acknowledge-report",
                report["report"]["basis_hash"],
                "--ledger-fingerprint",
                report["ledger_fingerprint"],
            ]
        )
        == 0
    )
    assert (
        json.loads(capsys.readouterr().out)["acknowledged"]["event"]["event_type"]
        == "review_acknowledged"
    )
    source.write_text('{"private-person": "secret"}', encoding="utf-8")
    assert main(args) == 1
    failure = capsys.readouterr()
    assert not failure.out and "secret" not in failure.err and "private-person" not in failure.err


def test_programming_faults_are_not_hidden() -> None:
    class BrokenRepository:
        def list(self):
            raise RuntimeError("bug")

    with pytest.raises(RuntimeError, match="bug"):
        CareerEventService(BrokenRepository()).list()
    assert isinstance(CareerEventService().list(), OutcomeOperationError)


def test_append_bounds_and_valid_missing_final_newline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = LocalEventRepository(tmp_path)
    events = CareerEventService(repository)
    first = confirm(events, request())
    repository.path.write_bytes(json.dumps(first.event.to_dict()).encode("utf-8"))
    second_request = replace(request(), opportunity_id="example-second")
    second = confirm(events, second_request)
    assert len(load_events(repository.path)) == 2
    assert second.appended
    before = repository.path.read_bytes()
    monkeypatch.setattr(outcome_storage, "MAX_LEDGER_EVENTS", 2)
    with pytest.raises(ValueError):
        confirm(events, replace(request(), opportunity_id="example-third"))
    monkeypatch.setattr(outcome_storage, "MAX_LEDGER_EVENTS", 20_000)
    monkeypatch.setattr(outcome_storage, "MAX_LEDGER_BYTES", len(before))
    with pytest.raises(ValueError):
        confirm(events, replace(request(), opportunity_id="example-third"))
    assert repository.path.read_bytes() == before
    monkeypatch.setattr(outcome_storage, "MAX_LEDGER_BYTES", len(before) - 1)
    assert events.list().code == "invalid_event"

"""Installed local-user event confirmation and descriptive outcome commands."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields, replace
from pathlib import Path

from career_os.infrastructure.outcomes import (
    create_event_service,
    create_outcome_service,
    read_private_json,
)
from career_os.public_api import (
    APPLICATION_CHANNELS,
    CONFIRMATION_METHODS,
    DISCOVERY_SOURCES,
    MAX_EVENT_REQUEST_BYTES,
    EventRequest,
    OpportunityService,
    SubmissionInput,
    require_opportunity_result,
    require_outcome_result,
)


def _parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--instance-root", type=Path, required=True)
    return parser


def _failure() -> int:
    print(
        "Rolevane outcome command failed: check the request, exact confirmation and local state.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = _parser("Preview or record an explicitly confirmed hiring event locally.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--request", type=Path)
    source.add_argument("--saved-opportunity", metavar="EXACT_ID")
    parser.add_argument("--occurred-at")
    parser.add_argument("--discovery-source", choices=sorted(DISCOVERY_SOURCES))
    parser.add_argument("--application-channel", choices=sorted(APPLICATION_CHANNELS))
    parser.add_argument("--confirmation-method", choices=sorted(CONFIRMATION_METHODS))
    parser.add_argument("--confirm-event", metavar="PREVIEW_SHA256")
    args = parser.parse_args(argv)
    try:
        service = create_event_service(args.instance_root)
        if args.saved_opportunity:
            if not all(
                (
                    args.occurred_at,
                    args.discovery_source,
                    args.application_channel,
                    args.confirmation_method,
                )
            ):
                parser.error(
                    "Saved submission needs occurrence time, discovery source, channel and confirmation method"
                )
            request = None
            if args.confirm_event is not None:
                # Recover only the exact committed assertion. A correction must not
                # force a real historical event to acquire a new assessment basis.
                for event in require_outcome_result(
                    service.list(opportunity_id=args.saved_opportunity)
                ):
                    if event.event_type != "submission_confirmed" or not event.payload.get(
                        "assessment_basis"
                    ):
                        continue
                    retained = SubmissionInput(
                        **{
                            field.name: event.payload[field.name]
                            for field in fields(SubmissionInput)
                            if field.name in event.payload
                        }
                    )
                    retry = EventRequest(
                        "submission_confirmed",
                        args.saved_opportunity,
                        args.occurred_at,
                        replace(
                            retained,
                            discovery_source=args.discovery_source,
                            application_channel=args.application_channel,
                            confirmation_method=args.confirmation_method,
                        ),
                    )
                    preview = require_outcome_result(service.propose(retry))
                    if preview.confirmation_fingerprint == args.confirm_event:
                        request = preview.request
                        break
            if request is None:
                request = _saved_request(args)
        else:
            payload, _ = read_private_json(
                args.instance_root,
                args.request,
                label="Event request",
                maximum_bytes=MAX_EVENT_REQUEST_BYTES,
            )
            request = EventRequest.from_dict(payload)
        proposal = require_outcome_result(service.propose(request))
        receipt = None
        if args.confirm_event is not None:
            receipt = require_outcome_result(
                service.confirm(
                    request,
                    confirmation_fingerprint=args.confirm_event,
                    actor="local_user",
                )
            ).to_dict()
        output = {"proposal": proposal.to_dict(), "recorded": receipt}
    except (ValueError, OSError):
        return _failure()
    print(json.dumps(output, sort_keys=True))
    return 0


def _saved_request(args: argparse.Namespace) -> EventRequest:
    from career_os.infrastructure.opportunities import LocalOpportunityRepository

    state = require_opportunity_result(
        OpportunityService(
            LocalOpportunityRepository(args.instance_root, read_only_resolution=True)
        ).resolve_supplied(args.saved_opportunity)
    )
    dossier = state.dossier
    return EventRequest(
        "submission_confirmed",
        dossier.opportunity_id,
        args.occurred_at,
        SubmissionInput(
            dossier.opportunity_id,
            dossier.to_dict()["company"],
            dossier.to_dict()["title"],
            args.discovery_source,
            args.application_channel,
            args.confirmation_method,
            dossier.source_hash,
            assessment_basis={
                "kind": "qualitative",
                "source_hash": dossier.source_hash,
                "evaluation_fingerprint": dossier.evaluation_fingerprint,
            },
        ),
    )


def list_main(argv: list[str] | None = None) -> int:
    parser = _parser("List private confirmed event records without changing them.")
    parser.add_argument("--opportunity-id")
    args = parser.parse_args(argv)
    try:
        events = require_outcome_result(
            create_event_service(args.instance_root).list(
                opportunity_id=args.opportunity_id,
            )
        )
    except (ValueError, OSError):
        return _failure()
    print(json.dumps({"events": [event.to_dict() for event in events]}, sort_keys=True))
    return 0


def report_main(argv: list[str] | None = None) -> int:
    parser = _parser("Report descriptive outcomes or acknowledge the exact evidence basis.")
    parser.add_argument("--as-of")
    parser.add_argument("--maturity-business-days", type=int, default=10)
    parser.add_argument("--review-interval", type=int, default=10)
    parser.add_argument("--acknowledge-report", metavar="REPORT_BASIS_SHA256")
    parser.add_argument("--ledger-fingerprint", metavar="REPORT_LEDGER_SHA256")
    parser.add_argument(
        "--review-scope", choices=("both", "submission_hygiene", "outcome_learning"), default="both"
    )
    args = parser.parse_args(argv)
    if args.acknowledge_report is not None and (
        args.as_of is None or args.ledger_fingerprint is None
    ):
        parser.error("Acknowledgment requires the exact report --as-of and --ledger-fingerprint")
    if args.acknowledge_report is None and args.ledger_fingerprint is not None:
        parser.error("--ledger-fingerprint is only used with --acknowledge-report")
    try:
        service = create_outcome_service(args.instance_root)
        if args.acknowledge_report is None:
            output = require_outcome_result(
                service.report(
                    as_of=args.as_of,
                    maturity_business_days=args.maturity_business_days,
                    review_interval=args.review_interval,
                )
            ).to_dict()
        else:
            output = {
                "acknowledged": require_outcome_result(
                    service.acknowledge(
                        report_basis_hash=args.acknowledge_report,
                        ledger_fingerprint=args.ledger_fingerprint,
                        as_of=args.as_of,
                        maturity_business_days=args.maturity_business_days,
                        review_interval=args.review_interval,
                        review_scope=args.review_scope,
                        actor="local_user",
                    )
                ).to_dict()
            }
    except (ValueError, OSError):
        return _failure()
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

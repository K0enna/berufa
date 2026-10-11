"""Bounded fictional companion contracts; no local-human authority on this surface."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, NoReturn, Protocol

MAX_COMPANION_REQUEST_BYTES = 256 * 1024
MAX_COMPANION_RESPONSE_BYTES = 32 * 1024
COMPANION_META_KEY = "companion_m1_state"
MAX_COMPANION_STATE_BYTES = 2 * 1024 * 1024
MAX_COMPANION_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_COMPANION_ARCHIVE_RECORDS = 4096
KINDS = frozenset({"evidence", "source_span", "opportunity", "interview", "offer", "pending"})
PURPOSES = frozenset(
    {
        "resume_work",
        "assess_role",
        "evidence_review",
        "prepare_interview",
        "compare_offers",
        "receipt_lookup",
    }
)
OPERATIONS = frozenset({"assessment", "decision", "evidence", "interview", "offer"})


class CompanionError(ValueError):
    """Expected failure with a content-free code; never wrap a raw payload."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def companion_json(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise CompanionError("invalid_input") from exc


def companion_digest(value: object) -> str:
    return hashlib.sha256(companion_json(value)).hexdigest()


def bounded_companion_json(raw: bytes, *, maximum: int = MAX_COMPANION_REQUEST_BYTES) -> object:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= maximum:
        raise CompanionError("input_too_large")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise CompanionError("invalid_input")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> NoReturn:
        raise CompanionError("invalid_input")

    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant
        )
        pending = [(payload, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 24:
                raise CompanionError("input_too_large")
            if isinstance(value, str):
                value.encode("utf-8")
                if "\x00" in value:
                    raise CompanionError("invalid_input")
            elif isinstance(value, dict):
                pending.extend((item, depth + 1) for pair in value.items() for item in pair)
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
        return payload
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CompanionError("invalid_input") from exc


def exact_fields(payload: object, fields: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != fields:
        raise CompanionError("invalid_input")
    return payload


def validate_companion_state(raw: str, instance_hash: str) -> dict[str, Any]:
    """Lifecycle readers validate the namespace without constructing a bridge."""
    encoded = raw.encode("utf-8")
    state = bounded_companion_json(
        encoded, maximum=MAX_COMPANION_STATE_BYTES + MAX_COMPANION_ARCHIVE_BYTES
    )
    if not isinstance(state, dict) or type(state.get("version")) is not int:
        raise CompanionError("integrity_error")
    version = state["version"]
    shape = {"version", "instance_hash", "generation", "enabled", "grants", "pending"}
    if version == 2:
        shape |= {"archive", "upgrade"}
    exact_fields(state, shape)
    if version == 1 and len(encoded) > MAX_COMPANION_STATE_BYTES:
        raise CompanionError("input_too_large")
    if version == 2:
        archive = state["archive"]
        if not isinstance(archive, dict) or len(archive) > MAX_COMPANION_ARCHIVE_RECORDS:
            raise CompanionError("integrity_error")
        if (
            len(companion_json(archive)) > MAX_COMPANION_ARCHIVE_BYTES
            or len(companion_json({k: v for k, v in state.items() if k != "archive"}))
            > MAX_COMPANION_STATE_BYTES
        ):
            raise CompanionError("input_too_large")
        _lifecycle_stamp(state["upgrade"])
    if (
        type(state["version"]) is not int
        or version not in {1, 2}
        or state["instance_hash"] != instance_hash
        or type(state["enabled"]) is not bool
        or not isinstance(state["generation"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", state["generation"])
    ):
        raise CompanionError("integrity_error")
    for field in ("grants", "pending"):
        if not isinstance(state[field], dict) or len(state[field]) > 256:
            raise CompanionError("integrity_error")
    for grant_id, grant in state["grants"].items():
        if not re.fullmatch(r"[0-9a-f]{64}", grant_id):
            raise CompanionError("integrity_error")
        exact_fields(
            grant,
            {
                "ref",
                "fields",
                "purpose",
                "recipient",
                "generation",
                "created_at",
                "expires_at",
                "revoked",
                "historical",
            },
        )
        CompanionRef.from_dict(grant["ref"])
        if (
            grant["recipient"] != "chatgpt"
            or not isinstance(grant["purpose"], str)
            or grant["purpose"] not in PURPOSES
            or grant["generation"] != state["generation"]
            or type(grant["revoked"]) is not bool
            or type(grant["historical"]) is not bool
            or not isinstance(grant["fields"], list)
            or not 1 <= len(grant["fields"]) <= 24
            or any(not isinstance(item, str) for item in grant["fields"])
            or len(set(grant["fields"])) != len(grant["fields"])
        ):
            raise CompanionError("integrity_error")
        _record_lifetime(grant, 30 * 86400)
    archive = state.get("archive", {})
    if set(archive) & set(state["pending"]):
        raise CompanionError("integrity_error")
    seen_keys = set()
    for pending_id, record in (*state["pending"].items(), *archive.items()):
        if not re.fullmatch(r"pending-[0-9a-f]{32}", pending_id):
            raise CompanionError("integrity_error")
        record_shape = {
            "operation",
            "key",
            "digest",
            "request",
            "basis",
            "created_at",
            "expires_at",
            "state",
            "receipt",
            "confirmation",
            "restored",
        }
        archived = pending_id in archive
        if version == 2 and isinstance(record, dict):
            if "cancellation" in record:
                record_shape.add("cancellation")
            if archived:
                record_shape.add("retirement")
        exact_fields(record, record_shape)
        if (
            not isinstance(record["operation"], str)
            or record["operation"] not in OPERATIONS
            or not isinstance(record["key"], str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", record["key"])
            or record["digest"] != companion_digest(record["request"])
            or not isinstance(record["basis"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["basis"])
            or not isinstance(record["state"], str)
            or record["state"] not in {"prepared", "executing", "completed", "cancelled", "expired"}
            or not isinstance(record["request"], dict)
            or len(companion_json(record["request"])) > MAX_COMPANION_REQUEST_BYTES
            or type(record["restored"]) is not bool
            or (record["receipt"] is not None and not isinstance(record["receipt"], dict))
        ):
            raise CompanionError("integrity_error")
        _record_lifetime(record, 86400)
        if version == 2:
            key = (record["operation"], record["key"])
            if key in seen_keys:
                raise CompanionError("integrity_error")
            seen_keys.add(key)
            if "cancellation" in record:
                _lifecycle_stamp(record["cancellation"], minimum=record["created_at"])
                if record["state"] != "cancelled" or record["confirmation"] is not None:
                    raise CompanionError("integrity_error")
            if archived:
                _lifecycle_stamp(record["retirement"], minimum=record["created_at"] + 30 * 86400)
                retired_at = record["retirement"]["at"]
                if (
                    record["confirmation"] is not None
                    or record["state"] == "executing"
                    or (record["state"] == "prepared" and retired_at < record["expires_at"])
                    or (record["state"] == "completed" and record["receipt"] is None)
                ):
                    raise CompanionError("integrity_error")
        confirmation = record["confirmation"]
        if confirmation is not None:
            exact_fields(
                confirmation,
                {"token_hash", "session_hash", "generation", "basis", "created_at", "expires_at"},
            )
            for field in ("token_hash", "session_hash", "generation", "basis"):
                if not isinstance(confirmation[field], str) or not re.fullmatch(
                    r"[0-9a-f]{64}", confirmation[field]
                ):
                    raise CompanionError("integrity_error")
            _record_lifetime(confirmation, 600)
    return state


def _lifecycle_stamp(stamp: object, *, minimum: int = 0) -> None:
    checked = exact_fields(stamp, {"at", "fingerprint"})
    if (
        type(checked["at"]) is not int
        or checked["at"] < minimum
        or not isinstance(checked["fingerprint"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", checked["fingerprint"])
    ):
        raise CompanionError("integrity_error")


def _record_lifetime(record: dict[str, Any], maximum: int) -> None:
    start, end = record["created_at"], record["expires_at"]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= start + maximum:
        raise CompanionError("integrity_error")


@dataclass(frozen=True, slots=True)
class CompanionRef:
    kind: str
    object_id: str
    revision: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, str)
            or self.kind not in KINDS
            or not isinstance(self.object_id, str)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,239}", self.object_id)
        ):
            raise CompanionError("invalid_input")
        if not isinstance(self.revision, str) or not re.fullmatch(r"[0-9a-f]{64}", self.revision):
            raise CompanionError("invalid_input")

    @classmethod
    def from_dict(cls, payload: object) -> CompanionRef:
        return cls(**exact_fields(payload, {"kind", "object_id", "revision"}))

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class CompanionRepository(Protocol):
    def list_permitted(
        self, kind: str, purpose: str, offset: int, limit: int
    ) -> dict[str, Any]: ...
    def read_permitted(
        self, ref: CompanionRef, fields: tuple[str, ...], purpose: str, historical: bool
    ) -> dict[str, Any]: ...
    def prepare(self, operation: str, request: dict[str, Any], key: str) -> dict[str, Any]: ...


class CompanionService:
    """Model-visible operations cannot grant, approve, delete or choose an instance."""

    def __init__(self, repository: CompanionRepository) -> None:
        self._repository = repository

    def call(self, operation: str, payload: object) -> dict[str, Any]:
        try:
            checked = bounded_companion_json(companion_json(payload))
            if operation == "list":
                args = exact_fields(checked, {"kind", "purpose", "offset", "limit"})
                if args["kind"] not in KINDS or args["purpose"] not in PURPOSES:
                    raise CompanionError("invalid_input")
                if type(args["offset"]) is not int or not 0 <= args["offset"] <= 256:
                    raise CompanionError("invalid_input")
                if type(args["limit"]) is not int or not 1 <= args["limit"] <= 20:
                    raise CompanionError("invalid_input")
                result = self._repository.list_permitted(**args)
            elif operation == "read":
                args = exact_fields(checked, {"ref", "fields", "purpose", "historical"})
                fields = args["fields"]
                if (
                    not isinstance(fields, list)
                    or not 1 <= len(fields) <= 24
                    or any(not isinstance(item, str) for item in fields)
                    or len(set(fields)) != len(fields)
                    or args["purpose"] not in PURPOSES
                    or type(args["historical"]) is not bool
                ):
                    raise CompanionError("invalid_input")
                result = self._repository.read_permitted(
                    CompanionRef.from_dict(args["ref"]),
                    tuple(fields),
                    args["purpose"],
                    args["historical"],
                )
            elif operation in OPERATIONS:
                args = exact_fields(checked, {"request", "idempotency_key"})
                key = args["idempotency_key"]
                if (
                    not isinstance(args["request"], dict)
                    or not isinstance(key, str)
                    or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key)
                ):
                    raise CompanionError("invalid_input")
                result = self._repository.prepare(operation, args["request"], key)
            else:
                raise CompanionError("unavailable_operation")
            if len(companion_json(result)) > MAX_COMPANION_RESPONSE_BYTES:
                raise CompanionError("response_too_large")
            return result
        except CompanionError as exc:
            return {"error": exc.code}
        except (TypeError, ValueError, KeyError, UnicodeError, RecursionError):
            return {"error": "invalid_input"}
        except PermissionError:
            return {"error": "not_found_or_denied"}
        except OSError:
            return {"error": "service_unavailable"}

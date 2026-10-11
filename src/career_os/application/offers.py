"""Supplied offer comparison and confirmed-save contracts; no IO on import."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Literal, Protocol, TypeVar, cast

from career_os.application.opportunities import is_windows_reserved_name
from career_os.offer_economics import OfferEconomics, compare_offer

MAX_OFFER_REQUEST_BYTES = 64 * 1024
_OFFER_FIELDS = {field.name for field in fields(OfferEconomics)}
_BASELINE_FIELDS = {"current_total_value", "current_benefits_value", "progression_benchmark"}
_REQUEST_FIELDS = {
    "schema_version",
    "comparison_id",
    "offer_label",
    "currency",
    "period",
    "value_basis",
    "offer",
    "baseline",
    "sources",
    "assumed_fields",
}


def _text(value: object, maximum: int = 2_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Offer text must be nonempty and bounded")
    if any(ord(char) < 32 for char in value):
        raise ValueError("Offer text must not contain control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Offer text must contain valid Unicode") from exc
    return value.strip()


def _number(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0 <= value <= 10_000_000
        or not math.isfinite(value)
    ):
        raise ValueError("Offer amounts must be finite numbers from 0 to 10,000,000")
    return int(value) if value == int(value) else float(value)


@dataclass(frozen=True, slots=True)
class OfferComparisonRequest:
    comparison_id: str
    offer_label: str
    currency: str
    value_basis: str
    offer: OfferEconomics
    baseline: dict[str, float | None]
    sources: dict[str, str]
    assumed_fields: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> OfferComparisonRequest:
        return cast(OfferComparisonRequest, cls._parse(payload, allow_unknown=False))

    @classmethod
    def _parse(
        cls, payload: object, *, allow_unknown: bool
    ) -> OfferComparisonRequest | UnknownOffer:
        if not isinstance(payload, dict) or set(payload) != _REQUEST_FIELDS:
            raise ValueError("Offer request must contain exactly the documented fields")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValueError("Unsupported offer request version")
        if payload["period"] != "annual":
            raise ValueError("Offer amounts must use an annual period")
        identifier = _text(payload["comparison_id"], 80)
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", identifier):
            raise ValueError("Comparison id must be a stable snake-case identifier")
        if is_windows_reserved_name(identifier):
            raise ValueError("Comparison id must not be a Windows-reserved name")
        currency = _text(payload["currency"], 3)
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("Currency must be an uppercase three-letter code")
        offer = payload["offer"]
        baseline = payload["baseline"]
        if not isinstance(offer, dict) or set(offer) != _OFFER_FIELDS:
            raise ValueError("All offer inputs must be explicit")
        if not isinstance(baseline, dict) or set(baseline) != _BASELINE_FIELDS:
            raise ValueError("All baseline inputs must be explicit")
        normalized_offer = {
            key: None if allow_unknown and value is None else _number(value)
            for key, value in offer.items()
        }
        normalized_baseline = {
            key: None
            if (allow_unknown or key == "current_benefits_value") and value is None
            else _number(value)
            for key, value in baseline.items()
        }
        source_keys = {f"offer.{key}" for key in _OFFER_FIELDS} | {
            f"baseline.{key}" for key in _BASELINE_FIELDS
        }
        sources = payload["sources"]
        if not isinstance(sources, dict) or set(sources) != source_keys:
            raise ValueError("Every numeric input requires a supplied source or uncertainty note")
        assumptions = payload["assumed_fields"]
        if (
            not isinstance(assumptions, list)
            or len(assumptions) > len(source_keys)
            or any(not isinstance(key, str) or key not in source_keys for key in assumptions)
            or len(set(assumptions)) != len(assumptions)
        ):
            raise ValueError("Assumed fields must be a unique list of input names")
        label = _text(payload["offer_label"], 300)
        value_basis = _text(payload["value_basis"], 500)
        normalized_sources = {key: _text(value) for key, value in sources.items()}
        unknowns = tuple(
            sorted(
                [f"offer.{key}" for key, value in normalized_offer.items() if value is None]
                + [
                    f"baseline.{key}"
                    for key, value in normalized_baseline.items()
                    if value is None and key != "current_benefits_value"
                ]
            )
        )
        if unknowns:
            normalized = {
                "schema_version": 1,
                "comparison_id": identifier,
                "offer_label": label,
                "currency": currency,
                "period": "annual",
                "value_basis": value_basis,
                "offer": normalized_offer,
                "baseline": normalized_baseline,
                "sources": normalized_sources,
                "assumed_fields": sorted(assumptions),
            }
            if len(_canonical(normalized)) > MAX_OFFER_REQUEST_BYTES:
                raise ValueError("Offer proposal exceeds the byte limit")
            return UnknownOffer(normalized, unknowns)
        return cls(
            identifier,
            label,
            currency,
            value_basis,
            OfferEconomics(**normalized_offer),
            normalized_baseline,
            normalized_sources,
            tuple(sorted(assumptions)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "period": "annual",
            **asdict(self),
            "assumed_fields": list(self.assumed_fields),
        }


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class UnknownOffer:
    request: dict[str, Any]
    unknown_fields: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "request": deepcopy(self.request),
            "unknown_fields": list(self.unknown_fields),
            "complete": False,
            "expected": None,
            "downside": None,
            "comparison_saved": False,
            "limitations": ["Missing amounts remain unknown; no numeric verdict is calculated."],
        }


@dataclass(frozen=True, slots=True)
class OfferComparison:
    request: OfferComparisonRequest
    expected: dict[str, Any]
    downside: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
            "authority": "user_supplied_model",
            "request": self.request.to_dict(),
            "expected": deepcopy(self.expected),
            "downside": deepcopy(self.downside),
            "downside_assumption": "No variable payout and no equity value; other inputs unchanged",
            "limitations": [
                "Supplied sources are references, not independently verified evidence",
                "All amounts must share the stated currency, annual period and value basis",
                "No currency conversion, tax, legal, financial or contractual advice",
                "Economic verdicts are model outputs, not career decisions or employer events",
            ],
        }
        return {
            **payload,
            "confirmation_fingerprint": hashlib.sha256(_canonical(payload)).hexdigest(),
        }


@dataclass(frozen=True, slots=True)
class OfferComparisonReceipt:
    comparison_id: str
    confirmation_fingerprint: str
    relative_path: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class OfferRepository(Protocol):
    def save(self, comparison: OfferComparison) -> OfferComparisonReceipt: ...


@dataclass(frozen=True, slots=True)
class OfferOperationError:
    code: Literal["human_required", "invalid_offer", "io_error"]
    message: str


_T = TypeVar("_T")


def require_offer_result(result: _T | OfferOperationError) -> _T:
    if isinstance(result, OfferOperationError):
        if result.code == "human_required":
            raise PermissionError(result.message)
        if result.code == "io_error":
            raise OSError(result.message)
        raise ValueError(result.message)
    return result


class OfferService:
    """Compare without effects; confirmation authority comes only from a trusted caller."""

    def __init__(self, repository: OfferRepository | None = None) -> None:
        self._repository = repository

    def _execute(self, operation: Callable[[], _T]) -> _T | OfferOperationError:
        try:
            return operation()
        except PermissionError as exc:
            if exc.errno is None:
                return OfferOperationError(
                    "human_required", "Current local-user confirmation is required"
                )
            return OfferOperationError("io_error", "Local offer storage is unavailable")
        except ValueError:
            return OfferOperationError(
                "invalid_offer", "Offer input or retained comparison is invalid"
            )
        except OSError:
            return OfferOperationError("io_error", "Local offer storage is unavailable")

    def _compare(self, request: OfferComparisonRequest) -> OfferComparison:
        validated = OfferComparisonRequest.from_dict(request.to_dict())
        if len(_canonical(validated.to_dict())) > MAX_OFFER_REQUEST_BYTES:
            raise ValueError("Offer request exceeds the byte limit")
        return OfferComparison(
            validated,
            compare_offer(validated.offer, validated.baseline),
            compare_offer(
                replace(validated.offer, target_variable=0, equity_face_value_annual=0),
                validated.baseline,
            ),
        )

    def compare(self, request: OfferComparisonRequest) -> OfferComparison | OfferOperationError:
        return self._execute(lambda: self._compare(request))

    def preflight(self, payload: object) -> OfferComparison | UnknownOffer | OfferOperationError:
        def preview() -> OfferComparison | UnknownOffer:
            parsed = OfferComparisonRequest._parse(payload, allow_unknown=True)
            return parsed if isinstance(parsed, UnknownOffer) else self._compare(parsed)

        return self._execute(preview)

    def save_comparison(
        self,
        request: OfferComparisonRequest,
        *,
        confirmation_fingerprint: str,
        actor: str,
    ) -> OfferComparisonReceipt | OfferOperationError:
        def save() -> OfferComparisonReceipt:
            if actor != "local_user":
                raise PermissionError("Only the local user may confirm a comparison")
            comparison = self._compare(request)
            if confirmation_fingerprint != comparison.to_dict()["confirmation_fingerprint"]:
                raise PermissionError("Comparison changed after preview")
            if self._repository is None:
                raise OSError("No local offer repository was supplied")
            return self._repository.save(comparison)

        return self._execute(save)

"""Deterministic offer values shared by public and legacy workflows."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OfferEconomics:
    base: float
    target_variable: float = 0
    variable_achievability: float = 0.8
    car_or_mobility: float = 0
    benefits: float = 0
    equity_face_value_annual: float = 0
    equity_probability: float = 0
    annual_office_travel_cost: float = 0

    def __post_init__(self) -> None:
        values = asdict(self)
        for name, value in values.items():
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise ValueError(f"{name} must be numeric")
            try:
                normalized = float(value)
            except OverflowError as exc:
                raise ValueError(f"{name} must be between 0 and 10,000,000") from exc
            if not math.isfinite(normalized) or value < 0 or value > 10_000_000:
                raise ValueError(f"{name} must be between 0 and 10,000,000")
        if self.variable_achievability > 1:
            raise ValueError("variable_achievability must be between 0 and 1")
        if self.equity_probability > 1:
            raise ValueError("equity_probability must be between 0 and 1")

    @property
    def guaranteed_total(self) -> float:
        return self.base + self.car_or_mobility + self.benefits - self.annual_office_travel_cost

    @property
    def expected_variable(self) -> float:
        return self.target_variable * self.variable_achievability

    @property
    def risk_adjusted_equity(self) -> float:
        return self.equity_face_value_annual * self.equity_probability

    @property
    def expected_total(self) -> float:
        return self.guaranteed_total + self.expected_variable + self.risk_adjusted_equity

    @property
    def target_cash_and_benefits(self) -> float:
        return self.guaranteed_total + self.target_variable

    @property
    def optimistic_total(self) -> float:
        return self.target_cash_and_benefits + self.equity_face_value_annual


def compare_offer(offer: OfferEconomics, economics: dict[str, Any]) -> dict[str, Any]:
    baseline = economics.get("current_total_value")
    benchmark = economics.get("progression_benchmark")
    current_benefits = economics.get("current_benefits_value")
    if not isinstance(baseline, int | float) or isinstance(baseline, bool):
        raise ValueError("Configured current_total_value is required")
    if not isinstance(benchmark, int | float) or isinstance(benchmark, bool):
        raise ValueError("Configured progression_benchmark is required")
    if current_benefits is not None and (
        not isinstance(current_benefits, int | float) or isinstance(current_benefits, bool)
    ):
        raise ValueError("current_benefits_value must be numeric when configured")

    def finite_number(value: int | float, field_name: str) -> float:
        try:
            normalized = float(value)
        except OverflowError as exc:
            raise ValueError(f"Configured {field_name} must be finite") from exc
        if not math.isfinite(normalized):
            raise ValueError(f"Configured {field_name} must be finite")
        return normalized

    baseline_value = finite_number(baseline, "current_total_value")
    benchmark_value = finite_number(benchmark, "progression_benchmark")
    benefits_value = (
        finite_number(current_benefits, "current_benefits_value")
        if current_benefits is not None
        else 0.0
    )
    comparable_baseline = baseline_value + benefits_value
    delta_baseline = offer.expected_total - comparable_baseline
    delta_benchmark = offer.expected_total - benchmark_value
    if current_benefits is not None and delta_benchmark >= 0 and delta_baseline > 0:
        verdict = "economic_progression"
    elif delta_baseline >= 0:
        verdict = "conditional_or_marginal_progression"
    else:
        verdict = "economic_regression"
    unknowns = []
    if current_benefits is None:
        unknowns.append(
            "Current company-car and other benefit value is not configured, so the baseline "
            "comparison is incomplete."
        )
    if offer.target_variable and offer.variable_achievability == 0.8:
        unknowns.append(
            "Variable achievability uses an 80% assumption; verify it against quota and "
            "payout evidence."
        )
    if offer.equity_face_value_annual and offer.equity_probability == 0:
        unknowns.append("Equity has zero risk-adjusted value until a probability is supplied.")
    return {
        "verdict": verdict,
        "configured_current_baseline": comparable_baseline,
        "progression_benchmark": benchmark_value,
        "delta_to_current": delta_baseline,
        "delta_to_benchmark": delta_benchmark,
        "unknowns": unknowns,
        "offer": {
            **asdict(offer),
            "expected_variable": offer.expected_variable,
            "risk_adjusted_equity": offer.risk_adjusted_equity,
            "guaranteed_total": offer.guaranteed_total,
            "expected_total": offer.expected_total,
            "target_cash_and_benefits": offer.target_cash_and_benefits,
            "optimistic_total": offer.optimistic_total,
        },
    }

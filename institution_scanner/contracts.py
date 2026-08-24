"""Canonical model contracts for the InstitutionScanner reliability layer.

The production contract is intentionally explicit and immutable.  New model ideas
must run as shadow challengers until out-of-sample evidence is strong enough to
justify a deliberate promotion.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

CONTRACT_VERSION: Final = "2026-08-24-v103-model-contract-v1"


@dataclass(frozen=True)
class ModelWeights:
    setup: float
    trigger: float
    execution: float

    def signature(self) -> str:
        return f"{self.setup:.4f}:{self.trigger:.4f}:{self.execution:.4f}"

    def total(self) -> float:
        return self.setup + self.trigger + self.execution


@dataclass(frozen=True)
class ModelContract:
    name: str
    role: str
    version: str
    weights: ModelWeights
    production: bool
    notes: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["weight_signature"] = self.weights.signature()
        return payload


PRODUCTION_CONTRACT: Final = ModelContract(
    name="champion",
    role="PRODUCTION_CHAMPION",
    version="v102-production-contract",
    weights=ModelWeights(setup=0.60, trigger=0.15, execution=0.25),
    production=True,
    notes=(
        "Locked production weights. Changes require explicit promotion from a "
        "validated challenger."
    ),
)

CHALLENGER_CONTRACT: Final = ModelContract(
    name="trigger_plus",
    role="SHADOW_CHALLENGER",
    version="v103-trigger-plus-shadow",
    weights=ModelWeights(setup=0.55, trigger=0.20, execution=0.25),
    production=False,
    notes=(
        "Shadow-only sensitivity test. It never changes production scores, "
        "CandidateViewRank, TradeReady, or publication eligibility."
    ),
)


def validate_contracts() -> None:
    """Raise if a contract is malformed before it can reach a runtime."""
    for contract in (PRODUCTION_CONTRACT, CHALLENGER_CONTRACT):
        if abs(contract.weights.total() - 1.0) > 1e-12:
            raise ValueError(
                f"{contract.name} weights must sum to 1.0; got "
                f"{contract.weights.total():.12f}"
            )
        for value in (
            contract.weights.setup,
            contract.weights.trigger,
            contract.weights.execution,
        ):
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{contract.name} contains an invalid weight: {value}")


validate_contracts()

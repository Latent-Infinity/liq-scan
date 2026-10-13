"""Immutable inputs and diagnostic outputs; no historical admission or orders."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Final, Literal

from liq.features.ranker_features import FeatureValue, SourceIdentity


class Champion(StrEnum):
    MOMENTUM = "momentum_12_1"
    VOLATILITY = "idio_vol_60d"
    PERSISTENCE = "surprise_persistence"
    REVERSAL = "reversal_21d"
    COMPOSITE = "breadth_gated_composite"


CHAMPIONS: Final = tuple(Champion)
BASE_CHAMPIONS: Final = CHAMPIONS[:-1]
INTERPRETATION_SHA256: Final = "1aa6fd40438320d75aee2357ceabb6891901fa21a8e47c1d4f49411b9a1b586a"
CLARIFICATION_SHA256: Final = "f114c1b0e9f34696bd2ca1b77bf77344efac7fc3a294a0624f608438b5774184"


@dataclass(frozen=True, slots=True)
class MonthlyFormation:
    """Caller supplies the admitted calendar, anchor close and next-session open."""

    anchor_month: date
    anchor_close: datetime
    cutoff: datetime
    calendar_source: SourceIdentity | None
    calendar_available_at: datetime | None


@dataclass(frozen=True, slots=True)
class MemberFeatures:
    """Common identity/basis evidence, then nullable approved causal features.

    The owning loader admits effective identity and basis. These metadata fields
    do not establish issuer history, price adjustments or calendar truth themselves.
    """

    security_id: str
    price_basis: str
    identity_source: SourceIdentity | None
    identity_available_at: datetime | None
    momentum: FeatureValue | None = None
    volatility: FeatureValue | None = None
    persistence: FeatureValue | None = None
    reversal: FeatureValue | None = None

    def components(self) -> tuple[FeatureValue | None, ...]:
        return self.momentum, self.volatility, self.persistence, self.reversal


@dataclass(frozen=True, slots=True)
class FeatureExclusion:
    """A specific absent component, retained before any outcome is supplied."""

    security_id: str
    champion: Champion
    reason: str


@dataclass(frozen=True, slots=True)
class DiagnosticWeight:
    security_id: str
    weight: float


@dataclass(frozen=True, slots=True)
class Selected:
    champion: Champion
    eligible_ids: tuple[str, ...]
    top: tuple[DiagnosticWeight, ...]
    bottom: tuple[DiagnosticWeight, ...]
    exclusions: tuple[FeatureExclusion, ...]
    state: Literal["selected"] = field(default="selected", init=False)


@dataclass(frozen=True, slots=True)
class Unavailable:
    champion: Champion
    reason: str
    exclusions: tuple[FeatureExclusion, ...] = ()
    state: Literal["unavailable"] = field(default="unavailable", init=False)


@dataclass(frozen=True, slots=True)
class NonInformative:
    champion: Champion
    eligible_ids: tuple[str, ...]
    exclusions: tuple[FeatureExclusion, ...]
    state: Literal["non_informative"] = field(default="non_informative", init=False)


@dataclass(frozen=True, slots=True)
class RegimeOff:
    champion: Literal[Champion.COMPOSITE] = field(default=Champion.COMPOSITE, init=False)
    state: Literal["off"] = field(default="off", init=False)


type DiagnosticSelection = Selected | Unavailable | NonInformative | RegimeOff


@dataclass(frozen=True, slots=True)
class MonthlySelections:
    formation: MonthlyFormation
    cohort_ids: tuple[str, ...]
    selections: tuple[DiagnosticSelection, ...]
    source_hashes: tuple[str, ...]
    interpretation_sha256: str = field(default=INTERPRETATION_SHA256, init=False)
    clarification_sha256: str = field(default=CLARIFICATION_SHA256, init=False)


@dataclass(frozen=True, slots=True)
class SelectionRefused(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason

"""Prospective fixed-five diagnostic selection over already admitted features.

No transport, prices, outcomes, fitting or portfolio orders enter this reducer.
Source loaders prove the calendar, effective cohort/identity and feature windows;
UTC month structure and source hashes are not themselves historical admission.
"""

import re
from datetime import date, datetime
from itertools import groupby
from math import isfinite

from liq.features.ranker_features import (
    BreadthValue,
    CohortSnapshot,
    FeatureUnavailable,
    FeatureValue,
    require_utc,
)
from liq.scan.classical_ranker_types import (
    BASE_CHAMPIONS,
    CHAMPIONS,
    Champion,
    DiagnosticSelection,
    DiagnosticWeight,
    FeatureExclusion,
    MemberFeatures,
    MonthlyFormation,
    MonthlySelections,
    NonInformative,
    RegimeOff,
    Selected,
    SelectionRefused,
    Unavailable,
)

__all__ = [
    "CHAMPIONS",
    "Champion",
    "MemberFeatures",
    "MonthlyFormation",
    "SelectionRefused",
    "Unavailable",
    "select_classical_ranker",
]


def _known_by(timestamp: datetime | None, cutoff: datetime) -> bool:
    if timestamp is None:
        return False
    try:
        require_utc(timestamp)
    except FeatureUnavailable:
        return False
    return timestamp <= cutoff


def _validate_population(
    formation: MonthlyFormation, cohort: CohortSnapshot, members: tuple[MemberFeatures, ...]
) -> None:
    try:
        require_utc(formation.cutoff)
        require_utc(formation.anchor_close)
        require_utc(cohort.observation_time)
    except FeatureUnavailable as exc:
        raise SelectionRefused("formation_requires_UTC") from exc
    anchor = formation.anchor_month
    if type(anchor) is not date or anchor.day != 1:
        raise SelectionRefused("anchor_month_requires_first_day_date")
    month = anchor.year * 12 + anchor.month
    if formation.anchor_close.year * 12 + formation.anchor_close.month != month:
        raise SelectionRefused("anchor_close_month_mismatch")
    if formation.cutoff.year * 12 + formation.cutoff.month != month + 1:
        raise SelectionRefused("cutoff_requires_following_month")
    if formation.calendar_source is None:
        raise SelectionRefused("calendar_identity_missing")
    if not _known_by(formation.calendar_available_at, formation.cutoff):
        raise SelectionRefused("calendar_availability_unresolved")
    if cohort.observation_time != formation.anchor_close:
        raise SelectionRefused("cohort_observation_mismatch")
    if not _known_by(cohort.available_at, formation.cutoff):
        raise SelectionRefused("cohort_availability_unresolved")
    if (
        not cohort.members
        or any(not name or name != name.strip() for name in cohort.members)
        or len(set(cohort.members)) != len(cohort.members)
    ):
        raise SelectionRefused("cohort_members_invalid")
    identifiers = tuple(row.security_id for row in members)
    if len(set(identifiers)) != len(identifiers) or set(identifiers) != set(cohort.members):
        raise SelectionRefused("member_identity_population_mismatch")
    for row in members:
        if row.identity_source is None:
            raise SelectionRefused("member_identity_source_missing")
        if not row.price_basis.strip():
            raise SelectionRefused("member_basis_missing")
        if not _known_by(row.identity_available_at, formation.cutoff):
            raise SelectionRefused("member_availability_unresolved")


def _feature_reason(feature: FeatureValue | None, cutoff: datetime) -> str | None:
    if feature is None:
        return "feature_missing"
    try:
        require_utc(feature.available_at)
    except FeatureUnavailable:
        return "feature_clock_unresolved"
    if feature.available_at > cutoff:
        return "feature_after_cutoff"
    if not feature.source_hashes or any(
        not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in feature.source_hashes
    ):
        return "feature_source_unresolved"
    if isinstance(feature.value, bool) or not isfinite(feature.value):
        return "feature_value_invalid"
    return None


def _claimed_hashes(feature: FeatureValue | None) -> tuple[str, ...]:
    """Preserve valid claimed source identities even for excluded observations.

    This reads only provenance, without inspecting the numerical value or treating
    its clock/source claim as admission. Malformed tokens are never published.
    """
    if feature is None:
        return ()
    return tuple(
        digest for digest in feature.source_hashes if re.fullmatch(r"[0-9a-f]{64}", digest)
    )


def _breadth_reason(
    breadth: BreadthValue | None, cohort: CohortSnapshot, cutoff: datetime
) -> str | None:
    if breadth is None:
        return "breadth_missing"
    reason = _feature_reason(breadth.breadth, cutoff)
    if reason is not None and reason != "feature_value_invalid":
        return reason.replace("feature_", "breadth_", 1)
    if cohort.source.sha256 not in breadth.breadth.source_hashes:
        return "breadth_cohort_mismatch"
    value = breadth.breadth.value
    if reason is not None or not 0 <= value <= 100:
        return "breadth_value_invalid"
    if type(breadth.enabled) is not bool or breadth.enabled != (value >= 90):
        return "breadth_gate_inconsistent"
    return None


def decile_count(population_size: int) -> int:
    """Use the selector's ceiling-decile size for a selectable population."""
    if type(population_size) is not int or population_size < 2:
        raise SelectionRefused("decile_population")
    return (population_size + 9) // 10


def _select(
    champion: Champion,
    scores: tuple[tuple[str, float], ...],
    exclusions: tuple[FeatureExclusion, ...],
) -> DiagnosticSelection:
    if len(scores) < 2:
        return Unavailable(champion, "fewer_than_two_eligible", exclusions)
    ordered = tuple(sorted(scores, key=lambda item: (-item[1], item[0])))
    identifiers = tuple(name for name, _ in ordered)
    if len({value for _, value in ordered}) == 1:
        return NonInformative(champion, identifiers, exclusions)
    count = decile_count(len(ordered))
    weight = 1 / count
    return Selected(
        champion,
        identifiers,
        tuple(DiagnosticWeight(name, weight) for name in identifiers[:count]),
        tuple(DiagnosticWeight(name, weight) for name in identifiers[-count:]),
        exclusions,
    )


def _average_ranks(scores: tuple[tuple[str, float], ...]) -> tuple[tuple[str, float], ...]:
    """Called only for a complete common intersection containing at least two IDs."""
    ordered = sorted(scores, key=lambda item: item[1])
    ranks = []
    for _, group in groupby(enumerate(ordered), key=lambda item: item[1][1]):
        tied = tuple(group)
        rank = (tied[0][0] + tied[-1][0]) / (2 * (len(ordered) - 1))
        ranks.extend((name, rank) for _, (name, _) in tied)
    return tuple(ranks)


def select_classical_ranker(
    formation: MonthlyFormation,
    cohort: CohortSnapshot,
    members: tuple[MemberFeatures, ...],
    breadth: BreadthValue | None,
) -> MonthlySelections:
    """Freeze five diagnostic selections and exclusions before attaching outcomes.

    The entire supplied cohort must have common identity/basis evidence. Feature
    absence is champion-specific; the composite requires all four components.
    Missing breadth cannot become an off regime, or block the base champions.
    """
    _validate_population(formation, cohort, members)
    assert formation.calendar_source is not None
    source_hashes = {formation.calendar_source.sha256, cohort.source.sha256}
    scores: list[dict[str, float]] = [{} for _ in BASE_CHAMPIONS]
    exclusions: list[list[FeatureExclusion]] = [[] for _ in BASE_CHAMPIONS]
    for row in sorted(members, key=lambda row: row.security_id):
        assert row.identity_source is not None
        source_hashes.add(row.identity_source.sha256)
        for index, (champion, feature) in enumerate(
            zip(BASE_CHAMPIONS, row.components(), strict=True)
        ):
            source_hashes.update(_claimed_hashes(feature))
            reason = _feature_reason(feature, formation.cutoff)
            if reason is not None:
                exclusions[index].append(FeatureExclusion(row.security_id, champion, reason))
            else:
                assert feature is not None
                scores[index][row.security_id] = feature.value
    selections = [
        _select(champion, tuple(values.items()), tuple(missing))
        for champion, values, missing in zip(BASE_CHAMPIONS, scores, exclusions, strict=True)
    ]
    if breadth is not None:
        source_hashes.update(_claimed_hashes(breadth.breadth))
    reason = _breadth_reason(breadth, cohort, formation.cutoff)
    if reason is not None:
        selections.append(Unavailable(Champion.COMPOSITE, reason))
    else:
        assert breadth is not None
        source_hashes.update(breadth.breadth.source_hashes)
        if not breadth.enabled:
            selections.append(RegimeOff())
        else:
            common = set.intersection(*(set(values) for values in scores))
            missing = tuple(item for items in exclusions for item in items)
            if len(common) < 2:
                selections.append(
                    Unavailable(Champion.COMPOSITE, "fewer_than_two_eligible", missing)
                )
            else:
                component_ranks = [
                    dict(_average_ranks(tuple((name, values[name]) for name in sorted(common))))
                    for values in scores
                ]
                composite = tuple(
                    (name, sum(ranks[name] for ranks in component_ranks) / 4)
                    for name in sorted(common)
                )
                selections.append(_select(Champion.COMPOSITE, composite, missing))
    return MonthlySelections(
        formation, tuple(sorted(cohort.members)), tuple(selections), tuple(sorted(source_hashes))
    )

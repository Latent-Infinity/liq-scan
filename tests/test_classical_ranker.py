"""Source-free contracts only; no invented prices, factor scores or regimes."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from math import nan

import pytest

from liq.features.ranker_features import BreadthValue, CohortSnapshot, FeatureValue, SourceIdentity
from liq.scan.classical_ranker import (
    CHAMPIONS,
    Champion,
    MemberFeatures,
    MonthlyFormation,
    SelectionRefused,
    Unavailable,
    select_classical_ranker,
)


def source(name: str, digit: str) -> SourceIdentity:
    return SourceIdentity("contract-only " + name, "v1", digit * 64)


def formation() -> MonthlyFormation:
    return MonthlyFormation(
        date(2024, 1, 1),
        datetime(2024, 1, 31, 21, tzinfo=UTC),
        datetime(2024, 2, 1, 14, 30, tzinfo=UTC),
        source("calendar", "1"),
        datetime(2024, 1, 1, tzinfo=UTC),
    )


def cohort() -> CohortSnapshot:
    current = formation()
    return CohortSnapshot(
        ("a", "b"), source("cohort", "2"), current.anchor_close, current.anchor_close
    )


def members() -> tuple[MemberFeatures, ...]:
    return tuple(
        MemberFeatures(
            name, "unadmitted contract basis", source("member", "3"), formation().anchor_close
        )
        for name in ("a", "b")
    )


def test_absent_features_remain_exclusions_and_unknown_breadth_is_unavailable() -> None:
    # Given complete identity metadata but no financial observations.
    result = select_classical_ranker(formation(), cohort(), members(), None)
    # Then every required champion is retained, without selections or invented cash returns.
    assert tuple(item.champion for item in result.selections) == CHAMPIONS
    assert all(isinstance(item, Unavailable) for item in result.selections)
    assert result.selections[-1].reason == "breadth_missing"
    for item in result.selections[:-1]:
        assert tuple(exclusion.security_id for exclusion in item.exclusions) == ("a", "b")
        assert {exclusion.reason for exclusion in item.exclusions} == {"feature_missing"}


def test_source_free_output_is_deterministic_under_input_permutation() -> None:
    snapshot = cohort()
    original = select_classical_ranker(formation(), snapshot, members(), None)
    permuted = select_classical_ranker(
        formation(),
        replace(snapshot, members=tuple(reversed(snapshot.members))),
        tuple(reversed(members())),
        None,
    )
    assert original == permuted


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("anchor_month", date(2024, 1, 2), "anchor_month"),
        ("anchor_close", datetime(2023, 12, 31, 21, tzinfo=UTC), "anchor_close"),
        ("cutoff", datetime(2024, 1, 31, 22, tzinfo=UTC), "following_month"),
        ("cutoff", datetime(2024, 3, 1, 14, 30, tzinfo=UTC), "following_month"),
        ("cutoff", datetime(2024, 2, 1, 14, 30), "UTC"),
        ("calendar_available_at", None, "calendar_availability"),
        (
            "calendar_available_at",
            formation().cutoff + timedelta(seconds=1),
            "calendar_availability",
        ),
    ],
)
def test_invalid_formation_metadata_refuses_before_any_score(field, value, reason: str) -> None:
    invalid = replace(formation(), **{field: value})
    with pytest.raises(SelectionRefused, match=reason):
        select_classical_ranker(invalid, cohort(), members(), None)


@pytest.mark.parametrize(
    "snapshot,reason",
    [
        (replace(cohort(), members=()), "cohort_members"),
        (replace(cohort(), members=("a", "a")), "cohort_members"),
        (replace(cohort(), members=("a", " b")), "cohort_members"),
        (
            replace(cohort(), observation_time=formation().anchor_close - timedelta(days=1)),
            "cohort_observation",
        ),
        (replace(cohort(), available_at=None), "cohort_availability"),
        (
            replace(cohort(), available_at=formation().cutoff + timedelta(seconds=1)),
            "cohort_availability",
        ),
    ],
)
def test_invalid_cohort_refuses_the_whole_formation(snapshot, reason: str) -> None:
    with pytest.raises(SelectionRefused, match=reason):
        select_classical_ranker(formation(), snapshot, members(), None)


@pytest.mark.parametrize(
    "rows",
    [
        members()[:-1],
        (members()[0], members()[0]),
        (replace(members()[0], security_id="other"), members()[1]),
    ],
)
def test_member_rows_must_cover_exact_unique_cohort(rows) -> None:
    with pytest.raises(SelectionRefused, match="member_identity"):
        select_classical_ranker(formation(), cohort(), rows, None)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("identity_source", None, "member_identity"),
        ("price_basis", "", "member_basis"),
        ("identity_available_at", None, "member_availability"),
        ("identity_available_at", formation().cutoff + timedelta(seconds=1), "member_availability"),
    ],
)
def test_mandatory_member_evidence_is_not_a_feature_specific_exclusion(
    field, value, reason: str
) -> None:
    rows = (replace(members()[0], **{field: value}), members()[1])
    with pytest.raises(SelectionRefused, match=reason):
        select_classical_ranker(formation(), cohort(), rows, None)


@pytest.mark.parametrize(
    "feature,reason",
    [
        (
            FeatureValue(nan, formation().cutoff + timedelta(seconds=1), ("4" * 64,)),
            "feature_after_cutoff",
        ),
        (FeatureValue(nan, datetime(2024, 1, 31, 21), ("4" * 64,)), "feature_clock_unresolved"),
        (FeatureValue(nan, formation().anchor_close, ()), "feature_source_unresolved"),
        (FeatureValue(nan, formation().anchor_close, ("bad",)), "feature_source_unresolved"),
        (FeatureValue(nan, formation().anchor_close, ("4" * 64,)), "feature_value_invalid"),
    ],
)
def test_unavailable_feature_excludes_only_its_champion(feature, reason: str) -> None:
    rows = (replace(members()[0], momentum=feature), members()[1])
    result = select_classical_ranker(formation(), cohort(), rows, None)
    momentum = result.selections[0]
    assert momentum.champion == Champion.MOMENTUM
    assert momentum.exclusions[0].reason == reason
    assert {item.reason for item in result.selections[1].exclusions} == {"feature_missing"}
    assert result.selections[-1].reason == "breadth_missing"


@pytest.mark.parametrize(
    "value,reason",
    [
        (
            BreadthValue(
                FeatureValue(nan, formation().cutoff + timedelta(seconds=1), ("2" * 64,)), False
            ),
            "breadth_after_cutoff",
        ),
        (
            BreadthValue(FeatureValue(nan, formation().anchor_close, ("bad",)), False),
            "breadth_source_unresolved",
        ),
        (
            BreadthValue(FeatureValue(nan, formation().anchor_close, ("4" * 64,)), False),
            "breadth_cohort_mismatch",
        ),
        (
            BreadthValue(FeatureValue(nan, formation().anchor_close, ("2" * 64,)), False),
            "breadth_value_invalid",
        ),
    ],
)
def test_unavailable_breadth_blocks_only_gated_composite(value, reason: str) -> None:
    result = select_classical_ranker(formation(), cohort(), members(), value)
    assert result.selections[-1].reason == reason
    assert all(item.reason == "fewer_than_two_eligible" for item in result.selections[:-1])


def test_excluded_future_feature_retains_its_claimed_source_identity() -> None:
    feature = FeatureValue(nan, formation().cutoff + timedelta(seconds=1), ("4" * 64,))
    rows = (replace(members()[0], momentum=feature), members()[1])
    result = select_classical_ranker(formation(), cohort(), rows, None)
    assert "4" * 64 in result.source_hashes
    assert isinstance(result.selections[0], Unavailable)
    assert result.selections[0].exclusions[0].reason == "feature_after_cutoff"


def test_excluded_breadth_retains_valid_hashes_and_omits_malformed_tokens() -> None:
    invalid = BreadthValue(FeatureValue(nan, formation().anchor_close, ("4" * 64, "bad")), False)
    result = select_classical_ranker(formation(), cohort(), members(), invalid)
    assert "4" * 64 in result.source_hashes
    assert "bad" not in result.source_hashes
    assert isinstance(result.selections[-1], Unavailable)
    assert result.selections[-1].reason == "breadth_source_unresolved"

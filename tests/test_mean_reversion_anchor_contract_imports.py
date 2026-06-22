from __future__ import annotations

from importlib import import_module

import pytest


def _anchor_exports() -> dict[str, object]:
    return vars(import_module("liq.scan.anchors.mean_reversion"))


@pytest.mark.xfail(
    strict=True,
    reason="mean-reversion anchor-vol provenance contract lands with scanner implementation",
)
def test_anchor_vol_source_contract_imports() -> None:
    assert _anchor_exports()["AnchorVolSource"] is not None


@pytest.mark.xfail(
    strict=True,
    reason="mean-reversion anchor event contract lands with scanner implementation",
)
def test_anchor_event_contract_imports() -> None:
    assert _anchor_exports()["AnchorEvent"] is not None


@pytest.mark.xfail(
    strict=True,
    reason="mean-reversion anchor extension contract lands with scanner implementation",
)
def test_mean_reversion_anchor_contract_imports() -> None:
    assert _anchor_exports()["MeanReversionAnchor"] is not None


@pytest.mark.xfail(
    strict=True,
    reason="mean-reversion anchor id helper lands with scanner implementation",
)
def test_compute_anchor_event_id_contract_imports() -> None:
    assert callable(_anchor_exports()["compute_anchor_event_id"])


@pytest.mark.xfail(
    strict=True,
    reason="mean-reversion excursion predicate lands with scanner predicate implementation",
)
def test_mean_reversion_excursion_predicate_contract_imports() -> None:
    predicate_exports = vars(import_module("liq.scan.predicates"))

    assert predicate_exports["MeanReversionExcursionPredicate"] is not None

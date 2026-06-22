from __future__ import annotations

from importlib import import_module


def _anchor_exports() -> dict[str, object]:
    return vars(import_module("liq.scan.anchors.mean_reversion"))


def test_anchor_vol_source_contract_imports() -> None:
    assert _anchor_exports()["AnchorVolSource"] is not None


def test_anchor_event_contract_imports() -> None:
    assert _anchor_exports()["AnchorEvent"] is not None


def test_mean_reversion_anchor_contract_imports() -> None:
    assert _anchor_exports()["MeanReversionAnchor"] is not None


def test_compute_anchor_event_id_contract_imports() -> None:
    assert callable(_anchor_exports()["compute_anchor_event_id"])


def test_mean_reversion_excursion_predicate_contract_imports() -> None:
    predicate_exports = vars(import_module("liq.scan.predicates"))

    assert predicate_exports["MeanReversionExcursionPredicate"] is not None

from __future__ import annotations

import importlib
import sys

FORBIDDEN_PREFIXES = (
    "liq.models",
    "liq.signals",
    "liq.risk",
    "liq.datasets",
    "liq.experiments",
)


def test_mean_reversion_anchor_imports_do_not_load_downstream_layers() -> None:
    before = set(sys.modules)
    importlib.import_module("liq.scan.anchors.mean_reversion")
    loaded = set(sys.modules) - before

    assert not [name for name in loaded if name.startswith(FORBIDDEN_PREFIXES)]

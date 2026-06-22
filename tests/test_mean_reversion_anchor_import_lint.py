"""Static import-lint for ``liq.scan.anchors.mean_reversion``.

Importing the anchor namespace must not pull downstream decision layers into
``sys.modules``. The scanner is the fact-emission layer; reversion judgements
(signals, risk, datasets, and experiments) live downstream and must not be
transitively loaded just by constructing a scanner anchor.

The check runs in a clean subprocess so it sees a pristine ``sys.modules``;
otherwise unrelated tests in the same session would taint the result.
"""

from __future__ import annotations

import json
import subprocess
import sys

FORBIDDEN_PREFIXES = (
    "liq.models",
    "liq.signals",
    "liq.risk",
    "liq.datasets",
    "liq.experiments",
)


def test_mean_reversion_anchor_namespace_does_not_load_forbidden_modules() -> None:
    code = (
        "import importlib, json, sys; "
        "importlib.import_module('liq.scan.anchors.mean_reversion'); "
        "print(json.dumps(sorted(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = set(json.loads(result.stdout))
    offenders = sorted(
        name for name in loaded if any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)
    )
    assert offenders == [], (
        f"liq.scan.anchors.mean_reversion transitively imports forbidden namespaces: {offenders}"
    )

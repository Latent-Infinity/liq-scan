"""Visibility test: ``liq-scan`` may only import from the public subset.

``liq-scan`` ships under MIT and is allowed to depend on:

* ``liq-core`` — shared types
* ``liq-store`` — parquet I/O
* ``liq-data`` — providers + universe + manifest + calendar
* ``liq-features`` — optional extra (not required)

Imports from private libs (``liq-models``, ``liq-sim``, etc.) are
prohibited; this test asserts the package's source files only
reference the allow-list above.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ALLOWED_LIQ_PREFIXES = ("liq.core", "liq.store", "liq.data", "liq.features", "liq.scan")


def _walk_src() -> list[Path]:
    src_root = Path(__file__).resolve().parents[1] / "src" / "liq" / "scan"
    return sorted(src_root.rglob("*.py"))


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_private_liq_imports() -> None:
    offenders: list[tuple[Path, str]] = []
    for src in _walk_src():
        for name in _module_imports(src):
            if not name.startswith("liq."):
                continue
            if any(name == p or name.startswith(p + ".") for p in _ALLOWED_LIQ_PREFIXES):
                continue
            offenders.append((src, name))
    assert not offenders, (
        "liq-scan imports private LIQ libs (only liq-core/liq-store/liq-data/liq-features "
        f"are allowed):\n{offenders}"
    )

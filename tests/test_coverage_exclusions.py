"""Coverage configuration must distinguish typed functions from stub bodies."""

import re
import tomllib
from pathlib import Path


def test_variadic_tuple_annotation_does_not_exclude_executable_function() -> None:
    config = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    patterns = config["tool"]["coverage"]["report"]["exclude_lines"]
    declaration = "def count_items(items: tuple[int, ...]) -> int:"
    assert not any(re.search(pattern, declaration) for pattern in patterns)


def test_standalone_ellipsis_stub_remains_excluded() -> None:
    config = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    patterns = config["tool"]["coverage"]["report"]["exclude_lines"]
    assert any(re.search(pattern, "    ...  # Protocol stub") for pattern in patterns)

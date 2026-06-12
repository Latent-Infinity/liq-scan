"""Smoke: the ``liq-scan`` CLI wires up and ``--help`` returns 0.

Subcommand-specific tests come with the subcommands; this file
exists so the console-script entry point and typer wiring can't
regress silently while the rest of the library is still scaffolding.
"""

from __future__ import annotations

from typer.testing import CliRunner

from liq.scan import __version__
from liq.scan.cli import app

_runner = CliRunner()


def test_help_returns_zero_and_mentions_liq_scan() -> None:
    result = _runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "liq-scan" in result.output.lower()


def test_version_flag_prints_version_and_exits_clean() -> None:
    result = _runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_args_shows_help_without_crashing() -> None:
    # no_args_is_help=True → typer prints help text and exits with the
    # idiomatic "missing command" status (2 on modern typer/click). The
    # contract this test protects is "no crash, help shown" — not a
    # specific exit code; subcommand additions later will need 0.
    result = _runner.invoke(app, [])
    assert result.exit_code in (0, 2)
    assert "Usage:" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)

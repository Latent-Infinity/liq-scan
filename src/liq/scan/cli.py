"""CLI entry point for ``liq-scan``.

Phase 0: scaffold only — ``liq-scan --help`` returns 0. Real
subcommands (``execute``, ``sweep``, ``--dry-run``) land in Phase 4 / 5.
"""

from __future__ import annotations

import typer

from liq.scan import __version__

app = typer.Typer(
    name="liq-scan",
    help="Cross-sectional universe screening for the LIQ Stack.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"liq-scan {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    _version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the liq-scan version and exit.",
    ),
) -> None:
    """liq-scan CLI root.

    Subcommands (``execute``, ``sweep``) are wired in Phase 4 / 5.
    """


def main() -> None:
    """Console-script entry point declared in ``pyproject.toml``."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()

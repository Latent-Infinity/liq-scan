"""Deterministic reverse/stressed DCF for the fundamental drawdown.

Pure math — no market I/O, no statistics library. A simplified FCF-to-equity
DCF (FCF treated as free cash flow to equity; the net-debt bridge is left out
as a screen-level approximation) with a Gordon-growth terminal value.

The fundamental drawdown ties the layers together via the market-implied
growth (IGB-10): the price is first inverted to the growth it requires under
base assumptions, then that baseline is stressed (lower FCF, lower growth,
higher WACC, lower terminal growth) and the resulting value compared to the
current market cap.
"""

from __future__ import annotations

from liq.scan.resilience.models import (
    BusinessProfile,
    DcfConfig,
    FundamentalScanInput,
)


def dcf_equity_value(
    fcf_0: float,
    growth: float,
    wacc: float,
    terminal_growth: float,
    horizon_years: int,
) -> float:
    """Present value of ``horizon`` years of growing FCF plus a terminal value."""
    if wacc <= terminal_growth:
        raise ValueError("wacc must exceed terminal_growth for a finite valuation")
    pv = 0.0
    fcf = fcf_0
    discount = 1.0 + wacc
    for year in range(1, horizon_years + 1):
        fcf *= 1.0 + growth
        pv += fcf / discount**year
    terminal_value = fcf * (1.0 + terminal_growth) / (wacc - terminal_growth)
    pv += terminal_value / discount**horizon_years
    return pv


def implied_growth(
    market_cap: float,
    fcf_0: float,
    cfg: DcfConfig,
    *,
    lo: float = -0.5,
    hi: float = 1.0,
    tolerance: float = 1e-4,
    max_iter: int = 100,
) -> float | None:
    """Solve for the FCF growth the current price implies (IGB-10).

    Bisection over a value that is monotonic increasing in growth. Returns
    ``None`` for non-positive FCF (a reverse DCF is undefined). The result is
    clamped to ``[lo, hi]``; a price requiring growth above ``hi`` returns
    ``hi`` (so the IGB gate still fails it rather than erroring).
    """
    if fcf_0 <= 0.0 or market_cap <= 0.0:
        return None

    def value_at(growth: float) -> float:
        return dcf_equity_value(
            fcf_0, growth, cfg.wacc_base, cfg.terminal_growth, cfg.horizon_years
        )

    if value_at(hi) < market_cap:
        return hi
    if value_at(lo) > market_cap:
        return lo
    low, high = lo, hi
    for _ in range(max_iter):
        mid = (low + high) / 2.0
        if value_at(mid) < market_cap:
            low = mid
        else:
            high = mid
        if high - low < tolerance:
            break
    return (low + high) / 2.0


def fundamental_drawdown(
    inp: FundamentalScanInput,
    profile: BusinessProfile,
    cfg: DcfConfig,
) -> tuple[float | None, float | None]:
    """Return ``(fundamental_drawdown, implied_growth)``.

    Both ``None`` when FCF is unavailable or non-positive (the unprofitable
    path is handled by the cash-runway gate, not the DCF). Otherwise the
    market-implied growth is stressed by the business profile and the stressed
    equity value is compared to the current market cap.
    """
    if inp.fcf is None or inp.fcf <= 0.0:
        return None, None
    igb = implied_growth(inp.market_cap, inp.fcf, cfg)
    if igb is None:
        return None, None

    stressed_fcf = inp.fcf * (1.0 - profile.fcf_haircut)
    stressed_growth = max(igb - profile.growth_haircut, -0.5)
    stressed_wacc = cfg.wacc_base + cfg.wacc_stress_add
    stressed_tg = max(cfg.terminal_growth - cfg.terminal_growth_stress_cut, 0.0)
    stressed_value = dcf_equity_value(
        stressed_fcf, stressed_growth, stressed_wacc, stressed_tg, cfg.horizon_years
    )
    drawdown = max(0.0, 1.0 - stressed_value / inp.market_cap)
    return drawdown, igb


__all__ = ["dcf_equity_value", "fundamental_drawdown", "implied_growth"]

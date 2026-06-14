"""Audit reconstruction tests for historical sweeps.

Operator scenario: a log line says "sweep X looked wrong" — can we
filter by `correlation_id == sweep:<sweep_id>` and reconstruct the
sequence (start → resumes → as-ofs → completion) without consulting
any other state?
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pytest

from liq.data.service import DataService
from liq.scan.engine import ScanEngine
from liq.scan.persistence import compute_sweep_id
from liq.scan.predicates import MovePredicate
from liq.scan.query import ScanQueryTemplate
from liq.scan.sweep import SweepConfig
from liq.scan.window import TradingMinutesWindow
from liq.store.parquet import ParquetStore
from tests.real_market_data import REAL_SYMBOL, link_real_spy_data


@pytest.fixture
def engine_two_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from liq.data.settings import get_settings, get_store

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    get_store.cache_clear()

    store = ParquetStore(str(tmp_path))
    link_real_spy_data(tmp_path)

    engine = ScanEngine(data_service=DataService(), store=store)
    template = ScanQueryTemplate(
        universe_ref=[REAL_SYMBOL],
        window=TradingMinutesWindow(kind="trading_minutes", n=30),
        predicate=MovePredicate(threshold_pct=0.1, direction="either"),
        ranking="abs_move",
        limit=None,
    )
    config = SweepConfig(
        query_name="audit",
        cadence="session_close",
        start=date(2024, 6, 3),
        end=date(2024, 6, 4),
    )
    yield engine, template, config
    get_settings.cache_clear()
    get_store.cache_clear()


def _events_for(records: list[logging.LogRecord], sweep_id: str) -> list[logging.LogRecord]:
    target = f"sweep:{sweep_id}"
    return [r for r in records if getattr(r, "correlation_id", None) == target]


class TestSweepAudit:
    def test_one_sweep_id_filters_full_sequence(
        self,
        engine_two_session,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        engine, template, config = engine_two_session
        caplog.set_level(logging.INFO, logger="liq.scan.engine")
        engine.sweep(template, config)

        sweep_id = compute_sweep_id(config)
        sweep_records = _events_for(caplog.records, sweep_id)
        events = [r.event for r in sweep_records]

        # Required envelope: start … completion.
        assert events[0] == "sweep_started"
        assert events[-1] == "sweep_completed"
        # Two sessions → two sweep_as_of.
        assert events.count("sweep_as_of") == 2
        # No resume on first sweep.
        assert "sweep_resumed" not in events

    def test_resume_emits_sweep_resumed_event(
        self,
        engine_two_session,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        engine, template, config = engine_two_session

        # First sweep: fail on the second execute so only session 1 commits.
        call = {"n": 0}
        real_execute = engine.execute

        def flaky(q, **kwargs):  # noqa: ANN001 — pyright is silent on monkeypatched methods
            call["n"] += 1
            if call["n"] == 2:
                raise RuntimeError("boom")
            return real_execute(q, **kwargs)

        monkeypatch.setattr(engine, "execute", flaky)
        with pytest.raises(RuntimeError):
            engine.sweep(template, config)

        # Resume — clear caplog so the second sweep's events are what we capture.
        caplog.clear()
        monkeypatch.setattr(engine, "execute", real_execute)
        caplog.set_level(logging.INFO, logger="liq.scan.engine")
        engine.sweep(template, config)

        sweep_id = compute_sweep_id(config)
        events = [r.event for r in _events_for(caplog.records, sweep_id)]
        assert "sweep_resumed" in events
        # Resume completes the remaining session and emits completion.
        assert events.count("sweep_as_of") == 1
        assert events[-1] == "sweep_completed"

    def test_every_event_carries_sweep_id_in_extra(
        self,
        engine_two_session,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        engine, template, config = engine_two_session
        caplog.set_level(logging.INFO, logger="liq.scan.engine")
        engine.sweep(template, config)

        sweep_id = compute_sweep_id(config)
        sweep_records = _events_for(caplog.records, sweep_id)
        assert sweep_records
        assert {getattr(r, "sweep_id", None) for r in sweep_records} == {sweep_id}

    def test_sweep_id_filter_includes_nested_execute_events(
        self,
        engine_two_session,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        engine, template, config = engine_two_session
        caplog.set_level(logging.INFO, logger="liq.scan.engine")
        engine.sweep(template, config)

        sweep_id = compute_sweep_id(config)
        events = [r.event for r in _events_for(caplog.records, sweep_id)]
        assert "scan_started" in events
        assert "coverage_verified" in events
        assert "predicate_evaluated" in events
        assert "scan_completed" in events

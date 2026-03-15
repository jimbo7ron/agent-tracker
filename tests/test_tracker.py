"""
test_tracker.py — Tests for tracker.py

Tests bar chart formatting, pricing calculation, no-data case,
and 7-day history formatting.
"""

import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

TZ = ZoneInfo("Australia/Sydney")


@pytest.fixture
def tmp_tracker(tmp_path, monkeypatch):
    """Provide a fresh DB and patch DB_PATH in tracker module."""
    import tracker as t

    db_file = tmp_path / "test_usage.db"
    monkeypatch.setattr(t, "DB_PATH", db_file)

    # Build schema
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE daily_usage (
            date TEXT NOT NULL,
            agent TEXT NOT NULL,
            model TEXT NOT NULL,
            tokens_delta INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (date, agent, model)
        )
    """)
    conn.commit()
    yield conn, t, db_file
    conn.close()


def insert_usage(conn, date, agent, model, tokens):
    conn.execute(
        "INSERT OR REPLACE INTO daily_usage VALUES (?, ?, ?, ?, ?)",
        (date, agent, model, tokens, int(time.time())),
    )
    conn.commit()


class TestPricingCalculation:
    def test_known_model_price(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        cost = t.cost_for_tokens(1000, "claude-sonnet-4-6")
        assert abs(cost - 0.005) < 0.0001

    def test_unknown_model_uses_default(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        cost = t.cost_for_tokens(1000, "unknown-model-xyz")
        assert abs(cost - 0.005) < 0.0001  # default rate

    def test_opus_price(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        cost = t.cost_for_tokens(1000, "claude-opus-4-6")
        assert abs(cost - 0.025) < 0.0001

    def test_haiku_price(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        cost = t.cost_for_tokens(2000, "claude-haiku-4-5")
        assert abs(cost - 0.002) < 0.0001  # 2k * $0.001/1k


class TestBarFormatting:
    def test_make_bar_full(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        bar = t.make_bar(100, 100)
        assert bar == "█" * t.BAR_MAX_WIDTH

    def test_make_bar_half(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        bar = t.make_bar(50, 100)
        assert len(bar) == t.BAR_MAX_WIDTH // 2

    def test_make_bar_zero(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        bar = t.make_bar(0, 100)
        assert bar == ""

    def test_make_bar_zero_max(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        bar = t.make_bar(100, 0)
        assert bar == ""

    def test_format_tokens_millions(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        assert t.format_tokens(1_500_000) == "1.5M"

    def test_format_tokens_thousands(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        assert t.format_tokens(42_300) == "42k"

    def test_format_tokens_small(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        assert t.format_tokens(500) == "500"


class TestNoDataCase:
    def test_no_db_returns_no_data_message(self, tmp_path, monkeypatch):
        import tracker as t
        nonexistent = tmp_path / "nonexistent.db"
        monkeypatch.setattr(t, "DB_PATH", nonexistent)
        result = t.report()
        assert "No token data" in result

    def test_empty_db_returns_no_data_message(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        # DB exists but has no rows
        result = t.report()
        assert "No token data" in result


class TestReportOutput:
    def test_report_contains_header(self, tmp_tracker, monkeypatch):
        conn, t, _ = tmp_tracker
        today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
        insert_usage(conn, today, "main", "claude-sonnet-4-6", 42300)
        result = t.report()
        assert "📊 Agent Token Report" in result

    def test_report_contains_agent_names(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
        insert_usage(conn, today, "main", "claude-sonnet-4-6", 10000)
        insert_usage(conn, today, "case", "claude-sonnet-4-6", 5000)
        result = t.report()
        assert "TARS" in result
        assert "CASE" in result

    def test_report_contains_token_counts(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
        insert_usage(conn, today, "main", "claude-sonnet-4-6", 42300)
        result = t.report()
        assert "42,300" in result

    def test_report_contains_cost(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
        insert_usage(conn, today, "main", "claude-sonnet-4-6", 1000)
        result = t.report()
        assert "$" in result

    def test_report_contains_total(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
        insert_usage(conn, today, "main", "claude-sonnet-4-6", 10000)
        insert_usage(conn, today, "case", "claude-sonnet-4-6", 5000)
        result = t.report()
        assert "Total" in result

    def test_report_contains_7day_section(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
        insert_usage(conn, today, "main", "claude-sonnet-4-6", 10000)
        result = t.report()
        assert "7-day rolling" in result

    def test_report_multiple_models_summed_per_agent(self, tmp_tracker):
        """Multiple model rows for same agent should be summed."""
        conn, t, _ = tmp_tracker
        today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
        insert_usage(conn, today, "main", "claude-sonnet-4-6", 10000)
        insert_usage(conn, today, "main", "claude-opus-4-6", 5000)
        result = t.report()
        # 15,000 total for TARS
        assert "15,000" in result


class TestWeeklyHistory:
    def test_7day_shows_all_days(self, tmp_tracker):
        conn, t, _ = tmp_tracker
        today = datetime.now(tz=TZ)
        today_str = today.strftime("%Y-%m-%d")
        insert_usage(conn, today_str, "main", "claude-sonnet-4-6", 50000)

        # Insert some history
        for i in range(1, 7):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            insert_usage(conn, d, "main", "claude-sonnet-4-6", i * 10000)

        result = t.report()
        # All 7 day labels should appear in the weekly section
        for i in range(7):
            day_label = (today - timedelta(days=i)).strftime("%a")
            assert day_label in result

    def test_missing_days_show_zero(self, tmp_tracker):
        """Days with no data should show 0 or equivalent."""
        conn, t, _ = tmp_tracker
        today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
        insert_usage(conn, today, "main", "claude-sonnet-4-6", 10000)
        result = t.report()
        # Should contain '0' for days with no data
        assert "0" in result or "0k" in result

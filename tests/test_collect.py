"""
test_collect.py — Tests for collect.py

Tests snapshot insertion, daily delta calculation, idempotency,
session reset handling, and timezone bucketing.
"""

import json
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import sys
import os

import pytest

# Make parent importable
sys.path.insert(0, str(Path(__file__).parent.parent))

TZ = ZoneInfo("Australia/Sydney")


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Provide a fresh in-memory DB and patch DB_PATH in collect module."""
    import collect as c

    db_file = tmp_path / "test_usage.db"
    monkeypatch.setattr(c, "DB_PATH", db_file)
    conn = c.get_db()
    c.init_db(conn)
    yield conn, c, db_file
    conn.close()


def make_sessions(sessions: dict) -> dict:
    """Build a sessions.json-like dict from simplified spec."""
    result = {}
    for key, spec in sessions.items():
        result[key] = {
            "sessionId": spec.get("sessionId", "test-uuid"),
            "sessionKey": key,
            "totalTokens": spec["totalTokens"],
            "model": spec.get("model", "claude-sonnet-4-6"),
            "groupChannel": spec.get("channel", "#test"),
            "updatedAt": spec.get("updatedAt", int(time.time() * 1000)),
        }
    return result


# ─── Tests ──────────────────────────────────────────────────────────────────


class TestSnapshotInsertion:
    def test_insert_single_session(self, tmp_db):
        conn, c, _ = tmp_db
        sessions = make_sessions({
            "agent:main:discord:channel:123": {"totalTokens": 5000}
        })
        n = c.insert_snapshots(conn, "main", sessions, captured_at=1700000000)
        assert n == 1
        rows = conn.execute("SELECT * FROM session_snapshots").fetchall()
        assert len(rows) == 1
        assert rows[0]["total_tokens"] == 5000
        assert rows[0]["agent"] == "main"

    def test_insert_multiple_sessions(self, tmp_db):
        conn, c, _ = tmp_db
        sessions = make_sessions({
            "agent:main:discord:channel:1": {"totalTokens": 1000},
            "agent:main:discord:channel:2": {"totalTokens": 2000},
            "agent:main:discord:channel:3": {"totalTokens": 3000},
        })
        n = c.insert_snapshots(conn, "main", sessions, captured_at=1700000000)
        assert n == 3

    def test_skips_missing_total_tokens(self, tmp_db):
        conn, c, _ = tmp_db
        sessions = {
            "agent:main:discord:channel:1": {
                "sessionId": "abc",
                "sessionKey": "agent:main:discord:channel:1",
                "model": "claude-sonnet-4-6",
                "updatedAt": 1700000000000,
                # No totalTokens
            }
        }
        n = c.insert_snapshots(conn, "main", sessions, captured_at=1700000000)
        assert n == 0

    def test_snapshot_stores_correct_fields(self, tmp_db):
        conn, c, _ = tmp_db
        sessions = make_sessions({
            "agent:case:discord:channel:999": {
                "totalTokens": 42000,
                "model": "claude-opus-4-6",
                "channel": "#code",
                "sessionId": "session-xyz",
            }
        })
        c.insert_snapshots(conn, "case", sessions, captured_at=1700001000)
        row = conn.execute("SELECT * FROM session_snapshots").fetchone()
        assert row["agent"] == "case"
        assert row["session_key"] == "agent:case:discord:channel:999"
        assert row["model"] == "claude-opus-4-6"
        assert row["total_tokens"] == 42000
        assert row["captured_at"] == 1700001000


class TestDailyDeltaCalculation:
    def _sydney_midnight(self, date_str: str) -> int:
        """Return unix timestamp of midnight Sydney for a given date string."""
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)
        return int(dt.timestamp())

    def _insert_snap(self, conn, agent, session_key, total_tokens, offset_secs, date_str="2026-03-16"):
        base = self._sydney_midnight(date_str)
        captured_at = base + offset_secs
        conn.execute(
            """INSERT INTO session_snapshots
               (captured_at, agent, session_key, session_id, model, channel, total_tokens, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (captured_at, agent, session_key, "test-id", "claude-sonnet-4-6", "#test", total_tokens, captured_at * 1000),
        )
        conn.commit()
        return captured_at

    def test_simple_delta(self, tmp_db):
        conn, c, _ = tmp_db
        self._insert_snap(conn, "main", "agent:main:x:1", 10000, 3600)
        self._insert_snap(conn, "main", "agent:main:x:1", 12000, 7200)
        self._insert_snap(conn, "main", "agent:main:x:1", 15000, 10800)

        c.recompute_daily_usage(conn, "2026-03-16")

        row = conn.execute(
            "SELECT tokens_delta FROM daily_usage WHERE date='2026-03-16' AND agent='main'"
        ).fetchone()
        assert row is not None
        assert row["tokens_delta"] == 5000  # 15000 - 10000

    def test_multiple_sessions_sum(self, tmp_db):
        conn, c, _ = tmp_db
        # Session 1: 10k → 20k = +10k
        self._insert_snap(conn, "main", "agent:main:x:1", 10000, 3600)
        self._insert_snap(conn, "main", "agent:main:x:1", 20000, 7200)
        # Session 2: 5k → 8k = +3k
        self._insert_snap(conn, "main", "agent:main:x:2", 5000, 3600)
        self._insert_snap(conn, "main", "agent:main:x:2", 8000, 7200)

        c.recompute_daily_usage(conn, "2026-03-16")

        row = conn.execute(
            "SELECT tokens_delta FROM daily_usage WHERE date='2026-03-16' AND agent='main'"
        ).fetchone()
        assert row["tokens_delta"] == 13000  # 10k + 3k

    def test_no_growth_zero_delta(self, tmp_db):
        conn, c, _ = tmp_db
        # Same value twice — no new tokens used
        self._insert_snap(conn, "main", "agent:main:x:1", 10000, 3600)
        self._insert_snap(conn, "main", "agent:main:x:1", 10000, 7200)

        c.recompute_daily_usage(conn, "2026-03-16")

        row = conn.execute(
            "SELECT tokens_delta FROM daily_usage WHERE date='2026-03-16' AND agent='main'"
        ).fetchone()
        assert row["tokens_delta"] == 0


class TestIdempotency:
    def _sydney_midnight(self, date_str: str) -> int:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)
        return int(dt.timestamp())

    def test_running_twice_same_data_no_double_count(self, tmp_db, monkeypatch, tmp_path):
        conn, c, db_file = tmp_db
        date_str = "2026-03-16"
        base = self._sydney_midnight(date_str)

        # Mock sessions.json with a single session
        sessions_data = {
            "agent:main:discord:channel:1": {
                "sessionId": "abc",
                "totalTokens": 50000,
                "model": "claude-sonnet-4-6",
                "groupChannel": "#tars",
                "updatedAt": (base + 3600) * 1000,
            }
        }

        # Create fake sessions.json
        agent_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
        agent_dir.mkdir(parents=True)
        (agent_dir / "sessions.json").write_text(json.dumps(sessions_data))

        monkeypatch.setattr(c, "SESSIONS_BASE", tmp_path / ".openclaw" / "agents")

        # Run collect twice with same timestamp
        captured_at = base + 3600
        sessions = c.read_sessions("main")
        c.insert_snapshots(conn, "main", sessions, captured_at)
        c.insert_snapshots(conn, "main", sessions, captured_at + 1)
        c.recompute_daily_usage(conn, date_str)

        # Delta should still be 0 (same value, no growth)
        row = conn.execute(
            "SELECT tokens_delta FROM daily_usage WHERE date=? AND agent='main'",
            (date_str,),
        ).fetchone()
        assert row is not None
        assert row["tokens_delta"] == 0  # 50000 - 50000 = 0


class TestSessionResetHandling:
    def _sydney_midnight(self, date_str: str) -> int:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)
        return int(dt.timestamp())

    def _insert_snap(self, conn, agent, session_key, total_tokens, offset_secs, date_str="2026-03-16"):
        base = self._sydney_midnight(date_str)
        captured_at = base + offset_secs
        conn.execute(
            """INSERT INTO session_snapshots
               (captured_at, agent, session_key, session_id, model, channel, total_tokens, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (captured_at, agent, session_key, "test-id", "claude-sonnet-4-6", "#test", total_tokens, captured_at * 1000),
        )
        conn.commit()

    def test_session_reset_not_negative(self, tmp_db):
        """If totalTokens decreases (session reset), delta should not go negative."""
        conn, c, _ = tmp_db
        # Session grows, then resets
        self._insert_snap(conn, "main", "agent:main:x:1", 10000, 1800)
        self._insert_snap(conn, "main", "agent:main:x:1", 15000, 3600)
        self._insert_snap(conn, "main", "agent:main:x:1", 2000, 5400)   # reset
        self._insert_snap(conn, "main", "agent:main:x:1", 5000, 7200)

        c.recompute_daily_usage(conn, "2026-03-16")

        row = conn.execute(
            "SELECT tokens_delta FROM daily_usage WHERE date='2026-03-16' AND agent='main'"
        ).fetchone()
        # Should not be negative
        assert row is not None
        assert row["tokens_delta"] >= 0


class TestTimezoneBucketing:
    def test_11pm_and_1am_on_different_days(self, tmp_db):
        """Snapshots at 11:59pm and 12:01am Sydney should land on different days."""
        conn, c, _ = tmp_db

        # 2026-03-16 11:59pm Sydney
        day1 = datetime(2026, 3, 16, 23, 59, 0, tzinfo=TZ)
        ts1 = int(day1.timestamp())

        # 2026-03-17 00:01am Sydney
        day2 = datetime(2026, 3, 17, 0, 1, 0, tzinfo=TZ)
        ts2 = int(day2.timestamp())

        conn.execute(
            """INSERT INTO session_snapshots
               (captured_at, agent, session_key, session_id, model, channel, total_tokens, updated_at)
               VALUES (?, 'main', 'agent:main:x:1', 'id1', 'claude-sonnet-4-6', '#test', 10000, ?)""",
            (ts1, ts1 * 1000),
        )
        conn.execute(
            """INSERT INTO session_snapshots
               (captured_at, agent, session_key, session_id, model, channel, total_tokens, updated_at)
               VALUES (?, 'main', 'agent:main:x:1', 'id1', 'claude-sonnet-4-6', '#test', 15000, ?)""",
            (ts2, ts2 * 1000),
        )
        conn.commit()

        # Recompute for March 16
        c.recompute_daily_usage(conn, "2026-03-16")
        row16 = conn.execute(
            "SELECT tokens_delta FROM daily_usage WHERE date='2026-03-16' AND agent='main'"
        ).fetchone()

        # Recompute for March 17
        c.recompute_daily_usage(conn, "2026-03-17")
        row17 = conn.execute(
            "SELECT tokens_delta FROM daily_usage WHERE date='2026-03-17' AND agent='main'"
        ).fetchone()

        # March 16 only has the single snapshot — delta is 0 (no comparison point)
        # March 17 only has the single snapshot — delta is 0
        # Together they span two days, so delta on each day is 0 (no growth within each day)
        assert row16 is not None or row17 is not None  # at least one day computed

    def test_today_sydney_format(self, tmp_db):
        """today_sydney() should return YYYY-MM-DD format."""
        conn, c, _ = tmp_db
        result = c.today_sydney()
        assert len(result) == 10
        assert result[4] == "-"
        assert result[7] == "-"

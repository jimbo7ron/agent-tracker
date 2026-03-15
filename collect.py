"""
collect.py — Hourly token usage collector for Agent Token Tracker v2.

Reads ~/.openclaw/agents/{agent}/sessions/sessions.json for each agent,
stores snapshots in SQLite, and recomputes daily_usage deltas.

Run: 0 * * * * /usr/bin/python3 /Users/tars/repos/agent-tracker/collect.py
"""

import json
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

AGENTS = ["main", "case", "kipp", "brand"]
SESSIONS_BASE = Path.home() / ".openclaw" / "agents"
DB_PATH = Path(__file__).parent / "usage.db"
LOG_DIR = Path.home() / ".openclaw" / "logs"
TZ = ZoneInfo("Australia/Sydney")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS session_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at INTEGER NOT NULL,
            agent TEXT NOT NULL,
            session_key TEXT NOT NULL,
            session_id TEXT NOT NULL,
            model TEXT,
            channel TEXT,
            total_tokens INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_agent_date
            ON session_snapshots(agent, captured_at);

        CREATE INDEX IF NOT EXISTS idx_snapshots_session
            ON session_snapshots(session_key, captured_at);

        CREATE TABLE IF NOT EXISTS daily_usage (
            date TEXT NOT NULL,
            agent TEXT NOT NULL,
            model TEXT NOT NULL,
            tokens_delta INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (date, agent, model)
        );
    """)
    conn.commit()


def read_sessions(agent: str) -> dict:
    path = SESSIONS_BASE / agent / "sessions" / "sessions.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[collect] ERROR reading {path}: {e}", file=sys.stderr)
        return {}


def insert_snapshots(conn: sqlite3.Connection, agent: str, sessions: dict, captured_at: int) -> int:
    """Insert snapshot rows for all sessions. Returns count inserted."""
    count = 0
    for session_key, s in sessions.items():
        total_tokens = s.get("totalTokens")
        session_id = s.get("sessionId", "")
        model = s.get("model", "unknown")
        channel = s.get("groupChannel") or s.get("channel", "")
        updated_at = s.get("updatedAt", 0)

        if total_tokens is None:
            continue

        conn.execute(
            """
            INSERT INTO session_snapshots
                (captured_at, agent, session_key, session_id, model, channel, total_tokens, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (captured_at, agent, session_key, session_id, model, channel, total_tokens, updated_at),
        )
        count += 1

    conn.commit()
    return count


def day_bounds_utc(date_str: str) -> tuple[int, int]:
    """Return (start_unix, end_unix) for a YYYY-MM-DD date in Sydney timezone."""
    local_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)
    local_end = local_start + timedelta(days=1)
    return int(local_start.timestamp()), int(local_end.timestamp())


def recompute_daily_usage(conn: sqlite3.Connection, date_str: str) -> None:
    """
    Recompute daily_usage for the given date (YYYY-MM-DD).

    Delta per session = MAX(total_tokens) - MIN(total_tokens) within the day.
    If total_tokens decreases (session reset), treat that snapshot's value as
    its own contribution (effectively MIN resets to 0 for that new sequence).
    We handle this with per-session ordered snapshot analysis.
    """
    start_ts, end_ts = day_bounds_utc(date_str)
    now_ts = int(time.time())

    # Fetch all snapshots for this day, ordered by session_key and captured_at
    rows = conn.execute(
        """
        SELECT agent, session_key, model, total_tokens, captured_at
        FROM session_snapshots
        WHERE captured_at >= ? AND captured_at < ?
        ORDER BY session_key, captured_at
        """,
        (start_ts, end_ts),
    ).fetchall()

    # Accumulate deltas: {(agent, model): delta}
    deltas: dict[tuple[str, str], int] = {}

    # Group by (session_key, agent, model) — model can change per session key
    # Use the model from the most recent snapshot for that session
    session_snapshots: dict[str, list] = defaultdict(list)
    session_meta: dict[str, tuple[str, str]] = {}  # session_key -> (agent, model)

    for row in rows:
        key = row["session_key"]
        session_snapshots[key].append(row["total_tokens"])
        session_meta[key] = (row["agent"], row["model"])

    for session_key, token_list in session_snapshots.items():
        agent, model = session_meta[session_key]
        agg_key = (agent, model)

        # Compute delta handling multiple resets (decreases).
        # Split into monotonically non-decreasing segments at each reset boundary,
        # accumulate (max - min) = (last - first) for each segment.
        delta = 0
        segment_start = token_list[0]
        prev = token_list[0]
        for curr in token_list[1:]:
            if curr < prev:
                # Reset: close out this segment and start a new one
                delta += prev - segment_start
                segment_start = curr
            prev = curr
        # Close final segment
        delta += prev - segment_start

        deltas[agg_key] = deltas.get(agg_key, 0) + delta

    # Upsert into daily_usage
    for (agent, model), tokens_delta in deltas.items():
        conn.execute(
            """
            INSERT INTO daily_usage (date, agent, model, tokens_delta, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date, agent, model) DO UPDATE SET
                tokens_delta = excluded.tokens_delta,
                updated_at = excluded.updated_at
            """,
            (date_str, agent, model, tokens_delta, now_ts),
        )

    conn.commit()


def today_sydney() -> str:
    """Return today's date string in Australia/Sydney timezone."""
    return datetime.now(tz=TZ).strftime("%Y-%m-%d")


def collect() -> None:
    # Ensure log dir exists so crontab redirect doesn't fail silently on first run
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    conn = get_db()
    init_db(conn)

    captured_at = int(time.time())
    total_sessions = 0

    for agent in AGENTS:
        sessions = read_sessions(agent)
        if not sessions:
            continue
        n = insert_snapshots(conn, agent, sessions, captured_at)
        total_sessions += n

    date_str = today_sydney()
    recompute_daily_usage(conn, date_str)

    conn.close()


if __name__ == "__main__":
    collect()

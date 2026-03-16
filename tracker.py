"""
tracker.py — Discord-friendly token usage reporter for Agent Token Tracker v2.

Reads from daily_usage table in SQLite and outputs a bar chart with
token counts and estimated costs.

Run: Called by Tesseract "Agent Token Report" job at 8am Sydney time.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path(__file__).parent / "usage.db"
TZ = ZoneInfo("Australia/Sydney")

AGENTS = ["main", "case", "kipp", "brand"]
AGENT_DISPLAY = {
    "main": "TARS",
    "case": "CASE",
    "kipp": "KIPP",
    "brand": "Brand",
}

# Blended cost per 1k tokens (input+cache+output mixed)
PRICE_PER_1K: dict[str, float] = {
    "claude-sonnet-4-6": 0.005,
    "claude-opus-4-6": 0.025,
    "claude-haiku-4-5": 0.001,
    "google/gemini-2.5-flash": 0.001,
    "google/gemini-3.1-pro-preview": 0.003,
    "default": 0.005,
}

BAR_MAX_WIDTH = 12


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def cost_for_tokens(tokens: int, model: str) -> float:
    rate = PRICE_PER_1K.get(model, PRICE_PER_1K["default"])
    return (tokens / 1000) * rate


def make_bar(value: int, max_value: int, width: int = BAR_MAX_WIDTH) -> str:
    if max_value <= 0:
        return ""
    filled = round((value / max_value) * width)
    return "█" * filled


def format_tokens(tokens: int) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.0f}k"
    return str(tokens)


def get_today_usage(conn: sqlite3.Connection, date_str: str) -> dict[str, dict]:
    """Returns {agent: {model: tokens, ...}} for today."""
    rows = conn.execute(
        "SELECT agent, model, tokens_delta FROM daily_usage WHERE date = ?",
        (date_str,),
    ).fetchall()

    result: dict[str, dict] = {a: {} for a in AGENTS}
    for row in rows:
        agent = row["agent"]
        if agent in result:
            result[agent][row["model"]] = result[agent].get(row["model"], 0) + row["tokens_delta"]
    return result


def get_week_usage(conn: sqlite3.Connection, dates: list[str]) -> dict[str, dict[str, int]]:
    """Returns {agent: {date: tokens}} for the given date list."""
    placeholders = ",".join("?" * len(dates))
    rows = conn.execute(
        f"SELECT agent, date, SUM(tokens_delta) as total FROM daily_usage WHERE date IN ({placeholders}) GROUP BY agent, date",
        dates,
    ).fetchall()

    result: dict[str, dict[str, int]] = {a: {d: 0 for d in dates} for a in AGENTS}
    for row in rows:
        agent = row["agent"]
        if agent in result:
            result[agent][row["date"]] = row["total"]
    return result


def report() -> str:
    if not DB_PATH.exists():
        return "📊 No token data yet today."

    conn = get_db()

    now = datetime.now(tz=TZ)
    today_str = now.strftime("%Y-%m-%d")
    today_display = now.strftime("%a %d %b %Y")

    # 7-day window: today + 6 prior days
    dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    day_labels = [(now - timedelta(days=i)).strftime("%a") for i in range(6, -1, -1)]

    today_data = get_today_usage(conn, today_str)
    week_data = get_week_usage(conn, dates)
    conn.close()

    # --- Today section ---
    today_totals: dict[str, int] = {}
    today_costs: dict[str, float] = {}
    for agent in AGENTS:
        models = today_data.get(agent, {})
        total = sum(models.values())
        cost = sum(cost_for_tokens(t, m) for m, t in models.items())
        today_totals[agent] = total
        today_costs[agent] = cost

    grand_total = sum(today_totals.values())
    grand_cost = sum(today_costs.values())

    if grand_total == 0:
        return "📊 No token data yet today."

    max_tokens = max(today_totals.values()) if today_totals else 1

    lines = [f"📊 Agent Token Report — {today_display}", "", "Today so far:"]

    # Max display name length for padding
    name_width = max(len(AGENT_DISPLAY[a]) for a in AGENTS)

    for agent in AGENTS:
        tokens = today_totals[agent]
        cost = today_costs[agent]
        name = AGENT_DISPLAY[agent].ljust(name_width)
        bar = make_bar(tokens, max_tokens).ljust(BAR_MAX_WIDTH)
        token_str = f"{tokens:,}"
        lines.append(f"  {name}  {bar}  {token_str:<8}  tokens  (~${cost:.2f})")

    lines.append(f"  {'─' * (name_width + 2 + BAR_MAX_WIDTH + 32)}")
    lines.append(f"  {'Total'.ljust(name_width + BAR_MAX_WIDTH + 4)}  {grand_total:,}  tokens  (~${grand_cost:.2f})")

    # --- 7-day rolling section ---
    lines.append("")
    lines.append("7-day rolling (per agent):")

    for agent in AGENTS:
        name = AGENT_DISPLAY[agent]
        day_vals = week_data.get(agent, {})
        parts = []
        for date_str, label in zip(dates, day_labels):
            val = day_vals.get(date_str, 0)
            parts.append(f"  {label} {format_tokens(val):>5}")
        lines.append(f"  {name + ':' :<6}" + "".join(parts))

    return "\n".join(lines)


def post_to_discord(message: str) -> bool:
    """Post a message to the #tars Discord channel via bot token."""
    import json
    import urllib.request

    secrets_path = Path.home() / ".openclaw" / "workspace" / "secrets.json"
    try:
        secrets = json.loads(secrets_path.read_text())
        token = secrets["discord_bot_token"]
        channel_id = "1467467217909321823"  # #tars
    except (KeyError, FileNotFoundError, json.JSONDecodeError):
        return False

    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    data = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "AgentTracker/2.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception:
        return False


if __name__ == "__main__":
    output = report()
    if output == "📊 No token data yet today.":
        # Nothing to report — stay silent
        pass
    else:
        # Post to Discord (Tesseract shellCommand jobs don't forward stdout)
        if not post_to_discord(output):
            # Fallback: print to stdout so it ends up in logs
            print(output)

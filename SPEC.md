# Agent Token Tracker v2 — Spec

## Overview

A lightweight Python tool that tracks token usage across all 4 OpenClaw agents (TARS, CASE, KIPP, Brand) by polling OpenClaw's `sessions.json` files hourly. Results are stored in SQLite and reported daily to Discord.

**Goal:** Give Jimbo visibility into how many tokens (and roughly how much money) each agent is consuming per day, with a historical trend.

**Why this matters:** We're on Anthropic's Max plan (~$200/mo). Understanding which agent/channel is burning the most tokens helps optimise costs and spot runaway jobs.

---

## Data Source

**File:** `~/.openclaw/agents/{agent}/sessions/sessions.json`

**Available for agents:** `main` (TARS), `case` (CASE), `kipp` (KIPP), `brand` (Brand)

**Structure:** A JSON object where each key is a `sessionKey` (e.g. `agent:main:discord:channel:1467467217909321823`) and each value is a session object containing:

```json
{
  "sessionId": "91cb1025-...",
  "sessionKey": "agent:main:discord:channel:1467467217909321823",
  "updatedAt": 1773611880899,
  "model": "claude-sonnet-4-6",
  "totalTokens": 281265,
  "inputTokens": 10,
  "outputTokens": 1250,
  "cacheRead": 280842,
  "cacheWrite": 422,
  "contextTokens": 1000000,
  "groupChannel": "#tars",
  "displayName": "discord:...",
  "channel": "discord"
}
```

**Key insight:** `totalTokens` is a **cumulative lifetime total** that only increases. To get daily usage, snapshot it hourly and calculate deltas.

---

## Architecture

### Files

```
~/repos/agent-tracker/
├── collect.py        # Hourly collector — reads sessions.json, writes snapshots to DB
├── tracker.py        # Reporter — reads DB, formats bar chart for Discord
├── usage.db          # SQLite database (DO NOT gitignore — this is our data store)
├── SPEC.md           # This file
├── README.md         # Usage and setup
└── tests/
    ├── test_collect.py
    └── test_tracker.py
```

### Database Schema

```sql
-- Raw hourly snapshots of sessions.json state
CREATE TABLE session_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at INTEGER NOT NULL,      -- Unix timestamp (seconds) when we captured this
    agent TEXT NOT NULL,               -- main, case, kipp, brand
    session_key TEXT NOT NULL,         -- full session key string
    session_id TEXT NOT NULL,          -- UUID of the session
    model TEXT,                        -- e.g. claude-sonnet-4-6
    channel TEXT,                      -- e.g. #tars
    total_tokens INTEGER NOT NULL,     -- cumulative total at snapshot time
    updated_at INTEGER NOT NULL        -- session's own updatedAt (ms)
);

CREATE INDEX idx_snapshots_agent_date ON session_snapshots(agent, captured_at);
CREATE INDEX idx_snapshots_session ON session_snapshots(session_key, captured_at);

-- Daily aggregates (computed from snapshots, upserted each hour)
CREATE TABLE daily_usage (
    date TEXT NOT NULL,                -- YYYY-MM-DD in Australia/Sydney timezone
    agent TEXT NOT NULL,               -- main, case, kipp, brand
    model TEXT NOT NULL,               -- model identifier
    tokens_delta INTEGER NOT NULL,     -- tokens consumed that day (delta, not cumulative)
    updated_at INTEGER NOT NULL,       -- when this row was last updated (Unix seconds)
    PRIMARY KEY (date, agent, model)
);
```

---

## collect.py

**Purpose:** Read current state from all agents' `sessions.json`, store snapshots, recompute daily totals.

**Run:** Every hour via crontab.

**Logic:**

1. For each agent in `[main, case, kipp, brand]`:
   - Read `~/.openclaw/agents/{agent}/sessions/sessions.json`
   - For each session entry, insert a snapshot row with current timestamp and `totalTokens`

2. Recompute today's `daily_usage`:
   - Group by `(agent, model)` for today
   - Delta = `MAX(total_tokens) - MIN(total_tokens)` per `(session_key, date)`, then sum across all sessions
   - If `totalTokens` decreases between snapshots (session reset), treat that interval's contribution as the new `total_tokens` value (not negative)
   - Upsert into `daily_usage`

3. Exit silently with code 0. Only write to stderr on real errors.

**Idempotent:** Running multiple times in the same hour is safe — each run inserts a new snapshot but daily delta recalculation is based on MAX-MIN, so duplicates don't inflate counts.

**Timezone:** Use `Australia/Sydney` for all date bucketing.

---

## tracker.py

**Purpose:** Read from `daily_usage` and generate a formatted report for Discord.

**Run:** Called by the Tesseract "Agent Token Report" job at 8am Sydney time.

**Output format (Discord-friendly, no tables):**

```
📊 Agent Token Report — Mon 16 Mar 2026

Today so far:
  TARS  ████████████  42,300  tokens  (~$0.21)
  CASE  ████          12,100  tokens  (~$0.06)
  KIPP  ██████        18,900  tokens  (~$0.09)
  Brand █             2,400   tokens  (~$0.01)
  ─────────────────────────────────────────
  Total               75,700  tokens  (~$0.37)

7-day rolling (per agent):
  TARS:  Mon 245k  Tue 312k  Wed 189k  Thu 421k  Fri 198k  Sat 87k  Sun 103k
  CASE:  Mon  42k  Tue  65k  Wed  31k  Thu  89k  Fri  45k  Sat  12k  Sun  28k
  KIPP:  Mon  98k  Tue 124k  Wed  67k  Thu 143k  Fri  89k  Sat  34k  Sun  51k
  Brand: Mon  15k  Tue  22k  Wed  18k  Thu  31k  Fri  19k  Sat   8k  Sun  12k
```

**Pricing table** (hardcoded, update when models change):
```python
PRICE_PER_1K = {
    "claude-sonnet-4-6":  0.005,   # $3/MTok input + $15/MTok output, blended ~$5/MTok = $0.005/1k
    "claude-opus-4-6":    0.025,   # ~$25/MTok blended
    "claude-haiku-4-5":   0.001,
    "google/gemini-2.5-flash": 0.001,
    "google/gemini-3.1-pro-preview": 0.003,
    "default":            0.005,
}
```

**If no data:** Print `📊 No token data yet today.`

---

## Crontab Integration

The existing Tesseract "Agent Token Report" job fires at 8am and calls `tracker.py`. That stays as-is.

Add a new system crontab entry (NOT Tesseract — this is silent background work):
```
0 * * * * /usr/bin/python3 /Users/tars/repos/agent-tracker/collect.py >> /Users/tars/.openclaw/logs/agent-tracker.log 2>&1
```

This runs collect.py at the top of every hour. The 8am Tesseract job then reads from the DB.

---

## Tests

### test_collect.py
- Test snapshot insertion with mocked `sessions.json`
- Test daily delta calculation with multiple snapshots
- Test that running twice with same data doesn't double-count
- Test session reset handling (total_tokens decreases)
- Test timezone bucketing (a run at 11:59pm and 12:01am land on different days)

### test_tracker.py
- Test bar chart formatting with known data
- Test pricing calculation
- Test "no data" case
- Test 7-day history formatting

---

## README.md Content

Should cover:
- What it does
- Setup (just add crontab entry)
- How to read the report
- How to backfill (if DB is wiped, data is lost — document this limitation)
- How the delta calculation works (so future devs understand it's not raw cumulative)
- SQLite file is intentionally committed to the repo — it IS the data store

---

## Notes for CASE

- Keep it simple — no external dependencies beyond stdlib + sqlite3
- The `usage.db` file is committed to git (it's the data store, not generated output)
- The `.gitignore` should NOT exclude `*.db` for this specific file — override if needed
- Python 3.11+ only (that's what's installed)
- Respect the existing `tracker.py` format as much as possible — Jimbo is used to the output
- The existing `parser.py` reads from cron runs which no longer exist — it can be deleted or left as legacy
- All paths should use `pathlib.Path` and `Path.home()` for portability
- No argparse needed — `collect.py` always collects, `tracker.py` always reports

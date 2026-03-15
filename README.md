# Agent Token Tracker v2

Lightweight Python tool that tracks token usage across all 4 OpenClaw agents (TARS, CASE, KIPP, Brand) by polling OpenClaw's `sessions.json` files hourly. Results are stored in SQLite and reported daily to Discord.

## What It Does

- **Collects** hourly snapshots of each agent's session token counts
- **Computes** daily deltas (tokens consumed that day, not cumulative lifetime totals)
- **Reports** a Discord-friendly bar chart with per-agent token counts and estimated costs
- **Stores** everything in `usage.db` (SQLite, committed to the repo — it's the data store)

## Files

```
collect.py    # Hourly collector — reads sessions.json, writes to SQLite
tracker.py    # Reporter — reads SQLite, formats output for Discord
usage.db      # SQLite database (committed — do NOT gitignore)
tests/        # Test suite
```

## Setup

Just add the crontab entry. No dependencies beyond Python 3.11+ stdlib.

```bash
crontab -e
```

Add:
```
0 * * * * /usr/bin/python3 /Users/tars/repos/agent-tracker/collect.py >> /Users/tars/.openclaw/logs/agent-tracker.log 2>&1
```

The 8am Tesseract "Agent Token Report" job calls `tracker.py` automatically.

## How the Delta Calculation Works

OpenClaw's `sessions.json` stores a **cumulative lifetime `totalTokens`** per session — it only ever increases. To get daily usage, we:

1. Snapshot `totalTokens` for every session every hour
2. For each day, compute `MAX(total_tokens) - MIN(total_tokens)` per session
3. Sum across all sessions for the day

**Session resets:** If `totalTokens` decreases between snapshots (e.g. a session is deleted and recreated), we treat the decrease as a reset. The prior peak counts as its contribution, and the new lower value becomes the new baseline.

**Result:** `daily_usage.tokens_delta` = tokens consumed by that agent+model that calendar day (Australia/Sydney timezone).

## Reading the Report

```
📊 Agent Token Report — Mon 16 Mar 2026

Today so far:
  TARS  ████████████  42,300  tokens  (~$0.21)
  CASE  ████          12,100  tokens  (~$0.06)
  KIPP  ██████        18,900  tokens  (~$0.09)
  Brand █             2,400   tokens  (~$0.01)
  ──────────────────────────────────────────────────
  Total               75,700  tokens  (~$0.37)

7-day rolling (per agent):
  TARS:    Mon  42k  Tue 312k  Wed 189k  Thu 421k  Fri 198k  Sat  87k  Sun 103k
  ...
```

Costs are blended estimates based on model pricing. See `PRICE_PER_1K` in `tracker.py`.

## Backfill Limitation

**If `usage.db` is deleted or wiped, historical data is lost.** There is no backfill mechanism — we can only compute deltas from the moment snapshots start being collected. The DB is committed to git precisely to avoid this: it IS the data store.

If you need to rebuild from scratch, daily totals will start from zero and accumulate going forward. Historical trends will be blank until enough snapshots are collected.

## Running Manually

```bash
# Collect a snapshot now
python3 collect.py

# Generate report
python3 tracker.py

# Run tests
python3 -m pytest tests/ -v
```

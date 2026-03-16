# Tesseract Smart Jobs — Coding Spec

**Date:** 2026-03-16
**Author:** TARS
**Assignee:** CASE
**Repo:** `jimbo7ron/openclaw-config` (Tesseract schedules)
**Status:** Ready

---

## Goal

Six Tesseract jobs currently fire an agent turn unconditionally, even when there's nothing to do. This burns tokens and creates noise. Make each job smart: either convert to a `shellCommand` pre-check that only posts when action is needed, or reduce agent involvement to a lightweight check.

---

## Background

Tesseract fires jobs by posting a message to a Discord channel, which an OpenClaw agent picks up and executes. The cost per fire is ~1,000–5,000 tokens minimum just for the agent to wake up, read context, and decide "nothing to do."

These 6 jobs should not involve the LLM unless there is actually something to act on.

---

## Jobs to Fix

### 1. `[789e1c26]` OpenClaw Update Check
**Current:** Runs `openclaw update --yes` — blindly upgrades.
**New behaviour:**
- Check if an update is available: `openclaw update --check` (or equivalent dry-run flag)
- If no update: `NO_REPLY`
- If update available: Post to #tars with:
  - Current version
  - New version available
  - Changelog / release notes (if accessible via CLI or GitHub)
  - Message: "Update available — run `openclaw update --yes` manually to install."
- **Do NOT install automatically**
- Implement as `shellCommand` in Tesseract if possible, otherwise as a lightweight agent-turn with strict instructions.

### 2. `[3ffaf07b]` Nightly Config Backup
**Current:** Always fires an agent turn to run `~/repos/openclaw-config/backup.sh`.
**New behaviour:**
- `shellCommand`: Run `backup.sh` directly. It already handles "nothing changed" gracefully (no commit if no diff).
- Remove the agent turn entirely — this should be a pure shell job.
- Log output to `~/.openclaw/logs/backup.log`
- Only post to Discord on error (non-zero exit code)

### 3. `[0a7ac97e]` Agent Token Report
**Current:** Always fires agent turn, often returns "no data".
**New behaviour:**
- `shellCommand` pre-check: `python3 ~/repos/agent-tracker/collect.py && python3 ~/repos/agent-tracker/tracker.py`
- `tracker.py` should exit 0 with no output if no data (agent stays silent)
- `tracker.py` should print the report to stdout if data exists — Tesseract posts the output
- No agent turn needed at all once token tracker v2 is built

### 4. `[eb3e59f2]` Check PRs (Main)
**Current:** Always fires agent turn.
**New behaviour:**
- `shellCommand` pre-check: `gh pr list --repo jimbo7ron/nexus --state open --json number,title,url`
- If no open PRs: exit silently
- If open PRs with unresolved review comments: post to #tars with PR links
- Script: `~/repos/tesseract/check_pr_comments.py` already exists — verify it works and wire it as `shellCommand`

### 5. `[eb3e59f2 / 257183d6]` PR Review + Email — Only Alert When New
**Current:** Check Email job fires every 15 min regardless.
**Already fixed:** `check_email.py` uses dedup via `processed_emails.json`. Verify this is working correctly.
**If not working:** Ensure `processed_emails.json` is being written after each check. Items already in the file should never trigger a Discord post.

### 6. `[257183d6]` PR Comments — Only Alert When New
**Current:** `check_pr_comments.py` may re-alert on already-seen comments.
**New behaviour:** Same dedup pattern as email — use `processed_pr_comments.json` in `~/.openclaw/logs/`. Already exists but verify it's working.

---

## Technical Implementation

### Tesseract shellCommand format
```json
{
  "name": "Job Name",
  "cronExpr": "0 4 * * *",
  "shellCommand": "/usr/bin/python3 /Users/tars/repos/tesseract/script.py",
  "enabled": true
}
```
Note: `shellCommand` jobs do NOT post to Discord automatically. The script must post via Discord API directly if needed (use `check_email.py` as reference for the pattern).

### OpenClaw update check
Investigate what `openclaw update --help` or `openclaw update --check` / `--dry-run` exposes. If no dry-run flag exists, check the GitHub releases API for `jimbo7ron/openclaw` or the npm registry for `openclaw` package version vs installed version.

---

## Files to Modify
- `~/repos/tesseract/schedules.json` — update job definitions
- `~/repos/tesseract/check_pr_comments.py` — verify dedup works
- `~/repos/tesseract/check_email.py` — verify dedup works
- New script: `~/repos/tesseract/check_openclaw_update.py` (for job 1)

---

## Definition of Done
- [ ] All 6 jobs updated in `schedules.json`
- [ ] `check_openclaw_update.py` written and tested
- [ ] `check_pr_comments.py` dedup verified
- [ ] `check_email.py` dedup verified
- [ ] No agent turn fires when there's nothing to do (verified by watching logs for 24h)
- [ ] Committed to `jimbo7ron/openclaw-config` or relevant repo
- [ ] Summary posted to #case

---

## Out of Scope
- Automatic OpenClaw updates (manual only)
- Changing the email check frequency

---

## Open Questions
- Does `openclaw update` have a `--check` or `--dry-run` flag? CASE to investigate.

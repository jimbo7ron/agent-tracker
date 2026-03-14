import sqlite3
import json
import os
import glob
import sys
import datetime

# DB setup
db_path = os.path.expanduser("~/repos/agent-tracker/usage.db")

def init_db():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS usage
                 (timestamp DATETIME, agent TEXT, model TEXT, tokens_in INTEGER, tokens_out INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS processed_files
                 (filename TEXT PRIMARY KEY)''')
    conn.commit()
    conn.close()

def is_file_processed(filename):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT 1 FROM processed_files WHERE filename = ?", (filename,))
    res = c.fetchone()
    conn.close()
    return res is not None

def mark_file_processed(filename):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO processed_files VALUES (?)", (filename,))
    conn.commit()
    conn.close()

def log_usage(agent, model, tokens_in, tokens_out, timestamp):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("INSERT INTO usage VALUES (?, ?, ?, ?, ?)",
              (timestamp, agent, model, tokens_in, tokens_out))
    conn.commit()
    conn.close()

def parse_cron_runs():
    """Parse legacy cron run files (older format)."""
    runs_dir = os.path.expanduser("~/.openclaw/cron/runs/")
    files = glob.glob(os.path.join(runs_dir, "*.jsonl"))

    for filepath in files:
        filename = os.path.basename(filepath)
        if is_file_processed(filename):
            continue

        print(f"Processing cron run {filename}...")
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        if "usage" not in data:
                            continue

                        session_key = data.get("sessionKey", "")
                        parts = session_key.split(":")
                        agent = parts[1] if len(parts) > 1 else data.get("agentId", "main")

                        model = data.get("model", "unknown")
                        tokens_in = data["usage"].get("input_tokens", 0)
                        tokens_out = data["usage"].get("output_tokens", 0)
                        ts = data.get("ts")
                        if ts:
                            timestamp = datetime.datetime.fromtimestamp(ts / 1000.0).isoformat()
                        else:
                            continue

                        log_usage(agent, model, tokens_in, tokens_out, timestamp)
                    except Exception as e:
                        print(f"  Error parsing line: {e}")

            mark_file_processed(filename)
            print(f"  Done.")
        except Exception as e:
            print(f"Error processing {filename}: {e}")

def parse_session_files():
    """Parse agent session files (current format — Tesseract-driven turns)."""
    agents_dir = os.path.expanduser("~/.openclaw/agents/")
    # Find all session files across all agents
    files = glob.glob(os.path.join(agents_dir, "*/sessions/*.jsonl"))

    for filepath in files:
        # Use a stable key: path relative to agents_dir
        rel_path = os.path.relpath(filepath, agents_dir)
        file_key = f"session:{rel_path}"

        if is_file_processed(file_key):
            continue

        # Extract agent name from path: agents/<agent>/sessions/<file>.jsonl
        parts = rel_path.split(os.sep)
        agent = parts[0] if parts else "unknown"

        try:
            with open(filepath, 'r') as f:
                content = f.read()

            lines = content.strip().split('\n')
            found_usage = False

            for line in lines:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)

                    # Only process assistant messages with usage data
                    if data.get("type") != "message":
                        continue
                    msg = data.get("message", {})
                    if msg.get("role") != "assistant":
                        continue
                    usage = msg.get("usage", {})
                    if not usage:
                        continue

                    tokens_in = usage.get("input", 0) or 0
                    tokens_out = usage.get("output", 0) or 0
                    total = usage.get("totalTokens", tokens_in + tokens_out)

                    # Skip zero-token delivery mirror messages
                    if total == 0:
                        continue

                    model = msg.get("model", "unknown")
                    # Skip internal delivery mirrors
                    if model in ("delivery-mirror", "unknown") or not model:
                        continue

                    # Timestamp from outer envelope
                    ts_str = data.get("timestamp")
                    if not ts_str:
                        continue
                    timestamp = ts_str  # Already ISO format

                    log_usage(agent, model, tokens_in, tokens_out, timestamp)
                    found_usage = True

                except Exception as e:
                    pass  # Skip malformed lines silently

            if found_usage:
                print(f"Processed session: {rel_path}")

            # Mark as processed regardless (don't reprocess next run)
            mark_file_processed(file_key)

        except Exception as e:
            print(f"Error processing {filepath}: {e}")

def parse_runs():
    init_db()
    parse_cron_runs()
    parse_session_files()

if __name__ == "__main__":
    parse_runs()

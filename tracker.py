import sqlite3
import datetime
import sys
import json
import os
import glob

# DB setup
db_path = os.path.expanduser("~/repos/agent-tracker/usage.db")
os.makedirs(os.path.dirname(db_path), exist_ok=True)

def init_db():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS usage
                 (timestamp DATETIME, agent TEXT, model TEXT, tokens_in INTEGER, tokens_out INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS processed_files
                 (filename TEXT PRIMARY KEY)''')
    conn.commit()
    conn.close()

def log_usage(agent, model, tokens_in, tokens_out, timestamp):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("INSERT INTO usage VALUES (?, ?, ?, ?, ?)",
              (timestamp, agent, model, tokens_in, tokens_out))
    conn.commit()
    conn.close()

def mark_file_processed(filename):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO processed_files VALUES (?)", (filename,))
    conn.commit()
    conn.close()

def is_file_processed(filename):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT 1 FROM processed_files WHERE filename = ?", (filename,))
    res = c.fetchone()
    conn.close()
    return res is not None

def parse_runs():
    init_db()
    # Capture cron runs AND all agent sessions
    search_paths = [
        os.path.expanduser("~/.openclaw/cron/runs/*.jsonl"),
        os.path.expanduser("~/.openclaw/agents/*/sessions/*.jsonl")
    ]
    
    files = []
    for path in search_paths:
        files.extend(glob.glob(path))
    
    for filepath in files:
        filename = os.path.basename(filepath)
        # Unique identifier by full path to avoid collision
        file_id = filepath
        if is_file_processed(file_id):
            continue
            
        print(f"Processing {filename}...")
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        # Look for usage in both cron and session message formats
                        usage = data.get("usage")
                        if not usage and "message" in data:
                            usage = data["message"].get("usage")
                        
                        if not usage:
                            continue
                            
                        # Extract Agent
                        # For cron: "agentId": "kipp"
                        # For sessions: session_key or agent subfolder in path
                        agent = data.get("agentId")
                        if not agent:
                            path_parts = filepath.split('/')
                            if 'agents' in path_parts:
                                agent = path_parts[path_parts.index('agents') + 1]
                        if not agent:
                            agent = "main"
                        
                        # Extract Model
                        model = data.get("model")
                        if not model and "message" in data:
                            model = data["message"].get("model")
                        if not model:
                            model = "unknown"
                        
                        # Extract tokens
                        tokens_in = usage.get("input_tokens", usage.get("input", 0))
                        tokens_out = usage.get("output_tokens", usage.get("output", 0))
                        
                        # Extract timestamp
                        ts = data.get("ts") or data.get("timestamp")
                        if isinstance(ts, (int, float)):
                             timestamp = datetime.datetime.fromtimestamp(ts / 1000.0).isoformat()
                        elif isinstance(ts, str):
                            timestamp = ts
                        else:
                            timestamp = datetime.datetime.now().isoformat()
                        
                        log_usage(agent, model, tokens_in, tokens_out, timestamp)
                    except Exception as e:
                        pass
            
            mark_file_processed(file_id)
            print(f"Done processing {filename}.")
        except Exception as e:
            print(f"Error processing file {filename}: {e}")

def print_chart():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    today = datetime.date.today().isoformat()
    c.execute("SELECT agent, model, SUM(tokens_in) + SUM(tokens_out) as total FROM usage WHERE date(timestamp) = ? GROUP BY agent, model ORDER BY total DESC", (today,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("No usage data for today.")
        return

    max_val = max(row[2] for row in rows)
    
    print(f"📊 **Agent Usage Summary - {today}**")
    for agent, model, total in rows:
        bar_length = int((total / max_val) * 20) if max_val > 0 else 0
        bar = "█" * bar_length
        formatted_total = f"{total/1_000_000:.1f}M" if total > 1_000_000 else f"{total/1_000:.0f}K"
        print(f"`{agent} ({model})`: {bar} {formatted_total}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "log":
            init_db()
            log_usage(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), datetime.datetime.now().isoformat())
        elif sys.argv[1] == "parse":
            parse_runs()
        elif sys.argv[1] == "mark":
            init_db()
            mark_file_processed(sys.argv[2])
    else:
        init_db()
        print_chart()

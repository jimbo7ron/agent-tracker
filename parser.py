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

def parse_runs():
    init_db()
    runs_dir = os.path.expanduser("~/.openclaw/cron/runs/")
    files = glob.glob(os.path.join(runs_dir, "*.jsonl"))
    
    for filepath in files:
        filename = os.path.basename(filepath)
        if is_file_processed(filename):
            continue
            
        print(f"Processing {filename}...")
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        if "usage" not in data:
                            continue
                            
                        # Infer agent from sessionKey (e.g. "agent:kipp:cron:...")
                        session_key = data.get("sessionKey", "")
                        parts = session_key.split(":")
                        agent = parts[1] if len(parts) > 1 else data.get("agentId", "main")
                        
                        model = data.get("model", "unknown")
                        tokens_in = data["usage"].get("input_tokens", 0)
                        tokens_out = data["usage"].get("output_tokens", 0)
                        timestamp = datetime.datetime.fromtimestamp(data["ts"] / 1000.0).isoformat()
                        
                        log_usage(agent, model, tokens_in, tokens_out, timestamp)
                    except Exception as e:
                        print(f"Error parsing line: {e}")
            
            mark_file_processed(filename)
            print(f"Done processing {filename}.")
        except Exception as e:
            print(f"Error processing file {filename}: {e}")

if __name__ == "__main__":
    parse_runs()

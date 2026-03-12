import sqlite3
import datetime
import sys
import json
import os

# DB setup
db_path = os.path.expanduser("~/repos/agent-tracker/usage.db")
os.makedirs(os.path.dirname(db_path), exist_ok=True)

def init_db():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS usage
                 (timestamp DATETIME, agent TEXT, model TEXT, tokens_in INTEGER, tokens_out INTEGER)''')
    # Track processed files to avoid re-parsing
    c.execute('''CREATE TABLE IF NOT EXISTS processed_files
                 (filename TEXT PRIMARY KEY)''')
    conn.commit()
    conn.close()

def log_usage(agent, model, tokens_in, tokens_out):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("INSERT INTO usage VALUES (?, ?, ?, ?, ?)",
              (datetime.datetime.now(), agent, model, tokens_in, tokens_out))
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

def report_usage():
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    today = datetime.date.today().isoformat()
    c.execute("SELECT agent, model, SUM(tokens_in), SUM(tokens_out) FROM usage WHERE date(timestamp) = ? GROUP BY agent, model", (today,))
    rows = c.fetchall()
    
    print(f"### Token Usage Report - {today}")
    print("| Agent | Model | Tokens In | Tokens Out | Total |")
    print("|-------|-------|-----------|------------|-------|")
    for row in rows:
        total = row[2] + row[3]
        print(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {total} |")
    conn.close()

if __name__ == "__main__":
    init_db()
    if len(sys.argv) > 1:
        if sys.argv[1] == "log":
            log_usage(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))
        elif sys.argv[1] == "mark":
            mark_file_processed(sys.argv[2])
        elif sys.argv[1] == "is_processed":
            if is_file_processed(sys.argv[2]):
                sys.exit(0)
            else:
                sys.exit(1)
    else:
        report_usage()

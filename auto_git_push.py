import os
import sys
import json
import time
import sqlite3
import datetime
import subprocess

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'presence.db')
TOTAL_DOMAINS = 641171
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'push_state.json')

def get_db_stats():
    if not os.path.exists(DB_PATH):
        return 0, 0, 0, 0, 0
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT status, COUNT(*) FROM urls GROUP BY status")
        counts = dict(cur.fetchall())
        conn.close()
        ver = counts.get('verified', 0)
        rej = counts.get('rejected', 0)
        dead = counts.get('dead', 0)
        blk = counts.get('blocked', 0)
        total_processed = ver + rej + dead + blk
        return total_processed, ver, rej, dead, blk
    except Exception as e:
        print(f"[AutoPush] Warning querying DB: {e}")
        return 0, 0, 0, 0, 0

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"push_count": 0, "previous_batch_processed": 0}

def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)

def ordinal(n):
    if 11 <= (n % 100) <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"

def do_push():
    state = load_state()
    push_count = state.get("push_count", 0) + 1
    prev_processed = state.get("previous_batch_processed", 0)

    total_processed, ver, rej, dead, blk = get_db_stats()
    current_batch_diff = total_processed - prev_processed
    percent_done = (total_processed / TOTAL_DOMAINS) * 100 if TOTAL_DOMAINS > 0 else 0

    now = datetime.datetime.now()
    day_str = ordinal(now.day)
    date_str = f"{day_str} {now.strftime('%B %Y')}"

    commit_msg = (
        f"So on the date {date_str} its a push {push_count} and Today we are going to Run and find the Domains "
        f"So our file size number of domains at {TOTAL_DOMAINS:,}. "
        f"Progress: {total_processed:,}/{TOTAL_DOMAINS:,} ({percent_done:.2f}% done). "
        f"Batch diff: +{current_batch_diff:,} domains processed since last push. "
        f"Current Totals: Verified={ver:,}, Rejected={rej:,}, Dead={dead:,}, Blocked={blk:,}. "
        f"Push {push_count}"
    )

    print("==================================================")
    print(f"[AutoPush] Preparing Push #{push_count} at {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[AutoPush] Commit Message:\n{commit_msg}")
    print("==================================================")

    # Regenerate fresh multi-sheet output.xlsx Excel workbook before staging
    try:
        from storage.excel_exporter import ExcelExporter
        exporter = ExcelExporter(db_path=DB_PATH, output_path="output.xlsx")
        stats = exporter.export()
        print(f"[AutoPush] Excel workbook output.xlsx updated: Verified={stats['verified']}, Rejected={stats['rejected']}, Dead={stats['dead']}, Blocked={stats['blocked']}")
    except Exception as e:
        print(f"[AutoPush] Excel export warning: {e}")

    # Stage specific safe files
    files_to_add = [
        "auto_git_push.py",
        "push_state.json",
        ".gitignore",
        "output.csv",
        "output.xlsx",
        "cli/",
        "config/",
        "core/",
        "storage/",
        "requirements.txt"
    ]
    subprocess.run(["git", "add"] + files_to_add, check=False)

    # Commit
    commit_res = subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, text=True)
    print(f"[Git Commit] {commit_res.stdout.strip()}")
    if commit_res.stderr:
        print(f"[Git Commit Error] {commit_res.stderr.strip()}")

    # Push to remote branch
    push_res = subprocess.run(["git", "push", "origin", "test2"], capture_output=True, text=True)
    print(f"[Git Push] {push_res.stdout.strip()}")
    if push_res.stderr:
        print(f"[Git Push Error] {push_res.stderr.strip()}")

    state["push_count"] = push_count
    state["previous_batch_processed"] = total_processed
    state["last_push_at"] = now.isoformat()
    save_state(state)
    print(f"[AutoPush] Push #{push_count} completed successfully!\n")

def run_hourly_loop():
    print("[AutoPush] Hourly automated Git push worker started.")
    while True:
        try:
            do_push()
        except Exception as e:
            print(f"[AutoPush] Error during push: {e}")
        print("[AutoPush] Sleeping for 1 hour (3600s) until next push...")
        time.sleep(3600)

if __name__ == "__main__":
    if "--loop" in sys.argv:
        run_hourly_loop()
    else:
        do_push()


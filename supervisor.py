import os
import sys
import time
import subprocess
import datetime

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

CRAWLER_LOG = os.path.join(LOG_DIR, 'crawler.log')
SUPERVISOR_LOG = os.path.join(LOG_DIR, 'supervisor.log')

def log_supervisor(msg: str):
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted = f"[{timestamp}] [Supervisor] {msg}\n"
    print(formatted, end='')
    with open(SUPERVISOR_LOG, 'a', encoding='utf-8') as f:
        f.write(formatted)

def run_supervisor():
    log_supervisor("Starting Presence Crawler Supervisor...")
    cmd = [sys.executable, "cli/main.py", "start"]
    
    restart_count = 0

    while True:
        log_supervisor(f"Launching crawler process: {' '.join(cmd)} (Restart count: {restart_count})")
        
        with open(CRAWLER_LOG, 'a', encoding='utf-8') as log_file:
            log_file.write(f"\n--- SESSION STARTED AT {datetime.datetime.now().isoformat()} ---\n")
            log_file.flush()

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            try:
                for line in iter(process.stdout.readline, ''):
                    if line:
                        print(line, end='')
                        log_file.write(line)
                        log_file.flush()
                process.wait()
            except KeyboardInterrupt:
                log_supervisor("KeyboardInterrupt received. Terminating crawler process gracefully...")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                log_supervisor("Supervisor stopped by user.")
                sys.exit(0)
            except Exception as e:
                log_supervisor(f"Unexpected supervisor error while running crawler: {e}")
                if process.poll() is None:
                    process.kill()

            exit_code = process.returncode
            log_supervisor(f"Crawler process exited with return code {exit_code}")

            if exit_code != 0:
                log_supervisor(f"ERROR: Crawler crashed with exit code {exit_code}! Restarting immediately...")
            else:
                log_supervisor("Crawler exited normally (return code 0). Restarting...")

        restart_count += 1
        time.sleep(2)

if __name__ == '__main__':
    run_supervisor()

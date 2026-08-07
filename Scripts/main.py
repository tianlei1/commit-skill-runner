"""main.py — Entry point: spawns Monitor and Skill Runner subprocesses, auto-restarts on crash."""
import os
import sys
import subprocess
import time
import logging
from pathlib import Path

ROOT = Path(__file__).parent.parent
PID_FILE = ROOT / "state" / "main.pid"

from dotenv import load_dotenv
import logging_utils
load_dotenv(ROOT / ".env")

LOG_FILE = ROOT / "logs" / "main.log"
RESTART_DELAY = 10  # seconds before restarting a crashed subprocess

SUBPROCESSES = {
    "result_runner": ROOT / "Scripts" / "result_runner.py",
    "monitor":       ROOT / "Scripts" / "monitor.py",
    "skill_runner":  ROOT / "Scripts" / "skill_runner.py",
}


log = logging.getLogger("main")


def _spawn(name):
    script = SUBPROCESSES[name]
    p = subprocess.Popen(
        [sys.executable, str(script)],
    )
    log.info("Started %s  pid=%d", name, p.pid)
    return p



def _check_single_instance():
    if not PID_FILE.exists():
        return
    try:
        stored_pid = int(PID_FILE.read_text(encoding='utf-8').strip())
    except Exception:
        return
    if stored_pid == os.getpid():
        return
    import subprocess as _sp
    r = _sp.run(['tasklist', '/fi', 'PID eq %d' % stored_pid, '/fo', 'csv', '/nh'],
                capture_output=True, text=True)
    if str(stored_pid) in r.stdout:
        print('Another main.py is already running (pid=%d). Exiting.' % stored_pid)
        raise SystemExit(0)

def main():
    logging_utils.setup_logging(LOG_FILE, "main")
    _check_single_instance()
    log.info("commit-skill-runner starting  pid=%d", os.getpid())

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    procs = {name: _spawn(name) for name in SUBPROCESSES}

    try:
        while True:
            for name, p in list(procs.items()):
                if p.poll() is not None:
                    log.warning(
                        "%s exited (code=%d) — restarting in %ds",
                        name, p.returncode, RESTART_DELAY,
                    )
                    time.sleep(RESTART_DELAY)
                    try:
                        procs[name] = _spawn(name)
                    except Exception as e:
                        log.error("Failed to restart %s: %s — will retry next cycle", name, e)
            time.sleep(5)
    except KeyboardInterrupt:
        log.info("Shutting down...")
        for name, p in procs.items():
            p.terminate()
            log.info("Terminated %s", name)
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

"""status.py — Show commit-skill-runner process status, queue, and state."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psutil

ROOT = Path(__file__).parent.parent

SCRIPTS = {
    "main":         "Scripts\\main.py",
    "monitor":      "Scripts\\monitor.py",
    "skill_runner": "Scripts\\skill_runner.py",
}


def find_proc(script_suffix):
    for p in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            cmdline = " ".join(p.info["cmdline"] or [])
            if script_suffix in cmdline:
                return p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None


def fmt_time(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def main():
    print()
    print("=== commit-skill-runner status ===")
    print()

    for name, suffix in SCRIPTS.items():
        p = find_proc(suffix)
        if p:
            started = fmt_time(p.info["create_time"])
            print(f"  [RUNNING] {name:<14} pid={p.pid}  started={started}")
        else:
            print(f"  [STOPPED] {name}")

    print()

    qfile = ROOT / "state" / "queue.json"
    if qfile.exists():
        q = json.loads(qfile.read_text(encoding="utf-8"))
        print(f"  Queue      : {len(q)} pending commit(s)")
    else:
        print("  Queue      : (no queue file)")

    lfile = ROOT / "state" / "last_seen.json"
    if lfile.exists():
        ls = json.loads(lfile.read_text(encoding="utf-8"))
        entries = "  ".join(f"{k}={v[:8]}" for k, v in ls.items())
        print(f"  Last seen  : {entries}")
    else:
        print("  Last seen  : (none)")

    lock = ROOT / "state" / "skill_running.lock"
    if lock.exists():
        lk = json.loads(lock.read_text(encoding="utf-8"))
        print(f"  Skill lock : running  sha={lk.get('short_sha')}  started={lk.get('started_at')}")
    else:
        print("  Skill lock : idle")

    print()


if __name__ == "__main__":
    main()

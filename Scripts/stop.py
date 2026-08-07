"""stop.py — Kill the commit-skill-runner background service and all its child processes."""
import sys
from pathlib import Path

import psutil

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import queue_store

RUNNING_LOCK = ROOT / "state" / "skill_running.lock"


def find_main_procs():
    result = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(p.info["cmdline"] or [])
            if "Scripts\\main.py" in cmdline or "Scripts/main.py" in cmdline:
                result.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return result


def kill_tree(proc):
    try:
        children = proc.children(recursive=True)
        for child in children:
            child.kill()
        proc.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


def clear_state():
    if queue_store.clear():
        print("Queue cleared.")
    else:
        print("Warning: failed to acquire queue lock to clear queue.")
    try:
        RUNNING_LOCK.unlink(missing_ok=True)
        print("Running lock cleared.")
    except Exception as e:
        print(f"Warning: failed to clear running lock: {e}")


def main():
    procs = find_main_procs()
    if not procs:
        print("commit-skill-runner is not running.")
        clear_state()
        return

    for p in procs:
        pid = p.pid
        kill_tree(p)
        print(f"commit-skill-runner stopped  pid={pid}")

    clear_state()


if __name__ == "__main__":
    main()

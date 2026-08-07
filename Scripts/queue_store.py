"""queue_store.py — File-based queue shared between Monitor and Skill Runner processes."""
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
QUEUE_FILE = ROOT / "state" / "queue.json"
LOCK_FILE = ROOT / "state" / "queue.lock"

_LOCK_TIMEOUT = 10
_STALE_LOCK_AGE = 30


def _acquire():
    deadline = time.time() + _LOCK_TIMEOUT
    while time.time() < deadline:
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - LOCK_FILE.stat().st_mtime > _STALE_LOCK_AGE:
                    LOCK_FILE.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            time.sleep(0.05)
    return False


def _release():
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _read():
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write(items):
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")


def enqueue(commit):
    """Return True if newly enqueued, None if already in queue, False on lock failure."""
    if not _acquire():
        return False
    try:
        items = _read()
        sha = commit.get("sha")
        if sha and any(i.get("sha") == sha for i in items):
            return None
        items.append(commit)
        _write(items)
        return True
    finally:
        _release()


def dequeue():
    if not _acquire():
        return None
    try:
        items = _read()
        if not items:
            return None
        item = items.pop(0)
        _write(items)
        return item
    finally:
        _release()


def clear():
    if not _acquire():
        return False
    try:
        _write([])
        return True
    finally:
        _release()


def is_empty():
    return len(_read()) == 0


def size():
    return len(_read())

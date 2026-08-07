"""ccl_py_test_skill.py — Sync py test repo to main and run the Python test suite."""
import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger("ccl_py_test_skill")

_PY_TEST_REPO = Path(os.environ.get("PY_TEST_REPO", ""))
_PY_TEST_CMD  = os.environ.get("PY_TEST_CMD", "")
_PID_FILE     = _PY_TEST_REPO / ".run_tests.pid" if _PY_TEST_REPO else Path(".run_tests.pid")

_test_proc       = None   # set by launch_and_wait, for module-lifetime access if needed
_test_started_at = None


def git_sync():
    """Fetch origin and reset to origin/main."""
    if not _PY_TEST_REPO.exists():
        return {"label": "git sync", "result": "fail", "detail": f"PY_TEST_REPO not found: {_PY_TEST_REPO}"}

    r = subprocess.run(
        ["git", "-C", str(_PY_TEST_REPO), "fetch", "origin"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return {"label": "git sync", "result": "fail", "detail": r.stderr.strip()}

    r = subprocess.run(
        ["git", "-C", str(_PY_TEST_REPO), "reset", "--hard", "origin/main"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return {"label": "git sync", "result": "fail", "detail": r.stderr.strip()}

    return {"label": "git sync", "result": "pass"}


def launch_and_wait():
    """Launch the test process, wait for it to finish, return the HTML report path."""
    global _test_proc, _test_started_at

    if not _PY_TEST_CMD:
        return {"label": "py test result", "result": "fail", "detail": "PY_TEST_CMD not set"}

    # ── Launch ─────────────────────────────────────────────────────────────────
    _PID_FILE.unlink(missing_ok=True)
    _test_started_at = time.time()
    try:
        _test_proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PY_TEST_CMD],
            cwd=str(_PY_TEST_REPO),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _PID_FILE.write_text(str(_test_proc.pid), encoding="utf-8")
        log.info("Test started pid=%d", _test_proc.pid)
    except Exception as e:
        return {"label": "py test result", "result": "fail", "detail": str(e)}

    time.sleep(5)
    if _test_proc.poll() is not None and _test_proc.returncode != 0:
        _PID_FILE.unlink(missing_ok=True)
        return {"label": "py test result", "result": "fail",
                "detail": f"process crashed on startup (exit {_test_proc.returncode})"}

    # ── Wait ───────────────────────────────────────────────────────────────────
    log.info("Waiting for py test process (pid=%d) to finish", _test_proc.pid)
    _test_proc.wait()
    _PID_FILE.unlink(missing_ok=True)
    log.info("py test process finished (exit=%d)", _test_proc.returncode)

    # ── Collect result ─────────────────────────────────────────────────────────
    reports_dir = _PY_TEST_REPO / "reports"
    if not reports_dir.exists():
        return {"label": "py test result", "result": "fail", "detail": "reports/ dir not found"}

    cutoff = _test_started_at or 0.0
    stamped = [(f.stat().st_mtime, f) for f in reports_dir.glob("*.html")]
    recent = [(mtime, f) for mtime, f in stamped if mtime >= cutoff]
    if not recent:
        return {"label": "py test result", "result": "fail", "detail": "no HTML report found in reports/"}

    _, report = max(recent)
    log.info("py test report: %s", report)
    return {"label": "py test result", "result": str(report)}


def steps():
    return [
        ("## Step — Git Sync",   git_sync),
        ("## Step — Run Test",   launch_and_wait),
    ]

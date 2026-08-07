"""ccl_regression_test_skill.py — Configure and run CCL DUT regression test."""
import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger("ccl_regression_test_skill")

_AUTOTEST_ROOT = Path(os.environ.get("AUTOTEST_ROOT", r"C:\work\TestCenter-AutoTest"))
_CONFIG_FILE   = _AUTOTEST_ROOT / "Config" / "config.yaml"
_START_BAT     = _AUTOTEST_ROOT / "StartTest.bat"
_PID_FILE      = _AUTOTEST_ROOT / ".run_tests.pid"


def regression_test():
    """Configure yaml, launch test, wait for completion, return result path."""
    import psutil

    # ── Configure ──────────────────────────────────────────────────────────────
    try:
        lines = _CONFIG_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as e:
        return {"label": "regression result", "result": "fail", "detail": str(e)}

    current_name = ""
    result_dir = None
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("name:"):
            current_name = stripped.split(":", 1)[1].strip().strip('"').strip("'")
        if stripped.startswith("result_dir:"):
            result_dir = stripped.split(":", 1)[1].strip().strip('"').strip("'")
        if stripped.startswith("enable_test:"):
            indent = len(line) - len(line.lstrip())
            want = "yes" if ("CCL" in current_name and "DUT" in current_name) else "no"
            line = " " * indent + f"enable_test: {want}\n"
        out.append(line)

    try:
        _CONFIG_FILE.write_text("".join(out), encoding="utf-8")
    except OSError as e:
        return {"label": "regression result", "result": "fail", "detail": str(e)}

    if not result_dir:
        return {"label": "regression result", "result": "fail", "detail": "result_dir not found in config"}

    # ── Launch ─────────────────────────────────────────────────────────────────
    _PID_FILE.unlink(missing_ok=True)
    started_at = time.time()
    try:
        subprocess.Popen(
            ["cmd", "/c", str(_START_BAT)],
            cwd=str(_AUTOTEST_ROOT),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except Exception as e:
        return {"label": "regression result", "result": "fail", "detail": str(e)}

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _PID_FILE.exists():
            break
        time.sleep(0.5)
    else:
        return {"label": "regression result", "result": "fail", "detail": "PID file did not appear within 10s"}

    # ── Wait ───────────────────────────────────────────────────────────────────
    log.info("Waiting for test to complete (polling %s every 30s)", _PID_FILE)
    start = time.monotonic()
    iteration = 0
    while True:
        try:
            pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            log.info("PID file gone after %ds — test finished", int(time.monotonic() - start))
            break
        if not psutil.pid_exists(pid):
            log.error("Process %d is dead but PID file still exists — test crashed", pid)
            _PID_FILE.unlink(missing_ok=True)
            return {"label": "regression result", "result": "fail", "detail": "test process crashed"}
        time.sleep(30)
        iteration += 1
        if iteration % 10 == 0:
            log.info("Still waiting (%ds elapsed)...", int(time.monotonic() - start))

    # ── Collect result ─────────────────────────────────────────────────────────
    try:
        result_path = Path(result_dir)
        if not result_path.exists():
            return {"label": "regression result", "result": "fail", "detail": f"result_dir not found: {result_dir}"}

        stamped_dirs = [(d.stat().st_mtime, d) for d in result_path.iterdir() if d.is_dir()]
        recent_dirs = [(mtime, d) for mtime, d in stamped_dirs if mtime >= started_at]
        if not recent_dirs:
            return {"label": "regression result", "result": "fail", "detail": "no result subdirectory found"}

        _, newest_dir = max(recent_dirs)
        html_files = list(newest_dir.glob("IntegrationResultReport*.html")) or list(newest_dir.glob("*.html"))
        if not html_files:
            return {"label": "regression result", "result": "fail", "detail": f"no HTML report in {newest_dir}"}

        _, report = max((f.stat().st_mtime, f) for f in html_files)
        log.info("Regression result report: %s", report)
        return {"label": "regression result", "result": str(report)}

    except Exception as e:
        return {"label": "regression result", "result": "fail", "detail": str(e)}


def steps():
    return [
        ("## Step — CCL Regression Test", regression_test),
    ]

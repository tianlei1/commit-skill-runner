"""compile_ui_bll_skill.py — Checkout commit, then build BLL and UI in parallel."""
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "Scripts"))

import html_reporter

log = logging.getLogger("compile_ui_bll_skill")

_SKILL_NAME = "compile_ui_bll_skill"
_REPO = os.environ.get("TESTCENTER_REPO", "")
_LOCK = Path(_REPO) / ".git" / "index.lock" if _REPO else None
LOG_DIR = ROOT / "logs"


def _remove_stale_lock():
    if _LOCK and _LOCK.exists():
        log.warning("Removing stale git index.lock before checkout")
        _LOCK.unlink(missing_ok=True)


def checkout(commit):
    if not _REPO:
        return {"label": "checkout", "result": "fail", "detail": "TESTCENTER_REPO not set in .env"}
    sha = commit["sha"]
    _remove_stale_lock()
    log.info("Checking out %s in %s", sha, _REPO)
    try:
        r1 = subprocess.run(["git", "-C", _REPO, "checkout", "-f", sha],
                            capture_output=True, text=True)
        r2 = subprocess.run(["git", "-C", _REPO, "restore", "."],
                            capture_output=True, text=True)
        ok = r1.returncode == 0 and r2.returncode == 0
        payload = {"label": "checkout", "result": "pass" if ok else "fail"}
        if not ok:
            payload["detail"] = (r1.stderr + r2.stderr).strip()
    except Exception as e:
        payload = {"label": "checkout", "result": "fail", "detail": str(e)}
    return payload


def build_parallel(commit):
    """Start BLL and UI builds concurrently; each sub-step reports its own result."""
    short = commit["short_sha"]
    build_results = {}

    def _build(label, dos_cmd, log_suffix):
        html_reporter.record_step_start(commit, _SKILL_NAME, f"## {label}")
        build_log = LOG_DIR / f"build_{short}_{log_suffix}.log"
        try:
            LOG_DIR.mkdir(exist_ok=True)
            with open(build_log, "w", encoding="utf-8", errors="replace") as lf:
                proc = subprocess.run(["cmd", "/c", f"dos {dos_cmd}"], stdout=lf, stderr=lf)
            result = "pass" if proc.returncode == 0 else "fail"
            if result != "pass":
                log.error("%s failed (exit %d) — see %s", label, proc.returncode, build_log)
        except Exception as e:
            log.error("%s raised: %s", label, e)
            result = "fail"
        build_results[label] = result
        html_reporter.record_step_result(commit, _SKILL_NAME, {"label": label, "result": result})

    t_bll = threading.Thread(target=_build, args=("build bll", "bd bll", "bll"), daemon=True)
    t_ui  = threading.Thread(target=_build, args=("build ui",  "bd ui",  "ui"),  daemon=True)
    t_bll.start()
    t_ui.start()
    t_bll.join()
    t_ui.join()

    overall = "pass" if all(v == "pass" for v in build_results.values()) else "fail"
    return {"label": "build", "result": overall}


build_parallel.parallel = True


def steps():
    return [
        ("## Step 1 — Checkout and clean", checkout),
        ("## Step 2 — Build BLL and UI",  build_parallel),
    ]

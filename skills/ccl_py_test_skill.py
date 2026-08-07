"""ccl_py_test_skill — sync Python test repo and run the test suite."""
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

log = logging.getLogger("ccl_py_test_skill")


def git_sync():
    repo = os.environ.get("PY_TEST_REPO", "").strip()
    if not repo:
        return {"label": "git sync", "result": "fail", "detail": "PY_TEST_REPO not set"}
    try:
        r = subprocess.run(["git", "-C", repo, "fetch", "origin"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return {"label": "git sync", "result": "fail", "detail": r.stderr.strip()}
        r = subprocess.run(["git", "-C", repo, "reset", "--hard", "origin/main"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return {"label": "git sync", "result": "fail", "detail": r.stderr.strip()}
        return {"label": "git sync", "result": "pass"}
    except Exception as e:
        return {"label": "git sync", "result": "fail", "detail": str(e)}



def setup_deps():
    cmd = os.environ.get("PY_TEST_CMD", "").strip()
    m = re.search(r'"([^"]+\.exe)"', cmd, re.IGNORECASE)
    if not m:
        return {"label": "setup deps", "result": "pass", "detail": "no python exe in cmd"}
    python = m.group(1)
    try:
        r = subprocess.run([python, "-m", "pip", "install", "psycopg2-binary", "-q"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            log.info("psycopg2-binary OK")
        else:
            log.warning("pip install psycopg2-binary failed: %s", r.stderr[-300:])
    except Exception as e:
        log.warning("pip install error: %s", e)
    return {"label": "setup deps", "result": "pass"}

def run_test():
    repo = os.environ.get("PY_TEST_REPO", "").strip()
    cmd  = os.environ.get("PY_TEST_CMD",  "").strip()
    if not repo:
        return {"label": "py test result", "result": "fail", "detail": "PY_TEST_REPO not set"}
    if not cmd:
        return {"label": "py test result", "result": "fail", "detail": "PY_TEST_CMD not set"}

    started_at = datetime.now()
    log.info("Running pytest  cmd=%s", cmd)
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            cwd=repo,
            timeout=7200,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        exit_code = proc.returncode
        log.info("pytest exited  rc=%d", exit_code)
        if proc.stdout:
            log.info("pytest stdout (last 4000): %s", proc.stdout[-4000:])
        if proc.stderr:
            log.warning("pytest stderr (last 2000): %s", proc.stderr[-2000:])
    except subprocess.TimeoutExpired:
        return {"label": "py test result", "result": "fail", "detail": "timeout after 7200s"}
    except Exception as e:
        return {"label": "py test result", "result": "fail", "detail": str(e)}

    runs_dir = Path(repo) / "runs"
    if runs_dir.exists():
        cutoff = started_at.timestamp() - 10
        new_subdirs = []
        for subdir in runs_dir.iterdir():
            if not subdir.is_dir():
                continue
            try:
                if subdir.stat().st_mtime >= cutoff:
                    new_subdirs.append(subdir)
            except OSError:
                continue
        if new_subdirs:
            newest_dir = max(new_subdirs, key=lambda d: d.stat().st_mtime)
            if exit_code == 0:
                return {"label": "py test result", "result": str(newest_dir)}
            else:
                return {"label": "py test result", "result": "fail",
                        "detail": "pytest exit %d, output at %s" % (exit_code, newest_dir)}

    if exit_code != 0:
        return {"label": "py test result", "result": "fail",
                "detail": "pytest exit %d, no output directory" % exit_code}
    return {"label": "py test result", "result": "pass", "detail": "no output directory created"}


def steps():
    return [
        ("## Step 1 — Git Sync", git_sync),
        ("## Step 2 — Setup Deps", setup_deps),
        ("## Step 3 — Run Test", run_test),
    ]

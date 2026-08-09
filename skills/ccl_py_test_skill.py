"""ccl_py_test_skill - sync Python test repo and run the test suite."""
import html as _html_mod
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



def _generate_html_report(run_dir, exit_code):
    html_path = run_dir / "report.html"
    snapshots_file = run_dir / "clear_snapshots.txt"
    status = "PASS" if exit_code == 0 else "FAIL"
    color = "#2a7f2a" if exit_code == 0 else "#cc2222"
    raw = ""
    if snapshots_file.exists():
        try:
            raw = snapshots_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            raw = "(could not read clear_snapshots.txt)"
    body = _html_mod.escape(raw)
    page = (
        "<!DOCTYPE html><html><head><meta charset=utf-8>"
        "<title>CCL Py Test - " + run_dir.name + "</title>"
        "<style>body{font-family:monospace;margin:20px}"
        ".s{font-size:1.2em;font-weight:bold;color:" + color + ";margin-bottom:12px}"
        "pre{background:#f8f8f8;border:1px solid #ddd;padding:12px;white-space:pre-wrap;font-size:.85em}"
        "</style></head><body>"
        "<h2>CCL Py Test Report</h2>"
        "<div><b>Run:</b> " + run_dir.name + "</div>"
        "<div class=s>" + status + "</div>"
        "<pre>" + body + "</pre>"
        "</body></html>"
    )
    html_path.write_text(page, encoding="utf-8")
    return html_path


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

    cutoff = started_at.timestamp() - 10
    reports_dir = Path(repo) / "reports"
    if reports_dir.exists():
        html_files = [f for f in reports_dir.glob("*.html") if f.stat().st_mtime >= cutoff]
        if html_files:
            report = str(max(html_files, key=lambda f: f.stat().st_mtime))
            log.info("HTML report found: %s", report)
            if exit_code == 0:
                return {"label": "py test result", "result": report}
            return {"label": "py test result", "result": "fail",
                    "detail": "pytest exit %d, report at %s" % (exit_code, report)}
    runs_dir = Path(repo) / "runs"
    if runs_dir.exists():
        new_subdirs = [s for s in runs_dir.iterdir()
                       if s.is_dir() and s.stat().st_mtime >= cutoff]
        if new_subdirs:
            newest_dir = max(new_subdirs, key=lambda d: d.stat().st_mtime)
            try:
                html_files = [_generate_html_report(newest_dir, exit_code)]
            except Exception as eg:
                log.warning("HTML report generation failed: %s", eg)
                html_files = []
            if html_files:
                report = str(html_files[0])
                if exit_code == 0:
                    return {"label": "py test result", "result": report}
                return {"label": "py test result", "result": "fail",
                        "detail": "pytest exit %d, report at %s" % (exit_code, report)}

    if exit_code != 0:
        return {"label": "py test result", "result": "fail",
                "detail": "pytest exit %d, no output directory" % exit_code}
    return {"label": "py test result", "result": "pass", "detail": "no output directory created"}


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


def steps():
    return [
        ("## Step 1 - Git Sync", git_sync),
        ("## Step 2 - Setup Deps", setup_deps),
        ("## Step 3 - Run Test", run_test),
    ]

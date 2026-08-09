"""regression_test_skill --- Launch CCL DUT regression test and wait for completion."""
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "Scripts"))

log = logging.getLogger("regression_test_skill")

_AUTOTEST_ROOT = Path(os.environ.get("AUTOTEST_ROOT", r"C:\work\TestCenter-AutoTest"))
_PYTHON  = _AUTOTEST_ROOT / "python" / "3.10.2" / "win64" / "python.exe"
_SCRIPT  = _AUTOTEST_ROOT / "Scripts" / "run_tests.py"
_CONFIG  = _AUTOTEST_ROOT / "Config" / "config.yaml"
_RESULT_DIR = Path(os.environ.get("REGRESSION_RESULT_DIR", r"C:\work\regression test"))
_MAX_WAIT_S = 7200


def _find_result_html(started_at):
    if not _RESULT_DIR.exists():
        return None
    cutoff = started_at.timestamp() - 60
    candidates = []
    for subdir in _RESULT_DIR.iterdir():
        if not subdir.is_dir():
            continue
        try:
            if subdir.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        subdir_htmls = list(subdir.rglob("IntegrationResultReport*.html"))
        if not subdir_htmls:
            subdir_htmls = list(subdir.rglob("*.html"))
        candidates.extend(subdir_htmls)
    if not candidates:
        return None
    return str(max(candidates, key=lambda f: f.stat().st_mtime))


def run_regression(commit):
    python = _PYTHON if _PYTHON.exists() else Path(sys.executable)
    started_at = datetime.now()
    log.info("Launching run_tests.py (may take 30-90 min)  python=%s", python)
    try:
        proc = subprocess.run(
            [str(python), str(_SCRIPT), str(_CONFIG)],
            timeout=_MAX_WAIT_S,
        )
        log.info("run_tests.py exited  rc=%d", proc.returncode)
    except subprocess.TimeoutExpired:
        return {"label": "regression result", "result": "fail",
                "detail": "timeout after %ds" % _MAX_WAIT_S}
    except Exception as e:
        return {"label": "regression result", "result": "fail", "detail": str(e)}
    ok = proc.returncode == 0
    html = _find_result_html(started_at)
    if html:
        log.info("Result report found: %s", html)
    else:
        log.info("No HTML report found")
    payload = {"label": "regression result", "result": "pass" if ok else "fail"}
    if html:
        payload["link"] = html
    if not ok and not html:
        payload["detail"] = f"exited rc={proc.returncode}"
    return payload


def steps():
    return [
        ("## Step 1 -- Run Regression Test", run_regression),
    ]

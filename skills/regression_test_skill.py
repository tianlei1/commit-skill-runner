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

_MAX_WAIT_S = 7200


def _find_result_html(started_at, result_dir):
    if not result_dir.exists():
        return None
    cutoff = started_at.timestamp() - 60
    candidates = []
    for subdir in result_dir.iterdir():
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
    autotest_root = os.environ.get("AUTOTEST_ROOT", "").strip()
    result_dir    = os.environ.get("REGRESSION_RESULT_DIR", "").strip()
    if not autotest_root:
        return {"label": "regression result", "result": "fail", "detail": "AUTOTEST_ROOT not set in .env"}
    if not result_dir:
        return {"label": "regression result", "result": "fail", "detail": "REGRESSION_RESULT_DIR not set in .env"}

    autotest_root = Path(autotest_root)
    python  = autotest_root / "python" / "3.10.2" / "win64" / "python.exe"
    script  = autotest_root / "Scripts" / "run_tests.py"
    config  = autotest_root / "Config" / "config.yaml"
    result_dir = Path(result_dir)

    if not python.exists():
        return {"label": "regression result", "result": "fail", "detail": f"python not found: {python}"}

    started_at = datetime.now()
    log.info("Launching run_tests.py (may take 30-90 min)  python=%s", python)
    try:
        proc = subprocess.run(
            [str(python), str(script), str(config)],
            timeout=_MAX_WAIT_S,
        )
        log.info("run_tests.py exited  rc=%d", proc.returncode)
    except subprocess.TimeoutExpired:
        return {"label": "regression result", "result": "fail",
                "detail": "timeout after %ds" % _MAX_WAIT_S}
    except Exception as e:
        return {"label": "regression result", "result": "fail", "detail": str(e)}
    ok = proc.returncode == 0
    html = _find_result_html(started_at, result_dir)
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

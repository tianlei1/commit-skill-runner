"""html_reporter.py — Thin HTTP client: forwards result calls to result_runner."""
import json
import logging
import time
import urllib.request
import urllib.error

RESULT_RUNNER_URL = "http://localhost:8099"
log = logging.getLogger("html_reporter")


def _post(path, data, retries=3):
    payload = json.dumps(data).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                RESULT_RUNNER_URL + path,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            return
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(0.3)
            else:
                log.warning("result_runner unavailable (%s): %s", path, e)


def init():
    """No-op: result_runner is a separate process managed by main.py."""
    pass


def record_skill_start(commit, skill_name):
    _post("/api/skill_start", {"commit": commit, "skill_name": skill_name})


def record_step_start(commit, skill_name, step_header):
    _post("/api/step_start", {"commit": commit, "skill_name": skill_name, "step_header": step_header})


def record_step_result(commit, skill_name, payload):
    _post("/api/step_result", {"commit": commit, "skill_name": skill_name, "payload": payload})


def record_skill_pass(commit, skill_name):
    _post("/api/skill_pass", {"commit": commit, "skill_name": skill_name})


def record_skill_fail(commit, skill_name):
    _post("/api/skill_fail", {"commit": commit, "skill_name": skill_name})


def record_commit_done(sha):
    _post("/api/commit_done", {"sha": sha})


# ── Legacy shims (used by _run_md_skill / _run_claude in skill_runner) ────────

def record_skill_result(entry):
    sha = entry.get("sha", "")
    commit = {
        "sha": sha,
        "short_sha": entry.get("short_sha", sha[:8]),
        "author":    entry.get("author", "unknown"),
        "repo":      entry.get("repo", ""),
    }
    skill_name = entry.get("skill_name", "unknown")
    record_step_result(commit, skill_name, entry)


def replay_queue_file(queue_file):
    """No-op: result_runner handles replay on startup."""
    pass

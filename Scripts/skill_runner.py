"""skill_runner.py — Skill Process: dequeues commits and runs the configured skill."""
import os
import re
import sys
import json
import time
import logging
import subprocess
import importlib.util
from pathlib import Path

import psutil

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
import logging_utils
import queue_store
import html_reporter

load_dotenv(ROOT / ".env")

LOG_FILE = ROOT / "logs" / "skill_runner.log"
RUNNING_LOCK = ROOT / "state" / "skill_running.lock"
LAST_SEEN = ROOT / "state" / "last_seen.json"
POLL_INTERVAL = 30

_WAIT_FOR_PID_PREFIX = "WAIT_FOR_PID:"
_PID_POLL_INTERVAL = 30   # seconds


log = logging.getLogger("skill_runner")


def is_skill_running():
    return RUNNING_LOCK.exists()


def set_skill_running(commit):
    RUNNING_LOCK.parent.mkdir(parents=True, exist_ok=True)
    RUNNING_LOCK.write_text(
        json.dumps({
            "sha": commit["sha"],
            "short_sha": commit["short_sha"],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }),
        encoding="utf-8",
    )


def clear_skill_running():
    try:
        RUNNING_LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def _update_last_seen(commit):
    label = f"{commit.get('repo', '')}:{commit.get('branch', '')}"
    try:
        state = json.loads(LAST_SEEN.read_text(encoding="utf-8")) if LAST_SEEN.exists() else {}
        state[label] = commit["sha"]
        LAST_SEEN.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except Exception as e:
        log.warning("Failed to update last_seen: %s", e)


def load_skill(skill_name):
    skill_dir = ROOT / "skills"
    md_path = skill_dir / f"{skill_name}.md"
    py_path = skill_dir / f"{skill_name}.py"
    if md_path.exists():
        return {"type": "md", "name": skill_name, "content": md_path.read_text(encoding="utf-8")}
    if py_path.exists():
        spec = importlib.util.spec_from_file_location(skill_name, py_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "steps"):
            raise AttributeError(
                f"Skill '{skill_name}' must expose a steps(commit) function "
                "returning a list of (step_header, callable) tuples"
            )
        return {"type": "py", "name": skill_name, "module": module}
    raise FileNotFoundError(f"Skill not found: {md_path} or {py_path}")


def run_skill(skill, commit):
    if skill["type"] == "md":
        _run_md_skill(skill, commit)
    else:
        _run_py_skill(skill, commit)


def _run_py_skill(skill, commit):
    """Run a .py skill whose steps() returns [(header, callable), ...].
    Enforces the same step-by-step contract as _run_md_skill:
    block until result, update HTML, stop on failure.

    Step callables may optionally declare a `commit` parameter; if they do,
    the runner passes the current commit dict automatically."""
    import inspect
    skill_name = skill["name"]
    html_reporter.record_skill_start(commit, skill_name)
    try:
        step_list = skill["module"].steps()
        if not step_list:
            raise RuntimeError(f"Skill '{skill_name}' returned no steps")

        for step_header, step_fn in step_list:
            html_reporter.record_step_start(commit, skill_name, step_header)
            log.info("Running step: %s / %s", skill_name, step_header)

            try:
                sig = inspect.signature(step_fn)
                payload = step_fn(commit) if sig.parameters else step_fn()
            except Exception as e:
                payload = {"label": step_header, "result": "fail", "detail": str(e)}

            html_reporter.record_step_result(commit, skill_name, payload)
            log.info("Step '%s / %s' → %s", skill_name, step_header, payload.get("result"))

            if payload.get("result", "").lower() in ("fail", "failed"):
                raise RuntimeError(
                    f"Step '{skill_name} / {step_header}' failed — stopping skill"
                )

        html_reporter.record_skill_pass(commit, skill_name)

    except Exception:
        html_reporter.record_skill_fail(commit, skill_name)
        raise


def _any_failed(results):
    return any(r.get("result", "").lower() == "fail" for r in results)


def _run_claude(prompt, commit, skill_name=None):
    """Run Claude with prompt, handle RESULT: and WAIT_FOR_PID: lines.
    Returns (wait_for_pid, results) where results is a list of captured RESULT payloads.
    Blocks until Claude exits — do not call from a background thread."""
    proc = subprocess.Popen(
        ["claude", "--print", "--dangerously-skip-permissions",
         "--allowedTools", "Bash,PowerShell,Read,Edit,Write,Glob,Grep"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    proc.stdin.write(prompt)
    proc.stdin.close()
    wait_for_pid = None
    results = []
    for line in proc.stdout:
        line = line.rstrip("\n")
        if not line:
            continue
        if line.startswith("RESULT:"):
            raw = line[len("RESULT:"):].strip()
            try:
                payload = json.loads(raw)
                if skill_name:
                    html_reporter.record_step_result(commit, skill_name, payload)
                else:
                    html_reporter.record_skill_result({"sha": commit["sha"], "short_sha": commit["short_sha"], "author": commit.get("author", "unknown"), "repo": commit.get("repo", ""), **payload})
                results.append(payload)
                log.info("Captured result: %s", raw)
            except json.JSONDecodeError as e:
                log.warning("Invalid RESULT line: %s — %s", raw, e)
        elif line.startswith(_WAIT_FOR_PID_PREFIX):
            pid_path = line[len(_WAIT_FOR_PID_PREFIX):].strip()
            if wait_for_pid is not None:
                log.warning("Duplicate WAIT_FOR_PID ignored: %s", pid_path)
            else:
                wait_for_pid = pid_path
                log.info("Received WAIT_FOR_PID: %s", wait_for_pid)
        else:
            log.info("[claude] %s", line)
    proc.wait()
    return wait_for_pid, results


def _pid_alive(pid):
    return psutil.pid_exists(pid)


def _poll_pid_file(pid_file):
    """Block until pid_file disappears or the process inside it dies. Returns True if clean exit."""
    pid_path = Path(pid_file)
    log.info("Polling PID file every %ds: %s", _PID_POLL_INTERVAL, pid_path)
    start = time.monotonic()
    iteration = 0
    while True:
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            log.info("PID file gone after %ds", int(time.monotonic() - start))
            return True
        if not _pid_alive(pid):
            log.error("Process %d is dead but PID file still exists — test crashed", pid)
            pid_path.unlink(missing_ok=True)
            return False
        time.sleep(_PID_POLL_INTERVAL)
        iteration += 1
        if iteration % 10 == 0:
            log.info("Still waiting for test to finish (%ds elapsed)...", int(time.monotonic() - start))


def _parse_skill_steps(content):
    """Split skill .md into (header, body) pairs on '## Step N' boundaries."""
    pattern = re.compile(r'^(## Step \d+[^\n]*)', re.MULTILINE)
    parts = pattern.split(content)
    steps = []
    i = 1
    while i < len(parts):
        header = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        steps.append((header, body))
        i += 2
    return steps


def _get_skill_preamble(content):
    match = re.search(r'^## Step \d+', content, re.MULTILINE)
    return content[:match.start()].strip() if match else ""


def _build_step_prompt(preamble, step_header, step_body, commit):
    commit_section = (
        "## Commit to process\n\n"
        "```json\n"
        + json.dumps(commit, indent=2)
        + "\n```"
    )
    stop_instruction = (
        "## CRITICAL — Single-step execution only\n\n"
        "You are executing EXACTLY ONE step. Do NOT run any other steps, regardless of what the skill description says.\n"
        "1. Run ONLY the commands listed in the step below.\n"
        "2. Print the required protocol line (either `RESULT: {...}` or `WAIT_FOR_PID: <path>`) "
        "as specified in that step — as a bare, standalone line with no surrounding text, explanation, or commentary.\n"
        "3. Do NOT describe what you did in prose. The protocol line is the ONLY output after running commands.\n"
        "4. STOP immediately after printing the protocol line — do not run anything else.\n\n"
        "You will be invoked again separately for each subsequent step. "
        "Ignore any 'execute steps in order' or similar instructions in the skill description above."
    )
    parts = []
    if preamble:
        parts.append(preamble)
    parts.append(stop_instruction)
    parts.append(f"{step_header}\n\n{step_body}")
    parts.append(commit_section)
    return "\n\n---\n\n".join(parts)


def _build_regression_parse_prompt(commit, test_started_at):
    autotest_root = os.environ.get("AUTOTEST_ROOT", "")
    return (
        "The CCL DUT regression test has completed.\n\n"
        f"1. Read `{autotest_root}\\Config\\config.yaml` to find the `result_dir` field.\n"
        f"2. Find the newest subdirectory inside `result_dir` that was created after {test_started_at}.\n"
        "3. Find the HTML result report file (e.g. `IntegrationResultReport.html`) in that directory.\n"
        "4. Print exactly one line to stdout and nothing else:\n\n"
        '   RESULT: {"label": "regression result", "result": "<full_path_to_html>"}\n\n'
        "   Or if the HTML is not found:\n\n"
        '   RESULT: {"label": "regression result", "result": "fail"}\n\n'
        "## Commit to process\n\n"
        "```json\n"
        + json.dumps(commit, indent=2)
        + "\n```"
    )


def _build_py_test_parse_prompt(commit, test_started_at):
    py_test_repo = os.environ.get("PY_TEST_REPO", "")
    return (
        "The Python test suite has completed.\n\n"
        f"1. Find the newest `.html` file inside `{py_test_repo}\\reports\\` "
        f"that was created after {test_started_at}.\n"
        "2. Print exactly one line to stdout and nothing else:\n\n"
        '   RESULT: {"label": "py test result", "result": "<full_path_to_report.html>"}\n\n'
        "   Or if no HTML report is found:\n\n"
        '   RESULT: {"label": "py test result", "result": "fail"}\n\n'
        "## Commit to process\n\n"
        "```json\n"
        + json.dumps(commit, indent=2)
        + "\n```"
    )


def _build_compile_parse_prompt(commit):
    result_file = str(ROOT / "state" / "build_result.json")
    return (
        "The BLL and UI build has completed.\n\n"
        f"1. Read `{result_file}`.\n"
        "2. Check the `bll_exit` and `ui_exit` values.\n"
        "3. Print the result lines and nothing else:\n\n"
        '   RESULT: {"label": "bd bll", "result": "pass"}  — if bll_exit == 0\n'
        '   RESULT: {"label": "bd bll", "result": "fail"}  — if bll_exit != 0\n'
        '   RESULT: {"label": "bd ui", "result": "pass"}   — if ui_exit == 0\n'
        '   RESULT: {"label": "bd ui", "result": "fail"}   — if ui_exit != 0 and ui_exit != -1\n\n'
        "   If ui_exit is -1, BLL failed so UI was skipped — print only the BLL result line.\n\n"
        "## Commit to process\n\n"
        "```json\n"
        + json.dumps(commit, indent=2)
        + "\n```"
    )


def _wait_for_test_and_parse(commit, wait_for_pid, skill_name):
    """Block until the test PID file disappears, then invoke Claude to parse results.
    Raises RuntimeError on failure. This call blocks for the full test duration."""
    test_started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    gone = _poll_pid_file(wait_for_pid)

    if skill_name and "compile_ui_bll" in skill_name:
        crash_label  = "bd bll"
        parse_prompt = _build_compile_parse_prompt(commit)
        parse_step   = "## Step — parse build results"
    elif skill_name and "py_test" in skill_name:
        crash_label  = "py test result"
        parse_prompt = _build_py_test_parse_prompt(commit, test_started_at)
        parse_step   = "## Step — parse py test results"
    else:
        crash_label  = "regression result"
        parse_prompt = _build_regression_parse_prompt(commit, test_started_at)
        parse_step   = "## Step — parse results"

    if not gone:
        html_reporter.record_step_result(commit, skill_name, {
            "label": crash_label,
            "result": "fail",
            "detail": "process crashed without cleaning up PID file",
        })
        raise RuntimeError("Test process died — stopping skill chain")

    log.info("Test complete — invoking Claude to parse results (blocking until done)")
    html_reporter.record_step_start(commit, skill_name, parse_step)
    _, parse_results = _run_claude(parse_prompt, commit, skill_name=skill_name)
    if not parse_results:
        raise RuntimeError(
            "Result parsing produced no RESULT lines — "
            "cannot determine test outcome; not proceeding to next skill"
        )
    if _any_failed(parse_results):
        raise RuntimeError("Result parsing reported failure — stopping skill chain")


def _run_md_skill(skill, commit):
    content = skill["content"].strip()
    steps = _parse_skill_steps(content)

    if not steps:
        raise RuntimeError(
            f"Skill '{skill['name']}' has no '## Step N' sections — "
            "all skills must define steps"
        )

    preamble = _get_skill_preamble(content)
    html_reporter.record_skill_start(commit, skill["name"])

    try:
        for step_header, step_body in steps:
            step_label = f"{skill['name']} / {step_header}"
            prompt = _build_step_prompt(preamble, step_header, step_body, commit)

            html_reporter.record_step_start(commit, skill["name"], step_header)
            log.info("Invoking Claude for step: %s (blocking until result received)", step_label)
            wait_for_pid, results = _run_claude(prompt, commit, skill_name=skill["name"])

            if not results and not wait_for_pid:
                fail_payload = {"label": "no output", "result": "fail"}
                html_reporter.record_step_result(commit, skill["name"], fail_payload)
                raise RuntimeError(
                    f"Step '{step_label}' produced neither RESULT nor WAIT_FOR_PID — "
                    "cannot determine outcome; not proceeding to next step"
                )

            if _any_failed(results):
                raise RuntimeError(
                    f"Step '{step_label}' failed — stopping skill chain"
                )

            log.info("Step '%s' passed with %d result(s)", step_label, len(results))

            if wait_for_pid:
                _wait_for_test_and_parse(commit, wait_for_pid, skill["name"])
                html_reporter.record_skill_pass(commit, skill["name"])
                return

        html_reporter.record_skill_pass(commit, skill["name"])

    except Exception:
        html_reporter.record_skill_fail(commit, skill["name"])
        raise


def main():
    logging_utils.setup_logging(LOG_FILE, "skill_runner")
    html_reporter.init()

    raw = os.environ.get("SKILL_LIST", "").strip()
    skill_names = [s.strip() for s in raw.split(",") if s.strip()]

    skills = []
    for name in skill_names:
        try:
            skills.append(load_skill(name))
        except Exception as e:
            log.error("Failed to load skill '%s': %s", name, e)

    if not skills:
        log.warning("No skills loaded — skill_runner will idle without processing commits")
    else:
        log.info("Skill runner started  pid=%d  skills=%s", os.getpid(), [s["name"] for s in skills])

    if is_skill_running():
        log.warning("Stale skill_running.lock detected — clearing")
        clear_skill_running()

    while True:
        try:
            if is_skill_running() or not skills:
                time.sleep(POLL_INTERVAL)
                continue

            commit = queue_store.dequeue()
            if commit is None:
                time.sleep(POLL_INTERVAL)
                continue

            _update_last_seen(commit)

            log.info(
                "Dequeued commit %s — %s (%s)",
                commit["short_sha"], commit["message"][:60], commit["author"],
            )
            log.info("Queue remaining: %d", queue_store.size())

            set_skill_running(commit)
            try:
                for skill in skills:
                    log.info("Starting skill '%s' for commit %s", skill["name"], commit["short_sha"])
                    try:
                        run_skill(skill, commit)
                        log.info("Skill '%s' finished for commit %s", skill["name"], commit["short_sha"])
                    except Exception as e:
                        log.error("Skill '%s' raised an exception: %s", skill["name"], e, exc_info=True)
                        break
            finally:
                html_reporter.record_commit_done(commit["sha"])
                clear_skill_running()

        except Exception as e:
            log.error("Unexpected error in skill_runner loop: %s", e, exc_info=True)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

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
            parallel = getattr(step_fn, "parallel", False)
            if not parallel:
                html_reporter.record_step_start(commit, skill_name, step_header)
            log.info("Running step: %s / %s", skill_name, step_header)

            try:
                sig = inspect.signature(step_fn)
                payload = step_fn(commit) if sig.parameters else step_fn()
            except Exception as e:
                payload = {"label": step_header, "result": "fail", "detail": str(e)}

            if not parallel:
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
    """Run Claude with prompt, capture RESULT: lines from stdout.
    Returns a list of captured RESULT payloads.
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
        close_fds=True,
    )
    proc.stdin.write(prompt)
    proc.stdin.close()
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
                results.append(payload)
                log.info("Captured result: %s", raw)
            except json.JSONDecodeError as e:
                log.warning("Invalid RESULT line: %s — %s", raw, e)
        else:
            log.info("[claude] %s", line)
    proc.wait()
    return results



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
        "2. Print the required `RESULT: {...}` line as specified in that step — "
        "as a bare, standalone line with no surrounding text, explanation, or commentary.\n"
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
            results = _run_claude(prompt, commit, skill_name=skill["name"])

            if not results:
                fail_payload = {"label": "no output", "result": "fail"}
                html_reporter.record_step_result(commit, skill["name"], fail_payload)
                raise RuntimeError(
                    f"Step '{step_label}' produced no RESULT — "
                    "cannot determine outcome; not proceeding to next step"
                )

            if _any_failed(results):
                raise RuntimeError(
                    f"Step '{step_label}' failed — stopping skill chain"
                )

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

"""monitor.py — Monitor Process: polls GitHub for CCL commits and enqueues them."""
import os
import sys
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
import requests
import queue_store

load_dotenv(ROOT / ".env")

LOG_FILE = ROOT / "logs" / "monitor.log"
STATE_FILE = ROOT / "state" / "last_seen.json"
GITHUB_API = "https://api.github.com"

_PID = os.getpid()


def _setup_logging():
    LOG_FILE.parent.mkdir(exist_ok=True)
    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-7s  [monitor:%(process)d]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


log = logging.getLogger("monitor")


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def parse_watch_targets(raw):
    targets = []
    for entry in raw.split("|"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 2)
        if len(parts) < 2:
            log.warning("Skipping invalid WATCH_TARGETS entry: %s", entry)
            continue
        repo = parts[0].strip()
        branch = parts[1].strip()
        paths = [p.strip() for p in parts[2].split(",") if p.strip()] if len(parts) == 3 else []
        targets.append({"repo": repo, "branch": branch, "paths": paths})
    return targets


def github_get(path, token=None, params=None):
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    url = f"{GITHUB_API}{path}"
    if params:
        # Use safe='/' to prevent requests from encoding path separators as %2F,
        # which causes GitHub API to return 404 for path-filtered commit queries.
        url = f"{url}?{urlencode(params, safe='/')}"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_commits(owner, repo, branch, paths, token):
    if paths:
        all_commits = {}
        for path in paths:
            results = github_get(
                f"/repos/{owner}/{repo}/commits",
                token=token,
                params={"sha": branch, "path": path, "per_page": 20},
            )
            for c in results:
                all_commits[c["sha"]] = c
        return sorted(all_commits.values(), key=lambda c: c["commit"]["author"]["date"], reverse=True)
    return github_get(
        f"/repos/{owner}/{repo}/commits",
        token=token,
        params={"sha": branch, "per_page": 20},
    )


def check_target(target, state, github_token):
    repo = target["repo"]
    branch = target["branch"]
    paths = target["paths"]
    owner, repo_name = repo.split("/", 1)
    label = f"{repo}:{branch}"
    last_sha = state.get(label)
    paths_display = ", ".join(paths) if paths else "all paths"

    log.debug("[%s] Checking (%s) ...", label, paths_display)
    commits = fetch_commits(owner, repo_name, branch, paths, github_token)

    if not commits:
        log.info("[%s] No commits found.", label)
        return False

    latest_sha = commits[0]["sha"]

    if last_sha is None:
        log.info("[%s] First run — initializing to %s, no commits queued.", label, latest_sha[:8])
        state[label] = latest_sha
        return True

    if latest_sha == last_sha:
        log.debug("[%s] No new commits since %s.", label, last_sha[:8])
        return False

    new_commits = []
    for c in commits:
        if c["sha"] == last_sha:
            break
        new_commits.append(c)

    count = len(new_commits)
    log.info("[%s] %d new commit(s) detected.", label, count)

    enqueued = 0
    for c in reversed(new_commits):
        commit_info = {
            "sha": c["sha"],
            "short_sha": c["sha"][:8],
            "message": c["commit"]["message"].split("\n")[0][:120],
            "author": c["commit"]["author"]["name"],
            "repo": repo,
            "branch": branch,
            "timestamp": c["commit"]["author"]["date"],
            "enqueued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        result = queue_store.enqueue(commit_info)
        if result is True:
            log.info(
                "[%s] Enqueued %s — %s (%s)",
                label, c["sha"][:8], commit_info["message"][:60], commit_info["author"],
            )
            enqueued += 1
        elif result is None:
            log.info("[%s] Skipped %s — already in queue", label, c["sha"][:8])
        else:
            log.error("[%s] Failed to enqueue %s", label, c["sha"][:8])

    log.info("[%s] %d enqueued, queue size now: %d.", label, enqueued, queue_store.size())
    return True


def check_once(targets, github_token):
    state = load_state()
    changed = False
    for target in targets:
        try:
            if check_target(target, state, github_token):
                changed = True
        except Exception as e:
            log.error("[%s/%s] %s", target.get("repo", "?"), target.get("branch", "?"), e)
    if changed:
        save_state(state)


def main():
    _setup_logging()

    github_token = os.environ.get("GITHUB_TOKEN")
    interval_seconds = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
    watch_raw = os.environ.get("WATCH_TARGETS", "")

    if not github_token:
        log.warning("GITHUB_TOKEN not set — only public repos accessible, rate limit 60 req/hour.")
    if not watch_raw:
        log.error("WATCH_TARGETS not set in .env")
        sys.exit(1)

    targets = parse_watch_targets(watch_raw)
    if not targets:
        log.error("No valid targets in WATCH_TARGETS.")
        sys.exit(1)

    log.info("Monitor started  pid=%d", _PID)
    for t in targets:
        paths_display = ", ".join(t["paths"]) if t["paths"] else "all paths"
        log.info("  Watching: %s [%s] -> %s", t["repo"], t["branch"], paths_display)
    log.info("Polling every %ds.", interval_seconds)

    while True:
        try:
            check_once(targets, github_token)
        except Exception as e:
            log.error("Unexpected error: %s", e)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()

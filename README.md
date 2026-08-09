# commit-skill-runner

Watches one or more GitHub repository branches for new commits and automatically runs a configured pipeline of skills on each commit. Results are displayed in a live-updating browser page.

## What it does

Every time a new commit lands on a watched branch, the runner:

1. Detects the commit via GitHub API polling
2. Enqueues it (survives restarts — queue is persisted to disk)
3. Runs each configured skill in order, step by step
4. Displays pass/fail per step in `results.html`, updated in real time via SSE

A typical pipeline: **checkout → compile → regression test → Python test**

## Architecture

```
main.py                     — supervisor: spawns the three subprocesses below, auto-restarts on crash
├── monitor.py              — polls GitHub every N seconds; enqueues new commits
├── skill_runner.py         — dequeues commits; runs each skill in sequence
└── result_runner.py        — HTTP + SSE server on :8099; writes results.html
```

Skills in `skills/` call `html_reporter` (thin HTTP client) to post step results to `result_runner`. The browser page auto-refreshes via SSE — no manual reload needed.

## Prerequisites

- Python 3.10+
- A GitHub Personal Access Token (for private repos; public repos work with a lower rate limit)

```
pip install psutil python-dotenv requests
```

## Setup

```
cp .env.example .env
```

Edit `.env` — at minimum fill in:

| Variable | Description |
|---|---|
| `GITHUB_TOKEN` | GitHub PAT — `repo` scope for private repos |
| `WATCH_TARGETS` | Which repo/branch/paths to watch (see format below) |
| `SKILL_LIST` | Comma-separated skill names to run per commit, in order |

Then fill in any skill-specific variables (see [Skill configuration](#skill-configuration) below).

### WATCH_TARGETS format

```
owner/repo:branch[:path1,path2]
```

Separate multiple targets with `|`. Path filtering is optional — omit to watch all paths.

```
# Watch all commits on integration branch:
WATCH_TARGETS=MyOrg/myrepo:integration

# Watch only commits that touch specific paths:
WATCH_TARGETS=MyOrg/myrepo:integration:src/foo,src/bar

# Watch multiple targets:
WATCH_TARGETS=MyOrg/repo1:main|MyOrg/repo2:develop:content/api
```

### SKILL_LIST format

```
SKILL_LIST=compile_ui_bll_skill,regression_test_skill,ccl_py_test_skill
```

Skills run left to right. If a skill fails, subsequent skills are skipped for that commit.

## Running

```
python Scripts/main.py
```

`main.py` starts all three subprocesses and keeps them alive — if one crashes it restarts automatically after 10 seconds. The browser opens automatically when the first commit is dequeued.

To stop everything, kill the `main.py` process (Ctrl+C or Task Manager). All subprocesses are terminated.

## Viewing results

Open `results.html` in a browser, or navigate to `http://localhost:8099`. The page live-updates as steps complete.

Each row is one commit. Each skill shows its steps inline:

```
compile_ui_bll_skill:  checkout: pass → build bll: pass → build ui: pass  (pass)
regression_test_skill: Run Regression Test: pass [IntegrationResultReport_xxx.html]  (pass)
ccl_py_test_skill:     git sync: pass → setup deps: pass → py test result: pass  (pass)
```

## Logs

| File | Contents |
|---|---|
| `logs/main.log` | Subprocess lifecycle |
| `logs/monitor.log` | GitHub polling, commits enqueued |
| `logs/skill_runner.log` | Skill execution, step results |
| `logs/result_runner.log` | HTTP requests from skill_runner |
| `logs/build_<sha>_bll.log` | Raw BLL build output |
| `logs/build_<sha>_ui.log` | Raw UI build output |

## Skills

Skills live in `skills/`. Two formats are supported:

| Format | When to use |
|---|---|
| `.py` | Needs real tools — compile, run tests, check exit codes |
| `.md` | Needs Claude's judgment — parse logs, interpret output |

`SKILL_LIST` in `.env` controls which skills run and in what order. Skill names are filenames without extension.

### Existing skills

| Skill | File | What it does |
|---|---|---|
| `compile_ui_bll_skill` | `skills/compile_ui_bll_skill.py` | Checks out the commit, then builds BLL and UI in parallel |
| `regression_test_skill` | `skills/regression_test_skill.py` | Runs the full regression test suite via `TestCenter-AutoTest`; links to the HTML report |
| `ccl_py_test_skill` | `skills/ccl_py_test_skill.py` | Syncs the Python test repo, installs deps, runs pytest |

### Adding a new skill

Create `skills/your_skill_name.py` and expose a `steps()` function:

```python
def my_step(commit):          # declare `commit` param if you need the commit dict
    # do work ...
    return {"label": "my step", "result": "pass"}   # or "fail"

def another_step():           # omit param if you don't need commit
    return {"label": "another step", "result": "pass"}

def steps():
    return [
        ("## Step 1 — My step",     my_step),
        ("## Step 2 — Another step", another_step),
    ]
```

Rules:
- `result` must be `"pass"` or `"fail"`.
- Add `"detail"` for error context: `{"label": "...", "result": "fail", "detail": "reason"}`.
- Add `"link"` with a local `.html` path to show a clickable report link in the results page.
- The runner stops the skill on the first failing step — do not implement this yourself.

Then add the skill name to `SKILL_LIST` in `.env` and restart.

## Skill configuration

Each skill reads its settings from `.env`. Add the relevant variables before running.

### compile_ui_bll_skill

| Variable | Description |
|---|---|
| `STC_BUILD_ROOT` | Path to the testcenter repo (e.g. `C:\work\testcenter`) |

### regression_test_skill

| Variable | Description |
|---|---|
| `AUTOTEST_ROOT` | Root of the `TestCenter-AutoTest` harness |
| `REGRESSION_RESULT_DIR` | Must match `result_dir` in `TestCenter-AutoTest/Config/config.yaml` |

### ccl_py_test_skill

| Variable | Description |
|---|---|
| `PY_TEST_REPO` | Root of the Python test repo |
| `PY_TEST_CMD` | Full pytest command (PowerShell syntax). Use `-k testname` to run a single test |

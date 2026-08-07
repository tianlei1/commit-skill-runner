# commit-skill-runner

Monitors GitHub repository commits and automatically runs a configured pipeline of skills on each new commit. Results are displayed in a live-updating browser page.

## Architecture

```
main.py
├── monitor.py        — polls GitHub every 60s, enqueues CCL commits
├── skill_runner.py   — dequeues commits, runs each skill in sequence
└── result_runner.py  — HTTP+SSE server, updates results.html in real time
```

Skills report results step-by-step to `result_runner` via HTTP. The browser auto-refreshes via SSE — no polling needed.

## Setup

```bash
cp .env.example .env
# Fill in .env with your token, watch targets, and skill list
pip install psutil python-dotenv requests
```

## Running

```bash
python Scripts/main.py
```

The browser opens automatically when the first commit is dequeued. Results persist across restarts via `state/result_queue.jsonl`.

## Skills

Skills live in `skills/`. Two formats are supported:

| Format | When to use |
|--------|------------|
| `.py`  | Needs real tools: compile, run tests, read files |
| `.md`  | Needs Claude's judgment: parse logs, interpret output |

See `CLAUDE.md` for the full skill authoring contract.

### Built-in skills

| Skill | What it does |
|-------|-------------|
| `compile_ui_bll` | Checkout commit, build BLL and UI in parallel |
| `ccl_regression_test_skill` | Configure and run CCL DUT regression test |
| `ccl_py_test_skill` | Sync py-test repo to main, run Python test suite |

## Configuration (`.env`)

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | Personal access token for GitHub API |
| `WATCH_TARGETS` | `owner/repo:branch[:path1,path2]` — pipe-separated |
| `SKILL_LIST` | Comma-separated skill names to run per commit |
| `STC_BUILD_ROOT` | Path to testcenter repo (compile_ui_bll) |
| `AUTOTEST_ROOT` | Path to TestCenter-AutoTest (ccl_regression_test_skill) |
| `PY_TEST_REPO` | Path to py-test repo (ccl_py_test_skill) |
| `PY_TEST_CMD` | Command to launch the Python test suite |

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

Skills are repo-specific — you write the skills that match your project's build and test workflow. The `SKILL_LIST` in `.env` controls which skills run and in what order.

## Configuration (`.env`)

Copy `.env.example` to `.env` and fill in the values for your project.

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | Personal access token for GitHub API |
| `WATCH_TARGETS` | `owner/repo:branch[:path1,path2]` — pipe-separated |
| `SKILL_LIST` | Comma-separated skill names to run per commit (in order) |

Any additional variables are skill-specific — each skill reads whatever it needs from the environment. See the skills in `skills/` and `.env.example` for examples.

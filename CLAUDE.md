# commit-skill-runner

Automated pipeline that dequeues commits and runs a configured list of skills on each one.

## Adding a New Skill

Skills live in `skills/`. Each skill file is either `.py` or `.md`.

### The Step Contract (applies to ALL skills)

Every skill must be written as a sequence of steps. The runner enforces these rules for every step — **do not implement this logic yourself**:

- **Block until done** — each step runs to completion before the next one starts.
- **Always report a result** — every step must produce a result (pass or fail). A step with no result is treated as a failure.
- **Stop on failure** — if a step fails, the runner stops the skill immediately and does not run subsequent steps.
- **Update HTML after every step** — the runner writes the result to `results.html` after each step, whether it passed or failed.

### Writing a `.py` Skill

Expose a `steps()` function (no parameters) that returns a list of `(step_header, callable)` tuples.

Each callable returns a result payload dict. If a step needs the current commit, declare `commit` as its parameter — the runner passes it automatically. Steps that don't need commit take no arguments.

```python
def checkout(commit):          # needs commit → runner passes it
    sha = commit["sha"]
    # do work ...
    return {"label": "checkout", "result": "pass"}  # or "fail"

def run_tests():               # doesn't need commit → no parameter
    # do work ...
    return {"label": "tests", "result": "pass"}

def steps():
    return [
        ("## Step 1 — Checkout", checkout),
        ("## Step 2 — Run tests", run_tests),
    ]
```

Rules:
- `result` must be `"pass"` or `"fail"`.
- Optionally include `"detail"` for error context: `{"label": "...", "result": "fail", "detail": "reason"}`.
- Do **not** call `html_reporter` or write to `result_queue` yourself — the runner does this.
- Do **not** implement stop-on-failure logic yourself — the runner does this.
- If a step raises an exception, the runner catches it, records it as `fail`, and stops the skill.

### Writing a `.md` Skill

Structure the file as `## Step N — Description` sections. Each step is sent to Claude as a separate, isolated invocation.

Each step **must** print exactly one of these lines as its final output — nothing else:

```
RESULT: {"label": "my label", "result": "pass"}
RESULT: {"label": "my label", "result": "fail"}
```

Or, for a long-running background process:

```
WAIT_FOR_PID: C:\path\to\process.pid
```

Rules:
- Print the `RESULT:` or `WAIT_FOR_PID:` line bare — no surrounding text, no explanation.
- A step that prints neither is treated as a failure.
- Do **not** instruct Claude to check previous step results or decide whether to continue — the runner controls step sequencing.

### Which format to use?

| Situation | Use |
|---|---|
| Needs real tools: compile, run tests, read files | `.py` — deterministic, captures output, exact exit codes |
| Needs Claude's judgment: parse logs, find files by pattern, interpret output | `.md` — Claude handles the reasoning |

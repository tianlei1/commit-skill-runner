"""result_runner.py — Result Process: HTTP+SSE server.
Receives step results from skill_runner, updates results.html, pushes SSE to browser."""

import http.server
import json
import logging
import os
import queue
import re
import socketserver
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

import logging_utils

RESULT_HTML = ROOT / "results.html"
BROWSER_FLAG = ROOT / "state" / "browser_opened.flag"
LOG_FILE = ROOT / "logs" / "result_runner.log"
PORT = 8099

# ── HTML template ─────────────────────────────────────────────────────────────

_HTML_TEMPLATE = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Skill Runner Results</title>
<style>
  body {{ font-family: monospace; margin: 20px; background: #fff; color: #222; }}
  h2 {{ margin-bottom: 4px; }}
  #status {{ font-size: 0.82em; color: #888; margin-bottom: 12px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ddd; padding: 7px 12px; text-align: left; vertical-align: top; }}
  th {{ background: #f4f4f4; white-space: nowrap; }}
  tr:hover > td {{ background: #fafafa; }}
  td.meta {{ white-space: nowrap; }}
  .skill-row {{ margin: 3px 0; line-height: 1.6; }}
  .skill-name {{ font-weight: bold; margin-right: 2px; }}
  .arrow {{ color: #bbb; margin: 0 3px; }}
  .step-result {{ }}
  .step-label {{ color: #555; }}
  .v-pass {{ color: #2a7f2a; font-weight: bold; }}
  .v-fail {{ color: #cc2222; font-weight: bold; }}
  .v-running {{ color: #b87a00; font-style: italic; }}
  .v-link a {{ color: #0055bb; }}
  .badge {{ font-size: 0.88em; margin-left: 6px; padding: 1px 6px; border-radius: 3px;
           border: 1px solid currentColor; }}
  .badge-pass {{ color: #2a7f2a; }}
  .badge-fail {{ color: #cc2222; }}
  .badge-running {{ color: #b87a00; }}
  .pending-skill {{ color: #aaa; font-style: italic; margin: 3px 0; }}
</style>
</head>
<body>
<h2>Skill Runner Results</h2>
<div id="status">Live &mdash; last update: <span id="ts">-</span></div>
<table>
<thead>
  <tr><th>Date</th><th>Commit</th><th>Commit Author</th><th>Results</th></tr>
</thead>
<tbody>
<!-- ROWS -->
</tbody>
</table>
<script>
  function connect() {{
    var es = new EventSource('http://localhost:{PORT}/events');
    es.onmessage = function() {{
      document.getElementById('ts').textContent = new Date().toLocaleTimeString();
      location.reload();
    }};
    es.onerror = function() {{ es.close(); setTimeout(connect, 3000); }};
  }}
  connect();
</script>
</body>
</html>
"""

_ROW_MARKER = "<!-- ROWS -->"

# ── In-memory state ───────────────────────────────────────────────────────────

_state_lock = threading.Lock()
_pending = {}   # sha → {sha, short_sha, author, repo, timestamp, skills: [...]}

# ── SSE ───────────────────────────────────────────────────────────────────────

_sse_clients: set = set()  # set of queue.Queue — Queue is identity-hashed
_sse_lock = threading.Lock()


def _sse_notify():
    with _sse_lock:
        dead = set()
        for q in _sse_clients:
            try:
                q.put_nowait("update")
            except queue.Full:
                dead.add(q)
        _sse_clients.difference_update(dead)


# ── Rendering ─────────────────────────────────────────────────────────────────

def _render_value(value):
    if value is None:
        return '<span class="v-running">running&#8230;</span>'
    v = str(value)
    lo = v.lower()
    if lo in ("pass", "success"):
        return f'<span class="v-pass">{v}</span>'
    if lo in ("fail", "failed"):
        return f'<span class="v-fail">{v}</span>'
    if lo.endswith((".html", ".htm")):
        uri = Path(v).as_uri()
        name = Path(v).name
        return f'<span class="v-link"><a href="{uri}">{name}</a></span>'
    return f'<span>{v}</span>'


def _render_skills_cell(entry):
    lines = []
    for skill in entry["skills"]:
        name = skill["name"]
        status = skill["status"]
        steps = skill["steps"]

        if status == "pending":
            lines.append(
                f'<div class="pending-skill">'
                f'<span class="skill-name">{name}</span>: (pending)</div>'
            )
            continue

        parts = []
        for step in steps:
            label = step["label"]
            val_html = _render_value(step["result"])
            link = step.get("link")
            if link:
                uri = Path(link).as_uri()
                name = Path(link).name
                val_html += f' <a href="{uri}">[{name}]</a>'
            parts.append(
                f'<span class="step-result">'
                f'<span class="step-label">{label}</span>: {val_html}'
                f'</span>'
            )

        chain = '<span class="arrow">&#8594;</span>'.join(parts) if parts else ""
        badge_cls = f"badge-{status}"
        badge = f'<span class="badge {badge_cls}">({status})</span>'
        lines.append(
            f'<div class="skill-row">'
            f'<span class="skill-name">{name}</span>:&nbsp;'
            f'{chain}{badge}'
            f'</div>'
        )
    return "\n".join(lines) if lines else "<em>processing&#8230;</em>"


def _render_row(entry):
    skills_html = _render_skills_cell(entry)
    short = entry["short_sha"]
    repo = entry.get("repo", "")
    sha = entry.get("sha", short)
    if repo:
        commit_url = f"https://github.com/{repo}/commit/{sha}"
        sha_html = f'<a href="{commit_url}" target="_blank">{short}</a>'
    else:
        sha_html = short
    return (
        f'<tr data-sha="{short}">'
        f'<td class="meta">{entry["timestamp"]}</td>'
        f'<td class="meta">{sha_html}</td>'
        f'<td class="meta">{entry["author"]}</td>'
        f'<td>{skills_html}</td>'
        f'</tr>\n'
    )


def _write_html():
    """Regenerate results.html from all _pending entries. Call under _state_lock."""
    rows = "".join(_render_row(e) for e in _pending.values())
    content = _HTML_TEMPLATE.replace(_ROW_MARKER, rows)
    RESULT_HTML.parent.mkdir(parents=True, exist_ok=True)
    RESULT_HTML.write_text(content, encoding="utf-8")


def _flush(sha):
    """Regenerate results.html and push SSE. Called under _state_lock."""
    if sha not in _pending:
        return
    _write_html()
    _sse_notify()


# ── State helpers (call under _state_lock) ────────────────────────────────────

def _get_or_create(commit):
    sha = commit["sha"]
    if sha not in _pending:
        _pending[sha] = {
            "sha":       sha,
            "short_sha": commit.get("short_sha", sha[:8]),
            "author":    commit.get("author", "unknown"),
            "repo":      commit.get("repo", ""),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "skills":    [],
        }
    return _pending[sha]


def _find_skill(entry, skill_name):
    for s in entry["skills"]:
        if s["name"] == skill_name:
            return s
    return None


def _step_display(step_header):
    return re.sub(r'^#+\s*', '', step_header).strip()


# ── API operations ────────────────────────────────────────────────────────────

def _op_skill_start(commit, skill_name):
    entry = _get_or_create(commit)
    if not _find_skill(entry, skill_name):
        entry["skills"].append({"name": skill_name, "status": "running", "steps": []})
    _flush(commit["sha"])


def _op_step_start(commit, skill_name, step_header):
    entry = _get_or_create(commit)
    skill = _find_skill(entry, skill_name)
    if skill is None:
        skill = {"name": skill_name, "status": "running", "steps": []}
        entry["skills"].append(skill)
    label = _step_display(step_header)
    skill["steps"].append({"label": label, "result": None, "status": "running"})
    _flush(commit["sha"])


def _op_step_result(commit, skill_name, payload):
    entry = _get_or_create(commit)
    skill = _find_skill(entry, skill_name)
    if skill is None:
        skill = {"name": skill_name, "status": "running", "steps": []}
        entry["skills"].append(skill)

    label  = payload.get("label", "unknown")
    result = payload.get("result", "unknown")
    link   = payload.get("link")
    status = "fail" if str(result).lower() in ("fail", "failed") else "pass"

    # Label-exact match first — lets parallel sub-steps each update their own entry
    for step in reversed(skill["steps"]):
        if step["status"] == "running" and step["label"] == label:
            step["result"] = result
            step["status"] = status
            if link:
                step["link"] = link
            break
    else:
        # Fallback: update the last running step (normal sequential case)
        for step in reversed(skill["steps"]):
            if step["status"] == "running":
                step["label"]  = label
                step["result"] = result
                step["status"] = status
                if link:
                    step["link"] = link
                break
        else:
            log.warning("step_result with no running step for '%s/%s' — ignored", skill_name, label)
    _flush(commit["sha"])


def _op_skill_finish(commit, skill_name, status):
    entry = _get_or_create(commit)
    skill = _find_skill(entry, skill_name)
    if skill:
        skill["status"] = status
    _flush(commit["sha"])


# ── Dispatch table ────────────────────────────────────────────────────────────

_PATH_DISPATCH = {
    "/api/skill_start":  ("skill_start",  ["commit", "skill_name"],                 _op_skill_start,                               True),
    "/api/step_start":   ("step_start",   ["commit", "skill_name", "step_header"],  _op_step_start,                                False),
    "/api/step_result":  ("step_result",  ["commit", "skill_name", "payload"],      _op_step_result,                               False),
    "/api/skill_pass":   ("skill_pass",   ["commit", "skill_name"],                 lambda c, sn: _op_skill_finish(c, sn, "pass"), False),
    "/api/skill_fail":   ("skill_fail",   ["commit", "skill_name"],                 lambda c, sn: _op_skill_finish(c, sn, "fail"), False),
    "/api/commit_done":  ("commit_done",  ["sha"],                                  None,                                          False),
}


# ── Browser auto-open ─────────────────────────────────────────────────────────

_browser_lock = threading.Lock()
_browser_opened = False


def _maybe_open_browser():
    global _browser_opened
    with _browser_lock:
        if _browser_opened:
            return
        _browser_opened = True
        if BROWSER_FLAG.exists():
            return
        BROWSER_FLAG.parent.mkdir(parents=True, exist_ok=True)
        BROWSER_FLAG.touch()
    log.info("Auto-opening browser at http://localhost:%d", PORT)
    threading.Thread(
        target=lambda: webbrowser.open(f"http://localhost:{PORT}"),
        daemon=True,
    ).start()


# ── HTTP server ───────────────────────────────────────────────────────────────

class _Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/events":
            self._handle_sse()
        else:
            self._serve_html()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._respond(400, b"bad json")
            return

        dispatch = _PATH_DISPATCH.get(self.path)
        if not dispatch:
            self._respond(404, b"not found")
            return

        try:
            ev_type, keys, op, open_browser = dispatch
            ev = {"type": ev_type, **{k: body[k] for k in keys}}
            if op is not None:
                with _state_lock:
                    op(*[ev[k] for k in keys])
            if open_browser:
                _maybe_open_browser()
            self._respond(200, b"ok")
        except Exception as e:
            log.exception("Error handling %s", self.path)
            self._respond(500, str(e).encode())

    def _respond(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        if RESULT_HTML.exists():
            content = RESULT_HTML.read_bytes()
        else:
            content = _HTML_TEMPLATE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _handle_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        q = queue.Queue(maxsize=4)
        with _sse_lock:
            _sse_clients.add(q)
        try:
            while True:
                try:
                    q.get(timeout=25)
                    self.wfile.write(b"data: update\n\n")
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except Exception:
            pass
        finally:
            with _sse_lock:
                _sse_clients.discard(q)

    def log_message(self, *args):
        pass  # suppress per-request stdout noise


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


# ── Entry point ───────────────────────────────────────────────────────────────

log = logging.getLogger("result_runner")


def main():
    logging_utils.setup_logging(LOG_FILE, "result_runner")
    log.info("result_runner starting  pid=%d", os.getpid())
    server = _ThreadingHTTPServer(("localhost", PORT), _Handler)
    log.info("Listening on http://localhost:%d", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()

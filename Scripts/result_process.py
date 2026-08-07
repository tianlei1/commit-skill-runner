"""result_process.py — Manual tool: rebuilds results.html from result_queue.jsonl.

Run this script directly to regenerate the HTML from the audit log,
e.g. after the HTML file is lost or corrupted.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

import html_reporter

RESULT_QUEUE = ROOT / "state" / "result_queue.jsonl"


if __name__ == "__main__":
    html_reporter.replay_queue_file(RESULT_QUEUE)
    print(f"HTML rebuilt from {RESULT_QUEUE}")
    print(f"Output: {html_reporter.RESULT_HTML}")

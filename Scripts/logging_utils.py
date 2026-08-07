"""logging_utils.py — Shared logging setup for all subprocesses."""
import logging
import sys


def setup_logging(log_file, name):
    log_file.parent.mkdir(exist_ok=True)
    fmt = logging.Formatter(
        fmt=f"%(asctime)s  %(levelname)-7s  [{name}:%(process)d]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

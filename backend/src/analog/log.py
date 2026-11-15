"""Logging for the `analog` CLI: progress on screen, and everything in a log file per command.

Every CLI run writes data/logs/<YYYYmmdd-HHMMSS>_<command>.log with timestamps, the full
command line, every message shown on screen (including plain print output), extra detail
(DEBUG level, e.g. each classifier bag), step durations, and the error or traceback if it fails.

In library code:   log = logging.getLogger(__name__)   ...   log.info("...")
                   with timed(log, "Scoring region X"): ...
Outside the CLI (notebooks, the API) nothing is configured, so these calls stay silent.

Environment: ANALOG_LOG_DIR changes the folder; ANALOG_NO_LOG_FILE=1 disables the file.
Only the newest KEEP files are kept.
"""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import paths

KEEP = 100
ROOT_LOGGER = "analog"
FILE_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)-18s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def log_dir() -> Path:
    return Path(os.environ.get("ANALOG_LOG_DIR") or (paths.DATA / "logs"))


def _fmt_secs(s: float) -> str:
    return f"{s:.1f} s" if s < 90 else f"{int(s // 60)} min {s % 60:.0f} s"


@contextlib.contextmanager
def timed(log: logging.Logger, what: str, level: int = logging.INFO):
    """Log `what…` when a step starts and `what: done in N s` when it ends (or fails)."""
    log.log(level, "%s…", what)
    t0 = time.perf_counter()
    try:
        yield
    except BaseException:
        log.log(level, "%s: stopped after %s", what, _fmt_secs(time.perf_counter() - t0))
        raise
    log.log(level, "%s: done in %s", what, _fmt_secs(time.perf_counter() - t0))


class _StdoutToLog:
    """Writes to the real stdout and also sends each complete line to the log file
    (so the many existing print() messages are logged with timestamps)."""

    def __init__(self, real, logger: logging.Logger):
        self.real, self.logger, self.buf = real, logger, ""

    def write(self, s: str) -> int:
        self.real.write(s)
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.logger.info(line.rstrip())
        return len(s)

    def flush(self) -> None:
        self.real.flush()

    def __getattr__(self, name):  # encoding, isatty, fileno... behave like the real stream
        return getattr(self.real, name)


def _prune(folder: Path) -> None:
    files = sorted(folder.glob("*.log"))
    for old in files[:-KEEP]:
        with contextlib.suppress(OSError):
            old.unlink()


@contextlib.contextmanager
def session(command: str, argv: list[str]):
    """Set up console + file logging for one CLI command, and record how it ended."""
    root = logging.getLogger(ROOT_LOGGER)
    out = logging.getLogger(ROOT_LOGGER + ".stdout")  # captured print() lines: file only
    old_level, old_prop = root.level, root.propagate
    root.setLevel(logging.DEBUG)
    root.propagate = False
    out.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console)

    file_handler, path = None, None
    if not os.environ.get("ANALOG_NO_LOG_FILE"):
        folder = log_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{datetime.now():%Y%m%d-%H%M%S}_{command}.log"
            file_handler = logging.FileHandler(path, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))
            root.addHandler(file_handler)
            out.addHandler(file_handler)
            _prune(folder)
        except OSError as e:  # a read-only folder must not stop the command
            root.warning("Could not create a log file in %s: %s", folder, e)
            file_handler, path = None, None

    real_stdout = sys.stdout
    if file_handler:
        sys.stdout = _StdoutToLog(real_stdout, out)
        console.setStream(real_stdout)  # logger messages go straight to the screen, logged once

    log = logging.getLogger(ROOT_LOGGER + ".cli")
    log.debug("command: analog %s", " ".join(argv))
    log.debug("python %s on %s; project root %s", platform.python_version(), platform.platform(), paths.ROOT)
    t0 = time.perf_counter()
    status = "failed"
    try:
        yield path
        status = "finished"
    except SystemExit as e:
        if e.code not in (None, 0):
            # Python prints the message on screen when it exits; record it in the file only.
            out.error("stopped: %s", e.code)
        else:
            status = "finished"
        raise
    except KeyboardInterrupt:
        status = "interrupted"
        raise
    except BaseException:
        log.debug("traceback:\n%s", traceback.format_exc())
        raise
    finally:
        sys.stdout = real_stdout
        elapsed = _fmt_secs(time.perf_counter() - t0)
        if file_handler:
            log.debug("%s in %s", status, elapsed)
            real_stdout.write(f"Log: {path}\n")
        for h in (console, file_handler):
            if h:
                root.removeHandler(h)
                out.removeHandler(h)
                h.close()
        root.setLevel(old_level)
        root.propagate = old_prop

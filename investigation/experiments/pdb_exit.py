"""Experiment (PLAN.md phase 2.3): leave pdb on SIGTERM, using the method in $PDB_EXIT.

- api-continue: make the prompt return "continue" (pdb continues normally)
- api-interrupt: raise KeyboardInterrupt in the main thread, then stop the prompt thread
- eof: dup2 /dev/null onto fd 0, i.e. Ctrl+D
- type: nothing in pytest: the harness types "continue" into screen
- none: nothing (control)

Load with -p pdb_exit (investigation/experiments in PYTHONPATH). Logs to $EXP_LOG.
"""

# Throwaway investigation code (removed in PLAN.md phase 7): lighter lint rules.
# ruff: noqa: ANN401, SLF001, SIM115

from __future__ import annotations

import pytest

import logging
import os
import signal
import sys
import threading
import time

from typing import Any

T0 = time.monotonic()
METHOD = os.environ.get("PDB_EXIT", "none")
_log_fd = os.open(os.environ["EXP_LOG"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
active_pdb: Any = None
REDIRECT = os.environ.get("REDIRECT") == "1"
_config: pytest.Config | None = None
_redirected = False


def _redirect() -> None:
    """Point fds 0/1/2 and pytest's saved copies of them away from the terminal."""
    global _redirected
    if _redirected:
        return
    _redirected = True
    out = os.open(os.environ["EXP_LOG"] + ".out", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    null = os.open(os.devnull, os.O_RDONLY)
    for f in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        try:
            if f is not None:
                f.flush()
        except Exception as e:
            log(f"flush {f!r} failed: {e!r}")
    capman = _config.pluginmanager.getplugin("capturemanager") if _config else None
    mc = getattr(capman, "_global_capturing", None)
    saved = []
    for cap, src in ((getattr(mc, "in_", None), null), (getattr(mc, "out", None), out),
                     (getattr(mc, "err", None), out)):
        if cap is not None and hasattr(cap, "targetfd_save"):
            os.dup2(src, cap.targetfd_save)
            saved.append(cap.targetfd_save)
    os.dup2(null, 0)
    os.dup2(out, 1)
    os.dup2(out, 2)
    os.close(out)
    os.close(null)
    log(f"redirected fds 0/1/2 and pytest saved fds {saved}")


def log(msg: str) -> None:
    os.write(_log_fd, f"t+{time.monotonic() - T0:7.3f}s [{threading.current_thread().name}] {msg}\n".encode())


def _exit_prompt(**kwargs: Any) -> None:
    """Make the pending prompt_toolkit prompt (running in another thread) return."""
    app = active_pdb.pt_app.app
    loop = app.loop
    log(f"exit_prompt: app.is_running={app.is_running} loop={loop!r}")
    if loop is not None and app.is_running:
        loop.call_soon_threadsafe(lambda: app.exit(**kwargs))


def _on_term(signum: int, frame: Any) -> None:
    log(f"{signal.Signals(signum).name}, method={METHOD}, in_pdb={active_pdb is not None}")
    if REDIRECT:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        _redirect()
    if active_pdb is None:
        return
    if METHOD == "api-continue":
        _exit_prompt(result="continue")
    elif METHOD == "api-interrupt":
        _exit_prompt(exception=EOFError)
        raise KeyboardInterrupt
    elif METHOD == "eof":
        fd = os.open(os.devnull, os.O_RDONLY)
        os.dup2(fd, 0)
        os.close(fd)
        log("fd 0 -> /dev/null")


def pytest_configure(config: pytest.Config) -> None:
    log(f"start pid={os.getpid()} method={METHOD}")
    with open(os.environ["EXP_LOG"] + ".pid", "w") as f:
        f.write(str(os.getpid()))
    global _config
    _config = config
    signal.signal(signal.SIGTERM, _on_term)
    if REDIRECT:
        signal.signal(signal.SIGHUP, _on_term)

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log(f"logging: {record.getMessage()}")
    logging.getLogger().addHandler(_Handler(level=logging.INFO))


def pytest_enter_pdb(config: pytest.Config, pdb: Any) -> None:
    global active_pdb
    active_pdb = pdb
    log(f"enter pdb {type(pdb).__mro__[1].__name__}")
    if os.environ.get("SAFE_PROMPT") == "1" and "_prompt" not in vars(pdb):
        # A dead terminal makes the prompt raise EOFError or OSError (EIO) from
        # its thread: turn that into "continue" instead of quit / INTERNAL_ERROR.
        orig = pdb._prompt

        def _prompt() -> str:
            try:
                return orig()
            except (EOFError, OSError) as e:
                log(f"prompt failed ({e!r}): continue")
                return "continue"
        pdb._prompt = _prompt


def pytest_leave_pdb(config: pytest.Config, pdb: Any) -> None:
    global active_pdb
    active_pdb = None
    log("leave pdb")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    log(f"sessionfinish exitstatus={exitstatus!r}")


def pytest_unconfigure(config: pytest.Config) -> None:
    log(f"unconfigure; threads alive: {[t.name for t in threading.enumerate()]}")

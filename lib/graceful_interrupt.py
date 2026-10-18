"""
Graceful interruption of pytest: a fixture setup or teardown in progress isn't interrupted.

By default, interrupting pytest (Ctrl+C, i.e. SIGINT) raises KeyboardInterrupt wherever it is. pytest then tears down
the fixtures that are set up, but a fixture setup interrupted before its yield never gets its teardown, and an
interrupted teardown is left half done. With this plugin, the first interruption:

- stops the session (no more tests), so that the fixtures are torn down,
- interrupts the current test, as usual (with --pdb, the debugger opens where the test is interrupted),
- lets a fixture setup in progress complete, and ends the session before the next fixture setup, or before the test,
- lets a teardown in progress go on.

A second interruption interrupts whatever pytest is doing, e.g. the teardown.

Only pytest itself is concerned: in a terminal, Ctrl+C also interrupts the commands that pytest runs (they're in its
process group), so a fixture setup running a command may fail anyway. A SIGINT sent to pytest only (kill -INT <pid>)
doesn't have this problem.

The plugin leaves alone a SIGINT that pytest ignores (e.g. started in the background of a script, with `&`), or that
something else handles.
"""

from __future__ import annotations

import pytest

import os
import signal
from types import FrameType

from typing import Any, Generator, NoReturn

def pytest_configure(config: pytest.Config) -> None:
    # Only if SIGINT raises KeyboardInterrupt, as by default
    if signal.getsignal(signal.SIGINT) is signal.default_int_handler:
        config.pluginmanager.register(GracefulInterrupt(config), "graceful_interrupt_handler")

class GracefulInterrupt:

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.session: pytest.Session | None = None
        self.setup_depth = 0
        self.in_teardown = False
        self.interrupted = False

        signal.signal(signal.SIGINT, self._on_sigint)

    def _on_sigint(self, signum: int, frame: FrameType | None) -> None:
        __tracebackhide__ = True

        # Let pytest handle the subsequent interruptions
        self.interrupted = True
        signal.signal(signal.SIGINT, signal.default_int_handler)

        # Notify pytest that it should stop
        if self.session is not None:
            self.session.shouldstop = "interrupted"

        # Pytest is currently running the teardown
        if self.in_teardown:
            message = "ending the session once the teardown is done"
        # Pytest is currently running a fixture setup
        elif self.setup_depth > 0:
            message = "ending the session once the fixture setup is done"
        # Pytest is in a safe section, interrupt it
        else:
            raise KeyboardInterrupt

        # Notify the user and let the plugin react to the `interrupted` flag later
        self._write(f"\n===== Interrupted: {message} (interrupt again not to wait) =====\n")

    @staticmethod
    def _exit(reason: str) -> NoReturn:
        """
        End the session after a fixture setup, for an interruption received during it.
        """
        pytest.exit(f"Interrupted, {reason}", returncode=pytest.ExitCode.INTERRUPTED)

    def _write(self, msg: str) -> None:
        """Write to pytest's output, even during a test whose output it captures"""
        capturing = getattr(self.config.pluginmanager.getplugin("capturemanager"), "_global_capturing", None)
        try:
            os.write(getattr(getattr(capturing, "out", None), "targetfd_save", 1), msg.encode())
        except OSError:
            pass

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.session = session

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(self, fixturedef: pytest.FixtureDef[Any],
                             request: pytest.FixtureRequest) -> Generator[None, Any, Any]:
        # Interrupted during a previous fixture setup, end the session before starting a new setup
        if self.interrupted:
            self._exit(f"before the setup of {fixturedef.argname}")
        self.setup_depth += 1
        try:
            return (yield)
        finally:
            self.setup_depth -= 1

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, Any, Any]:
        # Interrupted during the setup of the last fixture, end the session before the test
        if self.interrupted:
            self._exit("after the setup")
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_teardown(self, item: pytest.Item, nextitem: pytest.Item | None) -> Generator[None, Any, Any]:
        self.in_teardown = True
        try:
            return (yield)
        finally:
            self.in_teardown = False

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self) -> None:
        # After an interruption, the remaining fixtures are torn down from here
        self.in_teardown = True

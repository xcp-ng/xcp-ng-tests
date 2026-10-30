"""
Graceful interruption of pytest: a fixture setup or teardown in progress isn't interrupted.

By default, interrupting pytest (Ctrl+C, i.e. SIGINT) raises KeyboardInterrupt wherever it is. pytest then tears down
the fixtures that are set up, but a fixture setup interrupted before its yield never gets its teardown, and an
interrupted teardown is left half done. With this plugin, the first interruption:

- stops the session (no more tests), so that the fixtures are torn down,
- interrupts the current test, as usual (with --pdb, the debugger opens where the test is interrupted),
- lets a fixture setup in progress complete, and ends the session before the next fixture setup, or before the test,
- lets a teardown in progress go on.

A second interruption behaves as the interrupt would without this plugin, i.e. it sends a `KeyboardInterrupt` to
the code that is currently running.

Other sections of code can be shielded like a fixture setup: see shielded().

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
from contextlib import contextmanager
from types import FrameType

from typing import Any, Generator, Iterator, NoReturn

# The plugin's handler, if registered
_handler: GracefulInterrupt | None = None

def pytest_configure(config: pytest.Config) -> None:
    global _handler
    # Only if SIGINT raises KeyboardInterrupt, as by default
    if signal.getsignal(signal.SIGINT) is signal.default_int_handler:
        _handler = GracefulInterrupt(config)
        config.pluginmanager.register(_handler, "graceful_interrupt_handler")

@contextmanager
def shielded() -> Iterator[None]:
    """
    Shield a section of code like a fixture setup: a first interruption doesn't interrupt it, but ends the session once
    it's done. E.g. to create a resource and register its cleanup, which an interruption in between would skip.

    In a fixture setup or teardown, it does nothing: they're already shielded.
    """
    if _handler is None:
        yield
    else:
        with _handler.shielded():
            yield

class GracefulInterrupt:

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.session: pytest.Session | None = None
        self.setup_depth = 0
        self.shielded_depth = 0
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
        # Pytest is currently running a shielded section
        elif self.shielded_depth > 0:
            message = "ending the session once the shielded section is done"
        # Pytest is in a safe section, interrupt it
        else:
            raise KeyboardInterrupt

        # Notify the user and let the plugin react to the `interrupted` flag later
        self._write(f"\n===== Interrupted: {message} (interrupt again not to wait) =====\n")

    @staticmethod
    def _exit(reason: str) -> NoReturn:
        """
        End the session after a fixture setup or a shielded section, for an interruption received during it.
        """
        pytest.exit(f"Interrupted, {reason}", returncode=pytest.ExitCode.INTERRUPTED)

    @contextmanager
    def shielded(self) -> Iterator[None]:
        """
        The shielded sections: see the module-level function of the same name.
        """
        # Nothing to shield due to:
        # - an interruption has already been received
        # - running in a fixture that is already protected
        # - running in a teardown that is already protected
        if self.interrupted or self.setup_depth > 0 or self.in_teardown:
            yield
            return
        self.shielded_depth += 1
        try:
            yield
        finally:
            self.shielded_depth -= 1
        # Interrupted during the shielded section, end the session
        if self.interrupted and self.shielded_depth == 0:
            self._exit("after a shielded section")

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

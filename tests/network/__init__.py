from __future__ import annotations

import pytest

import logging
from contextlib import contextmanager

from lib.common import Defer, wait_for
from lib.vm import VM

from typing import Generator

@contextmanager
def tcpdump(
    defer: Defer,
    pcapfile: str,
    vm: VM,
    interface: str,
    filter: str | None = None,
    *,
    count: int | None = None,
) -> Generator[None, None, None]:
    """
    Run tcpdump on the vm with specific interface.
    tcpdump is running only in the returned context.
    """
    def cleanup():
        # if vm is gone, do not fail
        if vm.exists() and vm.is_running():
            vm.ssh(f"xargs kill < {pcapfile}.pid; rm -f -- {pcapfile}.pid")

    # check no concurrent run
    if vm.file_exists(f"{pcapfile}.pid"):
        pytest.fail(f"concurrent run detected: {pcapfile}.pid already exists")

    # tcpdump arguments
    args = f"-n -w {pcapfile} -i {interface}"
    if count is not None:
        args += f" -c {count}"
    if filter is not None:
        args += f" '{filter}'"

    # run tcpdump in background
    logging.info(f"Running tcpdump on '{vm.name()}'")
    vm.ssh(
        f"tcpdump --immediate-mode {args} & "
        f"echo $! > {pcapfile}.pid; ",
        background=True,
    )
    defer(cleanup)

    # check tcpdump has properly started
    wait_for(lambda: vm.file_exists(pcapfile), timeout_secs=2, retry_delay_secs=1)

    yield
    cleanup()

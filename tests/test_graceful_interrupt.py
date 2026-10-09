
import pytest

import logging
import os
import time

from lib.common import InterruptShield
from lib.host import Host

from typing import Iterator

def sleep_with_logs(n: int, title: str) -> None:
    for i in range(n):
        logging.info(f"[{i+1:02}/{n}] {title}")
        time.sleep(1)

@pytest.fixture
def fixture1(host: Host) -> Iterator[None]:

    logging.info("> Try interrupt during fixture 1 setup (3 seconds)")
    host.ssh("touch /tmp/dirty-1")
    sleep_with_logs(3, "fixture 1 setup")
    logging.info("< Fixture 1 setup finished")

    yield None

    logging.info("> Try interrupt during fixture 1 teardown (3 seconds)")
    sleep_with_logs(3, "fixture 1 teardown")
    host.ssh("unlink /tmp/dirty-1")
    logging.info("< Fixture 1 teardown finished")

@pytest.fixture
def fixture2(host: Host) -> Iterator[None]:

    logging.info("> Try interrupt during fixture 2 setup (3 seconds)")
    host.ssh("touch /tmp/dirty-2")
    sleep_with_logs(3, "fixture 2 setup")
    logging.info("< Fixture 2 setup finished")

    yield None

    logging.info("> Try interrupt during fixture 2 teardown (3 seconds)")
    sleep_with_logs(3, "fixture 2 teardown")
    host.ssh("unlink /tmp/dirty-2")
    logging.info("< Fixture 2 teardown finished")


@pytest.mark.flaky
def test_graceful_interrupt(fixture1: None, fixture2: None, interrupt_shield: InterruptShield) -> None:
    with interrupt_shield() as defer:
        logging.info("> Try interrupt during shielded section (3 seconds)")
        sleep_with_logs(3, "shielded section")
        logging.info("< Shielded section finished")
        defer(lambda: sleep_with_logs(3, "defer"))

    logging.info("> Try interrupt during unshielded section (3 seconds)")
    sleep_with_logs(3, "unshielded section")
    logging.info("< Unshielded section finished")

    if os.environ.get("FAIL_TEST_GRACEFUL_INTERRUPT"):
        logging.info("FAIL_TEST_GRACEFUL_INTERRUPT env var detected")
        1 / 0
    else:
        logging.info("No FAIL_TEST_GRACEFUL_INTERRUPT env var")

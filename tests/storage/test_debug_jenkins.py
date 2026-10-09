
import pytest

import logging
import time

from lib.host import Host

from typing import Iterator

@pytest.fixture
def my_fixture(host: Host) -> Iterator[None]:
    host.ssh("touch /tmp/dirty")
    yield None
    for i in range(60):
        logging.info(f"[{i:02}/60] before cleanup")
        time.sleep(1)
    host.ssh("unlink /tmp/dirty")

@pytest.mark.flaky
def test_debug_jenkins(my_fixture: None) -> None:
    logging.info("Sleep 1 minute")
    time.sleep(60)
    logging.info("Ooops")
    1 / 0

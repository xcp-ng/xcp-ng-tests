"""Two passing tests with a slow teardown: an abort during the first teardown must not start the second test."""
import pytest

import logging
import time

from typing import Iterator

@pytest.fixture
def slow_teardown() -> Iterator[None]:
    yield None
    for i in range(8):
        logging.info(f"[{i}/8] teardown")
        time.sleep(1)

def test_first(slow_teardown: None) -> None:
    pass

def test_second(slow_teardown: None) -> None:
    logging.info("SECOND TEST RAN")

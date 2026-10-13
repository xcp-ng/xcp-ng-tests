"""Two fixtures with slow teardowns, to see what a second interrupt does to the other one (PLAN.md 2.2)."""
import pytest

import logging
import time

from typing import Iterator

@pytest.fixture
def outer() -> Iterator[None]:
    yield None
    logging.info("outer teardown start")
    time.sleep(3)
    logging.info("outer teardown done")

@pytest.fixture
def inner(outer: None) -> Iterator[None]:
    yield None
    logging.info("inner teardown start")
    time.sleep(5)
    logging.info("inner teardown done")

def test_two_fixtures(inner: None) -> None:
    time.sleep(20)

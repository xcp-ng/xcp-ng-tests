"""
A chain of fixtures with slow setups (hosts -> vm -> started), to check aborts during a fixture setup.

Each fixture records its setup and teardown in $JOURNAL.
"""
import pytest

import os
import time

from typing import Iterator

def journal(msg: str) -> None:
    with open(os.environ["JOURNAL"], "a") as f:
        f.write(msg + "\n")

def slow(name: str, seconds: int) -> None:
    journal(f"{name} setup start")
    for _ in range(seconds):
        time.sleep(1)
    journal(f"{name} setup done")

@pytest.fixture(scope="session")
def hosts() -> Iterator[None]:
    slow("hosts", 2)
    yield None
    journal("hosts teardown")

@pytest.fixture
def vm(hosts: None) -> Iterator[None]:
    slow("vm", 4)
    yield None
    journal("vm teardown")

@pytest.fixture
def started(vm: None) -> Iterator[None]:
    slow("started", 3)
    yield None
    journal("started teardown")

def test_chain(started: None) -> None:
    journal("test start")
    time.sleep(10)
    journal("test end")

"""Host-less copy of tests/storage/test_debug_jenkins.py for local experiments.

Durations come from env vars so experiments can be fast: CALL_S, TEARDOWN_S.
The fixture marks progress in $WORKSPACE/.jenkins-pytest/dirty (created in setup,
removed at the very end of teardown).
"""
import pytest

import logging
import os
import time

from typing import Iterator

CALL_S = int(os.environ.get("CALL_S", "60"))
TEARDOWN_S = int(os.environ.get("TEARDOWN_S", "60"))
DIRTY = os.path.join(os.environ.get("WORKSPACE", os.getcwd()), ".jenkins-pytest", "dirty")

@pytest.fixture
def my_fixture() -> Iterator[None]:
    os.makedirs(os.path.dirname(DIRTY), exist_ok=True)
    open(DIRTY, "w").close()
    yield None
    for i in range(TEARDOWN_S):
        logging.info(f"[{i:02}/{TEARDOWN_S}] before cleanup")
        time.sleep(1)
    os.unlink(DIRTY)
    logging.info("cleanup done")

def test_local_debug(my_fixture: None) -> None:
    logging.info(f"Sleep {CALL_S}s")
    time.sleep(CALL_S)
    logging.info("Ooops")
    1 / 0

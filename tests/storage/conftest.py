from __future__ import annotations

import pytest

from lib.vm import VM
from tests.storage import install_randstream

def pytest_collection_modifyitems(config, items):
    # modify ordering so that ext is always tested first,
    # before more complex storage drivers
    for item in reversed(list(items)):
        if "_ext_" in item.path.name:
            items.remove(item)
            items.insert(0, item)

@pytest.fixture(scope='module')
def storage_test_vm(running_unix_vm: VM):
    install_randstream(running_unix_vm)
    yield running_unix_vm

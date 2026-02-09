import pytest

import logging
import urllib.request

from lib.host import Host

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state

from typing import Generator

@pytest.fixture(scope="package")
def host_with_perf(host_at_least_8_3: Host, host_with_saved_yum_state: Host) -> Generator[Host, None, None]:
    host = host_with_saved_yum_state

    logging.info("Getting perf package")

    host.yum_install(['perf'])
    yield host

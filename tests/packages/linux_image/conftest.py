import logging
import pytest
import urllib.request

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state

@pytest.fixture(scope="package")
def host_with_perf(host_at_least_8_3, host_with_saved_yum_state):
    host = host_with_saved_yum_state

    logging.info(f"Getting perf package")

    host.yum_install(['perf'])
    yield host

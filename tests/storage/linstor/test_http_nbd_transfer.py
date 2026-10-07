import pytest

import logging

from lib.host import Host

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state  # noqa: F401 # See pkgfixtures.py

from typing import Generator

# Requirements:
# - a XCP-ng host (that ships http-nbdkit-transfer-tests)


@pytest.fixture(scope="module")
def host_with_http_nbd_transfer_tests(
    host_with_saved_yum_state: Host,  # noqa: F811 # See pkgfixtures.py
) -> Generator[Host, None, None]:
    host = host_with_saved_yum_state
    host.yum_install(["http-nbd-transfer-tests"])
    yield host


@pytest.mark.usefixtures("host_with_http_nbd_transfer_tests")
class TestHttpNbdTransfer:
    def test_http_nbd_transfer_tests(
        self, host_with_http_nbd_transfer_tests: Host
    ) -> None:
        logging.info(
            "Run upstream's testsuite (on temp file)")
        host_with_http_nbd_transfer_tests.ssh(
            "pytest-3 /usr/share/http-nbd-transfer/tests --verbose"
        )

from __future__ import annotations

import pytest

import dataclasses
import logging

from lib.host import Host

from typing import Generator

# Requirements:
# - a XCP-ng host

@dataclasses.dataclass
class HttpNbdTransferTestsResource:
    host: Host

@pytest.fixture(scope='module')
def http_nbd_transfer_tests(
    hostA1: Host,
) -> Generator[HttpNbdTransferTestsResource, None, None]:
    hostA1.yum_install(['http-nbd-transfer-tests'])

    yield HttpNbdTransferTestsResource(
        host=hostA1
    )

    hostA1.yum_remove(['http-nbd-transfer-tests'])  # http-nbd-transfer and deps are remaining


class TestHttpNbdTransfer:
    def test_http_nbd_transfer_tests(self, http_nbd_transfer_tests: HttpNbdTransferTestsResource) -> None:
        logging.info("Run upstream's testsuite, it uses a temp file")
        http_nbd_transfer_tests.host.ssh('py.test /usr/*/http-nbd-transfer/tests --verbose')

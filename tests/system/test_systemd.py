import logging
import pytest
import re

# Requirements:
# - an XCP-ng host (--hosts) >= 8.2

pytest.fixture(scope='module')
def test_failed_units(host):
    failed_services = host.ssh(['systemctl', '--state=failed', '--full', '--all',
                               '--no-pager', '--no-legend'])
    for unit in failed_services.splitlines():
        logging.error(f"Unit {unit.split()[0]} failed")

    assert not failed_services

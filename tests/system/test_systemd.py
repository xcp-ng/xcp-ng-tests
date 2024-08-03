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

pytest.fixture(scope='module')
def test_unit_dep_cycles(host):
    analyse = host.ssh(['systemd-analyze', 'verify', 'default.target'])
    err = False
    for line in analyse.splitlines():
        m = re.match("^Found ordering cycle on .*$", line)
        if m is not None:
            logging.error(f"{m.group(0)}")
            err = True

    assert not err

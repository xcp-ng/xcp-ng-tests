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

white_list_issues = [
    "Cannot add dependency job for unit getty@tty1.service, ignoring: Unit is masked.",
    "Cannot add dependency job for unit display-manager.service, ignoring: Unit not found.",
]

pytest.fixture(scope='module')
def test_verify_default_target(host):
    analyse = host.ssh(['systemd-analyze', 'verify', 'default.target'])
    err = False
    for line in analyse.splitlines():
        if line not in white_list_issues:
            logging.error(f"{line}")
            err = True

    assert not err

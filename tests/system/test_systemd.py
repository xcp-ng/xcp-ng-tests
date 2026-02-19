import pytest

import logging
import re

from lib.host import Host

# Requirements:
# - an XCP-ng host (--hosts) >= 8.2

pytest.fixture(scope='module')
def test_failed_units(host: Host):
    failed_services = host.ssh('systemctl --state=failed --full --all --no-pager --no-legend')
    if failed_services:
        pytest.fail(failed_services)

white_list_issues = [
    "Cannot add dependency job for unit getty@tty1.service, ignoring: Unit is masked.",
    "Cannot add dependency job for unit display-manager.service, ignoring: Unit not found.",
    "Cannot add dependency job for unit qemuback.service, ignoring: Unit not found.",
    "Cannot add dependency job for unit sr_health_check.timer, ignoring: Unit not found.",
]

pytest.fixture(scope='module')
def test_verify_default_target(host):
    analyse = host.ssh('systemd-analyze verify default.target')
    err = False
    for line in analyse.splitlines():
        if line not in white_list_issues:
            logging.error(f"{line}")
            err = True

    assert not err

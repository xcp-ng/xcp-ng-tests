import pytest

import logging
import re

from lib.common import wait_for
from lib.host import Host

# Requirements:
# - an XCP-ng host (--hosts) >= 8.2

pytest.fixture(scope='module')
def test_failed_units(host: Host) -> None:
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
def test_verify_default_target(host: Host) -> None:
    def analyse_default_target() -> bool:
        # systemd-analyze is sometimes reporting "Out of memory.". These two command are there
        # to help understand what is using memory when it happens.
        # TODO: remove this line once the issue is fixed
        host.ssh('ps -eo pid,ppid,%mem,rss,args ww --sort=-rss | head -n 11')
        host.ssh('free')

        analyse = host.ssh('systemd-analyze verify default.target')
        polkit_msg = "Cannot add dependency job for unit polkit.service, ignoring: Unit not found."
        out_of_memory = False
        err = False
        for line in analyse.splitlines():
            if line == polkit_msg:
                pytest.xfail(f"drbd-reactor package must be fixed to remove dep to polkit: {polkit_msg}")
            if line == "Out of memory.":
                out_of_memory = True
                logging.warning(f"{line}")
                continue
            if line not in white_list_issues:
                logging.error(f"{line}")
                err = True
        assert not err
        return not out_of_memory

    wait_for(analyse_default_target, "Wait for systemd-analyze verify default.target to be clean")

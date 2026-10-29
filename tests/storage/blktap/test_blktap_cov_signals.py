from __future__ import annotations

import pytest

from lib.common import wait_for

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.blktap import TapCtl
    from lib.common import Defer
    from lib.host import Host

# The signals handled by a tapdisk: SIGUSR1 dumps its state in its log, SIGHUP reopens its log file.
#
# Requirements:
# - an XCP-ng host with blktap

def tapdisk_log(pid: int) -> str:
    return f'/var/log/blktap/tapdisk.{pid}.log'

def assert_tapdisk_answers(tapctl: TapCtl, pid: int, minor: int, image: str) -> None:
    infos = tapctl.list(pid=pid, minor=minor)
    assert len(infos) == 1 and infos[0].type is not None and image.startswith(infos[0].type), \
        f"The tapdisk doesn't list its image: {infos}"
    assert tapctl.stats(pid, minor)['name'] == image, "The tapdisk doesn't answer stats"

@pytest.mark.no_vm
@pytest.mark.parametrize('image', ['vhd', 'qcow2'], indirect=True)
def test_sigusr1_dumps_the_state(host: Host, tapctl: TapCtl, image: str) -> None:
    pid, minor = tapctl.create(image)
    image_format = image.split(':')[0]
    log = tapdisk_log(pid)

    host.ssh(f'kill -USR1 {pid}')

    wait_for(lambda: f'{image_format}_debug' in host.ssh(f'cat {log}'),
             f"Wait for the {image_format} debug dump in {log}", timeout_secs=10)
    assert_tapdisk_answers(tapctl, pid, minor, image)

@pytest.mark.no_vm
@pytest.mark.parametrize('image', ['vhd', 'qcow2'], indirect=True)
def test_sighup_reopens_the_log(host: Host, tapctl: TapCtl, defer: Defer, image: str) -> None:
    pid, minor = tapctl.create(image)
    log = tapdisk_log(pid)
    moved = f'{log}.moved'

    host.ssh(f'mv {log} {moved}')
    defer(lambda: host.ssh(f'rm -f {moved}'))
    host.ssh(f'kill -HUP {pid}')
    wait_for(lambda: host.ssh_with_result(f'test -e {log}').returncode == 0,
             f"Wait for {log} to be created again", timeout_secs=10)

    # what the tapdisk logs now goes to the new file
    host.ssh(f'kill -USR1 {pid}')
    wait_for(lambda: '_debug' in host.ssh(f'cat {log}'),
             f"Wait for the debug dump in the new {log}", timeout_secs=10)
    assert_tapdisk_answers(tapctl, pid, minor, image)

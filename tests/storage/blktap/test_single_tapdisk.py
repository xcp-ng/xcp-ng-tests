from __future__ import annotations

import pytest

import re

from lib.blktap import TapCtl
from lib.host import Host
from tests.storage.blktap import wait_for_tapdisk_exit

from typing import Generator

# Many VBDs served by a single tapdisk, without VM: exercises the list of VBDs of a
# tapdisk (equivalent to test-tapdisk-stress.sh from blktap-tools)
#
# Requirements:
# - an XCP-ng host with blktap

VBD_COUNT = 50

@pytest.fixture
def small_vhd_paths(host: Host) -> Generator[list[str], None, None]:
    """VBD_COUNT 2 MiB VHDs in a dom0 temporary directory."""
    directory = host.ssh('mktemp -d /tmp/blktap-test-XXXXXX')
    host.ssh(f'for i in $(seq {VBD_COUNT}); do vhd-util create -n {directory}/disk$i.vhd -s 2; done')
    yield [f"{directory}/disk{i}.vhd" for i in range(1, VBD_COUNT + 1)]
    host.ssh(f'rm -rf {directory}')

@pytest.mark.no_vm
def test_many_vbds_on_one_tapdisk(host: Host, tapctl: TapCtl, small_vhd_paths: list[str]) -> None:
    """A single tapdisk serves many VBDs, each with its own image, then releases them all."""
    pid = tapctl.spawn()
    images = {}  # minor -> image path
    for path in small_vhd_paths:
        minor, _ = tapctl.allocate()
        tapctl.attach(pid, minor)
        tapctl.open(pid, minor, f"vhd:{path}")
        images[minor] = path

    listed = {t.minor: t.path for t in tapctl.list() if t.pid == pid}
    assert listed == images, \
        "tap-ctl list doesn't show the expected minors and images"
    # tapdisk finds the VBD of each minor
    for minor, path in images.items():
        name = tapctl.stats(pid, minor)['name']
        assert name == f"vhd:{path}", \
            f"minor {minor} serves {name}, expected vhd:{path}"

    for minor in images:
        tapctl.close(pid, minor)
        tapctl.detach(pid, minor)
    wait_for_tapdisk_exit(host, pid)
    for minor in images:
        tapctl.free(minor)
    # tap-ctl list shows the allocated minors without tapdisk as "minor=N" lines
    left = {int(m) for m in re.findall(r'minor=(\d+)', host.ssh('tap-ctl list'))} & images.keys()
    assert not left, f"minors still allocated after free: {sorted(left)}"

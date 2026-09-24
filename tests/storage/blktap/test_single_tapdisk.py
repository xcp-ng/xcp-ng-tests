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
def small_images(request: pytest.FixtureRequest, host: Host) -> Generator[list[str], None, None]:
    """VBD_COUNT 2 MiB images in a dom0 temporary directory, in the format of an indirect parametrization."""
    image_format = request.param
    directory = host.ssh('mktemp -d /tmp/blktap-test-XXXXXX')
    if image_format == 'vhd':
        create = f'vhd-util create -n {directory}/disk$i.vhd -s 2'
    else:
        create = f'qemu-img create -q -f {image_format} {directory}/disk$i.{image_format} 2M'
    host.ssh(f'for i in $(seq {VBD_COUNT}); do {create}; done')
    yield [f"{image_format}:{directory}/disk{i}.{image_format}" for i in range(1, VBD_COUNT + 1)]
    host.ssh(f'rm -rf {directory}')

@pytest.mark.no_vm
@pytest.mark.parametrize('small_images', ['vhd', 'qcow2'], indirect=True)
def test_many_vbds_on_one_tapdisk(host: Host, tapctl: TapCtl, small_images: list[str]) -> None:
    """A single tapdisk serves many VBDs, each with its own image, then releases them all."""
    pid = tapctl.spawn()
    images = {}  # minor -> image
    for image in small_images:
        minor, _ = tapctl.allocate()
        tapctl.attach(pid, minor)
        tapctl.open(pid, minor, image)
        images[minor] = image

    listed = {t.minor: f"{t.type}:{t.path}" for t in tapctl.list() if t.pid == pid}
    assert listed == images, \
        "tap-ctl list doesn't show the expected minors and images"
    # tapdisk finds the VBD of each minor
    for minor, image in images.items():
        name = tapctl.stats(pid, minor)['name']
        assert name == image, \
            f"minor {minor} serves {name}, expected {image}"

    for minor in images:
        tapctl.close(pid, minor)
        tapctl.detach(pid, minor)
    wait_for_tapdisk_exit(host, pid)
    for minor in images:
        tapctl.free(minor)
    # tap-ctl list shows the allocated minors without tapdisk as "minor=N" lines
    left = {int(m) for m in re.findall(r'minor=(\d+)', host.ssh('tap-ctl list'))} & images.keys()
    assert not left, f"minors still allocated after free: {sorted(left)}"

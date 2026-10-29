from __future__ import annotations

import pytest

import logging

from lib.common import wait_for
from tests.storage.blktap import PATTERN_BLOCKS, blktap_tool, filled_md5, nbd_blocks, nbd_socket, stop_tapdisk
from tests.storage.blktap.test_nbd import nbd_client

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.blktap import TapCtl
    from lib.common import Defer
    from lib.host import Host

# Image drivers and tapdisk operations reached with a plain tap-ctl, I/O generated through the NBD
# server of the tapdisk.
#
# Requirements:
# - an XCP-ng host with blktap, qemu-img and python3 in dom0

def assert_parent_blocks(host: Host, qcow2_path: str, written: bool) -> None:
    """Check that the pattern blocks of the image are written (or all zeroes), reading it as raw."""
    raw = host.ssh('mktemp /tmp/blktap-test-XXXXXX.raw')
    try:
        host.ssh(f'qemu-img convert -f qcow2 -O raw {qcow2_path} {raw}')
        for offset, length, fill in PATTERN_BLOCKS:
            expected = fill if written else 0
            data = host.ssh(f'dd if={raw} bs=64K iflag=skip_bytes,count_bytes skip={offset} count={length} '
                            f'status=none | md5sum')
            assert data.split()[0] == filled_md5(expected, length), f"Block at {offset} isn't filled with {expected:#x}"
    finally:
        host.ssh(f'rm -f {raw}')

@pytest.mark.no_vm
def test_ram_image(host: Host, tapctl: TapCtl, defer: Defer) -> None:
    """The ram driver serves the content of its file from memory and keeps the writes out of the file."""
    path = host.ssh('mktemp /tmp/blktap-test-XXXXXX.raw')
    defer(lambda: host.ssh(f'rm -f {path}'))
    host.ssh(f'dd if=/dev/zero of={path} bs=1M count=16 status=none')

    pid, minor = tapctl.create(f'ram:{path}')
    socket_path = nbd_socket(pid, minor)

    nbd_client(host, socket_path, 'write', nbd_blocks(PATTERN_BLOCKS))
    nbd_client(host, socket_path, 'verify', nbd_blocks(PATTERN_BLOCKS))
    stats = tapctl.stats(pid, minor)
    assert stats['name'] == f'ram:{path}', f"Unexpected image in the stats of the tapdisk: {stats}"

    assert host.ssh(f'cmp {path} /dev/zero -n 16777216 && echo same') == 'same', \
        "The writes reached the file of the ram image"
    stop_tapdisk(host, tapctl, pid, minor)

COMMIT_STATUS_DONE = ('concluded', 'null')

@pytest.mark.no_vm
def test_qcow2_commit(host: Host, tapctl: TapCtl, defer: Defer) -> None:
    """tap-ctl commit merges the writes of a qcow2 image into its backing file."""
    parent = host.ssh('mktemp /tmp/blktap-test-XXXXXX.qcow2')
    child = host.ssh('mktemp /tmp/blktap-test-XXXXXX.qcow2')
    defer(lambda: host.ssh(f'rm -f {parent} {child}'))
    host.ssh(f'qemu-img create -f qcow2 {parent} 64M')
    host.ssh(f'qemu-img create -f qcow2 -b {parent} -F qcow2 {child}')

    pid, minor = tapctl.create(f'qcow2:{child}')
    nbd_client(host, nbd_socket(pid, minor), 'write', nbd_blocks(PATTERN_BLOCKS))

    assert_parent_blocks(host, parent, written=False)
    logging.info(f"Commit {child} into {parent}")
    tap_ctl = blktap_tool(host, 'tap-ctl')
    host.ssh(f'{tap_ctl} commit -p {pid} -m {minor} -a {child}')

    def job_done() -> bool:
        status = host.ssh(f'{tap_ctl} query -p {pid} -m {minor}')
        logging.info(status)
        return any(f"'{word}'" in status for word in COMMIT_STATUS_DONE)
    wait_for(job_done, "Wait for the commit job to finish", timeout_secs=60)
    stop_tapdisk(host, tapctl, pid, minor)

    assert_parent_blocks(host, parent, written=True)

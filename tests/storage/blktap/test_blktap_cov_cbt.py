from __future__ import annotations

import pytest

import base64
import logging
import random
import shlex

from lib.common import MiB
from lib.sr import SR
from tests.storage.blktap import blktap_tool, nbd_blocks, nbd_socket, stop_tapdisk, wait_for_guest_device
from tests.storage.blktap.test_nbd import nbd_client

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.blktap import TapCtl, VBDConnector
    from lib.common import Defer
    from lib.host import Host
    from lib.vdi import VDI
    from lib.vm import VM

# Changed block tracking: the write logger of tapdisk (block-log.c, tap-ctl open -C) and cbt-util.
# The writes are generated through the NBD server of the tapdisk, so that the dirty blocks expected
# are known.
#
# Requirements:
# - an XCP-ng host with blktap and python3 in dom0
# - for the VM tests: a small unix VM, and a main SR of the host of type lvm, ext or nfs (for test_xapi_cbt)

CBT_BLOCK_SIZE = 64 * 1024
IMAGE_SIZE = 100 * MiB

# prints the indexes of the bits set in the bitmap read on stdin (bit n of byte b is block 8 * b + n)
DIRTY_BLOCKS_SCRIPT = ('import sys; data = sys.stdin.buffer.read(); '
                       'print(" ".join(str(8 * i + bit) for i, byte in enumerate(data) for bit in range(8) '
                       'if byte >> bit & 1))')

def cbt_util(host: Host, *args: object) -> str:
    return host.ssh(shlex.join([blktap_tool(host, 'cbt-util')] + [str(arg) for arg in args]))

def dirty_blocks(host: Host, log: str) -> list[int]:
    cbt = blktap_tool(host, 'cbt-util')
    output = host.ssh(f'{cbt} get -n {shlex.quote(log)} -b | python3 -c {shlex.quote(DIRTY_BLOCKS_SCRIPT)}')
    return [int(block) for block in output.split()]

def blocks_of(*extents: tuple[int, int]) -> list[int]:
    """CBT blocks touched by the (offset, length) extents."""
    return sorted({block for offset, length in extents
                   for block in range(offset // CBT_BLOCK_SIZE, (offset + length - 1) // CBT_BLOCK_SIZE + 1)})

def write_extents(host: Host, pid: int, minor: int, *extents: tuple[int, int]) -> None:
    blocks = [(offset, length, 0x10 + i) for i, (offset, length) in enumerate(extents)]
    nbd_client(host, nbd_socket(pid, minor), 'write', nbd_blocks(blocks))

def new_log(host: Host, defer: Defer, size: int = IMAGE_SIZE) -> str:
    log = host.ssh('mktemp /tmp/blktap-test-XXXXXX.cbtlog')
    defer(lambda: host.ssh(f'rm -f {log}'))
    cbt_util(host, 'create', '-n', log, '-s', size)
    return log

# (offset, length): in the first block, over two blocks, the last bytes of the image
EXTENTS = [(0, 4096), (3 * CBT_BLOCK_SIZE + 61440, 8192), (IMAGE_SIZE - 512, 512)]

@pytest.mark.no_vm
@pytest.mark.parametrize('image', ['vhd', 'qcow2'], indirect=True)
def test_log_marks_the_written_blocks(host: Host, tapctl: TapCtl, defer: Defer, image: str) -> None:
    log = new_log(host, defer)
    assert dirty_blocks(host, log) == [], "The new log has dirty blocks"

    pid, minor = tapctl.create(image, log=log)
    write_extents(host, pid, minor, *EXTENTS)
    nbd_client(host, nbd_socket(pid, minor), 'read')  # reads don't mark anything
    stop_tapdisk(host, tapctl, pid, minor)

    assert dirty_blocks(host, log) == blocks_of(*EXTENTS), "The blocks marked aren't the ones written"

@pytest.mark.no_vm
@pytest.mark.parametrize('image', ['vhd', 'qcow2'], indirect=True)
def test_log_with_many_writes(host: Host, tapctl: TapCtl, defer: Defer, image: str) -> None:
    """Hundreds of writes of various sizes, some overlapping or adjacent, mark exactly the blocks they touch."""
    log = new_log(host, defer)
    rng = random.Random(7)
    extents: list[tuple[int, int]] = []
    for _ in range(400):
        length = rng.choice([512, 4096, 4096, 8192, 65536, 65536 + 4096, 256 * 1024])
        offset = rng.randrange(0, (IMAGE_SIZE - length) // 512) * 512
        extents.append((offset, length))

    pid, minor = tapctl.create(image, log=log)
    # in chunks, to keep the command line of the client short
    for start in range(0, len(extents), 100):
        write_extents(host, pid, minor, *extents[start:start + 100])
    stop_tapdisk(host, tapctl, pid, minor)

    assert dirty_blocks(host, log) == blocks_of(*extents), "The blocks marked aren't the ones written"

@pytest.mark.no_vm
@pytest.mark.parametrize('image', ['vhd', 'qcow2'], indirect=True)
def test_log_follows_pause_and_unpause(host: Host, tapctl: TapCtl, defer: Defer, image: str) -> None:
    """The log is dropped by an unpause without it, and a new one can be given at unpause."""
    log, other_log = new_log(host, defer), new_log(host, defer)
    first, second, third = (0, 4096), (20 * MiB, 4096), (40 * MiB, 4096)

    pid, minor = tapctl.create(image, log=log)
    write_extents(host, pid, minor, first)

    tapctl.pause(pid, minor)
    tapctl.unpause(pid, minor)  # without the log
    write_extents(host, pid, minor, second)

    tapctl.pause(pid, minor)
    tapctl.unpause(pid, minor, log=other_log)
    write_extents(host, pid, minor, third)
    stop_tapdisk(host, tapctl, pid, minor)

    assert dirty_blocks(host, log) == blocks_of(first), "The log kept tracking after an unpause without it"
    assert dirty_blocks(host, other_log) == blocks_of(third), "The log given at unpause isn't tracking"

@pytest.mark.no_vm
def test_cbt_util_fields(host: Host, defer: Defer) -> None:
    log = new_log(host, defer)
    parent, child = '11111111-2222-3333-4444-555555555555', '66666666-7777-8888-9999-aaaaaaaaaaaa'

    assert cbt_util(host, 'get', '-n', log, '-s') == str(IMAGE_SIZE), "The new log doesn't have the size given"
    assert cbt_util(host, 'get', '-n', log, '-f') == '0', "The new log is marked consistent"

    cbt_util(host, 'set', '-n', log, '-p', parent, '-c', child, '-f', 1)
    assert cbt_util(host, 'get', '-n', log, '-p') == parent, "The parent uuid isn't the one set"
    assert cbt_util(host, 'get', '-n', log, '-c') == child, "The child uuid isn't the one set"
    assert cbt_util(host, 'get', '-n', log, '-f') == '1', "The consistency flag isn't the one set"

    cbt_util(host, 'set', '-n', log, '-s', 2 * IMAGE_SIZE)
    assert cbt_util(host, 'get', '-n', log, '-s') == str(2 * IMAGE_SIZE), "The size isn't the one set"
    assert dirty_blocks(host, log) == [], "The bitmap isn't empty after the resize"

@pytest.mark.no_vm
def test_cbt_util_coalesce(host: Host, tapctl: TapCtl, defer: Defer) -> None:
    """Coalescing ORs the bitmap of the parent log into the one of the child."""
    parent_log, child_log = new_log(host, defer), new_log(host, defer)
    path = host.ssh('mktemp /tmp/blktap-test-XXXXXX.vhd')
    defer(lambda: host.ssh(f'rm -f {path}'))
    host.ssh(f'vhd-util create -n {path} -s {IMAGE_SIZE // MiB} -r')
    image = f'vhd:{path}'
    parent_extent, child_extent = (0, 4096), (30 * MiB, 4096)

    for log, extent in ((parent_log, parent_extent), (child_log, child_extent)):
        pid, minor = tapctl.create(image, log=log)
        write_extents(host, pid, minor, extent)
        stop_tapdisk(host, tapctl, pid, minor)

    cbt_util(host, 'coalesce', '-p', parent_log, '-c', child_log)

    assert dirty_blocks(host, child_log) == blocks_of(parent_extent, child_extent), \
        "The child log doesn't have the blocks of both logs"
    assert dirty_blocks(host, parent_log) == blocks_of(parent_extent), "The parent log changed"

def guest_write_script(device: str, extents: list[tuple[int, int]]) -> str:
    """Shell commands writing random data to the extents of the device, flushed to tapdisk at the end."""
    # no oflag=direct: it isn't supported by every dd (e.g. busybox)
    writes = [f'dd if=/dev/urandom of=/dev/{device} bs=4096 seek={offset // 4096} count={length // 4096} '
              f'conv=notrunc status=none' for offset, length in extents]
    return '; '.join(writes + [f'blockdev --flushbufs /dev/{device}'])

@pytest.mark.small_vm
@pytest.mark.unix_vm
@pytest.mark.parametrize('image', ['vhd', 'qcow2'], indirect=True)
def test_vm_writes_are_logged(host: Host, running_vm: VM, vbd_connector: VBDConnector, defer: Defer,
                              image: str) -> None:
    """The blocks written by the guest are the ones marked, also across a pause and an unpause with the log."""
    vm = running_vm
    device = 'xvdb'
    log = new_log(host, defer)
    rng = random.Random(42)
    # 4 KiB writes all over the image, and a sequential 4 MiB write
    scattered = [(rng.randrange(0, IMAGE_SIZE // 4096) * 4096, 4096) for _ in range(40)]
    sequential = [(50 * MiB, 4 * MiB)]
    later = [(rng.randrange(0, IMAGE_SIZE // 4096) * 4096, 4096) for _ in range(20)]

    pid, minor = vbd_connector.connect(vm, image, device, log=log)
    wait_for_guest_device(vm, device)
    vm.ssh(guest_write_script(device, scattered + sequential))

    logging.info("Pause and unpause the VBD with the log, then write more")
    vbd_connector.tapctl.pause(pid, minor)
    vbd_connector.tapctl.unpause(pid, minor, log=log)
    vm.ssh(guest_write_script(device, later))

    vbd_connector.disconnect(vm, device)
    assert dirty_blocks(host, log) == blocks_of(*scattered, *sequential, *later), \
        "The blocks marked aren't the ones written by the guest"

def cbtlog_blocks(host: Host, sr: SR, vdi: VDI) -> list[int]:
    """Dirty blocks of the CBT log of the VDI, activating it on an LVM SR (the log of a detached VDI is inactive)."""
    sr_type = sr.get_type()
    if sr_type == 'lvm':
        log = f'/dev/VG_XenStorage-{sr.uuid}/{vdi.uuid}.cbtlog'
        host.ssh(f'lvchange -ay {log}')
        try:
            return dirty_blocks(host, log)
        finally:
            host.ssh(f'lvchange -an {log}')
    assert sr_type in ('ext', 'nfs'), f"Unsupported SR type {sr_type}"
    return dirty_blocks(host, f'/var/run/sr-mount/{sr.uuid}/{vdi.uuid}.cbtlog')

@pytest.mark.small_vm
@pytest.mark.unix_vm
def test_xapi_cbt(host: Host, running_vm: VM, defer: Defer) -> None:
    """CBT enabled by the toolstack: the tapdisk tracks the writes, across a snapshot taken while plugged."""
    vm = running_vm
    sr = SR(host.main_sr_uuid(), host.pool)
    vdi = sr.create_vdi('blktap-cbt', virtual_size=IMAGE_SIZE)
    defer(vdi.destroy)
    host.xe('vdi-enable-cbt', {'uuid': vdi.uuid})
    # disabled before the destruction of the VDI (deferred functions run in reverse order)
    defer(lambda: host.xe('vdi-disable-cbt', {'uuid': vdi.uuid}))

    vbd = vm.connect_vdi(vdi)
    # disconnect_vdi() accepts a VBD already unplugged
    defer(lambda: vm.disconnect_vdi(vdi))
    device = vbd.param_get('device')
    assert device, f"No device for the VBD {vbd.uuid}"
    wait_for_guest_device(vm, device)

    rng = random.Random(3)
    first = [(rng.randrange(0, IMAGE_SIZE // 4096 // 2) * 4096, 4096) for _ in range(30)] + [(8 * MiB, 2 * MiB)]
    second = [(IMAGE_SIZE // 2 + rng.randrange(0, IMAGE_SIZE // 4096 // 2) * 4096, 4096) for _ in range(30)]
    vm.ssh(guest_write_script(device, first))

    snapshot = vdi.snapshot()
    # the data of the snapshot are destroyed, but not the snapshot itself, before the VDI is
    defer(snapshot.destroy)
    defer(lambda: host.xe('vdi-data-destroy', {'uuid': snapshot.uuid}))
    vm.ssh(guest_write_script(device, second))

    # the changed blocks can only be listed once the VDI isn't in use anymore
    vbd.unplug()
    changed = base64.b64decode(host.xe('vdi-list-changed-blocks',
                                       {'vdi-from-uuid': snapshot.uuid, 'vdi-to-uuid': vdi.uuid}))
    assert any(changed), "The toolstack reports no block changed since the snapshot"
    assert cbtlog_blocks(host, sr, vdi), "The CBT log of the VDI has no dirty block"
    assert cbtlog_blocks(host, sr, snapshot), "The CBT log of the snapshot has no dirty block"

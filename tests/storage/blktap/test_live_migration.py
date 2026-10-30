from __future__ import annotations

import pytest

import logging

from lib.blktap import XenStoreHelper
from lib.common import MiB, wait_for
from tests.storage import install_randstream
from tests.storage.blktap import RegionWriters, guest_queue_count, validate_spans

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host
    from lib.sr import SR
    from lib.vm import VM
    from tests.storage.storage import StreamSpan

# Live migration of a multi-queue VBD while the guest writes to it, then check of the written data.
#
# During a live migration, the guest writes reach the disk through paths that are only used then:
# - without storage motion, the source tapdisk must complete all the requests of all its queues
#   before it closes, since the guest resends its in-flight requests to the destination tapdisk;
# - with storage motion, the source tapdisk also mirrors each write to the destination through NBD.
# The data written before a migration doesn't take these paths: it is copied with the rest of the
# disk. So the data is written during the migrations, and each region of the disk is written only once,
# so that a lost write can't be hidden by a later write of the same region. After each migration, the
# data written during it is checked, as well as the data written during the previous ones.
# The VBD must use several queues on each host, which is checked before and after each migration.
#
# Requirements:
# - all the hosts: tapback multi-queue support and a qcow2 tapdisk driver supporting multi-queue
# - for the intra-pool tests: hostA2, second member of the pool, with a local SR supporting qcow2 VDIs,
#   and a shared SR on the pool, supporting qcow2 VDIs
# - for the cross-pool test: hostB1, master of a second pool, with a local SR supporting qcow2 VDIs
# - a small unix VM whose blkfront supports multi-queue, able to download randstream
#   (cloned with 4 vCPUs by these tests)

VCPU_COUNT = 4  # blkfront uses at most one queue per vCPU
WRITER_COUNT = VCPU_COUNT  # one writer per vCPU, so that the writes go through several queues
REGION_SIZE = 4 * MiB
# Each migration has its own range of regions, so that each region is written only once
REGIONS_PER_MIGRATION = 1024
# Pause of each writer between two regions: the writes must last longer than the migration (about 14 min
# with 1024 regions per migration, while a migration of the test VM between two local SRs took up to 6 min)
WRITER_PAUSE_SECS = 3

def vbd_devid(vm: VM, device: str, xenstore: XenStoreHelper) -> str:
    """XenStore device id of the VBD attached as device, found from its VDI."""
    # not computed from the device name: XAPI numbers the devices of HVM guests the IDE way (xvdb -> 832)
    vdi_uuid = vm.host.xe('vbd-list', {'vm-uuid': vm.uuid, 'device': device, 'params': 'vdi-uuid'}, minimal=True)
    domid = vm.param_get('dom-id')
    devids = [devid for devid in xenstore.ls(f"/local/domain/0/backend/vbd3/{domid}")
              if (xenstore.read(f"/local/domain/0/backend/vbd3/{domid}/{devid}/params") or '').endswith(f'/{vdi_uuid}')]
    assert len(devids) == 1, \
        f"Expected one backend for VDI {vdi_uuid} of {device}, found {devids}"
    return devids[0]

def assert_multiqueue(vm: VM, device: str) -> None:
    """The VBD uses as many queues as the backend advertises, at most one per vCPU, each with its ring."""
    domid = vm.param_get('dom-id')
    xenstore = XenStoreHelper(vm.host)
    devid = vbd_devid(vm, device, xenstore)
    max_queues = xenstore.read(f"/local/domain/0/backend/vbd3/{domid}/{devid}/multi-queue-max-queues")
    assert max_queues is not None and int(max_queues) > 1, \
        f"tapback on {vm.host} doesn't advertise multi-queue for {device} (multi-queue-max-queues={max_queues})"
    expected = min(int(max_queues), VCPU_COUNT)

    frontend = f"device/vbd/{devid}"
    num_queues = vm.xenstore_read(f"{frontend}/multi-queue-num-queues", accept_unknown_key=True)
    assert num_queues == str(expected), \
        f"blkfront chose multi-queue-num-queues={num_queues} for {device}, expected {expected} " \
        f"(multi-queue-max-queues={max_queues}, {VCPU_COUNT} vCPUs)"
    for i in range(expected):
        assert vm.xenstore_read(f"{frontend}/queue-{i}/ring-ref", accept_unknown_key=True) is not None, \
            f"No ring for queue {i} of {device}"
    queues = guest_queue_count(vm, device)
    assert queues == expected, \
        f"{device} has {queues} queue(s) in the guest, expected {expected}"
    logging.info(f"{device} on {vm.host}: {queues} queues (multi-queue-max-queues={max_queues})")

def migrate_during_writes(vm: VM, dev: str, first_region: int, dest_host: Host, dest_sr: SR) -> list[StreamSpan]:
    """Migrate the VM while writing regions of dev, return the regions written."""
    writers = RegionWriters(vm, dev, REGION_SIZE, first_region, REGIONS_PER_MIGRATION, WRITER_COUNT,
                            WRITER_PAUSE_SECS)
    writers.start()
    # qcow2, the multi-queue image format, also when the VDIs change SR
    vm.migrate(dest_host, dest_sr, image_format='qcow2')
    wait_for(lambda: vm.all_vdis_on_sr(dest_sr),
             "Wait for all VDIs on the destination SR")
    wait_for(lambda: vm.is_running_on_host(dest_host),
             "Wait for the VM to be running on the destination host")
    writers.assert_running()
    # the guest renegotiates the queues when it resumes on the destination
    assert_multiqueue(vm, dev.removeprefix('/dev/'))
    return writers.stop()

def migrations_during_writes(vm: VM, sr: SR, hops: list[tuple[Host, SR]]) -> None:
    """Write to a new qcow2 VDI on sr during each migration, check the written data after each one."""
    vdi = sr.create_vdi(virtual_size=len(hops) * REGIONS_PER_MIGRATION * REGION_SIZE, image_format='qcow2')
    # the VDI is destroyed with the VM
    vbd = vm.connect_vdi(vdi)
    vm.start(on=vm.host.uuid)
    vm.wait_for_vm_running_and_ssh_up()
    install_randstream(vm)
    device = vbd.param_get('device')
    assert device, \
        f"No device for VBD {vbd.uuid}"
    assert_multiqueue(vm, device)

    dev = f'/dev/{device}'
    written: list[StreamSpan] = []
    for hop, (dest_host, dest_sr) in enumerate(hops):
        written += migrate_during_writes(vm, dev, hop * REGIONS_PER_MIGRATION, dest_host, dest_sr)
        # the data written during this migration, and during the previous ones: it must survive the next ones.
        # Drop the guest page cache first, so that the reads go through tapdisk.
        vm.ssh(f'blockdev --flushbufs {dev}')
        validate_spans(vm, dev, written)

@pytest.fixture
def vm_on_shared_sr(host: Host, shared_sr: SR, vm_with_vcpu_count: VM) -> VM:
    """The VM with VCPU_COUNT vCPUs, halted, with all its VDIs on the shared SR."""
    vm = vm_with_vcpu_count
    # the clone is on the SR of the imported VM, and a migration without storage motion needs a shared SR
    if not vm.all_vdis_on_sr(shared_sr):
        vm.migrate(host, shared_sr)
        wait_for(lambda: vm.all_vdis_on_sr(shared_sr),
                 "Wait for all VDIs on the shared SR")
    return vm

@pytest.mark.small_vm
@pytest.mark.unix_vm
@pytest.mark.parametrize('vm_with_vcpu_count', [VCPU_COUNT], indirect=True)
def test_live_migration_during_writes(host: Host, hostA2: Host, shared_sr: SR, vm_on_shared_sr: VM) -> None:
    """Without storage motion: the source tapdisk is closed, the destination one is created."""
    migrations_during_writes(vm_on_shared_sr, shared_sr, [(hostA2, shared_sr), (host, shared_sr)])

@pytest.mark.small_vm
@pytest.mark.unix_vm
@pytest.mark.parametrize('vm_with_vcpu_count', [VCPU_COUNT], indirect=True)
def test_live_storage_migration_during_writes(host: Host, hostA2: Host, shared_sr: SR, local_sr_on_hostA2: SR,
                                              vm_on_shared_sr: VM) -> None:
    """With storage motion: the writes are mirrored to the destination through NBD."""
    migrations_during_writes(vm_on_shared_sr, shared_sr, [(hostA2, local_sr_on_hostA2), (host, shared_sr)])

@pytest.mark.small_vm
@pytest.mark.unix_vm
@pytest.mark.parametrize('vm_with_vcpu_count', [VCPU_COUNT], indirect=True)
def test_cross_pool_storage_migration_during_writes(host: Host, hostB1: Host, local_sr_on_hostB1: SR,
                                                    vm_with_vcpu_count: VM) -> None:
    """Across pools, always with storage motion: the writes are mirrored to the destination through NBD."""
    vm = vm_with_vcpu_count
    # start from the SR of the clone, and come back to it
    sr = vm.get_sr()
    migrations_during_writes(vm, sr, [(hostB1, local_sr_on_hostB1), (host, sr)])

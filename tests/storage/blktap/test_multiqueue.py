from __future__ import annotations

import pytest

from lib.blktap import VBDConnector, XenStoreHelper
from lib.vm import VM
from tests.storage.blktap import wait_for_guest_device, write_then_read

# Multi-queue VBDs: tapback advertises multi-queue-max-queues when the tapdisk driver
# supports it, then blkfront chooses multi-queue-num-queues (at most one queue per vCPU)
# and sets up one ring per queue.
#
# Requirements:
# - an XCP-ng host with tapback multi-queue support, and a qcow2 tapdisk driver supporting multi-queue
# - a small unix VM whose blkfront supports multi-queue, with dd and blockdev in the guest
#   (cloned with 4 vCPUs by these tests)

def start(vm: VM) -> VM:
    vm.start()
    vm.wait_for_vm_running_and_ssh_up()
    return vm

def guest_queue_count(vm: VM, device: str) -> int:
    """Number of hardware queues of the device, as seen by the guest block layer."""
    return len(vm.ssh(f'ls /sys/block/{device}/mq').split())

@pytest.mark.small_vm
@pytest.mark.unix_vm
@pytest.mark.parametrize('vm_with_vcpu_count', [4], indirect=True)  # blkfront uses at most one queue per vCPU
def test_qcow2_is_multiqueue(vm_with_vcpu_count: VM, qcow2_path: str, vbd_connector: VBDConnector,
                             xenstore: XenStoreHelper) -> None:
    """A qcow2 VBD is connected with several queues, each with its own ring."""
    vm = start(vm_with_vcpu_count)
    device = "xvdb"
    vbd_connector.connect(vm, f"qcow2:{qcow2_path}", device)
    wait_for_guest_device(vm, device)
    frontend = vbd_connector.frontend_path(device)

    max_queues = xenstore.read(f"{vbd_connector.backend_path(vm, device)}/multi-queue-max-queues")
    assert max_queues is not None and int(max_queues) > 1, \
        f"tapback doesn't advertise multi-queue (multi-queue-max-queues={max_queues})"
    num_queues = vm.xenstore_read(f"{frontend}/multi-queue-num-queues", accept_unknown_key=True)
    assert num_queues is not None, \
        "multi-queue-num-queues not written by blkfront: multi-queue not negotiated"
    assert int(num_queues) > 1, \
        f"blkfront chose {num_queues} queue(s), expected several"
    # one ring per queue, in queue-N/ subtrees, and no single ring in the vbd subtree
    for i in range(int(num_queues)):
        for key in ('ring-ref', 'event-channel'):
            assert vm.xenstore_read(f"{frontend}/queue-{i}/{key}", accept_unknown_key=True) is not None, \
                f"no {key} in queue-{i}"
    assert vm.xenstore_read(f"{frontend}/ring-ref", accept_unknown_key=True) is None, \
        "single-queue ring-ref present in a multi-queue VBD"
    assert guest_queue_count(vm, device) == int(num_queues)

    write_then_read(vm, device, size_mib=10)

@pytest.mark.small_vm
@pytest.mark.unix_vm
@pytest.mark.parametrize('vm_with_vcpu_count', [4], indirect=True)  # blkfront uses at most one queue per vCPU
def test_vhd_is_single_queue(vm_with_vcpu_count: VM, vhd_path: str, vbd_connector: VBDConnector,
                             xenstore: XenStoreHelper) -> None:
    """A VHD VBD (driver without multi-queue support) is connected with a single ring."""
    vm = start(vm_with_vcpu_count)
    device = "xvdb"
    vbd_connector.connect(vm, f"vhd:{vhd_path}", device)
    wait_for_guest_device(vm, device)
    frontend = vbd_connector.frontend_path(device)

    max_queues = xenstore.read(f"{vbd_connector.backend_path(vm, device)}/multi-queue-max-queues")
    assert max_queues is None, \
        f"tapback advertises multi-queue ({max_queues} queues) for a VHD"
    num_queues = vm.xenstore_read(f"{frontend}/multi-queue-num-queues", accept_unknown_key=True)
    assert num_queues is None, \
        f"multi-queue negotiated ({num_queues} queues) for a VHD"
    # a single ring, directly in the vbd subtree
    for key in ('ring-ref', 'event-channel'):
        assert vm.xenstore_read(f"{frontend}/{key}", accept_unknown_key=True) is not None, \
            f"no {key} in the vbd subtree"
    assert vm.xenstore_read(f"{frontend}/queue-0", accept_unknown_key=True) is None, \
        "queue-0 subtree present in a single-queue VBD"
    assert guest_queue_count(vm, device) == 1

    write_then_read(vm, device, size_mib=10)

import logging
import os
import pytest

from lib.common import wait_for
from lib.installer import AnswerFile
from lib.vdi import VDI

# test the answerfile fixture can run on 2 parametrized instances
# of the test in one run
@pytest.mark.answerfile(lambda: AnswerFile("INSTALL").top_append(
    {"TAG": "source", "type": "local"},
    {"TAG": "primary-disk", "text": "nvme0n1"},
))
@pytest.mark.parametrize("parm", [
    1,
    pytest.param(2, marks=[
        pytest.mark.dependency(depends=["TestFixtures::test_parametrized_answerfile[1]"]),
    ]),
])
@pytest.mark.dependency
def test_parametrized_answerfile(answerfile, parm):
    logging.debug("test_parametrized_answerfile with parm=%s", parm)

@pytest.fixture
def cloned_disk(imported_vm):
    vm = imported_vm
    all_vdis = [VDI(uuid, host=vm.host) for uuid in vm.vdi_uuids()]
    disk_vdis = [vdi for vdi in all_vdis if not vdi.readonly()]
    base_vdi, = disk_vdis

    clone = base_vdi.clone()
    yield clone

    clone.destroy()

@pytest.fixture
def vm_with_plugged_disk(imported_vm, cloned_disk):
    vm = imported_vm
    test_vdi = cloned_disk

    vm.start()
    vm.wait_for_vm_running_and_ssh_up()

    vbd = vm.create_vbd("1", test_vdi.uuid)
    try:
        vbd.plug()

        yield vm

    finally:
        logging.info("cleaning up")
        vbd.unplug()
        vbd.destroy()

@pytest.mark.small_vm
def test_vdi_modify(vm_with_plugged_disk):
    vm = vm_with_plugged_disk
    vm.ssh(["mount /dev/xvdb3 /mnt"])
    vm.ssh(["touch /tmp/foo"])
    vm.ssh(["umount /dev/xvdb3"])

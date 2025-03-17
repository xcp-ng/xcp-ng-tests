import logging
import pytest

from lib.common import wait_for

# These tests are basic tests meant to be run to check that a VM performs
# well, without obvious issues.
# However, it does not go beyond checking that basic operations work,
# because the VM may not have SSH installed, which is needed for more advanced scenarios.
#
# Requirements:
# - XCP-ng >= 8.1.
# - a two-host pool with 1 shared SR (for test_live_migrate)
# - the pool must have `suspend-image-SR` set (for suspend and checkpoint)
# - each host must have a local SR
# - any VM with guest tools installed. No SSH required for this test suite.
# - when using an existing VM, the VM can be on any host of the pool,
#   the local SR or shared SR: the test will adapt itself.
#   Note however that an existing VM will be left on a different SR after the tests.

@pytest.mark.multi_vms # run them on a variety of VMs
@pytest.mark.big_vm # and also on a really big VM ideally
def test_vm_start_stop(imported_vm):
    vm = imported_vm
    # if VM already running, stop it
    if (vm.is_running()):
        logging.info("VM already running, shutting it down first")
        vm.shutdown(verify=True)
    vm.start()
    # this also tests the guest tools at the same time since they are used
    # for retrieving the IP address and management agent status.
    vm.wait_for_os_booted()

    vm.shutdown(verify=True)

@pytest.mark.multi_vms # run them on a variety of VMs
@pytest.mark.big_vm # and also on a really big VM ideally
@pytest.mark.usefixtures("started_vm")
class TestBasicNoSSH:
    def test_pause(self, imported_vm):
        vm = imported_vm
        vm.pause(verify=True)
        vm.unpause()
        vm.wait_for_os_booted()

    def test_suspend(self, imported_vm):
        vm = imported_vm
        vm.suspend(verify=True)
        vm.resume()
        vm.wait_for_os_booted()

    def test_snapshot(self, imported_vm):
        vm = imported_vm
        snapshot = vm.snapshot()
        try:
            snapshot.revert()
            vm.start()
            vm.wait_for_os_booted()
        finally:
            snapshot.destroy(verify=True)

    def test_checkpoint(self, imported_vm):
        vm = imported_vm
        snapshot = vm.checkpoint()
        try:
            snapshot.revert()
            vm.resume()
            vm.wait_for_os_booted()
        finally:
            snapshot.destroy(verify=True)

    # Live migration tests
    # We want to test storage migration (memory+disks) and live migration without storage migration (memory only).
    # The order will depend on the initial location of the VM: a local SR or a shared SR.
    @pytest.mark.usefixtures("hostA2")
    def test_live_migrate(self, imported_vm, existing_shared_sr):
        def live_migrate(vm, dest_host, dest_sr, check_vdis=False):
            vm.migrate(dest_host, dest_sr)
            if check_vdis:
                wait_for(lambda: vm.all_vdis_on_sr(dest_sr), "Wait for all VDIs on destination SR")
            wait_for(lambda: vm.is_running_on_host(dest_host), "Wait for VM to be running on destination host")

        vm = imported_vm
        initial_sr = vm.get_sr()
        initial_sr_shared = initial_sr.is_shared()
        host1 = vm.host
        host2 = host1.pool.first_host_that_isnt(host1)
        # migrate to host 2
        if initial_sr_shared:
            logging.info("* VM on shared SR: preparing for live migration without storage motion *")
            live_migrate(vm, host2, initial_sr)
        else:
            logging.info("* VM on local SR: preparing for live migration with storage towards a shared SR *")
            live_migrate(vm, host2, existing_shared_sr, check_vdis=True)
        # migrate back to host 1, using the other migration method
        if initial_sr_shared:
            logging.info("* Preparing for live migration with storage, towards the other host's local storage *")
            host1_local_srs = host1.local_vm_srs()
            assert len(host1_local_srs) > 0, "Host must have at least one local SR"
            live_migrate(vm, host1, host1_local_srs[0], check_vdis=True)
        else:
            logging.info("* Preparing for live migration without storage motion *")
            live_migrate(vm, host1, existing_shared_sr)

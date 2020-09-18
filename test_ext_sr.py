import pytest
from lib.common import wait_for

# Requirements:
# - one XCP-ng host with an additional unused disk for the SR

@pytest.mark.incremental
class TestEXTSR:
    sr = None
    vm = None

    # Impact on other tests: installs xfsprogs and creates the SR
    def test_create_sr(self, host, sr_disk):
        TestEXTSR.sr = host.sr_create('ext', "EXT-local-SR", {'device': '/dev/' + sr_disk})
        wait_for(TestEXTSR.sr.exists, "Wait for SR to exist")

    # Impact on other tests: creates a VM on the SR and starts it
    def test_import_and_start_VM(self, host, vm_ref):
        vm = host.import_vm_url(vm_ref, TestEXTSR.sr.uuid)
        TestEXTSR.vm = vm # for teardown
        vm.start()
        vm.wait_for_os_booted()

    # Impact on other tests: none if succeeds
    # FIXME: only suited to linux VMs
    def test_snapshot(self, host):
        vm = TestEXTSR.vm
        vm.test_snapshot_on_running_linux_vm()

    # Impact on other tests: VM shutdown cleanly
    def test_vm_shutdown(self, host):
        vm = TestEXTSR.vm
        vm.shutdown(verify=True)

    # *** tests with reboots (longer tests). To be moved to another file?

    # Impact on other tests: none if succeeds
    def test_reboot(self, host):
        host.reboot(verify=True)
        wait_for(TestEXTSR.sr.all_pbds_attached, "Wait for PDB attached")
        vm = TestEXTSR.vm
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots

    # Impact on other tests: VM removed, leaving SR empty (and thus destroyable)
    def test_destroy_vm(self, host):
        if TestEXTSR.vm is not None:
            TestEXTSR.vm.destroy(verify=True)

    # Impact on other tests: SR destroyed
    # Prerequisites: SR attached but empty
    def test_destroy_sr(self, host):
        TestEXTSR.sr.destroy(verify=True)

    @classmethod
    def teardown_class(cls):
        if cls.sr is not None and cls.sr.exists():
            if cls.vm is not None and cls.vm.exists():
                cls.vm.destroy()
            cls.sr.destroy(verify=True)

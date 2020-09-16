import pytest
from lib.common import wait_for, wait_for_not, vm_image
import time

# Requirements:
# - one XCP-ng host >= 8.2 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

@pytest.fixture(scope='module')
def sr_disk(host):
    disks = host.disks()
    # there must be at least 2 disks
    assert len(disks) > 1, "at least two disks are required"
    # Using the second disk for SR
    yield disks[1]

@pytest.fixture(scope='module')
def host_with_xfsprogs(host):
    host.yum_install(['xfsprogs'])
    yield host
    # teardown
    host.yum_remove(['xfsprogs'])

@pytest.mark.incremental
class TestXFSSR:
    sr = None
    vm = None

    def test_create_xfs_sr_without_xfsprogs(self, host, sr_disk):
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/mkfs.xfs'), \
            "xfsprogs must not be installed on the host at the beginning of the tests"
        try:
            # though it is expected to fail, result assigned to TestXFSSR.sr for teardown in case it succeeds
            TestXFSSR.sr = host.sr_create('xfs', '/dev/' + sr_disk, "XFS-local-SR")
            assert False, "SR creation should not have succeeded!"
        except:
            print("SR creation failed, as expected.")

    # Impact on other tests: installs xfsprogs and creates the SR
    def test_create_sr(self, host_with_xfsprogs, sr_disk):
        host = host_with_xfsprogs
        TestXFSSR.sr = host.sr_create('xfs', '/dev/' + sr_disk, "XFS-local-SR")
        wait_for(TestXFSSR.sr.exists, "Wait for SR to exist")

    # Impact on other tests: creates a VM on the SR
    def test_import_and_start_VM(self, host):
        vm = host.import_vm_url(vm_image('mini-linux-x86_64-bios'), TestXFSSR.sr.uuid)
        TestXFSSR.vm = vm # for teardown
        vm.start()
        vm.wait_for_vm_running_and_ssh_up(wait_for_ip=True)

    # Impact on other tests: none if succeeds
    def test_snapshot(self):
        vm = TestXFSSR.vm
        vm.test_snapshot_on_linux_vm()

    # Impact on other tests: VM shutdown cleanly
    def test_vm_shutdown(self):
        vm = TestXFSSR.vm
        vm.shutdown()
        wait_for(vm.is_halted, "Wait for VM halted")

    # *** tests with reboots (longer tests). To be moved to another file?

    # Impact on other tests: none if succeeds
    def test_reboot(self, host):
        host.reboot()
        wait_for_not(host.is_enabled, "Wait for host down")
        wait_for(host.is_enabled, "Wait for host up", timeout_secs=300)
        wait_for(TestXFSSR.sr.all_pbds_attached, "Wait for PDB attached")
        vm = TestXFSSR.vm
        vm.start()
        vm.wait_for_vm_running_and_ssh_up(wait_for_ip=True)
        vm.shutdown()
        wait_for(vm.is_halted)

    # Impact on other tests: none if succeeds
    def test_xfsprogs_missing(self, host):
        sr = TestXFSSR.sr
        try:
            host.yum_remove(['xfsprogs'])
            TestXFSSR.xfsprogs_installed = False
            try:
                sr.scan()
                assert False, "SR scan should have failed"
            except:
                print("SR scan failed as expected.")
            host.reboot()
            wait_for_not(host.is_enabled, "Wait for host down")
            wait_for(host.is_enabled, "Wait for host up", timeout_secs=300)
            # give the host some time to try to attach the SR
            time.sleep(10)
            print("Assert PBD not attached")
            assert not sr.all_pbds_attached()
            host.yum_install(['xfsprogs'])
            TestXFSSR.xfsprogs_installed = True
            sr.plug_pbds()
            wait_for(sr.all_pbds_attached, "Wait for PDB attached")
            sr.scan()
        finally:
            if not TestXFSSR.xfsprogs_installed:
                host.yum_install(['xfsprogs'])
                TestXFSSR.xfsprogs_installed = True

    # *** End of tests with reboots

    # Impact on other tests: VM removed, leaving SR empty (and thus destroyable)
    def test_destroy_vm(self):
        if TestXFSSR.vm is not None:
            TestXFSSR.vm.destroy()
            wait_for_not(TestXFSSR.vm.exists, "Wait for VM destroyed")

    # Impact on other tests: SR destroyed
    # Prerequisites: SR attached but empty
    def destroy_sr(self):
        TestXFSSR.sr.destroy()
        wait_for_not(TestXFSSR.sr.exists, "Wait for SR destroyed")

    @classmethod
    def teardown_class(cls):
        if cls.sr is not None and cls.sr.exists():
            # forget the SR: easier than destroy, especially if there are VDIs
            cls.sr.forget()


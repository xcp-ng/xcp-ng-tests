import pytest
from lib.common import wait_for
import time

# Requirements:
# - one XCP-ng host >= 8.2 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

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
            TestXFSSR.sr = host.sr_create('xfs', "XFS-local-SR", {'device': '/dev/' + sr_disk})
            assert False, "SR creation should not have succeeded!"
        except Exception:
            print("SR creation failed, as expected.")

    # Impact on other tests: installs xfsprogs and creates the SR
    def test_create_sr(self, host_with_xfsprogs, sr_disk):
        host = host_with_xfsprogs
        TestXFSSR.sr = host.sr_create('xfs', "XFS-local-SR", {'device': '/dev/' + sr_disk})
        wait_for(TestXFSSR.sr.exists, "Wait for SR to exist")

    # Impact on other tests: creates a VM on the SR and starts it
    def test_import_and_start_VM(self, host, vm_ref):
        vm = host.import_vm_url(vm_ref, TestXFSSR.sr.uuid)
        TestXFSSR.vm = vm # for teardown
        vm.start()
        vm.wait_for_os_booted()

    # Impact on other tests: none if succeeds
    # FIXME: only suited to linux VMs
    def test_snapshot(self, host):
        vm = TestXFSSR.vm
        vm.test_snapshot_on_running_linux_vm()

    # Impact on other tests: VM shutdown cleanly
    def test_vm_shutdown(self, host):
        vm = TestXFSSR.vm
        vm.shutdown(verify=True)

    # *** tests with reboots (longer tests). To be moved to another file?

    # Impact on other tests: none if succeeds
    def test_reboot(self, host):
        host.reboot(verify=True)
        wait_for(TestXFSSR.sr.all_pbds_attached, "Wait for PDB attached")
        vm = TestXFSSR.vm
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # Impact on other tests: none if succeeds
    def test_xfsprogs_missing(self, host):
        sr = TestXFSSR.sr
        xfsprogs_installed = True
        try:
            host.yum_remove(['xfsprogs'])
            xfsprogs_installed = False
            try:
                sr.scan()
                assert False, "SR scan should have failed"
            except Exception:
                print("SR scan failed as expected.")
            host.reboot(verify=True)
            # give the host some time to try to attach the SR
            time.sleep(10)
            print("Assert PBD not attached")
            assert not sr.all_pbds_attached()
            host.yum_install(['xfsprogs'])
            xfsprogs_installed = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not xfsprogs_installed:
                host.yum_install(['xfsprogs'])

    # *** End of tests with reboots

    # Impact on other tests: VM removed, leaving SR empty (and thus destroyable)
    def test_destroy_vm(self, host):
        if TestXFSSR.vm is not None:
            TestXFSSR.vm.destroy(verify=True)

    # Impact on other tests: SR destroyed
    # Prerequisites: SR attached but empty
    def test_destroy_sr(self, host):
        TestXFSSR.sr.destroy(verify=True)

    @classmethod
    def teardown_class(cls):
        if cls.sr is not None and cls.sr.exists():
            if cls.vm is not None and cls.vm.exists():
                cls.vm.destroy()
            cls.sr.destroy(verify=True)

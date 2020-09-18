import pytest
from lib.common import wait_for, wait_for_not, vm_image
import time

# Requirements:
# - one XCP-ng host >= 8.2
# - remote cephfs mountpoint
# - access to XCP-ng RPM repository from the host

@pytest.fixture(scope='module')
def host_with_cephfsprogs(host):
    host.yum_install(['centos-release-ceph-jewel'])
    host.yum_install(['ceph-common'])
    yield host
    # teardown
    host.yum_remove(['ceph-common', 'centos-release-ceph-jewel'])

@pytest.mark.incremental
class TestCephFSSR:
    from data import REMOTE_deviceconfig
    sr = None
    vm = None
    device_config = {}
    device_config['server'] = REMOTE_deviceconfig['server']
    device_config['serverpath'] = REMOTE_deviceconfig['serverpath']
    device_config['options'] = REMOTE_deviceconfig['options']

    def test_create_cephfs_sr_without_cephfsprogs(self, host):
        # Thiis test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/mount.ceph'), \
            "mount.ceph must not be installed on the host at the beginning of the tests"
        try:
            # though it is expected to fail, result assigned to TestCephFSSR.sr for teardown in case it succeeds
            TestCephFSSR.sr = host.sr_create('cephfs', "CephFS-SR", self.device_config)
            assert False, "SR creation should not have succeeded!"
        except:
            print("SR creation failed, as expected.")

    # Impact on other tests: installs cephfsprogs and creates the SR
    def test_create_sr(self, host_with_cephfsprogs):
        host = host_with_cephfsprogs
        TestCephFSSR.sr = host.sr_create('cephfs', "CephFS-SR", self.device_config)
        wait_for(TestCephFSSR.sr.exists, "Wait for SR to exist")

    # Impact on other tests: creates a VM on the SR and starts it
    def test_import_and_start_VM(self, host):
        vm = host.import_vm_url(vm_image('mini-linux-x86_64-bios'), TestCephFSSR.sr.uuid)
        TestCephFSSR.vm = vm # for teardown
        #vm.convert_type('pv')
        vm.start()
        vm.wait_for_os_booted()

    # Impact on other tests: none if succeeds
    # FIXME: only suited to linux VMs
    def test_snapshot(self, host):
        vm = TestCephFSSR.vm
        vm.test_snapshot_on_running_linux_vm()

    # Impact on other tests: VM shutdown cleanly
    def test_vm_shutdown(self, host):
        vm = TestCephFSSR.vm
        vm.shutdown(verify=True)

    # *** tests with reboots (longer tests). To be moved to another file?

    # Impact on other tests: none if succeeds
    def test_reboot(self, host):
        host.reboot(verify=True)
        wait_for(TestCephFSSR.sr.all_pbds_attached, "Wait for PBD attached")
        vm = TestCephFSSR.vm
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # Impact on other tests: none if succeeds
    def test_cephfsprogs_missing(self, host):
        sr = TestCephFSSR.sr
        cephfsprogs_installed = True
        try:
            host.yum_remove(['ceph-common', 'centos-release-ceph-jewel'])
            cephfsprogs_installed = False
            try:
                sr.scan()
                assert False, "SR scan should have failed"
            except:
                print("SR scan failed as expected.")
            host.reboot(verify=True)
            # give the host some time to try to attach the SR
            time.sleep(10)
            print("Assert PBD not attached")
            assert not sr.all_pbds_attached()
            host.yum_install(['centos-release-ceph-jewel'])
            host.yum_install(['ceph-common'])
            cephfsprogs_installed = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not cephfsprogs_installed:
                host.yum_install(['centos-release-ceph-jewel'])
                host.yum_install(['ceph-common'])

    # *** End of tests with reboots

    # Impact on other tests: VM removed, leaving SR empty (and thus destroyable)
    def test_destroy_vm(self, host):
        if TestCephFSSR.vm is not None:
            TestCephFSSR.vm.destroy(verify=True)

    # Impact on other tests: SR destroyed
    # Prerequisites: SR attached but empty
    def destroy_sr(self, host):
        TestCephFSSR.sr.destroy(verify=True)

    @classmethod
    def teardown_class(cls):
        if cls.sr is not None and cls.sr.exists():
            if cls.vm is not None and cls.vm.exists():
                cls.vm.destroy()
            cls.sr.destroy(verify=True)

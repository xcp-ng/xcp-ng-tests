import pytest
from lib.common import wait_for
import time

# Requirements:
# - one XCP-ng host >= 8.2 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

@pytest.fixture(scope='module')
def host_with_zfs(host, sr_disk):
    host.yum_install(['zfs'], save_state=True)
    host.ssh(['modprobe', 'zfs'])
    host.ssh(['zpool', 'create', 'vol0', '/dev/' + sr_disk])
    yield host
    # teardown
    host.ssh(['zpool', 'destroy', 'vol0'])
    host.yum_restore_saved_state()

@pytest.mark.incremental
class TestZFSSR:
    sr = None
    vm = None

    def test_create_zfs_sr_without_zfs(self, host, sr_disk):
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/zpool'), \
            "zfs must not be installed on the host at the beginning of the tests"
        try:
            # though it is expected to fail, result assigned to TestZFSSR.sr for teardown in case it succeeds
            TestZFSSR.sr = host.sr_create('zfs', "ZFS-local-SR", {'location': 'vol0'})
            assert False, "SR creation should not have succeeded!"
        except:
            print("SR creation failed, as expected.")

    # Impact on other tests: installs zfs and creates the SR
    def test_create_sr(self, host_with_zfs, sr_disk):
        host = host_with_zfs
        TestZFSSR.sr = host.sr_create('zfs', "ZFS-local-SR", {'location': 'vol0'})
        wait_for(TestZFSSR.sr.exists, "Wait for SR to exist")

    # Impact on other tests: creates a VM on the SR and starts it
    def test_import_and_start_VM(self, host, vm_ref):
        vm = host.import_vm_url(vm_ref, TestZFSSR.sr.uuid)
        TestZFSSR.vm = vm # for teardown
        vm.start()
        vm.wait_for_os_booted()

    # Impact on other tests: none if succeeds
    # FIXME: only suited to linux VMs
    def test_snapshot(self, host):
        vm = TestZFSSR.vm
        vm.test_snapshot_on_running_linux_vm()

    # Impact on other tests: VM shutdown cleanly
    def test_vm_shutdown(self, host):
        vm = TestZFSSR.vm
        vm.shutdown(verify=True)

    # *** tests with reboots (longer tests). To be moved to another file?

    # Impact on other tests: none if succeeds
    def test_reboot(self, host):
        host.reboot(verify=True)
        wait_for(TestZFSSR.sr.all_pbds_attached, "Wait for PDB attached")
        vm = TestZFSSR.vm
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # Impact on other tests: none if succeeds
    def test_zfs_missing(self, host):
        sr = TestZFSSR.sr
        zfs_installed = True
        try:
            host.yum_remove(['zfs'])
            zfs_installed = False
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
            host.yum_install(['zfs'])
            host.ssh(['modprobe', 'zfs'])
            host.ssh(['zpool', 'import', 'vol0'])
            zfs_installed = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not zfs_installed:
                host.yum_install(['zfs'])
                host.ssh(['modprobe', 'zfs'])

    # Impact on other tests: none if succeeds
    def test_zfs_unmounted(self, host):
        sr = TestZFSSR.sr
        pool_imported = True
        try:
            # Simulate broken mountpoint
            host.ssh(['zpool', 'export', 'vol0'])
            pool_imported = False
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
            host.ssh(['zpool', 'import', 'vol0'])
            pool_imported = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not pool_imported:
                host.ssh(['zpool', 'import', 'vol0'])

    # *** End of tests with reboots

    # Impact on other tests: VM removed, leaving SR empty (and thus destroyable)
    def test_destroy_vm(self, host):
        if TestZFSSR.vm is not None:
            TestZFSSR.vm.destroy(verify=True)

    # Impact on other tests: SR destroyed
    # Prerequisites: SR attached but empty
    def test_destroy_sr(self, host):
        TestZFSSR.sr.destroy(verify=True)

    @classmethod
    def teardown_class(cls):
        if cls.sr is not None and cls.sr.exists():
            if cls.vm is not None and cls.vm.exists():
                cls.vm.destroy()
            cls.sr.destroy(verify=True)

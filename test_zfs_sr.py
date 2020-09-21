import pytest
from lib.common import wait_for
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
def host_with_zfs(host, sr_disk):
    host.yum_install(['zfs'])
    disk = sr_disk
    host.ssh(['modprobe', 'zfs'])
    host.ssh(['zpool', 'create', 'vol0', '/dev/' + disk])
    yield host
    # teardown
    host.ssh(['zpool', 'destroy', 'vol0'])
    host.yum_remove(['zfs'])

@pytest.mark.incremental
class TestZFSSR:
    sr = None
    vm = None

    def test_create_zfs_sr_without_zfs(self, host, sr_disk):
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/zpool'), \
            "zfs must not be installed on the host at the beginning of the tests"
        try:
            # though it is expected to fail, result assigned to TestXFSSR.sr for teardown in case it succeeds
            TestZFSSR.sr = host.sr_create('zfs', "ZFS-local-SR", {'location': 'vol0'})
            assert False, "SR creation should not have succeeded!"
        except:
            print("SR creation failed, as expected.")

    # Impact on other tests: installs xfsprogs and creates the SR
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
            zfs_installed = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not zfs_installed:
                host.yum_install(['zfs'])

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
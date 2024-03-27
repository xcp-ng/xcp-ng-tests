import logging
import time
import pytest

from lib.commands import SSHCommandFailed
from lib.common import wait_for, vm_image
from tests.storage import vdi_is_open

# Requirements:
# - one XCP-ng host >= 8.3 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

pytestmark = pytest.mark.usefixtures("host_at_least_8_3")

class TestZfsvolSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_and_destroy_sr(self, host, sr_disk_wiped):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('zfs-vol', "ZFS-local-SR-test", {'device': '/dev/' + sr_disk_wiped}, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("zfsvol_sr")
class TestZfsvolSrBasics:
    @pytest.mark.xfail # needs support for cloning non-snapshots
    def test_vdi_clone(self, vdi_on_zfsvol_sr):
        clone = vdi_on_zfsvol_sr.clone()
        clone.destroy()

    def test_vdi_snap_clone(self, vdi_on_zfsvol_sr):
        snap = vdi_on_zfsvol_sr.snapshot()
        clone = snap.clone()
        clone.destroy()
        snap.destroy()

@pytest.mark.usefixtures("zfsvol_sr")
class TestZfsvolVm:
    @pytest.mark.xfail
    @pytest.mark.quicktest
    def test_quicktest(self, zfsvol_sr):
        zfsvol_sr.run_quicktest()

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_zfsvol_sr):
        vm = vm_on_zfsvol_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.xfail # needs support for destroying snapshots
    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_zfsvol_sr):
        vm = vm_on_zfsvol_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_zfsvol_sr, host, zfsvol_sr):
        sr = zfsvol_sr
        vm = vm_on_zfsvol_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots

@pytest.mark.usefixtures("zfsvol_sr")
class TestZfsngSrSingleVdiDestroy:
    "Destruction tests of a single VDI involved in various topologies"
    @pytest.mark.xfail # needs support for destroying non-snapshots blocked by snaps
    def test_vdi_destroy_with_snap_but_no_clones(self, zfsvol_sr):
        vdi = zfsvol_sr.create_vdi('ZFS-local-VDI-test')
        snap = vdi.snapshot()
        vdi.destroy()

    @pytest.mark.xfail # needs support for destroying non-snapshots blocked by snaps
    def test_vdi_destroy_with_several_snaps_but_no_clones(self, zfsvol_sr):
        vdi = zfsvol_sr.create_vdi('ZFS-local-VDI-test')
        snaps = []
        for i in range(3):
            snaps.append(vdi.snapshot())
        vdi.destroy()

    def test_vdi_destroy_with_snap_and_clone(self, zfsvol_sr):
        vdi = zfsvol_sr.create_vdi('ZFS-local-VDI-test')
        snap = vdi.snapshot()
        clone = snap.clone()
        vdi.destroy()

    def test_vdi_destroy_with_snap_and_several_clones(self, zfsvol_sr):
        vdi = zfsvol_sr.create_vdi('ZFS-local-VDI-test')
        snap = vdi.snapshot()
        clones = []
        for i in range(3):
            clones.append(snap.clone())
        vdi.destroy()

# FIXME below should be independent from SR type and label

def create_vdi_and_snaps_chain(sr, vdi_label):
    "Create a chain of alternating VDI snapshots and clones on first host."
    vdis = [sr.create_vdi(vdi_label)]
    for i in range(2):
        vdis.append(vdis[-1].snapshot())
        vdis.append(vdis[-1].clone())
    return vdis

def teardown_vdi_chain(sr, vdis, order):
    "Destroy a list of VDIs in order specified by a sequence of VDIs indices."
    for i in order:
        vdi = vdis[i]
        logging.debug("zfs state: %r", sr.main_host().ssh(
            "zfs list -Hp -t all -o name,origin".split()))
        try:
            vdi.destroy()
        except Exception:
            logging.error("failed to destroy %s", vdi)
            raise

@pytest.mark.usefixtures("zfsvol_sr")
class TestZfsvolSrVdiChainSnapDestroy:
    "VDI chain destruction tests with alternating snap/clone"
    def test_vdi_and_snaps_teardown(self, zfsvol_sr):
        "Destroy snapshot chain in reverse order of creation"
        vdis = create_vdi_and_snaps_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        assert len(vdis) == 5
        for (i, is_snap) in enumerate([False, True, False, True, False]):
            assert (vdis[i].snapshot_of is not None) == is_snap, \
                f"vdis[{i}] should {'' if is_snap else 'not '}be a snapshot"
        teardown_vdi_chain(zfsvol_sr, vdis, (4, 3, 2, 1, 0))

    @pytest.mark.xfail # needs support for destroying non-snapshots blocked by snaps
    def test_vdi_and_snaps_destroy_first_vdi(self, zfsvol_sr):
        "Destroy first-created VDI, then the rest in reverse order of creation"
        vdis = create_vdi_and_snaps_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        teardown_vdi_chain(zfsvol_sr, vdis, (0, 4, 3, 2, 1))

    @pytest.mark.xfail # needs support for destroying non-snapshots blocked by snaps
    def test_vdi_and_snaps_destroy_intermediate_vdi(self, zfsvol_sr):
        "Destroy second-created VDI, then the rest in reverse order of creation"
        vdis = create_vdi_and_snaps_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        teardown_vdi_chain(zfsvol_sr, vdis, (2, 4, 3, 1, 0))

    # orderings expected to work on first proto

    def test_vdi_and_snaps_destroy_01432(self, zfsvol_sr):
        vdis = create_vdi_and_snaps_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        teardown_vdi_chain(zfsvol_sr, vdis, (0, 1, 4, 3, 2))

    def test_vdi_and_snaps_destroy_01234(self, zfsvol_sr):
        vdis = create_vdi_and_snaps_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        teardown_vdi_chain(zfsvol_sr, vdis, (0, 1, 2, 3, 4))

    def test_vdi_and_snaps_destroy_23410(self, zfsvol_sr):
        vdis = create_vdi_and_snaps_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        teardown_vdi_chain(zfsvol_sr, vdis, (2, 3, 4, 1, 0))

    def test_vdi_and_snaps_destroy_23014(self, zfsvol_sr):
        vdis = create_vdi_and_snaps_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        teardown_vdi_chain(zfsvol_sr, vdis, (2, 3, 0, 1, 4))

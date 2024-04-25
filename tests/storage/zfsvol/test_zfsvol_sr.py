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
        # Create and destroy tested in the same test to leave the host
        # as unchanged as possible
        sr = host.sr_create('zfs-vol', "ZFS-local-SR-test",
                            {'device': '/dev/' + sr_disk_wiped}, verify=True)
        try:
            # import a VM in order to detect vm import issues here
            # rather than in the vm_on_xfs_fixture used in the next
            # tests, because errors in fixtures break teardown
            vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
            vm.destroy(verify=True)
        finally:
            sr.destroy(verify=True)

@pytest.mark.usefixtures("zfsvol_sr")
class TestZfsvolSrBasics:
    def test_vdi_resize(self, vdi_on_zfsvol_sr):
        logging.info("Resize up")
        vdi_on_zfsvol_sr.resize(1024 * 1024)
        logging.info("Attempt to resize down")
        try:
            vdi_on_zfsvol_sr.resize(64 * 1024)
        except Exception as e:
            if "shrinking not allowed" in str(e):
                # properly refused
                pass
            else:
                logging.error("unexpected error on downsize attempt: %s", e)
                raise

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

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshots_revert(self, vm_on_zfsvol_sr):
        vm = vm_on_zfsvol_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.wait_for_vm_running_and_ssh_up()

        snap1, snap2, snap3 = None, None, None
        snap1 = vm.snapshot()
        vm.ssh_touch_file(f"/{snap1.uuid}")
        snap2 = vm.snapshot()
        vm.ssh_touch_file(f"/{snap2.uuid}")
        snap3 = vm.snapshot()

        # we are in "snap3" state, check all 6 "from A to B"
        # combinations
        snap1.revert()
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        logging.info("Check files state")
        vm.ssh([f"test ! -f /{snap1.uuid}"])
        vm.ssh([f"test ! -f /{snap2.uuid}"])
        snap2.revert()
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        logging.info("Check files state")
        vm.ssh([f"test -f /{snap1.uuid}"])
        vm.ssh([f"test ! -f /{snap2.uuid}"])
        snap3.revert()
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        logging.info("Check files state")
        vm.ssh([f"test -f /{snap1.uuid}"])
        vm.ssh([f"test -f /{snap2.uuid}"])
        snap2.revert()
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        logging.info("Check files state")
        vm.ssh([f"test -f /{snap1.uuid}"])
        vm.ssh([f"test ! -f /{snap2.uuid}"])
        snap1.revert()
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        logging.info("Check files state")
        vm.ssh([f"test ! -f /{snap1.uuid}"])
        vm.ssh([f"test ! -f /{snap2.uuid}"])
        snap3.revert()
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        logging.info("Check files state")
        vm.ssh([f"test -f /{snap1.uuid}"])
        vm.ssh([f"test -f /{snap2.uuid}"])

# FIXME: we don't support snapshot destruction yet
#        snap1.destroy(verify=True)
#        snap2.destroy(verify=True)
#        snap3.destroy(verify=True)

    def get_messages(self, name):
        args = {
            'obj-uuid': self.uuid,
            'name': name,
            'params': 'uuid',
        }

        lines = self.host.xe('message-list', args).splitlines()

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
    def test_vdi_destroy_with_snap_but_no_clones(self, zfsvol_sr):
        vdi = zfsvol_sr.create_vdi('ZFS-local-VDI-test')
        snap = vdi.snapshot()
        vdi.destroy()

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

def create_vdi_chain(sr, vdi_label):
    "Create a chain of alternating VDI snapshots and clones on first host."
    vdis = [sr.create_vdi(vdi_label)]
    for i in range(2):
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

    def test_vdi_and_snaps_destroy_first_vdi(self, zfsvol_sr):
        "Destroy first-created VDI, then the rest in reverse order of creation"
        vdis = create_vdi_and_snaps_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        teardown_vdi_chain(zfsvol_sr, vdis, (0, 4, 3, 2, 1))

    @pytest.mark.xfail # needs GC of unused extra non-snapshots
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

    # other topologies

    def test_destroy_vdi_with_snap_with_multiple_clones(self, zfsvol_sr):
        base_vdi = zfsvol_sr.create_vdi('ZFS-local-VDI-test')
        snap = base_vdi.snapshot()
        clones = [snap.clone(), snap.clone()]

        base_vdi.destroy()

@pytest.mark.xfail
@pytest.mark.usefixtures("zfsvol_sr")
class TestZfsvolSrVdiChainDestroy:
    "VDI chain destruction tests with just clones and no snaps"
    def test_destroy_210(self, zfsvol_sr):
        vdis = create_vdi_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        assert len(vdis) == 3
        teardown_vdi_chain(zfsvol_sr, vdis, (2, 1, 0))

    def test_destroy_201(self, zfsvol_sr):
        vdis = create_vdi_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        teardown_vdi_chain(zfsvol_sr, vdis, (2, 0, 1))

    def test_destroy_012(self, zfsvol_sr):
        vdis = create_vdi_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        teardown_vdi_chain(zfsvol_sr, vdis, (0, 1, 2))

    def test_destroy_021(self, zfsvol_sr):
        vdis = create_vdi_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        teardown_vdi_chain(zfsvol_sr, vdis, (0, 2, 1))

    def test_destroy_102(self, zfsvol_sr):
        vdis = create_vdi_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        teardown_vdi_chain(zfsvol_sr, vdis, (1, 0, 2))

    def test_destroy_120(self, zfsvol_sr):
        vdis = create_vdi_chain(zfsvol_sr, 'ZFS-local-VDI-test')
        teardown_vdi_chain(zfsvol_sr, vdis, (1, 2, 0))

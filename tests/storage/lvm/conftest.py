import logging
import pytest

from lib.commands import SSHCommandFailed

@pytest.fixture(scope='session')
def lvm_sr(host, sr_disk):
    """ An LVM SR on first host. """
    sr = host.sr_create('lvm', "LVM-local-SR", {'device': '/dev/' + sr_disk})
    yield sr
    # teardown
    try:
        sr.destroy()
    except SSHCommandFailed as e:
        # We found out that successively running the test_cold_intrapool_migration and
        # the test_live_intrapool_migration may leave a VDI chain at teardown,
        # after the VM destroy, that is not correctly garbage collected before the SR
        # destroy (the parent VDI remains).
        # Mar 22 19:22:31 r620-q2 SMGC: [29353] SR 4659 ('LVM-local-SR') (6 VDIs in 5 VHD trees):
        # Mar 22 19:22:31 r620-q2 SMGC: [29353]         *8341e744[VHD](2.000G//2.012G|n)
        # Mar 22 19:22:31 r620-q2 SMGC: [29353]             *53b46c10[VHD](2.000G//40.000M|n)
        # Mar 22 19:22:31 r620-q2 SMGC: [29353]         *e7657971[VHD](2.000G//2.012G|n)
        # Mar 22 19:22:31 r620-q2 SMGC: [29353]         *43b9baf6[VHD](2.000G//8.000M|n)
        # Mar 22 19:22:31 r620-q2 SMGC: [29353]         *080f8024[VHD](2.000G//8.000M|n)
        # Mar 22 19:22:31 r620-q2 SMGC: [29353]         *4f3fa3ba[VHD](2.000G//172.000M|n)
        # Mar 22 19:22:31 r620-q2 SMGC: [29353]
        # Mar 22 19:22:31 r620-q2 SMGC: [29353] Found 5 VDIs for deletion:
        # Mar 22 19:22:31 r620-q2 SMGC: [29353]   *53b46c10[VHD](2.000G//40.000M|n)
        # Mar 22 19:22:31 r620-q2 SMGC: [29353]   *e7657971[VHD](2.000G//2.012G|n)
        # Mar 22 19:22:31 r620-q2 SMGC: [29353]   *43b9baf6[VHD](2.000G//8.000M|n)
        # Mar 22 19:22:31 r620-q2 SMGC: [29353]   *080f8024[VHD](2.000G//8.000M|n)
        # Mar 22 19:22:31 r620-q2 SMGC: [29353]   *4f3fa3ba[VHD](2.000G//172.000M|n)
        # Re-attach the SR and force another garbage collection (that removed the last VDI
        # in our tests) and then attempt another sr delete.
        # Here's what the second GC run looks like:
        # Mar 22 19:22:43 r620-q2 SMGC: [30330] SR 4659 ('LVM-local-SR') (1 VDIs in 1 VHD trees):
        # Mar 22 19:22:43 r620-q2 SMGC: [30330]         *8341e744[VHD](2.000G//2.012G|n)
        # Mar 22 19:22:43 r620-q2 SMGC: [30330]
        # Mar 22 19:22:43 r620-q2 SMGC: [30330] Found 1 VDIs for deletion:
        # Mar 22 19:22:43 r620-q2 SMGC: [30330]   *8341e744[VHD](2.000G//2.012G|n)
        if "the SR is not empty" in e.stdout:
            # reattach the SR
            sr.plug_pbds()
            # force garbage collection
            sr.force_gc()
            # run sr.destroy() again
            sr.destroy()

@pytest.fixture(scope='module')
def vm_on_lvm_sr(host, lvm_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=lvm_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)

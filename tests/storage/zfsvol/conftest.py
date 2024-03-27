import logging
import pytest

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state, sr_disk_wiped

@pytest.fixture(scope='package')
def host_with_zfsvol(host_with_saved_yum_state):
    host = host_with_saved_yum_state
    host.yum_install(['zfs']) # FIXME xcp-ng-xapi-storage-zfs-ng ?
    host.ssh(['modprobe', 'zfs'])
    yield host

@pytest.fixture(scope='package')
def zfsvol_sr(host, sr_disk_wiped, host_with_zfsvol):
    """ A ZFS Volume SR on first host. """
    sr = host.sr_create('zfs-vol', "ZFS-local-SR-test", {'device': '/dev/' + sr_disk_wiped})
    yield sr
    # teardown violently - we don't want to require manual recovery when a test fails
    sr.forget()
    host.ssh(["zpool", "destroy", "sr-" + sr.uuid])

@pytest.fixture(scope='module')
def vdi_on_zfsvol_sr(zfsvol_sr):
    vdi = zfsvol_sr.create_vdi('ZFS-local-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_zfsvol_sr(host, zfsvol_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=zfsvol_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)

import pytest

import logging

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state_toolstack_restart, sr_disk_wiped

@pytest.fixture(scope='package')
def host_with_zfsvol(host_with_saved_yum_state_toolstack_restart):
    host = host_with_saved_yum_state_toolstack_restart
    host.yum_install(['xcp-ng-xapi-storage-volume-zfsvol'])
    host.restart_toolstack(verify=True)
    yield host

@pytest.fixture(scope='package')
def zfsvol_sr(host, sr_disk_wiped, host_with_zfsvol):
    """ A ZFS Volume SR on first host. """
    device = '/dev/' + sr_disk_wiped
    sr = host.sr_create('zfs-vol', "ZFS-local-SR-test", {'device': device})
    yield sr
    # teardown violently - we don't want to require manual recovery when a test fails
    sr.forget()
    host.ssh(["wipefs", "-a", device])

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

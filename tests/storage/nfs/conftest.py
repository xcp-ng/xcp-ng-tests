import pytest

import logging

from lib import config

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from lib.sr import SR

# --- Dispatch fixture for NFS versions ----------------------------------------

@pytest.fixture
def dispatch_nfs(request):
    yield request.getfixturevalue(request.param)

# --- NFS3 fixtures ------------------------------------------------------------

@pytest.fixture(scope='package')
def nfs_device_config():
    return config.sr_device_config("NFS_DEVICE_CONFIG")

@pytest.fixture(scope='package')
def nfs_sr(host, nfs_device_config):
    """ A NFS SR on first host. """
    sr = host.sr_create('nfs', "NFS-SR-test", nfs_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_nfs_sr(nfs_sr: 'SR'):
    vdi = nfs_sr.create_vdi('NFS-VDI-test', virtual_size='1GiB')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_nfs_sr(host, nfs_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=nfs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)

# --- NFS4+ only fixtures ------------------------------------------------------

@pytest.fixture(scope='package')
def nfs4_device_config():
    return config.sr_device_config("NFS4_DEVICE_CONFIG")

@pytest.fixture(scope='package')
def nfs4_sr(host, nfs4_device_config):
    """ A NFS4+ SR on first host. """
    sr = host.sr_create('nfs', "NFS4-SR-test", nfs4_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_nfs4_sr(nfs4_sr):
    vdi = nfs4_sr.create_vdi('NFS4-VDI-test', virtual_size='1GiB')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_nfs4_sr(host, nfs4_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=nfs4_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)

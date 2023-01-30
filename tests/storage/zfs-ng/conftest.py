import logging
import pytest

POOL_PATH = '/zfspool'
POOL_NAME = 'zfspool'

VOLUME_NAME = 'vol0'
VOLUME_PATH = '/' + VOLUME_NAME

@pytest.fixture(scope='package')
def zfsng_sr(host):
    sr = host.sr_create('zfs-ng', "ZFS-ng-local-SR-test", {'devices': 'sdb'}, verify=True)
    yield sr
    # teardown
    sr.destroy(verify=True)

@pytest.fixture(scope='module')
def vm_on_zfs_sr(host, zfsng_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=zfsng_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)

@pytest.fixture(scope='module')
def vdi_on_zfsng_sr(zfsng_sr):
    vdi = zfsng_sr.create_vdi('ZFS-local-VDI-test', 1024*1024)
    yield vdi
    vdi.destroy()

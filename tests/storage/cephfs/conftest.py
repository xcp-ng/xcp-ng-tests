import pytest

import logging

from lib import config
from lib.common import exec_nofail, raise_errors
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM

# explicit import for package-scope fixtures
from pkgfixtures import pool_with_saved_yum_state

from typing import Generator

@pytest.fixture(scope='package')
def pool_without_ceph(host: Host) -> Generator[Pool, None, None]:
    for h in host.pool.hosts:
        assert not host.file_exists('/usr/sbin/mount.ceph'), \
            "mount.ceph must not be installed on the host at the beginning of the tests"
    yield host.pool

@pytest.fixture(scope='package')
def pool_with_ceph(pool_without_ceph: Pool, pool_with_saved_yum_state: Pool) -> Generator[Pool, None, None]:
    pool = pool_with_saved_yum_state
    for h in pool.hosts:
        h.yum_install(['centos-release-ceph-jewel'], enablerepo="base,extras")
        h.yum_install(['ceph-common'], enablerepo="base,extras")
    yield pool

@pytest.fixture(scope='package')
def cephfs_device_config() -> dict[str, str]:
    return config.sr_device_config("CEPHFS_DEVICE_CONFIG")

@pytest.fixture(scope='package')
def cephfs_sr(host: Host, cephfs_device_config: dict[str, str], pool_with_ceph: Pool) -> Generator[SR, None, None]:
    """ A CephFS SR on first host. """
    sr = host.sr_create('cephfs', "CephFS-SR-test", cephfs_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_cephfs_sr(cephfs_sr: SR) -> Generator[VDI, None, None]:
    vdi = cephfs_sr.create_vdi('CephFS-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_cephfs_sr(host: Host, cephfs_sr: SR, vm_ref: str) -> Generator[VM, None, None]:
    vm = host.import_vm(vm_ref, sr_uuid=cephfs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)

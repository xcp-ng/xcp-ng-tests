from __future__ import annotations

import pytest

import functools
import logging
import os

import lib.commands as commands

# explicit import for package-scope fixtures
from pkgfixtures import pool_with_saved_yum_state

from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from lib.host import Host
    from lib.pool import Pool

GROUP_NAME = 'linstor_group'
STORAGE_POOL_NAME = f'{GROUP_NAME}/thin_device'
LINSTOR_RELEASE_PACKAGE = 'xcp-ng-release-linstor'
LINSTOR_PACKAGE = 'xcp-ng-linstor'

@pytest.fixture(scope='package')
def lvm_disks(pool_with_unused_512B_disk: Pool,
              unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
              provisioning_type: str) -> Generator[None]:
    """
    Common LVM PVs on which a LV is created on each host of the pool.

    On each host in the pool, create PV on each of those disks whose
    DEVICE NAME exists ACROSS THE WHOLE POOL. Then make a VG out of
    all those, then a LV taking up the whole VG space.

    Return the list of device node paths for that list of devices
    used in all hosts.
    """
    hosts = pool_with_unused_512B_disk.hosts

    @functools.cache
    def host_devices(host: Host) -> list[str]:
        return [os.path.join("/dev", disk["name"]) for disk in unused_512B_disks[host][0:1]]

    for host in hosts:
        devices = host_devices(host)
        for device in devices:
            try:
                host.ssh(['pvcreate', '-ff', '-y', device])
            except commands.SSHCommandFailed as e:
                if e.stdout.endswith('Mounted filesystem?'):
                    host.ssh(['vgremove', '-f', GROUP_NAME, '-y'])
                    host.ssh(['pvcreate', '-ff', '-y', device])
                elif e.stdout.endswith('excluded by a filter.'):
                    host.ssh(['wipefs', '-a', device])
                    host.ssh(['pvcreate', '-ff', '-y', device])
                else:
                    raise e

        host.ssh(['vgcreate', GROUP_NAME] + devices)
        if provisioning_type == 'thin':
            host.ssh(['lvcreate', '-l', '100%FREE', '-T', STORAGE_POOL_NAME])

    # FIXME ought to provide storage_pool_name and get rid of that other fixture
    yield None

    for host in hosts:
        host.ssh(['vgremove', '-f', GROUP_NAME])
        for device in host_devices(host):
            host.ssh(['pvremove', device])

@pytest.fixture(scope="package")
def storage_pool_name(provisioning_type):
    return GROUP_NAME if provisioning_type == "thick" else STORAGE_POOL_NAME

@pytest.fixture(params=["thin"], scope="session")
def provisioning_type(request):
    return request.param

@pytest.fixture(scope='package')
def pool_with_linstor(hostA2, lvm_disks, pool_with_saved_yum_state):
    import concurrent.futures
    pool = pool_with_saved_yum_state

    def check_linstor_installed(host):
        if host.is_package_installed(LINSTOR_PACKAGE):
            raise Exception(
                f'{LINSTOR_PACKAGE} is already installed on host {host}. This should not be the case.'
            )

    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.map(check_linstor_installed, pool.hosts)

    def install_linstor(host):
        logging.info(f"Installing {LINSTOR_PACKAGE} on host {host}...")
        host.yum_install([LINSTOR_RELEASE_PACKAGE])
        host.yum_install([LINSTOR_PACKAGE], enablerepo="xcp-ng-linstor-testing")
        # Needed because the linstor driver is not in the xapi sm-plugins list
        # before installing the LINSTOR packages.
        host.ssh(["systemctl", "restart", "multipathd"])
        host.restart_toolstack(verify=True)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.map(install_linstor, pool.hosts)

    yield pool

    # Need to remove this package as we have separate run of `test_create_sr_without_linstor`
    # for `thin` and `thick` `provisioning_type`.
    def remove_linstor(host):
        logging.info(f"Cleaning up python-linstor from host {host}...")
        host.yum_remove(["python-linstor"])

    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.map(remove_linstor, pool.hosts)

@pytest.fixture(scope='package')
def linstor_sr(pool_with_linstor, provisioning_type, storage_pool_name):
    sr = pool_with_linstor.master.sr_create('linstor', 'LINSTOR-SR-test', {
        'group-name': storage_pool_name,
        'redundancy': str(min(len(pool_with_linstor.hosts), 3)),
        'provisioning': provisioning_type
    }, shared=True)
    yield sr
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_linstor_sr(linstor_sr):
    vdi = linstor_sr.create_vdi('LINSTOR-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_linstor_sr(host, linstor_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=linstor_sr.uuid)
    yield vm
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)

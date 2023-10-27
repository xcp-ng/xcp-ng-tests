import logging
import pytest

import lib.commands as commands

# explicit import for package-scope fixtures
from pkgfixtures import pool_with_saved_yum_state

GROUP_NAME = 'linstor_group'
STORAGE_POOL_NAME = f'{GROUP_NAME}/thin_device'
LINSTOR_RELEASE_PACKAGE = 'xcp-ng-release-linstor'
LINSTOR_PACKAGE = 'xcp-ng-linstor'

@pytest.fixture(scope='package')
def lvm_disk(host, sr_disk_for_all_hosts):
    device = '/dev/' + sr_disk_for_all_hosts
    hosts = host.pool.hosts

    for host in hosts:
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

        host.ssh(['vgcreate', GROUP_NAME, device])
        host.ssh(['lvcreate', '-l', '100%FREE', '-T', STORAGE_POOL_NAME])

    yield device

    for host in hosts:
        host.ssh(['vgremove', '-f', GROUP_NAME])
        host.ssh(['pvremove', device])

@pytest.fixture(scope='package')
def pool_with_linstor(hostA2, lvm_disk, pool_with_saved_yum_state):
    pool = pool_with_saved_yum_state
    for host in pool.hosts:
        if host.is_package_installed(LINSTOR_PACKAGE):
            raise Exception(
                f'{LINSTOR_PACKAGE} is already installed on host {host}. This should not be the case.'
            )

    for host in pool.hosts:
        host.yum_install([LINSTOR_RELEASE_PACKAGE])
        host.yum_install([LINSTOR_PACKAGE], enablerepo="xcp-ng-linstor-testing")
        # Needed because the linstor driver is not in the xapi sm-plugins list
        # before installing the LINSTOR packages.
        host.restart_toolstack(verify=True)

    yield pool

@pytest.fixture(scope='package')
def linstor_sr(pool_with_linstor):
    sr = pool_with_linstor.master.sr_create('linstor', 'LINSTOR-SR-test', {
        'group-name': STORAGE_POOL_NAME,
        'redundancy': str(min(len(pool_with_linstor.hosts), 3)),
        'provisioning': 'thin'
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

import logging
import pytest

from lib.common import wait_for_not

from tests.storage.linstor import GROUP_NAME, create_linstor_sr

LINSTOR_RELEASE_PACKAGE = 'xcp-ng-release-linstor'
LINSTOR_PACKAGE = 'xcp-ng-linstor'

@pytest.fixture(scope='package')
def vg_for_all_hosts(host, sr_disk_for_all_hosts):
    hosts = host.pool.hosts

    disk = '/dev/' + sr_disk_for_all_hosts

    for host in hosts:
        host.ssh(['pvcreate', '-ff', '-y', disk])
        host.ssh(['vgcreate', GROUP_NAME, disk])

    yield GROUP_NAME

    for host in hosts:
        host.ssh(['vgremove', '-y', GROUP_NAME])
        host.ssh(['pvremove', '-y', disk])

@pytest.fixture(scope='package')
def hosts_with_linstor(host):
    master = host
    hosts = master.pool.hosts

    # Configure master host.
    master.yum_save_state()
    master.yum_install([LINSTOR_RELEASE_PACKAGE])
    master.yum_install([LINSTOR_PACKAGE])

    # Configure slaves.
    for host in hosts[1:]:
        host.yum_save_state()
        host.yum_install([LINSTOR_RELEASE_PACKAGE])
        host.yum_install([LINSTOR_PACKAGE])

    yield hosts

    for host in hosts:
        host.yum_restore_saved_state()

@pytest.fixture(scope='package')
def linstor_sr(hosts_with_linstor, vg_for_all_hosts):
    sr = create_linstor_sr('LINSTOR-SR-test', hosts_with_linstor)
    yield sr
    sr.destroy()

@pytest.fixture(scope='module')
def vm_on_linstor_sr(host, linstor_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=linstor_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)

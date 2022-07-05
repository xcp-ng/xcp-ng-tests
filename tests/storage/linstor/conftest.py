import logging
import pytest

from lib.common import wait_for_not

GROUP_NAME = 'linstor_group'
LINSTOR_PACKAGES = ['drbd', 'kmod-drbd', 'linstor-client', 'linstor-controller', 'linstor-satellite', 'python-linstor']

# FIXME: make fixtures robust: clean behind if something fails in a setup or teardown

@pytest.fixture(scope='package')
def lvm_disks(host):
    disks = []
    hosts = host.pool.hosts

    for host in hosts:
        local_disks = host.disks()
        assert len(local_disks) > 1, "at least two disks are required"
        disks.append('/dev/' + local_disks[1])

    for i in range(len(hosts)):
        hosts[i].ssh(['pvcreate', '-ff', '-y', disks[i]])
        hosts[i].ssh(['vgcreate', GROUP_NAME, disks[i]])

    yield disks

    for i in range(len(hosts)):
        hosts[i].ssh(['vgremove', GROUP_NAME])
        hosts[i].ssh(['pvremove', disks[i]])

def check_linstor_packages(host):
    if not host.check_packages_available(LINSTOR_PACKAGES):
        raise Exception('Unable to find LINSTOR packages in the yum repositories of {}'.format(host))

@pytest.fixture(scope='package')
def hosts_with_linstor(host, additional_repos):
    master = host
    hosts = master.pool.hosts

    for host in hosts:
        check_linstor_packages(host)

    # Configure master host.
    master.yum_save_state()
    master.yum_install(LINSTOR_PACKAGES)
    master.ssh(['systemctl', 'restart', 'linstor-satellite'])
    master.ssh(['systemctl', 'restart', 'linstor-controller'])

    # Waiting for startup...
    try:
        wait_for_not(lambda: master.ssh_with_result(['linstor', 'node', 'list']).returncode)
    except Exception as e:
        master.yum_restore_saved_state()
        raise e

    # Configure slaves.
    for host in hosts[1:]:
        host.yum_save_state()
        host.yum_install(LINSTOR_PACKAGES)
        host.ssh(['systemctl', 'restart', 'linstor-satellite'])

    yield hosts

    for host in hosts:
        host.yum_restore_saved_state()

def delete_linstor_nodes(hosts_with_linstor):
    master = hosts_with_linstor[0]
    master_ip = master.pool.host_ip(master.uuid)
    control_arg = '--controllers=' + master_ip

    for host in hosts_with_linstor:
        try:
            host.ssh(['linstor', control_arg, 'node', 'delete', '`uname -n`'])
        except Exception:
            logging.error('Failed to delete properly node on host {}'.format(host))
            pass

def create_linstor_sr(hosts_with_linstor):
    master = hosts_with_linstor[0]
    pool = master.pool
    master_ip = pool.host_ip(master.uuid)
    control_arg = '--controllers=' + master_ip

    delete_linstor_nodes(hosts_with_linstor)

    for host in hosts_with_linstor[1:]:
        host_ip = pool.host_ip(host.uuid)
        host.ssh(['linstor', control_arg, 'node', 'create', '`uname -n`', host_ip, '--node-type', 'Satellite'])
    master.ssh(['linstor', 'node', 'create', '`uname -n`', master_ip, '--node-type', 'Combined'])

    try:
        return master.sr_create('linstor', 'LINSTOR-SR-test', {
            'hosts': ','.join([host.hostname() for host in hosts_with_linstor]),
            'group-name': GROUP_NAME,
            'redundancy': len(hosts_with_linstor),
            'provisioning': 'thick'
        }, shared=True)
    except Exception as e:
        delete_linstor_nodes(hosts_with_linstor)
        raise e

def destroy_linstor_sr(hosts_with_linstor, sr):
    sr.destroy(verify=True, force=True)
    delete_linstor_nodes(hosts_with_linstor)

@pytest.fixture(scope='package')
def linstor_sr(hosts_with_linstor, lvm_disks):
    sr = create_linstor_sr(hosts_with_linstor)
    yield sr
    destroy_linstor_sr(hosts_with_linstor, sr)

@pytest.fixture(scope='module')
def vdi_on_linstor_sr(linstor_sr):
    vdi = linstor_sr.create_vdi('LINSTOR-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_linstor_sr(host, linstor_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=linstor_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)

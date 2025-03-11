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
def lvm_disks(host, sr_disks_for_all_hosts, provisioning_type):
    devices = [f"/dev/{disk}" for disk in sr_disks_for_all_hosts]
    hosts = host.pool.hosts

    for host in hosts:
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

    yield devices

    for host in hosts:
        devices = host.ssh('vgs ' + GROUP_NAME + ' -o pv_name --no-headings').split("\n")
        host.ssh(['vgremove', '-f', GROUP_NAME])
        for device in devices:
            host.ssh(['pvremove', '-ff', '-y', device.strip()])

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

@pytest.fixture(scope='module')
def prepare_linstor_packages(hostB1):
    if not hostB1.is_package_installed(LINSTOR_PACKAGE):
        logging.info("Installing %s on host %s", LINSTOR_PACKAGE, hostB1)
        hostB1.yum_install([LINSTOR_RELEASE_PACKAGE])
        hostB1.yum_install([LINSTOR_PACKAGE], enablerepo="xcp-ng-linstor-testing")
        # Needed because the linstor driver is not in the xapi sm-plugins list
        # before installing the LINSTOR packages.
        hostB1.ssh(["systemctl", "restart", "multipathd"])
        hostB1.restart_toolstack(verify=True)
    yield
    hostB1.yum_remove([LINSTOR_PACKAGE]) # Package cleanup

@pytest.fixture(scope='module')
def setup_lvm_on_host(hostB1):
    # Ensure that the host has disks available to use, we do not care about disks symmetry across pool
    # We need the disk to be "raw" (non LVM_member etc) to use
    disks = [d for d in hostB1.available_disks() if hostB1.raw_disk_is_available(d)]
    assert disks, "hostB1 requires at least one raw disk"
    devices = [f"/dev/{d}" for d in disks]

    for disk in devices:
        logging.info("Found Disk %s", disk)
        hostB1.ssh(['pvcreate', disk])
    hostB1.ssh(['vgcreate', GROUP_NAME] + devices)

    yield "linstor_group", devices

@pytest.fixture(scope='module')
def join_host_to_pool(host, hostB1):
    assert len(hostB1.pool.hosts) == 1, "This test requires second host to be a single host"
    original_pool = hostB1.pool
    logging.info("Joining host %s to pool %s", hostB1, host)
    hostB1.join_pool(host.pool)
    yield
    host.pool.eject_host(hostB1)
    hostB1.pool = original_pool

@pytest.fixture(scope='module')
def vm_with_reboot_check(vm_on_linstor_sr):
    vm = vm_on_linstor_sr
    vm.start()
    vm.wait_for_os_booted()
    yield vm
    vm.shutdown(verify=True)
    # Ensure VM is able to start and shutdown on modified SR
    vm.start()
    vm.wait_for_os_booted()
    vm.shutdown(verify=True)

@pytest.fixture(scope='module')
def evacuate_host_and_prepare_removal(host, hostA2, vm_with_reboot_check):
    assert len(host.pool.hosts) >= 3, "This test requires Pool to have more than 3 hosts"

    vm = vm_with_reboot_check
    try:
        host.ssh(f'xe host-evacuate uuid={hostA2.uuid}')
    except Exception as e:
        logging.warning("Host evacuation failed: %s", e)
        if "lacks the feature" in getattr(e, "stdout", ""):
            vm.shutdown(verify=True, force_if_fails=True)
            host.ssh(f'xe host-evacuate uuid={hostA2.uuid}')
            available_hosts = [h.uuid for h in host.pool.hosts if h.uuid != hostA2.uuid]
            if available_hosts:
                vm.start(on=available_hosts[0])
    yield

@pytest.fixture(scope='module')
def remove_host_from_linstor(host, hostA2, linstor_sr, evacuate_host_and_prepare_removal):
    import time
    # Select a host that is not running the LINSTOR controller (port 3370)
    linstor_controller_host = None
    for h in host.pool.hosts:
        if h.ssh_with_result(["ss -tuln | grep :3370"]).returncode == 0:
            linstor_controller_host = h
            break

    # If the controller is running on the host to be ejected (hostA2), stop the services first
    if linstor_controller_host and linstor_controller_host.uuid == hostA2.uuid:
        logging.info("Ejecting host is running LINSTOR controller, stopping services first.")
        hostA2.ssh("systemctl stop linstor-controller.service")
        hostA2.ssh("systemctl stop drbd-reactor.service")
        hostA2.ssh("systemctl stop drbd-graceful-shutdown.service")
        time.sleep(30)  # Give time for services to stop

    ejecting_host = hostA2.xe('host-param-get', {'uuid': hostA2.uuid, 'param-name': 'name-label'})
    controller_option = "--controllers=" + ",".join([m.hostname_or_ip for m in host.pool.hosts])

    hostA2.ssh("systemctl stop linstor-satellite.service")

    pbd = host.xe('pbd-list', {'sr-uuid': linstor_sr.uuid, 'host-uuid': hostA2.uuid}, minimal=True)
    host.xe('pbd-unplug', {'uuid': pbd})

    logging.info(host.ssh_with_result(["linstor", controller_option, "node", "delete", ejecting_host]).stdout)
    host.pool.eject_host(hostA2)

    yield

    logging.info("Rejoining hostA2 to the pool after test")
    hostA2.join_pool(host.pool)
    # We dont want linstor services to be running on a deleted node
    hostA2.ssh("systemctl stop linstor-satellite.service")
    hostA2.ssh("systemctl stop drbd-graceful-shutdown.service")
    # TODO: Package list is not retained in teardown
    # hostA2.saved_packages_list = hostA2.packages()
    # hostA2.saved_rollback_id = hostA2.get_last_yum_history_tid()

@pytest.fixture(scope='module')
def get_sr_size(linstor_sr):
    sr = linstor_sr
    sr_size = int(sr.pool.master.xe('sr-param-get', {'uuid': sr.uuid, 'param-name': 'physical-size'}))
    logging.info("SR Size: %s", sr_size)
    yield
    new_sr_size = int(sr.pool.master.xe('sr-param-get', {'uuid': sr.uuid, 'param-name': 'physical-size'}))
    logging.info("New SR Size vs Old SR Size: %s vs %s", new_sr_size, sr_size)
    assert new_sr_size != sr_size, "SR size did not change"

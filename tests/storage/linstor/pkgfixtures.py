from __future__ import annotations

import pytest

import logging
from dataclasses import dataclass

import lib.commands as commands

try:
    from data import LINSTOR_REDUNDANCY  # type: ignore
except ImportError:
    LINSTOR_REDUNDANCY = 2

from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from lib.host import Host
    from lib.pool import Pool
    from lib.sr import SR

# Due to a bug in the way pytest handles the setup and teardown of package-scoped fixtures,
# we moved the following fixtures out of this package's conftest.py.
# To workaround the bug, the fixture must be imported either in a package's own conftest.py,
# or directly in a test module. Then the fixtures will truly be handled as package-scoped.
# Reference: https://github.com/pytest-dev/pytest/issues/8189

GROUP_NAME = 'linstor_group'
STORAGE_POOL_NAME = f'{GROUP_NAME}/thin_device'
LINSTOR_RELEASE_PACKAGE = 'xcp-ng-release-linstor'
LINSTOR_PACKAGE = 'xcp-ng-linstor'

@dataclass
class LinstorConfig:
    uninstall_linstor: bool = True

@pytest.fixture(scope='package')
def _linstor_config() -> LinstorConfig:
    return LinstorConfig()

@pytest.fixture(scope='package')
def lvm_disk_paths(
    unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
) -> dict[Host, list[str]]:
    return {host: [disk.path for disk in disks[0:1]] for (host, disks) in unused_512B_disks.items()}

@pytest.fixture(scope='package')
def lvm_disks(
    pool_with_unused_512B_disk: Pool,
    lvm_disk_paths: dict[Host, list[str]],
    provisioning_type: str,
) -> Generator[None, None, None]:
    """
    Common LVM PVs on which a LV is created on each host of the pool.

    On each host in the pool, create PV on each of those disks whose
    DEVICE NAME exists ACROSS THE WHOLE POOL. Then make a VG out of
    all those, then a LV taking up the whole VG space.

    Return the list of device node paths for that list of devices
    used in all hosts.
    """
    hosts = pool_with_unused_512B_disk.hosts

    for host in hosts:
        devices = lvm_disk_paths[host]
        for device in devices:
            try:
                host.ssh(f'pvcreate -ff -y {device}')
            except commands.SSHCommandFailed as e:
                if e.stdout.endswith('Mounted filesystem?'):
                    host.ssh(f'vgremove -f {GROUP_NAME} -y')
                    host.ssh(f'pvcreate -ff -y {device}')
                elif e.stdout.endswith('excluded by a filter.'):
                    host.ssh(f'wipefs -a {device}')
                    host.ssh(f'pvcreate -ff -y {device}')
                else:
                    raise e

        host.ssh(f'vgcreate {GROUP_NAME} ' + ' '.join(devices))
        if provisioning_type == 'thin':
            host.ssh(f'lvcreate -l 100%FREE -T {STORAGE_POOL_NAME}')

    # FIXME ought to provide storage_pool_name and get rid of that other fixture
    yield None

    for host in hosts:
        host.ssh(f'vgremove -f {GROUP_NAME}')
        for device in lvm_disk_paths[host]:
            host.ssh(f'pvremove {device}')

@pytest.fixture(scope='package')
def storage_pool_name(provisioning_type: str) -> str:
    return GROUP_NAME if provisioning_type == 'thick' else STORAGE_POOL_NAME

@pytest.fixture(scope='package')
def pool_with_linstor(
    hostA2: Host,
    lvm_disks: None,
    pool_with_saved_yum_state: Pool,
    _linstor_config: LinstorConfig
) -> Generator[Pool, None, None]:
    import concurrent.futures
    pool = pool_with_saved_yum_state

    def check_linstor_installed(host: Host) -> None:
        if host.is_package_installed(LINSTOR_PACKAGE):
            raise Exception(
                f'{LINSTOR_PACKAGE} is already installed on host {host}. This should not be the case.'
            )

    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.map(check_linstor_installed, pool.hosts)

    def install_linstor(host: Host) -> None:
        logging.info(f"Installing {LINSTOR_PACKAGE} on host {host}...")
        host.yum_install([LINSTOR_RELEASE_PACKAGE])
        host.yum_install([LINSTOR_PACKAGE], enablerepo="xcp-ng-linstor-testing")
        # Needed because the linstor driver is not in the xapi sm-plugins list
        # before installing the LINSTOR packages.
        host.ssh('systemctl restart multipathd')
        host.restart_toolstack(verify=True)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.map(install_linstor, pool.hosts)

    yield pool

    def _disable_yum_rollback(host: Host) -> None:
        host.saved_rollback_id = None

    if not _linstor_config.uninstall_linstor:
        pool.exec_on_hosts_on_error_continue(_disable_yum_rollback)
        return

    # Need to remove this package as we have separate run of `test_create_sr_without_linstor`
    # for `thin` and `thick` `provisioning_type`.
    def remove_linstor(host: Host) -> None:
        logging.info(f"Cleaning up python-linstor from host {host}...")
        host.yum_remove(["python-linstor"])
        host.restart_toolstack(verify=True)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.map(remove_linstor, pool.hosts)

@pytest.fixture(scope='package')
def linstor_redundancy(pool_with_linstor: Pool) -> int:
    return min(len(pool_with_linstor.hosts), LINSTOR_REDUNDANCY)

@pytest.fixture(scope='package')
def linstor_sr(
    pool_with_linstor: Pool,
    linstor_redundancy: int,
    provisioning_type: str,
    storage_pool_name: str,
    lvm_disks: None,
    _linstor_config: LinstorConfig
) -> Generator[SR, None, None]:
    sr = pool_with_linstor.master.sr_create('linstor', 'LINSTOR-SR-test', {
        'group-name': storage_pool_name,
        'redundancy': str(linstor_redundancy),
        'provisioning': provisioning_type
    }, shared=True)
    yield sr
    try:
        sr.destroy()
    except Exception as e:
        _linstor_config.uninstall_linstor = False
        raise pytest.fail("Could not destroy linstor SR, leaving packages in place for manual cleanup") from e

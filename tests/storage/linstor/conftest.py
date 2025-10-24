from __future__ import annotations

import pytest

import functools
import json
import logging
import os
from dataclasses import dataclass

import lib.commands as commands
from lib.common import safe_split
from lib.sr import SR

# explicit import for package-scope fixtures
from pkgfixtures import pool_with_saved_yum_state

from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from lib.host import Host
    from lib.pool import Pool
    from lib.sr import SR
    from lib.vdi import VDI
    from lib.vm import VM

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
def lvm_disks(
    pool_with_unused_512B_disk: Pool,
    unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
    provisioning_type: str,
) -> Generator[None]:
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
def storage_pool_name(provisioning_type: str) -> str:
    return GROUP_NAME if provisioning_type == "thick" else STORAGE_POOL_NAME

@pytest.fixture(params=["thin"], scope="session")
def provisioning_type(request: pytest.FixtureRequest) -> str:
    return request.param

@pytest.fixture(scope='package')
def pool_with_linstor(
    hostA2: Host,
    lvm_disks: None,
    pool_with_saved_yum_state: Pool,
    _linstor_config: LinstorConfig
) -> Generator[Pool]:
    import concurrent.futures
    pool = pool_with_saved_yum_state

    def check_linstor_installed(host: Host):
        if host.is_package_installed(LINSTOR_PACKAGE):
            raise Exception(
                f'{LINSTOR_PACKAGE} is already installed on host {host}. This should not be the case.'
            )

    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.map(check_linstor_installed, pool.hosts)

    def install_linstor(host: Host):
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

    def _disable_yum_rollback(host: Host):
        host.saved_rollback_id = None

    if not _linstor_config.uninstall_linstor:
        pool.exec_on_hosts_on_error_continue(_disable_yum_rollback)
        return

    # Need to remove this package as we have separate run of `test_create_sr_without_linstor`
    # for `thin` and `thick` `provisioning_type`.
    def remove_linstor(host: Host):
        logging.info(f"Cleaning up python-linstor from host {host}...")
        host.yum_remove(["python-linstor"])
        host.restart_toolstack(verify=True)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.map(remove_linstor, pool.hosts)

@pytest.fixture(scope='package')
def linstor_sr(
    pool_with_linstor: Pool,
    provisioning_type: str,
    storage_pool_name: str,
    _linstor_config: LinstorConfig
) -> Generator[SR]:
    sr = pool_with_linstor.master.sr_create('linstor', 'LINSTOR-SR-test', {
        'group-name': storage_pool_name,
        'redundancy': str(min(len(pool_with_linstor.hosts), 3)),
        'provisioning': provisioning_type
    }, shared=True)
    yield sr
    try:
        sr.destroy()
    except Exception as e:
        _linstor_config.uninstall_linstor = False
        raise pytest.fail("Could not destroy linstor SR, leaving packages in place for manual cleanup") from e

@pytest.fixture(scope='module')
def vdi_on_linstor_sr(linstor_sr: SR) -> Generator[VDI]:
    vdi = linstor_sr.create_vdi('LINSTOR-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_linstor_sr(host: Host, linstor_sr: SR, vm_ref: str):
    vm = host.import_vm(vm_ref, sr_uuid=linstor_sr.uuid)
    yield vm
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)

@pytest.fixture(scope='function')
def host_and_corrupted_vdi_on_linstor_sr(host: Host, linstor_sr: SR, vm_ref: str):
    vm: VM = host.import_vm(vm_ref, sr_uuid=linstor_sr.uuid)
    pool: Pool = host.pool
    master: Host = pool.master

    def get_vdi_volume_name_from_linstor() -> str:
        result = master.ssh([
            "linstor-kv-tool",
            "--dump-volumes",
            "-g",
            f"xcp-sr-{GROUP_NAME}_thin_device"
        ])
        volumes = json.loads(result)
        for k, v in volumes.items():
            path = safe_split(k, "/")
            if len(path) < 4:
                continue
            uuid = path[2]
            data_type = path[3]
            if uuid == vdi_uuid and data_type == "volume-name":
                return v
        raise FileNotFoundError(f"Could not find matching linstor volume for `{vdi_uuid}`")

    def get_vdi_host(path: str) -> Host:
        for h in pool.hosts:
            result = h.ssh(["test", "-e", path], simple_output=False, check=False)
            if result.returncode == 0:
                return h
        raise FileNotFoundError(f"Could not find matching host for `{vdi_uuid}`")

    try:
        vdi_uuid: str = next((
            vdi.uuid for vdi in vm.vdis if vdi.sr.uuid == linstor_sr.uuid
        ))

        volume_name = get_vdi_volume_name_from_linstor()
        lv_path = f"/dev/{GROUP_NAME}/{volume_name}_00000"
        vdi_host = get_vdi_host(lv_path)
        logging.info("[%s]: corrupting `%s`", host, lv_path)
        vdi_host.ssh([
            "dd",
            "if=/dev/urandom",
            f"of={lv_path}",
            "bs=4096",
            # Lower values seems to go undetected sometimes
            "count=10000" # ~40MB
        ])
        yield vm, vdi_host, volume_name
    finally:
        logging.info("<< Destroy corrupted VDI")
        vm.destroy(verify=True)

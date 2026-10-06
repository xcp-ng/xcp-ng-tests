from __future__ import annotations

import pytest

import logging

from lib import config
from lib.host import Host
from lib.sr import SR
from lib.vdi import ImageFormat
from lib.vm import VM
from tests.ha.ha import assert_pool_ready, destroy_ha_vdis, restore_original_master, restore_pool_after_ha

from typing import Generator

@pytest.fixture(scope='package')
def nfs_device_config() -> dict[str, str]:
    return config.sr_device_config('NFS_DEVICE_CONFIG')


@pytest.fixture(scope='package')
def nfs_sr(host: Host, image_format: ImageFormat, nfs_device_config: dict[str, str]) -> Generator[SR, None, None]:
    assert_pool_ready(host.pool)
    sr = host.sr_create(
        'nfs', 'NFS-SR-test', nfs_device_config | {'preferred-image-formats': image_format}, shared=True
    )
    yield sr
    restore_pool_after_ha(host.pool)
    restore_original_master(host.pool)
    # sr-destroy refuses a non-empty SR.
    destroy_ha_vdis(host.pool, sr)
    sr.destroy()


@pytest.fixture(scope='module')
def ha_protected_vm(host: Host, nfs_sr: SR, vm_ref: str) -> Generator[VM, None, None]:
    vm = host.import_vm(vm_ref, sr_uuid=nfs_sr.uuid)
    yield vm
    logging.info(f'<< Destroy HA protected VM {vm.uuid}')
    # Scenarios leave the survivor as master.
    restore_pool_after_ha(host.pool)
    restore_original_master(host.pool)
    vm.destroy(verify=True)
